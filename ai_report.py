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
7日間の生活記録を短く整理する文章作成アシスタントです。

目的は、医師が30秒以内で読める「診察メモ」を作ることです。

【絶対ルール】
- 300〜500文字以内
- 長文は禁止
- 箇条書き中心
- 結論を先に書く
- 同じ内容を繰り返さない
- 前置きや一般論を書かない
- 記録にない内容を作らない
- 病名を診断しない
- 薬の開始・中止・増減を提案しない
- 気圧が症状の原因だと断定しない
- 記録が少ない項目は「記録が少なく判断できない」と簡潔に書く
- 医師へそのまま見せられる自然な日本語にする

【必須形式】

【7日間まとめ】

■心
・主な状態
・特に悪かった日や変化があれば1点だけ

■不安
・主な強さ
・強くなった場面があれば1点だけ

■電車
・乗れた：○回
・一部乗れた：○回
・乗れなかった：○回
・予定なし：○回

■生活
・できたこと
・難しかったこと

■今回先生に伝えたいこと
・相談したいことを2〜3点

必要な情報だけを書き、必ず短くまとめてください。
""".strip()


def _json_safe(value: Any) -> Any:
    """date、datetime、DecimalなどをJSONへ変換可能な値にします。"""
    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]

    return value


def _validate_period(
    period_start: date,
    period_end: date,
) -> None:
    """レポート期間が正確に7日間か確認します。"""
    if period_end < period_start:
        raise AIReportError(
            "レポート終了日が開始日より前になっています。"
        )

    number_of_days = (period_end - period_start).days + 1

    if number_of_days != 7:
        raise AIReportError(
            "AI診察レポートは7日間単位で作成してください。"
        )


def _build_user_prompt(source: dict[str, Any]) -> str:
    """記録データをAIへ渡すための依頼文を作ります。"""
    source_json = json.dumps(
        _json_safe(source),
        ensure_ascii=False,
        indent=2,
    )

    return f"""
以下は、AmeCareに本人が入力した7日間の記録です。

このデータだけを根拠に、診察時に医師へ見せるための短い診察メモを作成してください。
空欄や記録のない日は、推測で補わないでください。
医師が30秒以内で読めるよう、300〜500文字以内・箇条書き中心でまとめてください。

心の強さと不安の強さは、それぞれ1〜5の本人評価です。

電車結果は以下の意味です。
- success: ◎ 乗れた
- partial: △ 一部乗れた
- failed: × 乗れなかった
- no_plan: － 乗る予定なし

移動方法は以下の意味です。
- alone: 一人
- accompanied: 付き添いあり

気圧状態は以下の意味です。
- normal: 通常
- caution: 注意
- warning: 警戒
- unknown: 不明

【記録データ】
{source_json}
""".strip()


def _extract_output_text(response: Any) -> str:
    """Responses APIの応答から本文を取り出します。"""
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
        raise AIReportError(
            "AIからレポート本文が返されませんでした。"
        )

    return result


def generate_weekly_report(
    *,
    period_start: date,
    period_end: date,
    overwrite: bool = False,
) -> dict[str, Any]:
    """
    7日間の記録からAI診察レポートを作成してDBへ保存します。

    overwrite=Falseで既存レポートがある場合は、
    APIを再実行せず保存済みレポートを返します。
    """
    _validate_period(period_start, period_end)

    existing = get_weekly_report(period_start, period_end)

    if existing and not overwrite:
        return existing

    if not config.OPENAI_API_KEY:
        raise AIReportError(
            "OPENAI_API_KEYが設定されていません。"
            "RenderのEnvironmentへ追加してください。"
        )

    source = build_report_source(period_start, period_end)
    safe_source = _json_safe(source)

    heart_count = int(
        safe_source.get("summary", {}).get("heart_log_count") or 0
    )
    train_count = int(
        safe_source.get("summary", {}).get("train_log_count") or 0
    )

    if heart_count == 0 and train_count == 0:
        raise AIReportError(
            "この7日間には心ログも電車ログもありません。"
        )

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
            "AI診察レポートの作成に失敗しました。"
            "次回アプリを開いたときに再試行します。"
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
    """
    終了済みで未作成の7日間レポートを自動生成します。

    既に作成済みの期間、記録がない期間、まだ終了していない期間は
    スキップします。
    """
    generated = 0
    skipped = 0
    errors: list[dict[str, str]] = []
    today = date.today()

    # 古い期間から順番に処理します。
    periods = list(reversed(list_report_periods()))

    for period in periods:
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

        heart_count = int(summary.get("heart_log_count") or 0)
        train_count = int(summary.get("train_log_count") or 0)

        if heart_count == 0 and train_count == 0:
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

    return {
        "generated": generated,
        "skipped": skipped,
        "errors": errors,
    }


def create_report_preview(
    *,
    period_start: date,
    period_end: date,
) -> dict[str, Any]:
    """AIを呼び出さず、対象期間の状態を確認します。"""
    _validate_period(period_start, period_end)

    source = _json_safe(
        build_report_source(period_start, period_end)
    )
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
        "can_generate": (
            period_complete
            and (heart_count > 0 or train_count > 0)
            and not existing_report
        ),
        "existing_report": existing_report,
    }
