"""Claude as the vision backend (Anthropic SDK, JSON Schema constrained output)."""
from __future__ import annotations

import base64
import io
import json
from typing import Any, Sequence

_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_MAX_PNG_BYTES = 3_500_000  # the API rejects images over 5 MB; stay well below it


def _encode_image(png: bytes) -> tuple[str, str]:
    """(media_type, base64). Oversized PNGs (noisy game frames) are re-encoded as JPEG."""
    if len(png) > _MAX_PNG_BYTES:
        from PIL import Image

        buf = io.BytesIO()
        Image.open(io.BytesIO(png)).convert("RGB").save(buf, format="JPEG", quality=85)
        return "image/jpeg", base64.standard_b64encode(buf.getvalue()).decode()
    return "image/png", base64.standard_b64encode(png).decode()


class ClaudeVision:
    """`output_config.format` (json_schema) guarantees parseable JSON; effort stays low because
    each call is a simple read of one screen and the agent makes many of them.

    With `fallbacks=True` a request declined by a safety classifier is re-run server-side on
    Anthropic's recommended fallback model (beta `server-side-fallback-2026-07-01`)."""

    def __init__(self, model: str = "claude-opus-5-5", effort: str = "low", fallbacks: bool = True,
                 client: Any = None) -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic()  # ANTHROPIC_API_KEY, or an `ant auth login` profile
        self._client = client
        self._model = model
        self._effort = effort
        self._fallbacks = fallbacks

    def ask(self, png: bytes, task: str, required_keys: Sequence[str],
            schema: dict[str, Any] | None = None) -> dict[str, Any]:
        media_type, data = _encode_image(png)
        output_config: dict[str, Any] = {"effort": self._effort}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        params: dict[str, Any] = dict(
            model=self._model,
            max_tokens=16000,  # thinking tokens count against it
            output_config=output_config,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
                {"type": "text", "text": task},
            ]}],
        )
        if self._fallbacks:
            resp = self._client.beta.messages.create(
                betas=[_FALLBACK_BETA], extra_body={"fallbacks": "default"}, **params)
        else:
            resp = self._client.messages.create(**params)

        # Check stop_reason before reading content: a decline arrives as a normal 200.
        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            raise ValueError(f"Claude declined the request (category: {getattr(details, 'category', None)})")
        if resp.stop_reason == "max_tokens":
            raise ValueError("Claude answer was cut off by max_tokens")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            out = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Claude returned non-JSON text: {e}") from e
        if not isinstance(out, dict) or any(k not in out for k in required_keys):
            raise ValueError(f"Claude JSON is missing keys {list(required_keys)}: {out!r}")
        return out
