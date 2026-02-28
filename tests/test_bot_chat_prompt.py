from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from jukkabot.bot import JukkaBot


def test_load_chat_prompt_from_project_file_reads_relative_path() -> None:
    test_root = Path(".tmp_test_prompt") / f"case_{uuid4().hex}"
    try:
        bot = JukkaBot.__new__(JukkaBot)
        bot.config_path = test_root / "config.json"
        prompt_path = test_root / "resources" / "prompts" / "chat_system.txt"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text("project prompt", encoding="utf-8")

        text = bot._load_chat_prompt_from_project_file("resources/prompts/chat_system.txt")

        assert text == "project prompt"
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_load_chat_prompt_from_project_file_rejects_outside_project() -> None:
    test_root = Path(".tmp_test_prompt") / f"case_{uuid4().hex}"
    bot = JukkaBot.__new__(JukkaBot)
    bot.config_path = test_root / "config.json"

    text = bot._load_chat_prompt_from_project_file("../outside.txt")

    assert text is None
