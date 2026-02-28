from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    token: str
    admin_user_ids: set[int]
    tracker_api_key: str | None


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

    return Settings(
        token=token,
        admin_user_ids=admin_user_ids,
        tracker_api_key=tracker_api_key,
    )
