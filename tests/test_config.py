from __future__ import annotations

from pytest import MonkeyPatch

from jukkabot.config import load_settings


def test_load_settings_ignores_invalid_admin_ids(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("jukkabot.config.load_dotenv", lambda: None)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "token")
    monkeypatch.setenv("ADMIN_USER_IDS", "123,invalid,456")

    settings = load_settings()

    assert settings.admin_user_ids == {123, 456}


def test_load_settings_parses_chat_web_search_flag(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("jukkabot.config.load_dotenv", lambda: None)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "token")
    monkeypatch.setenv("CHAT_ENABLE_WEB_SEARCH", "off")

    settings = load_settings()

    assert settings.chat_enable_web_search is False


def test_load_settings_parses_openai_timeout_and_image_model(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr("jukkabot.config.load_dotenv", lambda: None)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "token")
    monkeypatch.setenv("OPENAI_IMAGE_MODEL", "gpt-image-1")
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("OPENAI_IMAGE_TIMEOUT_SECONDS", "130")

    settings = load_settings()

    assert settings.openai_image_model == "gpt-image-1"
    assert settings.openai_timeout_seconds == 45
    assert settings.openai_image_timeout_seconds == 130
