"""A fake console + fake tools: the whole state machine runs on any OS, no Xbox needed."""
from __future__ import annotations

import json
import tempfile
import unittest
from typing import Any, Callable

from xbox_agent.agent import Tools, XboxAgent
from xbox_agent.config import Settings
from xbox_agent.models import Button, Command, Frame
from xbox_agent.tools.store import SqliteStore

SCREEN = {
    "sign_in_prompt": "sign_in_prompt", "login_code": "login_code", "home": "home",
    "guide_menu": "guide_menu", "profile_menu": "guide_menu", "store": "store",
    "redeem": "redeem", "result": "redeem", "library": "library", "game_page": "game_page",
    "game_loading": "game_loading", "game_main_menu": "game_main_menu", "settings": "settings",
    "account_menu": "settings", "accounts": "accounts", "confirm_remove": "confirm_dialog",
    "quit_confirm": "confirm_dialog", "add_menu": "guide_menu", "password_prompt": "password_prompt",
    "play_consoles": "play_consoles", "connecting": "unknown", "web_sign_in": "web_sign_in", "error_page": "error",
    "game_signed_out": "game_main_menu", "guide_signed_out": "guide_menu",
    "game_signed_in": "game_main_menu", "guide_signed_in": "guide_menu",
    "game_quit_ok": "game_main_menu", "guide_with_quit": "guide_menu",
}
RESULT_TEXT = {
    "redeemed": "Successfully redeemed! Enjoy your game",
    "code_invalid": "That code isn't valid. Check the code and try again",
    "code_used": "This code has already been redeemed",
    "network": "Something went wrong. Try again later",
    "unknown": "Hmm...",
}


class FakeConsole:
    def __init__(self, scene: str = "sign_in_prompt"):
        self.scene = scene
        self.focus = 0
        self.field = ""
        self.signed_in = False
        self.game_running = False
        self.loading_snaps = 0
        self.connecting_snaps = 0
        self.web_login_after: int | None = None  # the human signs in after this many looks at the sign-in page
        self.web_login_looks = 0
        self.connect_stuck = False  # pressing "Verbinden" never leads to a console screen
        self.web_ignores_pad = False  # the consoles web page does not react to the gamepad at all
        self.manual_connect_after: int | None = None  # the human clicks Connect after this many looks at the page
        self.manual_connect_looks = 0
        self.error_snaps = 0
        self.error_looks = 0  # the error page clears by itself once it has been looked at this many times (0 = never)
        self.accounts = ["AgentTest99", "OtherUser"]
        self.active_tag: str | None = "AgentTest99"  # whose profile the console shows as the active user
        self.main_tag: str | None = None  # a main account that stays signed in while a new one is added
        self.new_tag: str | None = "AgentTest99"  # who the admin signs in as with the code
        self.adding = False
        self.add_goes_home = False  # "Add new" does nothing useful: the signed-in home screen is shown again
        self.pending_account: str | None = None
        self.outcomes: dict[str, str] = {}  # normalized code -> outcome kind
        self.result = "unknown"
        self.weird: set[str] = set()  # codes whose result text OCR cannot classify
        self.last_code = ""
        self.black = 0
        self.paste_works = True
        self.has_play = True
        self.login_text = "Go to microsoft.com/link and enter the code QX7M2K9P"
        self.pressed_a_on_redeem = 0

    # -- UI model
    def items(self) -> list[str]:
        s = self.scene
        table: dict[str, list[str]] = {
            "sign_in_prompt": ["Email, phone, or Skype", "Use another device", "Get a new email", "Forgot your account?"],
            "play_consoles": ["Meine Konsole", "Verbinden"],
            "guide_signed_out": ["Sign in"],
            "guide_signed_in": ["Home", "My games & apps"],
            "guide_with_quit": ["Home", "Quit"],
            "home": ["My games & apps", "Store"],
            "store": ["Games", "Redeem a code"],
            "library": ["Call of Duty: Modern Warfare II", "Call of Duty: Modern Warfare", "Forza Horizon"],
            "game_page": ["Play" if self.has_play else "Install", "Manage game"],
            "profile_menu": ["Settings", "Sign out", "Add or switch"],
            "add_menu": ["Add new", "Switch"],
            "settings": ["Account", "General"],
            "account_menu": ["Remove accounts", "Sign-in & security"],
            "confirm_remove": ["Remove", "Cancel"],
            "quit_confirm": ["Quit", "Cancel"],
        }
        if s == "guide_menu":
            return ["Home", "Store", "Profile & system"] + (["Quit"] if self.game_running else [])
        if s == "accounts":
            return list(self.accounts)
        return table.get(s, [])

    def _go(self, scene: str, focus: int = 0) -> None:
        self.scene, self.focus = scene, focus

    def press(self, b: Button) -> None:
        s = self.scene
        if s == "play_consoles" and self.web_ignores_pad:
            return
        if b is Button.GUIDE:
            return self._go({"game_signed_out": "guide_signed_out", "game_signed_in": "guide_signed_in",
                             "game_quit_ok": "guide_with_quit"}.get(s, "guide_menu"))
        if b is Button.B:
            if s not in ("game_loading", "game_main_menu"):
                self._go("home")
            return
        items = self.items()
        if b in (Button.UP, Button.DOWN):
            if items:
                self.focus = max(0, min(len(items) - 1, self.focus + (1 if b is Button.DOWN else -1)))
            return
        if b is not Button.A:
            return
        if s == "redeem":
            self.pressed_a_on_redeem += 1
            code = self.field.replace("-", "")
            self.last_code = code
            self.result = self.outcomes.get(code, "code_invalid")
            return self._go("result")
        if not items:
            return
        label = items[self.focus]
        step = {
            ("sign_in_prompt", "Use another device"): "login_code",
            ("guide_signed_out", "Sign in"): "sign_in_prompt",
            ("guide_signed_in", "Home"): "home",
            ("guide_with_quit", "Quit"): "sign_in_prompt",
            ("home", "My games & apps"): "library", ("home", "Store"): "store",
            ("guide_menu", "Home"): "home", ("guide_menu", "Store"): "store",
            ("guide_menu", "Profile & system"): "profile_menu", ("guide_menu", "Quit"): "quit_confirm",
            ("store", "Redeem a code"): "redeem",
            ("library", "Call of Duty: Modern Warfare"): "game_page",
            ("profile_menu", "Settings"): "settings", ("settings", "Account"): "account_menu", ("profile_menu", "Add or switch"): "add_menu",
            ("account_menu", "Remove accounts"): "accounts",
        }.get((s, label))
        if s == "play_consoles" and label == "Verbinden":
            self.connecting_snaps = 0
            return self._go("play_consoles" if self.connect_stuck else "connecting")
        if s == "game_page" and label == "Play":
            self.game_running, self.loading_snaps = True, 0
            return self._go("game_loading")
        if s == "add_menu" and label == "Add new":
            self.adding = True
            return self._go("home" if self.add_goes_home else "sign_in_prompt")
        if s == "profile_menu" and label == "Sign out":
            if self.adding and self.main_tag:  # the main account is still signed in and becomes the active user again
                self.adding, self.active_tag = False, self.main_tag
                return self._go("home")
            self.signed_in = False
            return self._go("sign_in_prompt")
        if s == "accounts":
            self.pending_account = label
            return self._go("confirm_remove", focus=1)  # Cancel is focused first
        if s == "confirm_remove":
            if label == "Remove" and self.pending_account in self.accounts:
                self.accounts.remove(self.pending_account)
            return self._go("accounts")
        if s == "quit_confirm":
            self.game_running = False
            return self._go("home")
        if step:
            self._go(step)

    def admin_login(self) -> None:
        self.signed_in = True
        if self.adding:
            self.active_tag = self.new_tag
        self._go("home")

    def snapshot(self) -> dict[str, Any]:
        if self.scene == "game_loading":
            self.loading_snaps += 1
            if self.loading_snaps > 2:
                self.scene = "game_main_menu"
        if self.scene == "web_sign_in" and self.web_login_after is not None:
            self.web_login_looks += 1
            if self.web_login_looks > self.web_login_after:
                self.scene = "sign_in_prompt"
        if self.scene == "play_consoles" and self.manual_connect_after is not None:
            self.manual_connect_looks += 1
            if self.manual_connect_looks > self.manual_connect_after:
                self.scene = "connecting"
        if self.scene == "error_page" and self.error_looks:
            self.error_snaps += 1
            if self.error_snaps > self.error_looks:
                self.scene = "play_consoles"
        if self.scene == "connecting":
            self.connecting_snaps += 1
            if self.connecting_snaps > 2:
                self.scene = "sign_in_prompt"
        s = self.scene
        if s == "login_code":
            ocr = self.login_text
        elif s == "redeem":
            ocr = f"Redeem a code Enter 25-character code {self.field}"
        elif s == "result":
            ocr = RESULT_TEXT["unknown"] if self.last_code in self.weird else RESULT_TEXT[self.result]
        else:
            ocr = " ".join(self.items())
        return {"scene": s, "items": self.items(), "focus": self.focus, "field": self.field,
                "ocr": ocr, "result": self.result, "signed_in": self.signed_in,
                "pending": self.pending_account, "code": "QX7M2K9P", "active": self.active_tag}


class FakeGamepad:
    def __init__(self, console: FakeConsole):
        self.console, self.taps, self.sticks = console, [], []

    def tap(self, button: Button) -> None:
        self.taps.append(button)
        self.console.press(button)

    def stick(self, x: float, y: float, duration_s: float) -> None:
        self.sticks.append((x, y, duration_s))


class FakeClipboard:
    def __init__(self):
        self.text, self.history = "", []

    def copy(self, text: str) -> None:
        self.text = text
        self.history.append(text)

    def clear(self) -> None:
        self.text = ""


class FakeWindow:
    def __init__(self, console: FakeConsole, clipboard: FakeClipboard):
        self.console, self.clipboard = console, clipboard
        self.focusable, self.reloads, self.pastes = True, 0, 0
        self.focus_calls = 0
        self.opened: list[str] = []

    def focus(self) -> bool:
        self.focus_calls += 1
        return self.focusable

    def ensure_open(self, url: str) -> bool:
        if self.opened:  # the window already exists after the first call
            return False
        self.opened.append(url)
        return True

    def is_foreground(self) -> bool:
        return self.focusable

    def reload(self) -> None:
        self.reloads += 1
        self.console.black = 0

    def paste(self) -> None:
        self.pastes += 1
        if self.console.paste_works and self.console.scene == "redeem":
            self.console.field = self.clipboard.text


class FakeCapture:
    def __init__(self, console: FakeConsole):
        self.console = console
        self.on_grab: Any = None  # optional hook, called with the scene as it was before this grab

    def grab(self) -> Frame:
        if self.on_grab:
            self.on_grab(self.console.scene)
        if self.console.black > 0:
            self.console.black -= 1
            return Frame(b"{}", "black", 0.0)
        snap = self.console.snapshot()
        digest = f"{snap['scene']}|{snap['focus']}|{snap['field']}|{snap['result']}"
        return Frame(json.dumps(snap).encode(), digest, 100.0)


class FakeOcr:
    def read(self, png: bytes, *, psm: int = 6) -> str:
        return json.loads(png).get("ocr", "")


class FakeVision:
    def __init__(self):
        self.calls: list[str] = []

    def ask(self, png: bytes, task: str, required_keys, schema=None) -> dict[str, Any]:
        key = required_keys[0]
        self.calls.append(key)
        s = json.loads(png)
        if key == "screen":
            return {"screen": SCREEN.get(s["scene"], "unknown")}
        if key == "code":
            return {"code": s["code"], "confidence": 0.95}
        if key == "items":
            return {"layout": "vertical", "items": [
                {"label": label, "focused": i == s["focus"], "row": i, "col": 0}
                for i, label in enumerate(s["items"])]}
        if key == "outcome":
            return {"outcome": s["result"]}
        if key == "text":
            return {"text": s["field"]}
        if key == "logged_in":
            return {"logged_in": s["signed_in"] and s["scene"] in ("home", "guide_signed_in"), "gamertag": s["active"]}
        if key == "state":
            return {"state": {"game_loading": "loading", "game_main_menu": "main_menu"}.get(s["scene"], "unknown")}
        if key == "match":
            return {"match": s["scene"] == "confirm_remove" and f'"{s["pending"]}"' in task}
        raise AssertionError(f"unexpected vision request {key}")


class FakeTime:
    def __init__(self):
        self.now = 0.0

    def sleep(self, s: float) -> None:
        self.now += s

    def clock(self) -> float:
        return self.now


class FakeDashboard:
    """Delivers one scripted command per poll; hooks let a test react to events."""

    def __init__(self, time_: FakeTime):
        self.time = time_
        self.queue: list[Command] = []
        self.events: list[tuple[str, dict[str, Any], bytes | None]] = []
        self.on_deliver: dict[str, Callable[[Command], None]] = {}
        self.on_event: dict[str, Callable[[dict[str, Any]], None]] = {}
        self.acked: list[str] = []
        self.empty_polls = 0
        self._n = 0

    def push(self, type_: str, **payload: Any) -> None:
        self._n += 1
        self.queue.append(Command(str(self._n), type_, payload))

    def fetch_commands(self, wait_s: float) -> list[Command]:
        if not self.queue:
            self.empty_polls += 1
            assert self.empty_polls < 500, "agent is waiting but no command is scripted (deadlock)"
            self.time.now += wait_s
            return []
        cmd = self.queue.pop(0)
        if cmd.type in self.on_deliver:
            self.on_deliver[cmd.type](cmd)
        return [cmd]

    def ack(self, command: Command) -> None:
        self.acked.append(command.id)

    def post_event(self, kind: str, payload: dict[str, Any], image: bytes | None = None) -> None:
        self.events.append((kind, payload, image))
        if kind in self.on_event:
            self.on_event[kind](payload)

    def kinds(self) -> list[str]:
        return [k for k, _, _ in self.events]

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [p for k, p, _ in self.events if k == kind]


class Rig:
    """Everything wired together; tests poke at `console`, `dash`, `agent`."""

    def __init__(self, scene: str = "sign_in_prompt", dashboard: Any = None, **cfg_overrides: Any):
        self.tmp = tempfile.TemporaryDirectory()
        self.time = FakeTime()
        self.console = FakeConsole(scene)
        self.clipboard = FakeClipboard()
        self.window = FakeWindow(self.console, self.clipboard)
        self.gamepad = FakeGamepad(self.console)
        self.vision = FakeVision()
        self.dash = dashboard or FakeDashboard(self.time)
        self.store = SqliteStore(f"{self.tmp.name}/db.sqlite3", f"{self.tmp.name}/shots")
        self.lines: list[str] = []
        cfg_overrides.setdefault("allow_delete", True)
        self.cfg = Settings(poll_interval_s=1.0, **cfg_overrides)
        self.capture = FakeCapture(self.console)
        tools = Tools(self.gamepad, self.window, self.capture, FakeOcr(), self.vision,
                      self.clipboard, self.dash, self.store)
        self.agent = XboxAgent(tools, self.cfg, sleep=self.time.sleep, clock=self.time.clock,
                               out=self.lines.append, token_factory=lambda: "tok123")
        if isinstance(self.dash, FakeDashboard):  # a real dashboard has no hooks; its admin logs in by hand
            self.dash.on_deliver["admin_login_confirmed"] = (
                lambda c: self.console.admin_login() if c.payload.get("session_id") == "s1" else None)

    def close(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def reports(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.lines]

    def last_report(self) -> dict[str, Any]:
        return self.dash.of("session_report")[-1]


class RigTestCase(unittest.TestCase):
    def make_rig(self, *args: Any, **kw: Any) -> Rig:
        rig = Rig(*args, **kw)
        self.addCleanup(rig.close)
        return rig
