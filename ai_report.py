from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from openai import OpenAI

from config import config
from database import (
    build_report_source,
    get_weekly_report,
    list_report_periods,
    save_generated_report,
)


class AIReportError(RuntimeError):
    """AI診察レポートの作成に失敗したときの例外です。"""


SYSTEM_INSTRUCTIONS = """
あなたは、精神科・心療内科の診察時に本人が医師へ状況を伝えやすくするため、
7日間の生活記録を整理する文章作成アシスタントです。

次のルールを必ず守ってください。

【目的】
- 本人が記録した事実を、医師が短時間で確認しやすい日本語へ整理する
- 心の状態、不安、電車利用、日常生活、気圧との時間的な重なりをまとめる
- 次回診察で相談したい点を整理する

【禁止事項】
- 病名を診断しない
- 薬の開始・中止・増減を勧めない
- 記録にない出来事や症状を作らない
- 気圧が症状の原因だと断定しない
- 医師の代わりになる判断をしない
- 本人を責める表現を使わない
- 過度に深刻化したり、逆に軽視したりしない

【書き方】
- 日本語で書く
- 医師へそのまま見せられる自然な文章にする
- 事実と推測を分ける
- 記録が少ない項目は「記録が少なく判断できない」と明記する
- 気圧との関係は慎重な表現にする
- 長すぎない文章にする
- Markdownの見出しと箇条書きを使用する

【必須構成】
# 7日間の概要
# 心の状態
# 不安の状態
# 電車利用
# 日常生活でできたこと・難しかったこと
# 気圧との重なり
# 診察で相談したいこと
# 記録上の注意
""".strip()


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _validate_period(period_start: date, period_end: date) -> None:
    if period_end < period_start:
        raise AIReportError("レポート終了日が開始日より前になっています。")

    if (period_end - period_start).days + 1 != 7:
        raise AIReportError("AI診察レポートは7日間単位で作成してください。")


def _build_user_prompt(source: dict[str, Any]) -> str:
    source_json = json.dumps(_json_safe(source), ensure_ascii=False, indent=2)

    return f"""
以下は、AmeCareに本人が入力した7日間の記録です。

このデータだけを根拠に、診察時に医師へ見せるためのレポートを作成してください。
空欄や記録のない日は、推測で補わないでください。

心の強さと不安の強さは、それぞれ1～5の本人評価です。
電車結果は以下の意味です。
- success: ◎ 乗れた
- partial: △ 一部乗れた
- failed: × 乗れなかった
- no_plan: － 乗る予定なし

【記録データ】
{source_json}
""".strip()


def _extract_output_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    collected: list[str] = []
    for output_item in getattr(response, "output", []) or []:
        for content_item in getattr(output_item, "content", []) or []:
            text = getattr(content_item, "text", None)
            if isinstance(text, str) and text.strip():
                collected.append(text.strip())

    result = "\n\n".join(collected).strip()
    if not result:
        raise AIReportError("AIからレポート本文が返されませんでした。")
    return result


def generate_weekly_report(
    *,
    period_start: date,
    period_end: date,
    overwrite: bool = False,
) -> dict[str, Any]:
    _validate_period(period_start, period_end)

    existing = get_weekly_report(period_start, period_end)
    if existing and not overwrite:
        return existing

    if not config.OPENAI_API_KEY:
        raise AIReportError(
            "OPENAI_API_KEYが設定されていません。RenderのEnvironmentへ追加してください。"
        )

    source = build_report_source(period_start, period_end)
    safe_source = _json_safe(source)
    summary = safe_source.get("summary", {})
    heart_count = int(summary.get("heart_log_count") or 0)
    train_count = int(summary.get("train_log_count") or 0)

    if heart_count == 0 and train_count == 0:
        raise AIReportError("この7日間には心ログも電車ログもありません。")

    client = OpenAI(
        api_key=config.OPENAI_API_KEY,
        timeout=60.0,
        max_retries=2,
    )

    try:
        response = client.responses.create(
            model=config.AI_MODEL,
            instructions=SYSTEM_INSTRUCTIONS,
            input=_build_user_prompt(safe_source),
        )
    except Exception as exc:
        raise AIReportError(
            "AI診察レポートの作成に失敗しました。次回アプリを開いたときに再試行します。"
        ) from exc

    generated_content = _extract_output_text(response)

    return save_generated_report(
        period_start=period_start,
        period_end=period_end,
        generated_content=generated_content,
        ai_model=config.AI_MODEL,
        source_snapshot=safe_source,
    )


def generate_missing_completed_reports() -> dict[str, Any]:
    generated = 0
    skipped = 0
    errors: list[dict[str, str]] = []
    today = date.today()

    for period in reversed(list_report_periods()):
        period_start = period["period_start"]
        period_end = period["period_end"]

        if today < period_end:
            skipped += 1
            continue

        if get_weekly_report(period_start, period_end):
            skipped += 1
            continue

        source = build_report_source(period_start, period_end)
        summary = source.get("summary", {})

        if (
            int(summary.get("heart_log_count") or 0) == 0
            and int(summary.get("train_log_count") or 0) == 0
        ):
            skipped += 1
            continue

        try:
            generate_weekly_report(
                period_start=period_start,
                period_end=period_end,
                overwrite=False,
            )
            generated += 1
        except AIReportError as exc:
            errors.append(
                {
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "message": str(exc),
                }
            )

    return {"generated": generated, "skipped": skipped, "errors": errors}


def create_report_preview(*, period_start: date, period_end: date) -> dict[str, Any]:
    _validate_period(period_start, period_end)

    source = _json_safe(build_report_source(period_start, period_end))
    summary = source.get("summary", {})
    heart_count = int(summary.get("heart_log_count") or 0)
    train_count = int(summary.get("train_log_count") or 0)
    existing_report = get_weekly_report(period_start, period_end)
    period_complete = date.today() >= period_end

    return {
        "period_start": period_start,
        "period_end": period_end,
        "heart_log_count": heart_count,
        "train_log_count": train_count,
        "has_records": heart_count > 0 or train_count > 0,
        "period_complete": period_complete,
        "can_generate": period_complete and (heart_count > 0 or train_count > 0) and not existing_report,
        "existing_report": existing_report,
    }
