from __future__ import annotations

import json
import logging
import re
import socket
import time
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
EMPTY_OUTPUT_FALLBACK_REPLY = "nah this is insane :D"
MAX_INPUT_MESSAGE_CHARS = 3000
MAX_TOTAL_INPUT_CHARS = 18000
MAX_API_RETRIES = 3


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
        input_messages = self._build_input_messages(messages)

        base_payload = {
            "model": self.model,
            "input": input_messages,
        }
        optional_payload = {
            "max_output_tokens": self.max_output_tokens,
        }
        raw = self._request_with_adaptive_payload(base_payload, optional_payload)
        payload_data = self._parse_response_payload(raw)

        output = self._extract_output_text(payload_data)
        if output:
            return output

        reason = self._extract_non_text_reason(payload_data)
        if reason == "incomplete:max_output_tokens":
            retry_tokens = min(max(self.max_output_tokens * 2, self.max_output_tokens + 64), 1024)
            if retry_tokens > self.max_output_tokens:
                logger.warning(
                    "Model %s hit max_output_tokens=%s with non-text output; "
                    "retrying with max_output_tokens=%s.",
                    self.model,
                    self.max_output_tokens,
                    retry_tokens,
                )
                retry_raw = self._request_with_adaptive_payload(
                    base_payload,
                    {"max_output_tokens": retry_tokens},
                )
                retry_payload = self._parse_response_payload(retry_raw)
                retry_output = self._extract_output_text(retry_payload)
                if retry_output:
                    return retry_output
                reason = self._extract_non_text_reason(retry_payload) or reason

        if reason:
            logger.warning(
                "OpenAI API returned non-text output for model %s: %s",
                self.model,
                reason,
            )
        else:
            logger.warning("OpenAI API returned empty output for model %s", self.model)
        return EMPTY_OUTPUT_FALLBACK_REPLY

    def _parse_response_payload(self, raw: str) -> dict[str, Any]:
        try:
            payload_data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OpenAIServiceError("OpenAI API returned invalid JSON.") from exc
        if not isinstance(payload_data, dict):
            raise OpenAIServiceError("Unexpected OpenAI response shape.")
        return payload_data

    def _request_with_adaptive_payload(
        self,
        base_payload: dict[str, Any],
        optional_payload: dict[str, Any],
    ) -> str:
        max_attempts = MAX_API_RETRIES
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
            except (TimeoutError, socket.timeout) as exc:
                if _attempt < max_attempts - 1:
                    delay = 0.5 * (_attempt + 1)
                    logger.warning(
                        "OpenAI API request timed out for model %s (attempt %s/%s); "
                        "retrying in %.1fs.",
                        self.model,
                        _attempt + 1,
                        max_attempts,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                raise OpenAIServiceError("OpenAI API request timed out.") from exc
            except URLError as exc:
                reason = getattr(exc, "reason", None)
                if isinstance(reason, (TimeoutError, socket.timeout)):
                    if _attempt < max_attempts - 1:
                        delay = 0.5 * (_attempt + 1)
                        logger.warning(
                            "OpenAI API network timeout for model %s (attempt %s/%s); "
                            "retrying in %.1fs.",
                            self.model,
                            _attempt + 1,
                            max_attempts,
                            delay,
                        )
                        time.sleep(delay)
                        continue
                    raise OpenAIServiceError("OpenAI API request timed out.") from exc
                raise OpenAIServiceError("Could not reach OpenAI API.") from exc

        raise OpenAIServiceError(
            "OpenAI API request failed: model rejected supported parameter set."
        )

    def _build_input_messages(self, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        system_prompt = self.system_prompt.strip()
        if len(system_prompt) > MAX_INPUT_MESSAGE_CHARS:
            system_prompt = f"{system_prompt[:MAX_INPUT_MESSAGE_CHARS]}..."
        input_messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]

        cleaned: list[dict[str, str]] = []
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
            if len(trimmed) > MAX_INPUT_MESSAGE_CHARS:
                trimmed = f"{trimmed[:MAX_INPUT_MESSAGE_CHARS]}..."
            cleaned.append({"role": role, "content": trimmed})

        total_chars = len(system_prompt)
        selected_reversed: list[dict[str, str]] = []
        for message in reversed(cleaned):
            content = message["content"]
            projected = total_chars + len(content)
            if selected_reversed and projected > MAX_TOTAL_INPUT_CHARS:
                break
            selected_reversed.append(message)
            total_chars = projected
            if total_chars >= MAX_TOTAL_INPUT_CHARS:
                break

        input_messages.extend(reversed(selected_reversed))
        return input_messages

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

    def _extract_non_text_reason(self, payload_data: dict[str, Any]) -> str | None:
        status = payload_data.get("status")
        if status == "incomplete":
            details = payload_data.get("incomplete_details")
            if isinstance(details, dict):
                reason = details.get("reason")
                if isinstance(reason, str) and reason.strip():
                    return f"incomplete:{reason.strip()}"
            return "incomplete"

        output = payload_data.get("output")
        if not isinstance(output, list):
            return None
        for item in output:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "refusal":
                summary = item.get("summary")
                if isinstance(summary, list) and summary:
                    first = summary[0]
                    if isinstance(first, dict):
                        text = first.get("text")
                        if isinstance(text, str) and text.strip():
                            return f"refusal:{text.strip()[:120]}"
                return "refusal"
            if item_type != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "refusal":
                    text = block.get("refusal")
                    if isinstance(text, str) and text.strip():
                        return f"refusal:{text.strip()[:120]}"
                    return "refusal"
        return None
