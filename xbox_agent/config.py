"""Runtime settings, read from environment variables (see README)."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


@dataclass
class Settings:
    dashboard_url: str = "http://127.0.0.1:8000"
    dashboard_token: str = ""
    vision_provider: str = "auto"  # auto | gemini | claude
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    claude_model: str = "claude-opus-5-5"
    claude_effort: str = "low"  # low | medium | high | xhigh | max
    claude_fallbacks: bool = True
    tesseract_cmd: str = ""
    window_title: str = "Xbox"
    crop_top_px: int = 80
    db_path: str = "xbox_agent.sqlite3"
    shots_dir: str = "shots"

    # timing (seconds)
    settle_s: float = 1.2
    poll_interval_s: float = 5.0
    reload_wait_s: float = 8.0
    redeem_result_timeout_s: float = 25.0
    login_detect_timeout_s: float = 60.0
    admin_login_timeout_s: float = 900.0
    game_ready_timeout_s: float = 600.0
    delete_confirm_timeout_s: float = 1800.0
    heartbeat_s: float = 300.0

    # limits
    nav_max_moves: int = 25
    nav_scan_limit: int = 10
    stall_limit: int = 3
    reconnect_attempts: int = 3
    pause_on_code_error: bool = False

    # on-screen text / menu labels
    login_code_regex: str = r"\b[A-Z0-9]{6,10}\b"
    login_labels: tuple[str, ...] = ("another device", "with a code", "use a code")
    redeem_path: tuple[str, ...] = ("store", "redeem")
    library_labels: tuple[str, ...] = ("my games", "library")
    game_match: str = r"modern\s*warfare"
    game_exclude: str = r"modern\s*warfare\s*(ii|iii|2|3)\b|warzone"
    delete_path: tuple[str, ...] = ("profile", "settings", "account", "remove accounts")

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        return cls(
            dashboard_url=e("XBOX_AGENT_DASHBOARD_URL", cls.dashboard_url),
            dashboard_token=e("XBOX_AGENT_DASHBOARD_TOKEN", ""),
            vision_provider=e("XBOX_AGENT_VISION_PROVIDER", cls.vision_provider),
            gemini_api_key=e("GEMINI_API_KEY", ""),
            gemini_model=e("XBOX_AGENT_GEMINI_MODEL", cls.gemini_model),
            claude_model=e("XBOX_AGENT_CLAUDE_MODEL", cls.claude_model),
            claude_effort=e("XBOX_AGENT_CLAUDE_EFFORT", cls.claude_effort),
            claude_fallbacks=e("XBOX_AGENT_CLAUDE_FALLBACKS", "1") != "0",
            tesseract_cmd=e("TESSERACT_CMD", ""),
            window_title=e("XBOX_AGENT_WINDOW_TITLE", cls.window_title),
            crop_top_px=int(e("XBOX_AGENT_CROP_TOP_PX", cls.crop_top_px)),
            db_path=e("XBOX_AGENT_DB", cls.db_path),
            shots_dir=e("XBOX_AGENT_SHOTS_DIR", cls.shots_dir),
            settle_s=_f("XBOX_AGENT_SETTLE_S", cls.settle_s),
            admin_login_timeout_s=_f("XBOX_AGENT_ADMIN_LOGIN_TIMEOUT_S", cls.admin_login_timeout_s),
            game_ready_timeout_s=_f("XBOX_AGENT_GAME_READY_TIMEOUT_S", cls.game_ready_timeout_s),
            delete_confirm_timeout_s=_f(
                "XBOX_AGENT_DELETE_CONFIRM_TIMEOUT_S", cls.delete_confirm_timeout_s
            ),
            pause_on_code_error=e("XBOX_AGENT_PAUSE_ON_CODE_ERROR", "0") == "1",
        )
