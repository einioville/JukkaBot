from __future__ import annotations

import base64
import io
import json
from urllib.error import HTTPError

import pytest

from jukkabot.openai_service import (
    EMPTY_OUTPUT_FALLBACK_REPLY,
    OpenAIService,
    OpenAIServiceError,
)


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._data = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._data

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        return None


def test_generate_reply_reads_output_text(monkeypatch: pytest.MonkeyPatch) -> None:
    service = OpenAIService(api_key="fake")

    def fake_urlopen(request, timeout):  # noqa: ANN001
        assert request.full_url == "https://api.openai.com/v1/responses"
        assert timeout == 30
        return _FakeResponse({"output_text": "Hello from API"})

    monkeypatch.setattr("jukkabot.openai_service.urlopen", fake_urlopen)
    reply = service.generate_reply([{"role": "user", "content": "Hi"}])
    assert reply == "Hello from API"


def test_generate_reply_maps_unauthorized_error(monkeypatch: pytest.MonkeyPatch) -> None:
    service = OpenAIService(api_key="fake")

    def fake_urlopen(_request, timeout):  # noqa: ANN001, ARG001
        raise HTTPError(
            url="https://api.openai.com/v1/responses",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"error":{"message":"Bad key"}}'),
        )

    monkeypatch.setattr("jukkabot.openai_service.urlopen", fake_urlopen)
    with pytest.raises(OpenAIServiceError) as excinfo:
        service.generate_reply([{"role": "user", "content": "Hi"}])
    assert "invalid" in str(excinfo.value).lower()


def test_generate_reply_retries_without_unsupported_parameter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = OpenAIService(api_key="fake")
    payloads: list[dict[str, object]] = []

    def fake_urlopen(request, timeout):  # noqa: ANN001, ARG001
        payload = json.loads(request.data.decode("utf-8"))
        payloads.append(payload)
        if len(payloads) == 1:
            raise HTTPError(
                url="https://api.openai.com/v1/responses",
                code=400,
                msg="Bad Request",
                hdrs=None,
                fp=io.BytesIO(
                    b'{"error":{"message":"Unsupported parameter: \'max_output_tokens\' is not supported with this model."}}'
                ),
            )
        return _FakeResponse({"output_text": "Recovered"})

    monkeypatch.setattr("jukkabot.openai_service.urlopen", fake_urlopen)
    reply = service.generate_reply([{"role": "user", "content": "Hi"}])

    assert reply == "Recovered"
    assert "max_output_tokens" in payloads[0]
    assert "max_output_tokens" not in payloads[1]
    assert payloads[0]["temperature"] == 0.8
    assert payloads[1]["temperature"] == 0.8


def test_generate_reply_retries_without_unsupported_temperature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = OpenAIService(api_key="fake")
    payloads: list[dict[str, object]] = []

    def fake_urlopen(request, timeout):  # noqa: ANN001, ARG001
        payload = json.loads(request.data.decode("utf-8"))
        payloads.append(payload)
        if len(payloads) == 1:
            raise HTTPError(
                url="https://api.openai.com/v1/responses",
                code=400,
                msg="Bad Request",
                hdrs=None,
                fp=io.BytesIO(
                    b'{"error":{"message":"Unsupported parameter: \'temperature\' is not supported with this model."}}'
                ),
            )
        return _FakeResponse({"output_text": "Recovered"})

    monkeypatch.setattr("jukkabot.openai_service.urlopen", fake_urlopen)
    reply = service.generate_reply([{"role": "user", "content": "Hi"}])

    assert reply == "Recovered"
    assert payloads[0]["temperature"] == 0.8
    assert "temperature" not in payloads[1]


def test_generate_reply_falls_back_on_empty_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = OpenAIService(api_key="fake")
    calls = 0

    def fake_urlopen(_request, timeout):  # noqa: ANN001, ARG001
        nonlocal calls
        calls += 1
        return _FakeResponse({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}})

    monkeypatch.setattr("jukkabot.openai_service.urlopen", fake_urlopen)
    reply = service.generate_reply([{"role": "user", "content": "Hi"}])

    assert reply == EMPTY_OUTPUT_FALLBACK_REPLY
    assert calls == 2


def test_generate_reply_recovers_after_max_tokens_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = OpenAIService(api_key="fake")
    payloads: list[dict[str, object]] = []

    def fake_urlopen(request, timeout):  # noqa: ANN001, ARG001
        payload = json.loads(request.data.decode("utf-8"))
        payloads.append(payload)
        if len(payloads) == 1:
            return _FakeResponse(
                {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}
            )
        return _FakeResponse({"output_text": "Recovered after retry"})

    monkeypatch.setattr("jukkabot.openai_service.urlopen", fake_urlopen)
    reply = service.generate_reply([{"role": "user", "content": "Hi"}])

    assert reply == "Recovered after retry"
    assert payloads[0]["max_output_tokens"] == 220
    assert payloads[1]["max_output_tokens"] == 440


def test_generate_reply_retries_timeout_and_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = OpenAIService(api_key="fake")
    calls = 0

    def fake_urlopen(_request, timeout):  # noqa: ANN001, ARG001
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("timed out")
        return _FakeResponse({"output_text": "after timeout"})

    monkeypatch.setattr("jukkabot.openai_service.urlopen", fake_urlopen)
    reply = service.generate_reply([{"role": "user", "content": "Hi"}])

    assert reply == "after timeout"
    assert calls == 2


def test_generate_reply_enables_web_search_tools_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = OpenAIService(api_key="fake", enable_web_search=True)
    payloads: list[dict[str, object]] = []

    def fake_urlopen(request, timeout):  # noqa: ANN001, ARG001
        payloads.append(json.loads(request.data.decode("utf-8")))
        return _FakeResponse({"output_text": "with web search"})

    monkeypatch.setattr("jukkabot.openai_service.urlopen", fake_urlopen)
    reply = service.generate_reply(
        [{"role": "user", "content": "What's new today?"}],
        use_web_search=True,
    )

    assert reply == "with web search"
    assert payloads
    assert payloads[0]["tool_choice"] == "auto"
    assert payloads[0]["tools"] == [{"type": "web_search_preview"}]


def test_generate_reply_retries_without_unsupported_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = OpenAIService(api_key="fake", enable_web_search=True)
    payloads: list[dict[str, object]] = []

    def fake_urlopen(request, timeout):  # noqa: ANN001, ARG001
        payload = json.loads(request.data.decode("utf-8"))
        payloads.append(payload)
        if len(payloads) == 1:
            raise HTTPError(
                url="https://api.openai.com/v1/responses",
                code=400,
                msg="Bad Request",
                hdrs=None,
                fp=io.BytesIO(
                    b'{"error":{"message":"Unknown parameter: \'tools\'"}}'
                ),
            )
        return _FakeResponse({"output_text": "fallback without tools"})

    monkeypatch.setattr("jukkabot.openai_service.urlopen", fake_urlopen)
    reply = service.generate_reply(
        [{"role": "user", "content": "Need online answer"}],
        use_web_search=True,
    )

    assert reply == "fallback without tools"
    assert "tools" in payloads[0]
    assert "tool_choice" in payloads[0]
    assert "tools" not in payloads[1]
    assert "tool_choice" not in payloads[1]


def test_generate_reply_sends_image_input_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    service = OpenAIService(api_key="fake")
    payloads: list[dict[str, object]] = []
    image_data = base64.b64encode(b"\x89PNG\r\n").decode("ascii")

    def fake_urlopen(request, timeout):  # noqa: ANN001, ARG001
        payloads.append(json.loads(request.data.decode("utf-8")))
        return _FakeResponse({"output_text": "saw image"})

    monkeypatch.setattr("jukkabot.openai_service.urlopen", fake_urlopen)
    reply = service.generate_reply(
        [
            {
                "role": "user",
                "content": "Describe this image",
                "images": [{"mime_type": "image/png", "data_base64": image_data}],
            }
        ]
    )

    assert reply == "saw image"
    sent_input = payloads[0]["input"]
    assert isinstance(sent_input, list)
    user_message = sent_input[1]
    assert user_message["role"] == "user"
    content = user_message["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "input_text"
    assert content[1]["type"] == "input_image"
    assert content[1]["image_url"].startswith("data:image/png;base64,")


def test_generate_image_uses_generations_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = OpenAIService(api_key="fake")
    calls: list[dict[str, object]] = []
    image_payload = base64.b64encode(b"image-bytes").decode("ascii")

    def fake_urlopen(request, timeout):  # noqa: ANN001, ARG001
        assert request.full_url == "https://api.openai.com/v1/images/generations"
        calls.append(json.loads(request.data.decode("utf-8")))
        return _FakeResponse({"data": [{"b64_json": image_payload}]})

    monkeypatch.setattr("jukkabot.openai_service.urlopen", fake_urlopen)
    result = service.generate_image("A red car in snow")

    assert calls
    assert calls[0]["model"] == "gpt-image-1"
    assert calls[0]["prompt"] == "A red car in snow"
    assert calls[0]["size"] == "1024x1024"
    assert result.image_bytes == b"image-bytes"
    assert result.mime_type == "image/png"


def test_generate_image_uses_edits_endpoint_with_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = OpenAIService(api_key="fake")
    seen_headers: list[dict[str, str]] = []
    seen_body: list[bytes] = []
    image_payload = base64.b64encode(b"edited-image").decode("ascii")

    def fake_urlopen(request, timeout):  # noqa: ANN001, ARG001
        assert request.full_url == "https://api.openai.com/v1/images/edits"
        headers = {key.casefold(): value for key, value in request.header_items()}
        seen_headers.append(headers)
        seen_body.append(request.data or b"")
        return _FakeResponse({"data": [{"b64_json": image_payload, "mime_type": "image/webp"}]})

    monkeypatch.setattr("jukkabot.openai_service.urlopen", fake_urlopen)
    result = service.generate_image(
        "Make this cinematic",
        reference_image={
            "filename": "ref.png",
            "mime_type": "image/png",
            "data": b"\x89PNG\r\n",
        },
    )

    assert seen_headers
    assert seen_headers[0]["content-type"].startswith("multipart/form-data; boundary=")
    assert b'name="model"' in seen_body[0]
    assert b"name=\"image\"; filename=\"ref.png\"" in seen_body[0]
    assert b"Make this cinematic" in seen_body[0]
    assert result.image_bytes == b"edited-image"
    assert result.mime_type == "image/webp"
