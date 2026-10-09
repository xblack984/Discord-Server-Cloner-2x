"""Window capture (mss) and OCR (Tesseract)."""
from __future__ import annotations

import hashlib
import io

from ..models import Frame


class MssCapture:
    """Grabs only the browser client area, without the title bar and the top 80px strip."""

    def __init__(self, window) -> None:
        self._window = window

    def grab(self) -> Frame:
        import mss
        import mss.tools

        left, top, width, height = self._window.client_region()
        with mss.mss() as sct:
            shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
        rgb = shot.rgb
        sample = rgb[::101]
        luma = sum(sample) / max(1, len(sample))
        digest = hashlib.blake2b(rgb[::37], digest_size=8).hexdigest()
        return Frame(png=mss.tools.to_png(rgb, shot.size), digest=digest, mean_luma=luma)


class TesseractOcr:
    def __init__(self, tesseract_cmd: str = "", lang: str = "eng") -> None:
        import pytesseract

        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        self._pt = pytesseract
        self._lang = lang

    def read(self, png: bytes, *, psm: int = 6) -> str:
        from PIL import Image, ImageOps

        img = ImageOps.autocontrast(Image.open(io.BytesIO(png)).convert("L"))
        img = img.resize((img.width * 2, img.height * 2))
        return self._pt.image_to_string(img, lang=self._lang, config=f"--psm {psm}")
