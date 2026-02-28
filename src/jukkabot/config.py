from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    token: str
    admin_user_ids: set[int]
    tracker_api_key: str | None
    openai_api_key: str | None
    openai_model: str
    chat_temperature: float
    chat_max_output_tokens: int
    chat_idle_timeout_seconds: int


def _env_int(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if minimum is not None and value < minimum:
        return minimum
    return value


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def load_settings() -> Settings:
    load_dotenv()
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is required.")

    raw_admins = os.getenv("ADMIN_USER_IDS", "").strip()
    admin_user_ids: set[int] = set()
    if raw_admins:
        for value in raw_admins.split(","):
            value = value.strip()
            if value:
                admin_user_ids.add(int(value))

    tracker_api_key = (
        os.getenv("TRACKER_API_KEY", "").strip() or os.getenv("TRN_API_KEY", "").strip()
    ) or None
    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip() or None
    openai_model = os.getenv("OPENAI_MODEL", "").strip() or "gpt-4.1-mini"
    chat_temperature = _env_float("CHAT_TEMPERATURE", 0.8, 0.0, 2.0)
    chat_max_output_tokens = _env_int("CHAT_MAX_OUTPUT_TOKENS", 220, minimum=1)
    chat_idle_timeout_seconds = _env_int("CHAT_IDLE_TIMEOUT_SECONDS", 300, minimum=60)

    return Settings(
        token=token,
        admin_user_ids=admin_user_ids,
        tracker_api_key=tracker_api_key,
        openai_api_key=openai_api_key,
        openai_model=openai_model,
        chat_temperature=chat_temperature,
        chat_max_output_tokens=chat_max_output_tokens,
        chat_idle_timeout_seconds=chat_idle_timeout_seconds,
    )
