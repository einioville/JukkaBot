from __future__ import annotations

import io
import json
from urllib.error import HTTPError

import pytest

from jukkabot.openai_service import OpenAIService, OpenAIServiceError


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
