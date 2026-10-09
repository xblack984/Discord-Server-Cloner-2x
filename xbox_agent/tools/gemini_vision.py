"""Gemini 2.5 Flash in JSON mode (response_mime_type=application/json)."""
from __future__ import annotations

import json
from typing import Any, Sequence


class GeminiVision:
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not set")
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model = model

    def ask(self, png: bytes, task: str, required_keys: Sequence[str],
            schema: dict[str, Any] | None = None) -> dict[str, Any]:
        from google.genai import types

        config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0)
        last_err: Exception | None = None
        for _ in range(2):  # one retry on malformed JSON
            resp = self._client.models.generate_content(
                model=self._model,
                contents=[types.Part.from_bytes(data=png, mime_type="image/png"), task],
                config=config,
            )
            try:
                data = json.loads(resp.text)
                if isinstance(data, dict) and all(k in data for k in required_keys):
                    return data
                last_err = ValueError(f"missing keys {list(required_keys)} in {data!r}")
            except (json.JSONDecodeError, TypeError) as e:
                last_err = e
        raise ValueError(f"Gemini returned unusable JSON: {last_err}")
