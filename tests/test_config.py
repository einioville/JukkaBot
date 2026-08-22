from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from jukkabot.config import DEFAULT_LOG_FILE, load_settings


def test_load_settings_ignores_invalid_admin_ids(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("jukkabot.config.load_dotenv", lambda: None)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "token")
    monkeypatch.setenv("ADMIN_USER_IDS", "123,invalid,456")

    settings = load_settings()

    assert settings.admin_user_ids == {123, 456}


def test_load_settings_reads_log_level_and_file(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("jukkabot.config.load_dotenv", lambda: None)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "token")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("LOG_FILE", "custom/place.log")

    settings = load_settings()

    assert settings.log_level == "DEBUG"
    assert settings.log_file == Path("custom/place.log")


def test_load_settings_falls_back_on_an_unknown_log_level(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr("jukkabot.config.load_dotenv", lambda: None)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "token")
    monkeypatch.setenv("LOG_LEVEL", "chatty")
    monkeypatch.delenv("LOG_FILE", raising=False)

    settings = load_settings()

    assert settings.log_level == "INFO"
    assert settings.log_file == DEFAULT_LOG_FILE


def test_load_settings_can_turn_the_file_log_off(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("jukkabot.config.load_dotenv", lambda: None)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "token")
    monkeypatch.setenv("LOG_FILE", "off")

    assert load_settings().log_file is None
