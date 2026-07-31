from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any, Iterator

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from config import config


@contextmanager
def get_db() -> Iterator[Any]:
    """
    PostgreSQL接続を返すコンテキストマネージャーです。

    正常終了時はcommit、例外発生時はrollbackします。
    """
    connection = psycopg2.connect(
        config.DATABASE_URL,
        cursor_factory=RealDictCursor,
        connect_timeout=10,
        sslmode="require",
    )

    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def fetch_one(query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    """1件だけ取得します。"""
    with get_db() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            row = cursor.fetchone()

    return dict(row) if row else None


def fetch_all(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """複数件を取得します。"""
    with get_db() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

    return [dict(row) for row in rows]


def execute(
    query: str,
    params: tuple[Any, ...] = (),
    *,
    returning: bool = False,
) -> dict[str, Any] | None:
    """
    INSERT / UPDATE / DELETEを実行します。

    returning=Trueの場合はRETURNING句の1件を返します。
    """
    with get_db() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)

            if returning:
                row = cursor.fetchone()
                return dict(row) if row else None

    return None


# ============================================================
# アプリ設定
# ============================================================

def get_app_settings() -> dict[str, Any]:
    """アプリ設定を1件取得します。なければ自動作成します。"""
    settings = fetch_one(
        """
        SELECT
            id,
            started_on,
            weather_area_code,
            weather_area_name,
            created_at,
            updated_at
        FROM app_settings
        ORDER BY created_at ASC
        LIMIT 1
        """
    )

    if settings:
        return settings

    created = execute(
        """
        INSERT INTO app_settings (
            started_on,
            weather_area_code,
            weather_area_name
        )
        VALUES (%s, %s, %s)
        RETURNING *
        """,
        (
            date.today(),
            config.KIATSU_AREA_CODE,
            config.KIATSU_AREA_NAME,
        ),
        returning=True,
    )

    if not created:
        raise RuntimeError("app_settings の作成に失敗しました。")

    return created


def update_app_settings(
    *,
    started_on: date | None = None,
    weather_area_code: str | None = None,
    weather_area_name: str | None = None,
) -> dict[str, Any]:
    """アプリ設定を更新します。"""
    current = get_app_settings()

    updated = execute(
        """
        UPDATE app_settings
        SET
            started_on = %s,
            weather_area_code = %s,
            weather_area_name = %s
        WHERE id = %s
        RETURNING *
        """,
        (
            started_on or current["started_on"],
            weather_area_code or current["weather_area_code"],
            weather_area_name or current["weather_area_name"],
            current["id"],
        ),
        returning=True,
    )

    if not updated:
        raise RuntimeError("app_settings の更新に失敗しました。")

    return updated


# ============================================================
# 7日間レポート期間
# ============================================================

def get_report_period(target_date: date | None = None) -> tuple[date, date]:
    """
    利用開始日を基準に、対象日が属する7日間を返します。
    """
    target = target_date or date.today()
    settings = get_app_settings()
    started_on = settings["started_on"]

    if target < started_on:
        return started_on, started_on + timedelta(days=6)

    elapsed_days = (target - started_on).days
    block_number = elapsed_days // 7
    period_start = started_on + timedelta(days=block_number * 7)
    period_end = period_start + timedelta(days=6)

    return period_start, period_end


def list_report_periods(until_date: date | None = None) -> list[dict[str, Any]]:
    """
    利用開始日から指定日までの7日間区切りを返します。
    """
    settings = get_app_settings()
    started_on = settings["started_on"]
    target = until_date or date.today()

    if target < started_on:
        target = started_on

    periods: list[dict[str, Any]] = []
    current_start = started_on

    while current_start <= target:
        current_end = current_start + timedelta(days=6)

        periods.append(
            {
                "period_start": current_start,
                "period_end": current_end,
                "is_complete": current_end < date.today(),
                "is_current": current_start <= date.today() <= current_end,
            }
        )

        current_start += timedelta(days=7)

    return list(reversed(periods))


# ============================================================
# 今日の心
# ============================================================

def get_heart_log(log_date: date) -> dict[str, Any] | None:
    return fetch_one(
        """
        SELECT *
        FROM heart_logs
        WHERE log_date = %s
        """,
        (log_date,),
    )


def save_heart_log(
    *,
    log_date: date,
    heart_level: int,
    anxiety_level: int,
    good_things: str | None,
    difficulties: str | None,
    memo: str | None,
    weather: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    1日1件の心ログを保存します。
    同じ日付が存在する場合は上書き更新します。
    """
    weather = weather or {}

    saved = execute(
        """
        INSERT INTO heart_logs (
            log_date,
            heart_level,
            anxiety_level,
            good_things,
            difficulties,
            memo,
            pressure,
            pressure_status,
            weather,
            weather_observed_at
        )
        VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s
        )
        ON CONFLICT (log_date)
        DO UPDATE SET
            heart_level = EXCLUDED.heart_level,
            anxiety_level = EXCLUDED.anxiety_level,
            good_things = EXCLUDED.good_things,
            difficulties = EXCLUDED.difficulties,
            memo = EXCLUDED.memo,
            pressure = EXCLUDED.pressure,
            pressure_status = EXCLUDED.pressure_status,
            weather = EXCLUDED.weather,
            weather_observed_at = EXCLUDED.weather_observed_at
        RETURNING *
        """,
        (
            log_date,
            heart_level,
            anxiety_level,
            good_things or None,
            difficulties or None,
            memo or None,
            weather.get("pressure"),
            weather.get("pressure_status"),
            weather.get("weather"),
            weather.get("observed_at"),
        ),
        returning=True,
    )

    if not saved:
        raise RuntimeError("心ログの保存に失敗しました。")

    return saved


def list_heart_logs(
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT *
        FROM heart_logs
        WHERE log_date BETWEEN %s AND %s
        ORDER BY log_date ASC
        """,
        (start_date, end_date),
    )


def list_recent_heart_logs(limit: int = 30) -> list[dict[str, Any]]:
    """心ログを新しい順で取得します。"""
    return fetch_all(
        """
        SELECT *
        FROM heart_logs
        ORDER BY log_date DESC
        LIMIT %s
        """,
        (limit,),
    )


# ============================================================
# 電車ログ
# ============================================================

def save_train_log(
    *,
    logged_at: datetime,
    log_date: date,
    result: str,
    travel_support: str | None,
    station_count: int | None,
    crowd_level: str | None,
    panic_flag: bool,
    failed_stage: str | None,
    before_state: str | None,
    during_state: str | None,
    after_state: str | None,
    memo: str | None,
    weather: dict[str, Any] | None = None,
) -> dict[str, Any]:
    weather = weather or {}

    saved = execute(
        """
        INSERT INTO train_logs (
            logged_at,
            log_date,
            result,
            travel_support,
            station_count,
            crowd_level,
            panic_flag,
            failed_stage,
            before_state,
            during_state,
            after_state,
            memo,
            pressure,
            pressure_status,
            weather,
            weather_observed_at
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s
        )
        RETURNING *
        """,
        (
            logged_at,
            log_date,
            result,
            travel_support or None,
            station_count,
            crowd_level or None,
            panic_flag,
            failed_stage or None,
            before_state or None,
            during_state or None,
            after_state or None,
            memo or None,
            weather.get("pressure"),
            weather.get("pressure_status"),
            weather.get("weather"),
            weather.get("observed_at"),
        ),
        returning=True,
    )

    if not saved:
        raise RuntimeError("電車ログの保存に失敗しました。")

    return saved


def get_train_log(log_id: str) -> dict[str, Any] | None:
    return fetch_one(
        """
        SELECT *
        FROM train_logs
        WHERE id = %s
        """,
        (log_id,),
    )


def list_train_logs(
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT *
        FROM train_logs
        WHERE log_date BETWEEN %s AND %s
        ORDER BY logged_at ASC
        """,
        (start_date, end_date),
    )


def list_train_logs_for_date(log_date: date) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT *
        FROM train_logs
        WHERE log_date = %s
        ORDER BY logged_at DESC
        """,
        (log_date,),
    )


def list_recent_train_logs(limit: int = 30) -> list[dict[str, Any]]:
    """電車ログを新しい順で取得します。"""
    return fetch_all(
        """
        SELECT *
        FROM train_logs
        ORDER BY logged_at DESC
        LIMIT %s
        """,
        (limit,),
    )


def delete_train_log(log_id: str) -> None:
    execute(
        """
        DELETE FROM train_logs
        WHERE id = %s
        """,
        (log_id,),
    )


# ============================================================
# 気圧履歴
# ============================================================

def save_weather_log(
    *,
    observed_at: datetime,
    pressure: float | None,
    pressure_status: str | None,
    weather: str | None,
    pressure_trend: str | None,
    area_code: str,
    area_name: str,
    raw_data: dict[str, Any] | list[Any] | None,
) -> dict[str, Any]:
    saved = execute(
        """
        INSERT INTO weather_logs (
            observed_at,
            pressure,
            pressure_status,
            weather,
            pressure_trend,
            area_code,
            area_name,
            raw_data
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (
            observed_at,
            pressure,
            pressure_status,
            weather,
            pressure_trend,
            area_code,
            area_name,
            Json(raw_data) if raw_data is not None else None,
        ),
        returning=True,
    )

    if not saved:
        raise RuntimeError("気圧情報の保存に失敗しました。")

    return saved


def get_latest_weather_log() -> dict[str, Any] | None:
    return fetch_one(
        """
        SELECT *
        FROM weather_logs
        ORDER BY observed_at DESC
        LIMIT 1
        """
    )


# ============================================================
# AI診察レポート
# ============================================================

def get_weekly_report(
    period_start: date,
    period_end: date,
) -> dict[str, Any] | None:
    return fetch_one(
        """
        SELECT *
        FROM weekly_reports
        WHERE period_start = %s
          AND period_end = %s
        """,
        (period_start, period_end),
    )


def get_weekly_report_by_id(report_id: str) -> dict[str, Any] | None:
    return fetch_one(
        """
        SELECT *
        FROM weekly_reports
        WHERE id = %s
        """,
        (report_id,),
    )


def list_weekly_reports() -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT *
        FROM weekly_reports
        ORDER BY period_start DESC
        """
    )


def save_generated_report(
    *,
    period_start: date,
    period_end: date,
    generated_content: str,
    ai_model: str,
    source_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """
    AI生成レポートを保存します。
    同じ期間のレポートがある場合は上書きします。
    """
    saved = execute(
        """
        INSERT INTO weekly_reports (
            period_start,
            period_end,
            status,
            generated_content,
            edited_content,
            ai_model,
            source_snapshot,
            generated_at
        )
        VALUES (
            %s,
            %s,
            'generated',
            %s,
            %s,
            %s,
            %s,
            CURRENT_TIMESTAMP
        )
        ON CONFLICT (period_start, period_end)
        DO UPDATE SET
            status = 'generated',
            generated_content = EXCLUDED.generated_content,
            edited_content = EXCLUDED.edited_content,
            ai_model = EXCLUDED.ai_model,
            source_snapshot = EXCLUDED.source_snapshot,
            generated_at = CURRENT_TIMESTAMP
        RETURNING *
        """,
        (
            period_start,
            period_end,
            generated_content,
            generated_content,
            ai_model,
            Json(source_snapshot),
        ),
        returning=True,
    )

    if not saved:
        raise RuntimeError("AI診察レポートの保存に失敗しました。")

    return saved


def update_report_content(
    *,
    report_id: str,
    edited_content: str,
) -> dict[str, Any]:
    saved = execute(
        """
        UPDATE weekly_reports
        SET
            edited_content = %s,
            status = 'edited'
        WHERE id = %s
        RETURNING *
        """,
        (
            edited_content,
            report_id,
        ),
        returning=True,
    )

    if not saved:
        raise RuntimeError("診察レポートの更新に失敗しました。")

    return saved


# ============================================================
# レポート作成用データ
# ============================================================

def build_report_source(
    period_start: date,
    period_end: date,
) -> dict[str, Any]:
    """
    AI診察レポートへ渡す元データをまとめます。
    """
    heart_logs = list_heart_logs(period_start, period_end)
    train_logs = list_train_logs(period_start, period_end)

    train_summary = {
        "success": 0,
        "partial": 0,
        "failed": 0,
        "no_plan": 0,
        "panic_count": 0,
        "alone_count": 0,
        "accompanied_count": 0,
    }

    for log in train_logs:
        result = log.get("result")

        if result in train_summary:
            train_summary[result] += 1

        if log.get("panic_flag"):
            train_summary["panic_count"] += 1

        if log.get("travel_support") == "alone":
            train_summary["alone_count"] += 1
        elif log.get("travel_support") == "accompanied":
            train_summary["accompanied_count"] += 1

    heart_levels = [
        int(log["heart_level"])
        for log in heart_logs
        if log.get("heart_level") is not None
    ]

    anxiety_levels = [
        int(log["anxiety_level"])
        for log in heart_logs
        if log.get("anxiety_level") is not None
    ]

    summary = {
        "heart_log_count": len(heart_logs),
        "train_log_count": len(train_logs),
        "average_heart_level": (
            round(sum(heart_levels) / len(heart_levels), 2)
            if heart_levels
            else None
        ),
        "average_anxiety_level": (
            round(sum(anxiety_levels) / len(anxiety_levels), 2)
            if anxiety_levels
            else None
        ),
        "train": train_summary,
    }

    return {
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "summary": summary,
        "heart_logs": heart_logs,
        "train_logs": train_logs,
    }


def test_database_connection() -> bool:
    """起動時や動作確認用の接続テストです。"""
    result = fetch_one("SELECT 1 AS ok")
    return bool(result and result.get("ok") == 1)
