"""Shared screenshot encoding for the vision backends."""
from __future__ import annotations

import base64
import io

MAX_PNG_BYTES = 3_500_000  # stay well below the smallest provider limit


def encode_image(png: bytes) -> tuple[str, str]:
    """(media_type, base64). Oversized PNGs (noisy game frames) are re-encoded as JPEG."""
    if len(png) > MAX_PNG_BYTES:
        from PIL import Image

        buf = io.BytesIO()
        Image.open(io.BytesIO(png)).convert("RGB").save(buf, format="JPEG", quality=85)
        return "image/jpeg", base64.standard_b64encode(buf.getvalue()).decode()
    return "image/png", base64.standard_b64encode(png).decode()
