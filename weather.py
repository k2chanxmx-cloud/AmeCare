from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import requests

from config import config
from database import get_latest_weather_log, save_weather_log


JST = ZoneInfo(config.APP_TIMEZONE)
ZUTOOL_API_BASE_URL = "https://zutool.jp/api/getweatherstatus"


class WeatherError(RuntimeError):
    """気圧情報の取得・解析に失敗したときの例外です。"""


def _to_float(value: Any) -> float | None:
    """数値らしい値をfloatへ変換します。"""
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    """整数らしい値をintへ変換します。"""
    if value is None:
        return None

    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _parse_api_datetime(value: Any) -> datetime | None:
    """
    頭痛ーるAPIで返される日時文字列をJSTのdatetimeへ変換します。

    想定例:
    - 2026-07-29 23
    - 2026-07-29 23:00
    - 2026-07-29T23:00:00
    """
    if not value:
        return None

    text = str(value).strip()

    formats = (
        "%Y-%m-%d %H",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%dT%H:%M:%S",
    )

    for date_format in formats:
        try:
            return datetime.strptime(text, date_format).replace(tzinfo=JST)
        except ValueError:
            continue

    try:
        parsed = datetime.fromisoformat(text)

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=JST)

        return parsed.astimezone(JST)
    except ValueError:
        return None


def _weather_text(weather_code: Any) -> str:
    """
    天気コードを大まかな日本語表示へ変換します。

    API側のコード仕様変更に備え、判別できない場合は
    元のコードをそのまま保持します。
    """
    if weather_code is None:
        return "不明"

    code_text = str(weather_code).strip()

    if not code_text:
        return "不明"

    code = _to_int(code_text)

    if code is None:
        return code_text

    first_digit = str(abs(code))[0]

    mapping = {
        "1": "晴れ",
        "2": "くもり",
        "3": "雨",
        "4": "雪",
    }

    return mapping.get(first_digit, f"天気コード {code_text}")


def _pressure_status(level: Any) -> str:
    """
    APIのpressure_levelをAmeCare用の状態へ変換します。

    0: normal
    1: caution
    2以上: warning
    """
    numeric_level = _to_int(level)

    if numeric_level is None:
        return "unknown"

    if numeric_level <= 0:
        return "normal"

    if numeric_level == 1:
        return "caution"

    return "warning"


def _pressure_trend(
    current_pressure: float | None,
    previous_pressure: float | None,
) -> str:
    """直前データとの差から上昇・下降・安定を判定します。"""
    if current_pressure is None or previous_pressure is None:
        return "unknown"

    difference = current_pressure - previous_pressure

    if difference >= 0.6:
        return "rising"

    if difference <= -0.6:
        return "falling"

    return "stable"


def _collect_hourly_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """
    yesterday / today / tomorrow等に分かれた時間別データを
    1つのリストへまとめます。
    """
    entries: list[dict[str, Any]] = []

    for key in (
        "yesterday",
        "today",
        "tomorrow",
        "dayaftertomorrow",
        "day_after_tomorrow",
    ):
        value = payload.get(key)

        if not isinstance(value, list):
            continue

        for item in value:
            if not isinstance(item, dict):
                continue

            copied = dict(item)
            copied["_source_day"] = key
            entries.append(copied)

    return entries


def _entry_datetime(
    entry: dict[str, Any],
    base_datetime: datetime,
) -> datetime | None:
    """時間別データ1件の日時を推定します。"""
    explicit_datetime = (
        entry.get("datetime")
        or entry.get("dateTime")
        or entry.get("date_time")
        or entry.get("observed_at")
    )

    parsed = _parse_api_datetime(explicit_datetime)

    if parsed:
        return parsed

    hour = _to_int(entry.get("time"))

    if hour is None or not 0 <= hour <= 23:
        return None

    source_day = entry.get("_source_day")

    day_offset = {
        "yesterday": -1,
        "today": 0,
        "tomorrow": 1,
        "dayaftertomorrow": 2,
        "day_after_tomorrow": 2,
    }.get(source_day, 0)

    target_date = (base_datetime + timedelta(days=day_offset)).date()

    return datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        hour,
        tzinfo=JST,
    )


def _pick_nearest_entry(
    payload: dict[str, Any],
    now: datetime,
) -> tuple[dict[str, Any], datetime]:
    """現在時刻に最も近い時間別データを選びます。"""
    entries = _collect_hourly_entries(payload)

    candidates: list[tuple[float, dict[str, Any], datetime]] = []

    for entry in entries:
        entry_time = _entry_datetime(entry, now)

        if entry_time is None:
            continue

        pressure = _to_float(entry.get("pressure"))

        if pressure is None:
            continue

        distance = abs((entry_time - now).total_seconds())
        candidates.append((distance, entry, entry_time))

    if not candidates:
        raise WeatherError(
            "気圧APIの応答から現在時刻に対応するデータを見つけられませんでした。"
        )

    candidates.sort(key=lambda item: item[0])
    _, selected_entry, selected_time = candidates[0]

    return selected_entry, selected_time


def fetch_weather_from_api() -> dict[str, Any]:
    """頭痛ーるの気圧情報を取得し、AmeCare用の形式に整えます。"""
    area_code = config.KIATSU_AREA_CODE
    url = f"{ZUTOOL_API_BASE_URL}/{area_code}"

    try:
        response = requests.get(
            url,
            timeout=15,
            headers={
                "Accept": "application/json",
                "User-Agent": f"{config.APP_NAME}/2.0",
            },
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise WeatherError(
            "気圧情報の取得に失敗しました。時間をおいて再度お試しください。"
        ) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise WeatherError(
            "気圧APIからJSON以外の応答が返されました。"
        ) from exc

    if not isinstance(payload, dict):
        raise WeatherError("気圧APIの応答形式が想定と異なります。")

    now = datetime.now(JST)
    selected, observed_at = _pick_nearest_entry(payload, now)

    pressure = _to_float(selected.get("pressure"))
    previous_pressure = None

    hourly_entries = _collect_hourly_entries(payload)
    timed_entries: list[tuple[datetime, float]] = []

    for entry in hourly_entries:
        entry_time = _entry_datetime(entry, now)
        entry_pressure = _to_float(entry.get("pressure"))

        if entry_time is None or entry_pressure is None:
            continue

        timed_entries.append((entry_time, entry_pressure))

    earlier_entries = [
        (entry_time, entry_pressure)
        for entry_time, entry_pressure in timed_entries
        if entry_time < observed_at
    ]

    if earlier_entries:
        earlier_entries.sort(key=lambda item: item[0], reverse=True)
        previous_pressure = earlier_entries[0][1]

    place_name = (
        payload.get("place_name")
        or payload.get("area_name")
        or config.KIATSU_AREA_NAME
    )

    return {
        "observed_at": observed_at,
        "pressure": pressure,
        "pressure_status": _pressure_status(
            selected.get("pressure_level")
        ),
        "pressure_trend": _pressure_trend(
            pressure,
            previous_pressure,
        ),
        "weather": _weather_text(selected.get("weather")),
        "area_code": area_code,
        "area_name": str(place_name),
        "raw_data": payload,
    }


def refresh_weather() -> dict[str, Any]:
    """最新の気圧情報をAPIから取得してDBへ保存します。"""
    weather = fetch_weather_from_api()

    return save_weather_log(
        observed_at=weather["observed_at"],
        pressure=weather["pressure"],
        pressure_status=weather["pressure_status"],
        weather=weather["weather"],
        pressure_trend=weather["pressure_trend"],
        area_code=weather["area_code"],
        area_name=weather["area_name"],
        raw_data=weather["raw_data"],
    )


def is_weather_stale(
    weather: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> bool:
    """保存済み気圧情報が更新期限を超えているか判定します。"""
    if not weather:
        return True

    observed_at = weather.get("observed_at")

    if not isinstance(observed_at, datetime):
        return True

    current_time = now or datetime.now(JST)

    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=JST)
    else:
        observed_at = observed_at.astimezone(JST)

    age = current_time - observed_at

    return age >= timedelta(
        minutes=config.WEATHER_REFRESH_MINUTES
    )


def get_current_weather(
    *,
    force_refresh: bool = False,
) -> dict[str, Any] | None:
    """
    現在表示する気圧情報を返します。

    - 保存データが新しければDBの値を使用
    - 期限切れならAPIから更新
    - API取得失敗時は、古い保存データがあればそれを返す
    """
    latest = get_latest_weather_log()

    if not force_refresh and not is_weather_stale(latest):
        return latest

    try:
        return refresh_weather()
    except WeatherError:
        if latest:
            fallback = dict(latest)
            fallback["is_stale"] = True
            return fallback

        raise


def weather_for_log() -> dict[str, Any]:
    """
    心ログ・電車ログ保存時に紐づける気圧情報を返します。
    """
    weather = get_current_weather()

    if not weather:
        return {}

    return {
        "pressure": weather.get("pressure"),
        "pressure_status": weather.get("pressure_status"),
        "weather": weather.get("weather"),
        "observed_at": weather.get("observed_at"),
    }


def pressure_status_label(status: str | None) -> str:
    labels = {
        "normal": "通常",
        "caution": "注意",
        "warning": "警戒",
        "unknown": "不明",
    }

    return labels.get(status or "unknown", "不明")


def pressure_trend_label(trend: str | None) -> str:
    labels = {
        "rising": "上昇中",
        "falling": "下降中",
        "stable": "安定",
        "unknown": "不明",
    }

    return labels.get(trend or "unknown", "不明")
