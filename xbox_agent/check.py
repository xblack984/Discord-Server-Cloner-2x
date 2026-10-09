"""Trial helpers: `--check` (is every component working?) and `--inspect` (what does the agent see?).

Neither mode presses a gamepad button or types anything."""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from . import vision_tasks as vt
from .config import Settings
from .models import Frame

Check = tuple[str, Callable[[], str]]


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def run_checks(checks: Sequence[Check]) -> list[CheckResult]:
    """Run every check; one failure never stops the others."""
    results = []
    for name, fn in checks:
        try:
            results.append(CheckResult(name, True, fn()))
        except Exception as e:
            results.append(CheckResult(name, False, f"{type(e).__name__}: {e}"))
    return results


def format_results(results: Sequence[CheckResult]) -> str:
    return "\n".join(f"[{'OK' if r.ok else 'FAIL'}] {r.name}: {r.detail}" for r in results)


def default_checks(cfg: Settings) -> list[Check]:
    """Windows checks, in dependency order. Imports are lazy so a missing package fails only its own line."""
    shared: dict[str, Any] = {}

    def need(key: str, step: str) -> Any:
        if key not in shared:
            raise RuntimeError(f"skipped: needs the '{step}' check to pass first")
        return shared[key]

    def python() -> str:
        if sys.version_info < (3, 11):
            raise RuntimeError(f"Python 3.11+ required, found {sys.version.split()[0]}")
        return sys.version.split()[0]

    def gamepad() -> str:
        from .tools.windows_input import VGamepad

        VGamepad()
        return "virtual Xbox 360 pad created (ViGEmBus is installed)"

    def tesseract() -> str:
        import pytesseract

        if cfg.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = cfg.tesseract_cmd
        return f"Tesseract {pytesseract.get_tesseract_version()}"

    def window() -> str:
        from .tools.windows_input import window_from_settings

        w = window_from_settings(cfg)
        opened = False
        if cfg.open_play_url and cfg.play_url:
            opened = w.ensure_open(cfg.play_url)  # opens the page in a new window, as the agent will
            if opened:
                time.sleep(cfg.page_load_s)
        if not w.focus():
            raise RuntimeError(f"no visible window whose title contains '{cfg.window_title}'")
        shared["window"] = w
        exe = getattr(w, "browser_path", "")
        how = f"opened {cfg.play_url} in a new window of {exe} and focused it" if opened else "focused an existing window"
        return f"{how}; capture region (left, top, w, h) = {w.client_region()}"

    def capture() -> str:
        from .tools.capture import MssCapture

        frame = MssCapture(need("window", "window")).grab()
        if frame.mean_luma < 4.0:
            raise RuntimeError("the captured frame is black (is the stream running? is the window covered?)")
        shared["frame"] = frame
        return f"{len(frame.png) // 1024} KB PNG, mean brightness {frame.mean_luma:.0f}"

    def vision() -> str:
        from .tools.vision import build_vision

        frame = need("frame", "capture")
        v = build_vision(cfg)
        screen = vt.classify_screen(v, frame.png)  # a real API call: validates key, model and schema
        return f"{type(v).__name__} answered, screen = '{screen}'"

    def dashboard() -> str:
        from .tools.dashboard import HttpDashboard

        HttpDashboard(cfg.dashboard_url, cfg.dashboard_token, retries=1).fetch_commands(0)
        return f"{cfg.dashboard_url} accepted the agent token"

    return [("python", python), ("gamepad (vgamepad/ViGEmBus)", gamepad), ("tesseract", tesseract),
            ("browser window", window), ("screen capture", capture), ("vision backend", vision),
            ("dashboard", dashboard)]


def inspect_screen(window: Any, capture: Any, ocr: Any, vision: Any, out_dir: str = "inspect") -> dict[str, Any]:
    """Read-only look at the current screen: what OCR reads, what the vision model thinks it is, and
    which menu labels it sees. Use it to tune the menu labels in .env before a real run."""
    if not window.focus():
        raise RuntimeError("browser window could not be brought to the foreground")
    frame: Frame = capture.grab()
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    shot = folder / f"inspect_{time.strftime('%Y%m%d_%H%M%S')}.png"
    shot.write_bytes(frame.png)
    menu = vt.read_menu(vision, frame.png)
    return {
        "screenshot": str(shot),
        "mean_brightness": round(frame.mean_luma, 1),
        "screen": vt.classify_screen(vision, frame.png),
        "ocr_text": ocr.read(frame.png).strip()[:800],
        "menu_layout": menu.layout,
        "menu_items": [{"label": i.label, "focused": i.focused, "row": i.row, "col": i.col} for i in menu.items],
    }
