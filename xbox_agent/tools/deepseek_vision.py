"""DeepSeek as the vision backend, over its OpenAI-style chat-completions HTTP API.

Plain httpx on purpose: no OpenAI SDK is needed or used. Notes on the API (from public docs and
reports, not from this code having talked to it):
  * only image-capable models accept images; others answer 400;
  * with images only `response_format: {"type": "json_object"}` works (json_schema is refused), so the
    JSON Schema goes into the prompt and the answer is validated here;
  * thinking may be on by default and can use up max_tokens before any visible text (finish_reason
    "length", empty content), so we ask for it off and fall back if the API rejects that field.
"""
from __future__ import annotations

import json
import re
from typing import Any, Sequence

from .images import encode_image

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


class DeepSeekVision:
    def __init__(self, api_key: str, model: str = "deepseek-flash", base_url: str = "https://api.deepseek.com",
                 thinking: str = "off", client: Any = None, max_tokens: int = 8192) -> None:
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY is not set")
        self._key = api_key
        self._model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._send_thinking_off = thinking.lower() == "off"
        self._client = client  # built on first use so importing/selecting this backend needs no httpx
        self._max_tokens = max_tokens

    def _http(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.Client(timeout=90.0)
        return self._client

    def _post(self, body: dict[str, Any]) -> Any:
        return self._http().post(self._url, json=body, headers={"Authorization": f"Bearer {self._key}"})

    def ask(self, png: bytes, task: str, required_keys: Sequence[str],
            schema: dict[str, Any] | None = None) -> dict[str, Any]:
        media_type, data = encode_image(png)
        prompt = task if schema is None else (
            f"{task}\n\nYour whole answer must be one JSON object that validates against this JSON Schema:\n"
            f"{json.dumps(schema, separators=(',', ':'))}")
        body: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{data}"}},
                {"type": "text", "text": prompt},
            ]}],
        }
        if self._send_thinking_off:
            body["thinking"] = {"type": "disabled"}

        resp = self._post(body)
        if resp.status_code == 400 and "thinking" in body:
            # this deployment does not know the field: retry without it, and stop sending it
            self._send_thinking_off = False
            resp = self._post({k: v for k, v in body.items() if k != "thinking"})
        if resp.status_code >= 400:
            raise RuntimeError(f"DeepSeek HTTP {resp.status_code}: {str(resp.text)[:300]}")

        choice = resp.json()["choices"][0]
        reason = choice.get("finish_reason")
        if reason == "length":
            raise ValueError("DeepSeek answer was cut off (finish_reason=length); thinking may be using the token budget")
        if reason == "content_filter":
            raise ValueError("DeepSeek declined the request (content_filter)")
        text = _FENCE.sub("", (choice.get("message") or {}).get("content") or "").strip()
        if not text:
            raise ValueError("DeepSeek returned an empty answer")
        try:
            out = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"DeepSeek returned non-JSON text: {e}") from e
        if not isinstance(out, dict) or any(k not in out for k in required_keys):
            raise ValueError(f"DeepSeek JSON is missing keys {list(required_keys)}: {out!r}")
        return out
