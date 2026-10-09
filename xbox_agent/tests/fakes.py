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
    "quit_confirm": "confirm_dialog", "password_prompt": "password_prompt",
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
        self.accounts = ["AgentTest99", "OtherUser"]
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
            "sign_in_prompt": ["Sign in", "Sign in on another device"],
            "home": ["My games & apps", "Store"],
            "store": ["Games", "Redeem a code"],
            "library": ["Call of Duty: Modern Warfare II", "Call of Duty: Modern Warfare", "Forza Horizon"],
            "game_page": ["Play" if self.has_play else "Install", "Manage game"],
            "profile_menu": ["Settings", "Sign out"],
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
        if b is Button.GUIDE:
            return self._go("guide_menu")
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
            ("sign_in_prompt", "Sign in on another device"): "login_code",
            ("home", "My games & apps"): "library", ("home", "Store"): "store",
            ("guide_menu", "Home"): "home", ("guide_menu", "Store"): "store",
            ("guide_menu", "Profile & system"): "profile_menu", ("guide_menu", "Quit"): "quit_confirm",
            ("store", "Redeem a code"): "redeem",
            ("library", "Call of Duty: Modern Warfare"): "game_page",
            ("profile_menu", "Settings"): "settings", ("settings", "Account"): "account_menu",
            ("account_menu", "Remove accounts"): "accounts",
        }.get((s, label))
        if s == "game_page" and label == "Play":
            self.game_running, self.loading_snaps = True, 0
            return self._go("game_loading")
        if s == "profile_menu" and label == "Sign out":
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
        self._go("home")

    def snapshot(self) -> dict[str, Any]:
        if self.scene == "game_loading":
            self.loading_snaps += 1
            if self.loading_snaps > 2:
                self.scene = "game_main_menu"
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
                "pending": self.pending_account, "code": "QX7M2K9P"}


class FakeGamepad:
    def __init__(self, console: FakeConsole):
        self.console, self.taps = console, []

    def tap(self, button: Button) -> None:
        self.taps.append(button)
        self.console.press(button)

    def stick(self, x: float, y: float, duration_s: float) -> None:
        pass


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

    def focus(self) -> bool:
        return self.focusable

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

    def grab(self) -> Frame:
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

    def ask(self, png: bytes, task: str, required_keys) -> dict[str, Any]:
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
            return {"logged_in": s["signed_in"] and s["scene"] == "home", "gamertag": "AgentTest99"}
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

    def __init__(self, scene: str = "sign_in_prompt", **cfg_overrides: Any):
        self.tmp = tempfile.TemporaryDirectory()
        self.time = FakeTime()
        self.console = FakeConsole(scene)
        self.clipboard = FakeClipboard()
        self.window = FakeWindow(self.console, self.clipboard)
        self.gamepad = FakeGamepad(self.console)
        self.vision = FakeVision()
        self.dash = FakeDashboard(self.time)
        self.store = SqliteStore(f"{self.tmp.name}/db.sqlite3", f"{self.tmp.name}/shots")
        self.lines: list[str] = []
        self.cfg = Settings(poll_interval_s=1.0, **cfg_overrides)
        tools = Tools(self.gamepad, self.window, FakeCapture(self.console), FakeOcr(), self.vision,
                      self.clipboard, self.dash, self.store)
        self.agent = XboxAgent(tools, self.cfg, sleep=self.time.sleep, clock=self.time.clock,
                               out=self.lines.append, token_factory=lambda: "tok123")
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
