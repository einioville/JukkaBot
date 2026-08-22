from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_LOG_FILE = Path(__file__).resolve().parents[2] / "logs" / "jukkabot.log"


@dataclass(frozen=True)
class Settings:
    token: str
    admin_user_ids: set[int]
    log_level: str = "INFO"
    log_file: Path | None = DEFAULT_LOG_FILE


def _load_log_file() -> Path | None:
    """Where the rotating log goes; ``LOG_FILE=off`` turns the file log off."""
    raw = os.getenv("LOG_FILE", "").strip()
    if not raw:
        return DEFAULT_LOG_FILE
    if raw.lower() in {"off", "none", "false"}:
        return None
    return Path(raw).expanduser()


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
                try:
                    admin_user_ids.add(int(value))
                except ValueError:
                    logging.warning("Ignoring invalid ADMIN_USER_IDS entry: %s", value)

    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    if logging.getLevelName(log_level) == f"Level {log_level}":
        logging.warning("Ignoring unknown LOG_LEVEL %s; using INFO.", log_level)
        log_level = "INFO"

    return Settings(
        token=token,
        admin_user_ids=admin_user_ids,
        log_level=log_level,
        log_file=_load_log_file(),
    )
