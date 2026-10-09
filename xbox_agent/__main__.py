"""python -m xbox_agent [--once] [--check] [--inspect]

stdout carries only the agent's JSON step reports (one object per line); logs go to stderr.
--check and --inspect never press a button or type anything.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from .agent import Tools, XboxAgent
from .check import default_checks, format_results, inspect_screen, run_checks
from .config import Settings, load_env_file


def build_tools(cfg: Settings) -> Tools:
    # Imported here so the package (and its tests) load on non-Windows machines.
    from .tools.capture import MssCapture, TesseractOcr
    from .tools.clipboard import PyperclipClipboard
    from .tools.dashboard import HttpDashboard
    from .tools.store import SqliteStore
    from .tools.vision import build_vision
    from .tools.windows_input import VGamepad, window_from_settings

    window = window_from_settings(cfg)
    return Tools(
        gamepad=VGamepad(),
        window=window,
        capture=MssCapture(window),
        ocr=TesseractOcr(cfg.tesseract_cmd),
        vision=build_vision(cfg),
        clipboard=PyperclipClipboard(),
        dashboard=HttpDashboard(cfg.dashboard_url, cfg.dashboard_token),
        store=SqliteStore(cfg.db_path, cfg.shots_dir),
    )


def fail(error: Exception) -> int:
    """Startup problems (missing driver, key, window...) in plain words; --check pinpoints the component."""
    logging.getLogger("xbox_agent").debug("startup failure", exc_info=error)
    print(f"Could not start: {type(error).__name__}: {error}\n"
          "Run it with --check (run_agent.bat --check) to test each component separately.", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="xbox_agent")
    parser.add_argument("--once", action="store_true", help="handle a single session, then exit")
    parser.add_argument("--check", action="store_true", help="test every component (incl. one vision call) and exit")
    parser.add_argument("--inspect", action="store_true",
                        help="print what OCR and the vision model see on the current screen, then exit")
    args = parser.parse_args(argv)

    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    load_env_file()
    cfg = Settings.from_env()

    if args.check:
        results = run_checks(default_checks(cfg))
        print(format_results(results), file=sys.stderr)
        return 0 if all(r.ok for r in results) else 1

    if args.inspect:
        from .tools.capture import MssCapture, TesseractOcr
        from .tools.vision import build_vision
        from .tools.windows_input import window_from_settings

        try:
            window = window_from_settings(cfg)
            report = inspect_screen(window, MssCapture(window), TesseractOcr(cfg.tesseract_cmd), build_vision(cfg))
        except Exception as e:
            return fail(e)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    try:
        tools = build_tools(cfg)
    except Exception as e:
        return fail(e)
    agent = XboxAgent(tools, cfg)
    try:
        agent.run_once() if args.once else agent.run_forever()
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
