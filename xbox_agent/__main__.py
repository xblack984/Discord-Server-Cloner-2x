"""python -m xbox_agent [--once] [--check]

stdout carries only the agent's JSON step reports (one object per line); logs go to stderr.
"""
from __future__ import annotations

import argparse
import logging
import sys

from .agent import Tools, XboxAgent
from .config import Settings


def build_tools(cfg: Settings) -> Tools:
    # Imported here so the package (and its tests) load on non-Windows machines.
    from .tools.capture import MssCapture, TesseractOcr
    from .tools.clipboard import PyperclipClipboard
    from .tools.dashboard import HttpDashboard
    from .tools.gemini_vision import GeminiVision
    from .tools.store import SqliteStore
    from .tools.windows_input import VGamepad, Win32Window

    window = Win32Window(cfg.window_title, cfg.crop_top_px)
    return Tools(
        gamepad=VGamepad(),
        window=window,
        capture=MssCapture(window),
        ocr=TesseractOcr(cfg.tesseract_cmd),
        vision=GeminiVision(cfg.gemini_api_key, cfg.gemini_model),
        clipboard=PyperclipClipboard(),
        dashboard=HttpDashboard(cfg.dashboard_url, cfg.dashboard_token),
        store=SqliteStore(cfg.db_path, cfg.shots_dir),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="xbox_agent")
    parser.add_argument("--once", action="store_true", help="handle a single session, then exit")
    parser.add_argument("--check", action="store_true", help="verify the environment and exit")
    args = parser.parse_args(argv)

    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = Settings.from_env()
    tools = build_tools(cfg)

    if args.check:
        ok = tools.window.focus()
        print(f"browser window '{cfg.window_title}' focusable: {ok}", file=sys.stderr)
        if ok:
            frame = tools.capture.grab()
            print(f"capture ok, mean luma {frame.mean_luma:.1f}; OCR sample: "
                  f"{tools.ocr.read(frame.png)[:80]!r}", file=sys.stderr)
        return 0 if ok else 1

    agent = XboxAgent(tools, cfg)
    try:
        agent.run_once() if args.once else agent.run_forever()
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
