from __future__ import annotations

import asyncio

from jukkabot.cogs.chat import ChatCog, MAX_ATTACHMENT_BYTES, MAX_ATTACHMENT_TEXT_CHARS


class _FakeAttachment:
    def __init__(
        self,
        filename: str,
        content_type: str | None,
        payload: bytes,
        size: int | None = None,
    ) -> None:
        self.filename = filename
        self.content_type = content_type
        self._payload = payload
        self.size = len(payload) if size is None else size

    async def read(self) -> bytes:
        return self._payload


class _FakeMessage:
    def __init__(self, content: str, attachments: list[_FakeAttachment]) -> None:
        self.content = content
        self.attachments = attachments


def test_build_user_prompt_includes_text_attachment() -> None:
    cog = ChatCog.__new__(ChatCog)
    msg = _FakeMessage(
        content="check this",
        attachments=[
            _FakeAttachment(
                filename="notes.txt",
                content_type="text/plain",
                payload=b"hello from file",
            )
        ],
    )

    prompt = asyncio.run(cog._build_user_prompt(msg))  # type: ignore[arg-type]

    assert "check this" in prompt
    assert "[Attachment: notes.txt]" in prompt
    assert "hello from file" in prompt


def test_build_user_prompt_marks_unsupported_or_too_large() -> None:
    cog = ChatCog.__new__(ChatCog)
    msg = _FakeMessage(
        content="",
        attachments=[
            _FakeAttachment(
                filename="image.png",
                content_type="image/png",
                payload=b"abc",
            ),
            _FakeAttachment(
                filename="huge.txt",
                content_type="text/plain",
                payload=b"",
                size=MAX_ATTACHMENT_BYTES + 1,
            ),
        ],
    )

    prompt = asyncio.run(cog._build_user_prompt(msg))  # type: ignore[arg-type]

    assert "not a supported text file" in prompt
    assert "larger than" in prompt


def test_build_user_prompt_truncates_long_attachment_text() -> None:
    cog = ChatCog.__new__(ChatCog)
    long_text = ("x" * (MAX_ATTACHMENT_TEXT_CHARS + 50)).encode("utf-8")
    msg = _FakeMessage(
        content="",
        attachments=[
            _FakeAttachment(
                filename="long.md",
                content_type="text/markdown",
                payload=long_text,
            )
        ],
    )

    prompt = asyncio.run(cog._build_user_prompt(msg))  # type: ignore[arg-type]

    assert len(prompt) < len(long_text) + 100
    assert prompt.endswith("...")
