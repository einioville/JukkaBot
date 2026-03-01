from __future__ import annotations

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
