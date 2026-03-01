from __future__ import annotations

from pytest import MonkeyPatch

from jukkabot.config import load_settings


def test_load_settings_ignores_invalid_admin_ids(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("jukkabot.config.load_dotenv", lambda: None)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "token")
    monkeypatch.setenv("ADMIN_USER_IDS", "123,invalid,456")

    settings = load_settings()

    assert settings.admin_user_ids == {123, 456}
