from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class OpenAIServiceError(RuntimeError):
    pass


logger = logging.getLogger(__name__)
DEFAULT_CHAT_SYSTEM_PROMPT = (
    "You are JukkaBot, a natural and friendly Discord chat participant. "
    "Keep responses concise and conversational."
)


class OpenAIService:
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4.1-mini",
        system_prompt: str | None = None,
        temperature: float = 0.8,
        max_output_tokens: int = 220,
        timeout_seconds: int = 30,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.system_prompt = system_prompt or DEFAULT_CHAT_SYSTEM_PROMPT
        self.temperature = max(0.0, min(2.0, float(temperature)))
        self.max_output_tokens = max(1, int(max_output_tokens))
        self.timeout_seconds = max(1, int(timeout_seconds))
        self.base_url = "https://api.openai.com/v1/responses"
        self._unsupported_params: set[str] = set()

    def generate_reply(self, messages: list[dict[str, str]]) -> str:
        input_messages: list[dict[str, str]] = [{"role": "system", "content": self.system_prompt}]
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if role not in {"user", "assistant"}:
                continue
            if not isinstance(content, str):
                continue
            trimmed = content.strip()
            if not trimmed:
                continue
            input_messages.append({"role": role, "content": trimmed})

        base_payload = {
            "model": self.model,
            "input": input_messages,
        }
        optional_payload = {
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
        }
        raw = self._request_with_adaptive_payload(base_payload, optional_payload)

        try:
            payload_data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OpenAIServiceError("OpenAI API returned invalid JSON.") from exc
        if not isinstance(payload_data, dict):
            raise OpenAIServiceError("Unexpected OpenAI response shape.")

        output = self._extract_output_text(payload_data)
        if not output:
            raise OpenAIServiceError("OpenAI API returned empty output.")
        return output

    def _request_with_adaptive_payload(
        self,
        base_payload: dict[str, Any],
        optional_payload: dict[str, Any],
    ) -> str:
        max_attempts = 4
        for _attempt in range(max_attempts):
            payload = dict(base_payload)
            for key, value in optional_payload.items():
                if key not in self._unsupported_params:
                    payload[key] = value
            request = Request(
                self.base_url,
                method="POST",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )

            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    return response.read().decode("utf-8")
            except HTTPError as exc:
                message = self._extract_error_message(exc)
                if exc.code == 400:
                    unsupported = self._extract_unsupported_parameter(message)
                    if unsupported and unsupported in payload:
                        if unsupported not in self._unsupported_params:
                            self._unsupported_params.add(unsupported)
                            logger.warning(
                                "Model %s does not support parameter '%s'; "
                                "retrying without it.",
                                self.model,
                                unsupported,
                            )
                            continue
                if exc.code == 401:
                    raise OpenAIServiceError("OpenAI API key is invalid.") from exc
                if exc.code == 429:
                    raise OpenAIServiceError("OpenAI API rate limit reached.") from exc
                raise OpenAIServiceError(
                    f"OpenAI API request failed ({exc.code}): {message}"
                ) from exc
            except URLError as exc:
                raise OpenAIServiceError("Could not reach OpenAI API.") from exc

        raise OpenAIServiceError(
            "OpenAI API request failed: model rejected supported parameter set."
        )

    def _extract_unsupported_parameter(self, message: str) -> str | None:
        patterns = (
            r"Unsupported parameter:\s*'([^']+)'",
            r"Unknown parameter:\s*'([^']+)'",
            r"Parameter\s+'([^']+)'\s+is not supported",
            r"'([^']+)'\s+is not supported with this model",
        )
        for pattern in patterns:
            match = re.search(pattern, message, flags=re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def _extract_error_message(self, exc: HTTPError) -> str:
        try:
            raw = exc.read().decode("utf-8")
            payload_data = json.loads(raw)
            if isinstance(payload_data, dict):
                error = payload_data.get("error")
                if isinstance(error, dict):
                    message = error.get("message")
                    if isinstance(message, str) and message.strip():
                        return message.strip()
        except Exception:
            pass
        return exc.reason if isinstance(exc.reason, str) else "Unknown error"

    def _extract_output_text(self, payload_data: dict[str, Any]) -> str:
        output_text = payload_data.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()

        parts: list[str] = []
        output = payload_data.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "message":
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") != "output_text":
                        continue
                    text = block.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())

        return "\n".join(parts).strip()
