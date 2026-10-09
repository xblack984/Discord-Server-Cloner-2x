"""Runtime settings, read from environment variables (see README)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _t(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """Comma-separated env var -> tuple of non-empty, stripped items."""
    raw = os.environ.get(name)
    return tuple(x.strip() for x in raw.split(",") if x.strip()) if raw else default


def load_env_file(path: str | Path | None = None) -> None:
    """Minimal .env reader: KEY=VALUE lines, '#' comments, optional quotes. If a key appears twice in the
    file the LAST line wins (so appending a line overrides the template's). Variables already set in the
    real environment are never overridden. Defaults to xbox_agent/.env (next to this package, whatever the
    working directory) so it can't pick up another project's .env."""
    try:
        lines = Path(path or Path(__file__).parent / ".env").read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    values: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    for key, value in values.items():
        os.environ.setdefault(key, value)


@dataclass
class Settings:
    dashboard_url: str = "http://127.0.0.1:8000"
    dashboard_token: str = ""
    vision_provider: str = "auto"  # auto | gemini | deepseek | claude
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-flash"
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_thinking: str = "off"  # off = ask for non-thinking mode | default = leave it to the API
    claude_model: str = "claude-opus-5-5"
    claude_effort: str = "low"  # low | medium | high | xhigh | max
    claude_fallbacks: bool = True
    tesseract_cmd: str = ""
    window_title: str = "Xbox"  # part of the Remote Play window's title (used to reuse a window that is already open)
    window_exclude: tuple[str, ...] = ("XboxAgent",)  # titles with these words are never the Remote Play window
    play_url: str = "https://www.xbox.com/de-DE/play/consoles"
    open_play_url: bool = True  # the agent opens play_url in its own browser window
    browser: str = "auto"  # auto | chrome | edge | full path to chrome.exe / msedge.exe
    browser_profile: str = ""  # Chrome profile folder ("Default", "Profile 1") that you signed in with; empty = last used
    crop_top_px: int = 80
    db_path: str = "xbox_agent.sqlite3"
    shots_dir: str = "shots"

    # timing (seconds)
    settle_s: float = 1.2
    poll_interval_s: float = 5.0
    reload_wait_s: float = 8.0
    page_load_s: float = 8.0
    connect_timeout_s: float = 90.0
    web_login_timeout_s: float = 600.0  # how long to wait for you to sign in to Microsoft in the browser
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
    vision_attempts: int = 3  # tries per vision call (1 = no retry); transient failures only
    second_looks: int = 2  # extra looks at a screen the vision model could not recognise, before giving up
    # Decisions that otherwise stop the session and wait for you. "ask" keeps asking; the others decide.
    on_game_running: str = "ask"  # a game is on the console screen: ask | start (press A) | close | keep
    on_signed_in: str = "ask"  # the console is already signed in: ask | use (continue with that profile)
    allow_delete: bool = False  # profile removal is off until explicitly enabled

    # on-screen text / menu labels
    login_code_regex: str = r"\b[A-Z0-9]{6,10}\b"
    console_labels: tuple[str, ...] = ("verbinden", "connect")  # the connect button on the web consoles page
    # Each item is matched as a substring of what is on screen (case, ® signs and Arabic spelling variants
    # are ignored). Defaults cover English, German and Arabic consoles; in the *_path settings "a|b" means
    # "a or b". Only "الصفحة الرئيسية", "ألعابي" and "قطع الاتصال" were seen on a real screen; the other German
    # and Arabic words are best guesses: tune them with `--inspect`.
    login_labels: tuple[str, ...] = ("another device", "with a code", "use a code", "anderen gerät", "mit einem code",
                                     "mit code", "جهاز آخر", "باستخدام رمز")
    signin_labels: tuple[str, ...] = ("sign in", "anmelden", "تسجيل الدخول")
    redeem_path: tuple[str, ...] = ("store|متجر", "redeem|einlösen|استرداد")
    library_labels: tuple[str, ...] = ("my games", "library", "meine spiele", "bibliothek", "ألعابي", "مكتبة")
    home_labels: tuple[str, ...] = ("home", "startseite", "الصفحة الرئيسية")
    quit_labels: tuple[str, ...] = ("quit", "exit", "beenden", "إنهاء")
    signout_labels: tuple[str, ...] = ("sign out", "abmelden", "تسجيل الخروج")
    profile_labels: tuple[str, ...] = ("profile", "profil", "الملف الشخصي")
    play_labels: tuple[str, ...] = ("play", "launch", "resume", "spielen", "starten", "fortsetzen", "تشغيل", "استئناف")
    game_match: str = r"modern\s*warfare"
    game_exclude: str = r"modern\s*warfare\W*\s*(ii|iii|2|3)\b|warzone"
    delete_path: tuple[str, ...] = ("profile|profil|الملف الشخصي", "settings|einstellungen|إعدادات", "account|konto|حساب",
                                    "remove accounts|konten entfernen|إزالة الحسابات")

    def __post_init__(self) -> None:
        if self.on_game_running not in ("ask", "start", "close", "keep"):
            raise ValueError(f"XBOX_AGENT_ON_GAME_RUNNING must be ask, start, close or keep (got '{self.on_game_running}')")
        if self.on_signed_in not in ("ask", "use"):
            raise ValueError(f"XBOX_AGENT_ON_SIGNED_IN must be ask or use (got '{self.on_signed_in}')")

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        return cls(
            dashboard_url=e("XBOX_AGENT_DASHBOARD_URL", cls.dashboard_url),
            dashboard_token=e("XBOX_AGENT_DASHBOARD_TOKEN", ""),
            vision_provider=e("XBOX_AGENT_VISION_PROVIDER", cls.vision_provider),
            gemini_api_key=e("GEMINI_API_KEY", ""),
            gemini_model=e("XBOX_AGENT_GEMINI_MODEL", cls.gemini_model),
            deepseek_api_key=e("DEEPSEEK_API_KEY", ""),
            deepseek_model=e("XBOX_AGENT_DEEPSEEK_MODEL", cls.deepseek_model),
            deepseek_base_url=e("XBOX_AGENT_DEEPSEEK_BASE_URL", cls.deepseek_base_url),
            deepseek_thinking=e("XBOX_AGENT_DEEPSEEK_THINKING", cls.deepseek_thinking),
            claude_model=e("XBOX_AGENT_CLAUDE_MODEL", cls.claude_model),
            claude_effort=e("XBOX_AGENT_CLAUDE_EFFORT", cls.claude_effort),
            claude_fallbacks=e("XBOX_AGENT_CLAUDE_FALLBACKS", "1") != "0",
            tesseract_cmd=e("TESSERACT_CMD", ""),
            window_title=e("XBOX_AGENT_WINDOW_TITLE", cls.window_title),
            window_exclude=_t("XBOX_AGENT_WINDOW_EXCLUDE", cls.window_exclude),
            play_url=e("XBOX_AGENT_PLAY_URL", cls.play_url),
            open_play_url=e("XBOX_AGENT_OPEN_PLAY_URL", "1") != "0",
            browser=e("XBOX_AGENT_BROWSER", cls.browser),
            browser_profile=e("XBOX_AGENT_BROWSER_PROFILE", cls.browser_profile),
            web_login_timeout_s=_f("XBOX_AGENT_WEB_LOGIN_TIMEOUT_S", cls.web_login_timeout_s),
            page_load_s=_f("XBOX_AGENT_PAGE_LOAD_S", cls.page_load_s),
            connect_timeout_s=_f("XBOX_AGENT_CONNECT_TIMEOUT_S", cls.connect_timeout_s),
            console_labels=_t("XBOX_AGENT_CONSOLE_LABELS", cls.console_labels),
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
            allow_delete=e("XBOX_AGENT_ALLOW_DELETE", "0") == "1",
            vision_attempts=int(e("XBOX_AGENT_VISION_ATTEMPTS", cls.vision_attempts)),
            second_looks=int(e("XBOX_AGENT_SECOND_LOOKS", cls.second_looks)),
            on_game_running=e("XBOX_AGENT_ON_GAME_RUNNING", cls.on_game_running).strip().lower(),
            on_signed_in=e("XBOX_AGENT_ON_SIGNED_IN", cls.on_signed_in).strip().lower(),
            login_code_regex=e("XBOX_AGENT_LOGIN_CODE_REGEX", cls.login_code_regex),
            login_labels=_t("XBOX_AGENT_LOGIN_LABELS", cls.login_labels),
            signin_labels=_t("XBOX_AGENT_SIGNIN_LABELS", cls.signin_labels),
            home_labels=_t("XBOX_AGENT_HOME_LABELS", cls.home_labels),
            quit_labels=_t("XBOX_AGENT_QUIT_LABELS", cls.quit_labels),
            signout_labels=_t("XBOX_AGENT_SIGNOUT_LABELS", cls.signout_labels),
            profile_labels=_t("XBOX_AGENT_PROFILE_LABELS", cls.profile_labels),
            play_labels=_t("XBOX_AGENT_PLAY_LABELS", cls.play_labels),
            redeem_path=_t("XBOX_AGENT_REDEEM_PATH", cls.redeem_path),
            library_labels=_t("XBOX_AGENT_LIBRARY_LABELS", cls.library_labels),
            delete_path=_t("XBOX_AGENT_DELETE_PATH", cls.delete_path),
            game_match=e("XBOX_AGENT_GAME_MATCH", cls.game_match),
            game_exclude=e("XBOX_AGENT_GAME_EXCLUDE", cls.game_exclude),
        )
