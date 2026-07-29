from __future__ import annotations

import os
from dataclasses import dataclass


def _get_required_env(name: str) -> str:
    """必須の環境変数を取得します。未設定なら分かりやすいエラーを出します。"""
    value = os.getenv(name, "").strip()

    if not value:
        raise RuntimeError(
            f"必須の環境変数 {name} が設定されていません。"
            "Render の Environment から設定してください。"
        )

    return value


def _normalize_database_url(database_url: str) -> str:
    """
    PostgreSQL接続URLをSQLAlchemy/psycopg互換の形式へ整えます。

    一部サービスが返す postgres:// を postgresql:// に変換します。
    """
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql://", 1)

    return database_url


@dataclass(frozen=True)
class Config:
    """AmeCareの共通設定です。"""

    SECRET_KEY: str
    DATABASE_URL: str
    OPENAI_API_KEY: str | None

    KIATSU_AREA_CODE: str
    KIATSU_AREA_NAME: str

    WEATHER_REFRESH_MINUTES: int
    AI_MODEL: str

    APP_NAME: str
    APP_TIMEZONE: str

    @classmethod
    def from_env(cls) -> "Config":
        database_url = _normalize_database_url(
            _get_required_env("DATABASE_URL")
        )

        secret_key = _get_required_env("SECRET_KEY")

        openai_api_key = os.getenv("OPENAI_API_KEY", "").strip() or None

        area_code = os.getenv(
            "KIATSU_AREA_CODE",
            "13214",
        ).strip()

        area_name = os.getenv(
            "KIATSU_AREA_NAME",
            "東京・国分寺市",
        ).strip()

        weather_refresh_minutes_raw = os.getenv(
            "WEATHER_REFRESH_MINUTES",
            "60",
        ).strip()

        try:
            weather_refresh_minutes = int(weather_refresh_minutes_raw)
        except ValueError as exc:
            raise RuntimeError(
                "WEATHER_REFRESH_MINUTES は整数で設定してください。"
            ) from exc

        if weather_refresh_minutes < 1:
            raise RuntimeError(
                "WEATHER_REFRESH_MINUTES は1以上で設定してください。"
            )

        ai_model = os.getenv(
            "OPENAI_MODEL",
            "gpt-5-mini",
        ).strip()

        return cls(
            SECRET_KEY=secret_key,
            DATABASE_URL=database_url,
            OPENAI_API_KEY=openai_api_key,
            KIATSU_AREA_CODE=area_code,
            KIATSU_AREA_NAME=area_name,
            WEATHER_REFRESH_MINUTES=weather_refresh_minutes,
            AI_MODEL=ai_model,
            APP_NAME="AmeCare",
            APP_TIMEZONE="Asia/Tokyo",
        )


config = Config.from_env()
