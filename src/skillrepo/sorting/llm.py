"""OpenAI-compatible client for the local judge model."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from skillrepo.sorting.config import JudgeConfig

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class LLMError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class LLMClient:
    """Chat client. ``complete`` returns the assistant text."""

    def __init__(self, config: JudgeConfig):
        self.config = config
        self._json_mode = True

    def complete(self, messages: list[dict[str, str]], *, max_tokens: int = 2048) -> str:
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if self._json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            return self._post(payload)
        except LLMError as error:
            if self._json_mode and error.status == 400:
                self._json_mode = False
                payload.pop("response_format", None)
                return self._post(payload)
            raise

    def _post(self, payload: dict) -> str:
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        request = urllib.request.Request(
            self.config.endpoint("chat/completions"),
            data=data,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                body = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read(500).decode("utf-8", errors="replace")
            raise LLMError(f"HTTP {error.code} from the judge model: {detail[:240]}", error.code) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise LLMError(f"The judge model is unreachable: {error}") from error
        try:
            document = json.loads(body.decode("utf-8"))
            return document["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, json.JSONDecodeError, UnicodeError) as error:
            raise LLMError(f"The judge model returned an unreadable response: {error}") from error


def parse_json_object(text: str) -> dict:
    """Pull the JSON object out of a model reply."""

    cleaned = _FENCE.sub("", text.strip())
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("model reply did not contain a JSON object")
    value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("model reply was not a JSON object")
    return value
