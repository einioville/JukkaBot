from __future__ import annotations

import base64
import json
import logging
import re
import socket
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class OpenAIServiceError(RuntimeError):
    pass


class OpenAIQuotaExceededError(OpenAIServiceError):
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
BALANCE_PROBE_CACHE_SECONDS = 60


@dataclass(slots=True)
class GeneratedImage:
    image_bytes: bytes
    mime_type: str
    revised_prompt: str | None = None


class OpenAIService:
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4.1-mini",
        system_prompt: str | None = None,
        temperature: float = 0.8,
        max_output_tokens: int = 220,
        timeout_seconds: int = 30,
        image_timeout_seconds: int = 120,
        enable_web_search: bool = True,
        image_model: str = "gpt-image-1",
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.system_prompt = system_prompt or DEFAULT_CHAT_SYSTEM_PROMPT
        self.temperature = max(0.0, min(2.0, float(temperature)))
        self.max_output_tokens = max(1, int(max_output_tokens))
        self.timeout_seconds = max(1, int(timeout_seconds))
        self.image_timeout_seconds = max(30, int(image_timeout_seconds))
        self.enable_web_search = bool(enable_web_search)
        self.image_model = image_model.strip() or "gpt-image-1"
        self.base_url = "https://api.openai.com/v1/responses"
        self._unsupported_params: set[str] = set()
        self._balance_status: bool | None = None
        self._balance_status_checked_at: float = 0.0

    def has_available_balance(
        self,
        *,
        force_refresh: bool = False,
        probe_if_unknown: bool = True,
    ) -> bool:
        now = time.monotonic()
        if (
            not force_refresh
            and self._balance_status is not None
            and (now - self._balance_status_checked_at) < BALANCE_PROBE_CACHE_SECONDS
        ):
            return self._balance_status
        if not force_refresh and self._balance_status is None and not probe_if_unknown:
            return True

        try:
            self._run_balance_probe()
        except OpenAIQuotaExceededError:
            self._set_balance_status(False)
            return False
        except OpenAIServiceError:
            # Do not disable features based on transient/network issues.
            self._set_balance_status(True)
            return True
        self._set_balance_status(True)
        return True

    def _set_balance_status(self, has_balance: bool) -> None:
        self._balance_status = has_balance
        self._balance_status_checked_at = time.monotonic()

    def _run_balance_probe(self) -> None:
        base_payload = {
            "model": self.model,
            "input": [{"role": "user", "content": "balance probe"}],
        }
        self._request_with_adaptive_payload(base_payload, {"max_output_tokens": 1})

    def generate_reply(
        self,
        messages: list[dict[str, Any]],
        *,
        use_web_search: bool = False,
    ) -> str:
        input_messages = self._build_input_messages(messages)

        base_payload = {
            "model": self.model,
            "input": input_messages,
        }
        optional_payload = {
            "max_output_tokens": self.max_output_tokens,
            "temperature": self.temperature,
        }
        if use_web_search and self.enable_web_search:
            optional_payload["tools"] = [{"type": "web_search_preview"}]
            optional_payload["tool_choice"] = "auto"
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

    def generate_image(
        self,
        prompt: str,
        *,
        reference_image: dict[str, Any] | None = None,
        size: str = "1024x1024",
    ) -> GeneratedImage:
        cleaned_prompt = " ".join(prompt.strip().split())
        if not cleaned_prompt:
            raise OpenAIServiceError("Image prompt cannot be empty.")

        cleaned_size = size.strip() or "1024x1024"
        if reference_image is None:
            payload = {
                "model": self.image_model,
                "prompt": cleaned_prompt,
                "size": cleaned_size,
            }
            raw = self._request_json(
                "https://api.openai.com/v1/images/generations",
                payload,
                timeout_seconds=self.image_timeout_seconds,
                model_name=self.image_model,
            )
            return self._parse_generated_image(raw)

        image_filename = reference_image.get("filename")
        image_mime_type = reference_image.get("mime_type")
        image_data = reference_image.get("data")
        if not isinstance(image_filename, str) or not image_filename.strip():
            image_filename = "reference.png"
        if not isinstance(image_mime_type, str) or not image_mime_type.startswith("image/"):
            raise OpenAIServiceError("Reference image mime type is invalid.")
        if not isinstance(image_data, (bytes, bytearray)) or not image_data:
            raise OpenAIServiceError("Reference image bytes are missing.")

        fields = [
            ("model", self.image_model),
            ("prompt", cleaned_prompt),
            ("size", cleaned_size),
        ]
        files = [
            (
                "image",
                image_filename.strip(),
                image_mime_type,
                bytes(image_data),
            )
        ]
        raw = self._request_multipart(
            "https://api.openai.com/v1/images/edits",
            fields,
            files,
            timeout_seconds=self.image_timeout_seconds,
            model_name=self.image_model,
        )
        return self._parse_generated_image(raw)

    def _parse_response_payload(self, raw: str) -> dict[str, Any]:
        try:
            payload_data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OpenAIServiceError("OpenAI API returned invalid JSON.") from exc
        if not isinstance(payload_data, dict):
            raise OpenAIServiceError("Unexpected OpenAI response shape.")
        return payload_data

    def _parse_generated_image(self, raw: str) -> GeneratedImage:
        payload_data = self._parse_response_payload(raw)
        data = payload_data.get("data")
        if not isinstance(data, list) or not data:
            raise OpenAIServiceError("Image API returned no image data.")

        first = data[0]
        if not isinstance(first, dict):
            raise OpenAIServiceError("Image API returned invalid image payload.")
        revised_prompt = first.get("revised_prompt")
        if not isinstance(revised_prompt, str):
            revised_prompt = None

        b64_json = first.get("b64_json")
        if isinstance(b64_json, str) and b64_json.strip():
            try:
                image_bytes = base64.b64decode(b64_json, validate=True)
            except ValueError as exc:
                raise OpenAIServiceError("Image API returned invalid base64 image data.") from exc
            mime_type = first.get("mime_type")
            if not isinstance(mime_type, str) or not mime_type.startswith("image/"):
                mime_type = "image/png"
            return GeneratedImage(
                image_bytes=image_bytes,
                mime_type=mime_type,
                revised_prompt=revised_prompt,
            )

        image_url = first.get("url")
        if isinstance(image_url, str) and image_url.strip():
            image_bytes, mime_type = self._download_binary(
                image_url,
                timeout_seconds=self.image_timeout_seconds,
            )
            return GeneratedImage(
                image_bytes=image_bytes,
                mime_type=mime_type,
                revised_prompt=revised_prompt,
            )
        raise OpenAIServiceError("Image API response did not include image bytes.")

    def _request_json(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: int,
        model_name: str,
    ) -> str:
        body = json.dumps(payload).encode("utf-8")

        def build_request() -> Request:
            return Request(
                url,
                method="POST",
                data=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )

        return self._request_with_retries(
            build_request,
            timeout_seconds=timeout_seconds,
            model_name=model_name,
        )

    def _request_multipart(
        self,
        url: str,
        fields: list[tuple[str, str]],
        files: list[tuple[str, str, str, bytes]],
        *,
        timeout_seconds: int,
        model_name: str,
    ) -> str:
        body, content_type = self._build_multipart_body(fields, files)

        def build_request() -> Request:
            return Request(
                url,
                method="POST",
                data=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": content_type,
                },
            )

        return self._request_with_retries(
            build_request,
            timeout_seconds=timeout_seconds,
            model_name=model_name,
        )

    def _request_with_retries(
        self,
        build_request: Callable[[], Request],
        *,
        timeout_seconds: int,
        model_name: str,
    ) -> str:
        max_attempts = MAX_API_RETRIES
        for _attempt in range(max_attempts):
            request = build_request()
            try:
                with urlopen(request, timeout=timeout_seconds) as response:
                    self._set_balance_status(True)
                    return response.read().decode("utf-8")
            except HTTPError as exc:
                message = self._extract_error_message(exc)
                if exc.code == 401:
                    raise OpenAIServiceError("OpenAI API key is invalid.") from exc
                if exc.code == 429:
                    if self._is_quota_exhausted_message(message):
                        self._set_balance_status(False)
                        raise OpenAIQuotaExceededError(
                            "OpenAI API balance is exhausted."
                        ) from exc
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
                        model_name,
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
                            model_name,
                            _attempt + 1,
                            max_attempts,
                            delay,
                        )
                        time.sleep(delay)
                        continue
                    raise OpenAIServiceError("OpenAI API request timed out.") from exc
                raise OpenAIServiceError("Could not reach OpenAI API.") from exc
        raise OpenAIServiceError("OpenAI API request failed after retries.")

    def _build_multipart_body(
        self,
        fields: list[tuple[str, str]],
        files: list[tuple[str, str, str, bytes]],
    ) -> tuple[bytes, str]:
        boundary = f"----JukkaBotBoundary{int(time.time() * 1000)}"
        boundary_bytes = boundary.encode("utf-8")
        lines: list[bytes] = []
        for key, value in fields:
            safe_key = key.replace('"', "")
            lines.extend(
                [
                    b"--" + boundary_bytes + b"\r\n",
                    (
                        f'Content-Disposition: form-data; name="{safe_key}"\r\n\r\n'
                    ).encode("utf-8"),
                    value.encode("utf-8"),
                    b"\r\n",
                ]
            )
        for field_name, file_name, mime_type, file_data in files:
            safe_field_name = field_name.replace('"', "")
            safe_file_name = file_name.replace('"', "")
            lines.extend(
                [
                    b"--" + boundary_bytes + b"\r\n",
                    (
                        "Content-Disposition: form-data; "
                        f'name="{safe_field_name}"; filename="{safe_file_name}"\r\n'
                    ).encode("utf-8"),
                    f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"),
                    file_data,
                    b"\r\n",
                ]
            )
        lines.append(b"--" + boundary_bytes + b"--\r\n")
        body = b"".join(lines)
        return body, f"multipart/form-data; boundary={boundary}"

    def _download_binary(
        self, url: str, *, timeout_seconds: int | None = None
    ) -> tuple[bytes, str]:
        request = Request(url, method="GET")
        timeout = self.timeout_seconds if timeout_seconds is None else max(1, timeout_seconds)
        try:
            with urlopen(request, timeout=timeout) as response:
                image_bytes = response.read()
                content_type_header = response.headers.get("Content-Type", "")
        except (HTTPError, URLError, TimeoutError, socket.timeout) as exc:
            raise OpenAIServiceError("Could not download generated image from OpenAI.") from exc
        if not image_bytes:
            raise OpenAIServiceError("Generated image download was empty.")
        mime_type = content_type_header.split(";", 1)[0].strip().casefold()
        if not mime_type.startswith("image/"):
            mime_type = "image/png"
        return image_bytes, mime_type

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
                    self._set_balance_status(True)
                    return response.read().decode("utf-8")
            except HTTPError as exc:
                message = self._extract_error_message(exc)
                if exc.code == 400:
                    unsupported = self._extract_unsupported_parameter(message)
                    if unsupported and unsupported.startswith("tools["):
                        unsupported = "tools"
                    if unsupported and unsupported in payload:
                        if unsupported not in self._unsupported_params:
                            self._unsupported_params.add(unsupported)
                            if unsupported == "tools":
                                self._unsupported_params.add("tool_choice")
                            logger.warning(
                                "Model %s does not support parameter '%s'; "
                                "retrying without it.",
                                self.model,
                                unsupported,
                            )
                            continue
                    if (
                        "tools" in payload
                        and self._looks_like_unsupported_tools_error(message)
                        and "tools" not in self._unsupported_params
                    ):
                        self._unsupported_params.add("tools")
                        self._unsupported_params.add("tool_choice")
                        logger.warning(
                            "Model %s does not support web search tools; "
                            "retrying without tools.",
                            self.model,
                        )
                        continue
                if exc.code == 401:
                    raise OpenAIServiceError("OpenAI API key is invalid.") from exc
                if exc.code == 429:
                    if self._is_quota_exhausted_message(message):
                        self._set_balance_status(False)
                        raise OpenAIQuotaExceededError(
                            "OpenAI API balance is exhausted."
                        ) from exc
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

    def _build_input_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        system_prompt = self.system_prompt.strip()
        input_messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]

        cleaned: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            if role not in {"user", "assistant"}:
                continue
            content = message.get("content")
            trimmed = content.strip() if isinstance(content, str) else ""
            if len(trimmed) > MAX_INPUT_MESSAGE_CHARS:
                trimmed = f"{trimmed[:MAX_INPUT_MESSAGE_CHARS]}..."
            image_blocks = self._build_image_blocks(message.get("images"))
            if not trimmed and not image_blocks:
                continue
            if image_blocks:
                content_blocks: list[dict[str, str]] = []
                if trimmed:
                    content_blocks.append({"type": "input_text", "text": trimmed})
                content_blocks.extend(image_blocks)
                cleaned.append({"role": role, "content": content_blocks})
                continue
            cleaned.append({"role": role, "content": trimmed})

        total_chars = len(system_prompt)
        selected_reversed: list[dict[str, Any]] = []
        for message in reversed(cleaned):
            projected = total_chars + self._message_content_text_length(message.get("content"))
            if selected_reversed and projected > MAX_TOTAL_INPUT_CHARS:
                break
            selected_reversed.append(message)
            total_chars = projected
            if total_chars >= MAX_TOTAL_INPUT_CHARS:
                break

        input_messages.extend(reversed(selected_reversed))
        return input_messages

    def _build_image_blocks(self, images: Any) -> list[dict[str, str]]:
        if not isinstance(images, list):
            return []

        blocks: list[dict[str, str]] = []
        for image in images:
            if not isinstance(image, dict):
                continue
            mime_type = image.get("mime_type")
            data_base64 = image.get("data_base64")
            if not isinstance(mime_type, str) or not mime_type.startswith("image/"):
                continue
            if not isinstance(data_base64, str):
                continue

            cleaned_data = "".join(data_base64.strip().split())
            if not cleaned_data:
                continue
            try:
                base64.b64decode(cleaned_data, validate=True)
            except ValueError:
                continue
            blocks.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{mime_type};base64,{cleaned_data}",
                }
            )
            if len(blocks) >= 3:
                break
        return blocks

    def _message_content_text_length(self, content: Any) -> int:
        if isinstance(content, str):
            return len(content)
        if not isinstance(content, list):
            return 0
        total = 0
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "input_text":
                continue
            text = block.get("text")
            if isinstance(text, str):
                total += len(text)
        return total

    def _extract_unsupported_parameter(self, message: str) -> str | None:
        patterns = (
            r"Unsupported parameter:\s*'([^']+)'",
            r"Unknown parameter:\s*'([^']+)'",
            r"Parameter\s+'([^']+)'\s+is not supported",
            r"'([^']+)'\s+is not supported with this model",
            r"Invalid field:\s*'([^']+)'",
        )
        for pattern in patterns:
            match = re.search(pattern, message, flags=re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def _looks_like_unsupported_tools_error(self, message: str) -> bool:
        lowered = message.casefold()
        if "tool" not in lowered and "web_search_preview" not in lowered:
            return False
        return (
            "not support" in lowered
            or "unknown" in lowered
            or "invalid" in lowered
            or "unrecognized" in lowered
        )

    def _is_quota_exhausted_message(self, message: str) -> bool:
        lowered = message.casefold()
        keywords = (
            "insufficient_quota",
            "insufficient quota",
            "exceeded your current quota",
            "billing",
            "credit balance",
            "out of credits",
            "balance is exhausted",
        )
        return any(keyword in lowered for keyword in keywords)

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
