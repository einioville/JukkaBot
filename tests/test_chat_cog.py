from __future__ import annotations

import asyncio
import base64
import random

from jukkabot.cogs.chat import (
    BRAINROT_GIF_URLS,
    ChatCog,
    GIF_CHANCE,
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENT_TEXT_CHARS,
    MAX_DISCORD_MESSAGE_CHARS,
    MAX_IMAGE_ATTACHMENT_BYTES,
)


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

    prompt = asyncio.run(  # type: ignore[arg-type]
        cog._build_user_prompt(msg, include_attachments=True)
    )

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

    prompt = asyncio.run(  # type: ignore[arg-type]
        cog._build_user_prompt(msg, include_attachments=True)
    )

    assert "not a supported text file" in prompt
    assert "larger than" in prompt


def test_build_user_prompt_skips_attachments_when_disabled() -> None:
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

    prompt = asyncio.run(  # type: ignore[arg-type]
        cog._build_user_prompt(msg, include_attachments=False)
    )

    assert "check this" in prompt
    assert "[Attachment: notes.txt]" not in prompt
    assert "hello from file" not in prompt


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

    prompt = asyncio.run(  # type: ignore[arg-type]
        cog._build_user_prompt(msg, include_attachments=True)
    )

    assert len(prompt) < len(long_text) + 100
    assert prompt.endswith("...")


def test_build_user_prompt_collects_supported_image_inputs() -> None:
    cog = ChatCog.__new__(ChatCog)
    payload = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    msg = _FakeMessage(
        content="what is in this",
        attachments=[
            _FakeAttachment(
                filename="shot.png",
                content_type="image/png",
                payload=payload,
            )
        ],
    )
    images: list[dict[str, str]] = []

    prompt = asyncio.run(  # type: ignore[arg-type]
        cog._build_user_prompt(msg, include_attachments=True, image_inputs_out=images)
    )

    assert "[Image attached: shot.png]" in prompt
    assert len(images) == 1
    assert images[0]["mime_type"] == "image/png"
    assert base64.b64decode(images[0]["data_base64"]) == payload


def test_build_user_prompt_omits_too_large_image_inputs() -> None:
    cog = ChatCog.__new__(ChatCog)
    msg = _FakeMessage(
        content="please read",
        attachments=[
            _FakeAttachment(
                filename="big.png",
                content_type="image/png",
                payload=b"data",
                size=MAX_IMAGE_ATTACHMENT_BYTES + 1,
            )
        ],
    )
    images: list[dict[str, str]] = []

    prompt = asyncio.run(  # type: ignore[arg-type]
        cog._build_user_prompt(msg, include_attachments=True, image_inputs_out=images)
    )

    assert "larger than" in prompt
    assert images == []


def test_read_supported_image_attachment_returns_bytes() -> None:
    cog = ChatCog.__new__(ChatCog)
    payload = b"\x89PNG\r\n\x1a\npayload"
    attachment = _FakeAttachment(
        filename="ref.png",
        content_type="image/png",
        payload=payload,
    )

    blob, note = asyncio.run(cog._read_supported_image_attachment(attachment))  # type: ignore[arg-type]

    assert note is None
    assert blob is not None
    mime_type, image_bytes = blob
    assert mime_type == "image/png"
    assert image_bytes == payload


def test_read_supported_image_attachment_rejects_non_image() -> None:
    cog = ChatCog.__new__(ChatCog)
    attachment = _FakeAttachment(
        filename="doc.pdf",
        content_type="application/pdf",
        payload=b"%PDF",
    )

    blob, note = asyncio.run(cog._read_supported_image_attachment(attachment))  # type: ignore[arg-type]

    assert blob is None
    assert note is not None
    assert "not a supported image file" in note


def test_extract_memory_fact_payload() -> None:
    cog = ChatCog.__new__(ChatCog)
    cog.bot = type("Bot", (), {"user": type("User", (), {"id": 123})()})()

    assert cog._extract_memory_fact_payload("Muista: ville likes fortnite") == "ville likes fortnite"
    assert (
        cog._extract_memory_fact_payload("<@123> Muista: Ville tykkaa fortnitesta")
        == "Ville tykkaa fortnitesta"
    )
    assert (
        cog._extract_memory_fact_payload("<@123>, Muista: Ville juo kahvia")
        == "Ville juo kahvia"
    )
    assert cog._extract_memory_fact_payload("remember that ville likes fortnite") is None
    assert cog._extract_memory_fact_payload("just chatting") is None


def test_split_discord_chunks_splits_long_text() -> None:
    cog = ChatCog.__new__(ChatCog)
    text = "a" * (MAX_DISCORD_MESSAGE_CHARS + 50)

    chunks = cog._split_discord_chunks(text)

    assert len(chunks) >= 2
    assert all(len(chunk) <= MAX_DISCORD_MESSAGE_CHARS + 4 for chunk in chunks)


def test_resolve_random_gif_urls_prefers_bot_config() -> None:
    cog = ChatCog.__new__(ChatCog)
    cog.bot = type(
        "Bot",
        (),
        {"chat_random_gif_urls": ["https://example.com/a.gif", "https://example.com/b.gif"]},
    )()

    urls = cog._resolve_random_gif_urls()

    assert urls == ("https://example.com/a.gif", "https://example.com/b.gif")


def test_resolve_random_gif_urls_falls_back_to_default() -> None:
    cog = ChatCog.__new__(ChatCog)
    cog.bot = type("Bot", (), {"chat_random_gif_urls": []})()

    urls = cog._resolve_random_gif_urls()

    assert urls == BRAINROT_GIF_URLS


class _FakeChannel:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, content: str, allowed_mentions=None) -> None:  # noqa: ANN001
        del allowed_mentions
        self.sent.append(content)


def test_maybe_send_random_gif_sends_when_random_hits(monkeypatch) -> None:  # noqa: ANN001
    cog = ChatCog.__new__(ChatCog)
    cog.bot = type(
        "Bot",
        (),
        {"chat_random_gif_urls": ["https://example.com/a.gif"]},
    )()
    channel = _FakeChannel()

    monkeypatch.setattr(random, "random", lambda: GIF_CHANCE - 0.01)
    asyncio.run(cog._maybe_send_random_gif(channel))  # type: ignore[arg-type]

    assert channel.sent == ["https://example.com/a.gif"]


def test_maybe_send_random_gif_skips_when_random_misses(monkeypatch) -> None:  # noqa: ANN001
    cog = ChatCog.__new__(ChatCog)
    cog.bot = type(
        "Bot",
        (),
        {"chat_random_gif_urls": ["https://example.com/a.gif"]},
    )()
    channel = _FakeChannel()

    monkeypatch.setattr(random, "random", lambda: GIF_CHANCE + 0.01)
    asyncio.run(cog._maybe_send_random_gif(channel))  # type: ignore[arg-type]

    assert channel.sent == []
