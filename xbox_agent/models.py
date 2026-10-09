"""Shared data types: workflow states, error categories, the JSON step report."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class State(str, Enum):
    IDLE = "IDLE"
    START_SESSION = "START_SESSION"
    REACH_LOGIN = "REACH_LOGIN"
    EXTRACT_CODE = "EXTRACT_CODE"
    SEND_CODE = "SEND_CODE"
    WAIT_ADMIN_LOGIN = "WAIT_ADMIN_LOGIN"
    DETECT_LOGGED_IN = "DETECT_LOGGED_IN"
    REDEEM_CODES = "REDEEM_CODES"
    OPEN_LIBRARY = "OPEN_LIBRARY"
    LAUNCH_COD_MW = "LAUNCH_COD_MW"
    WAIT_GAME_READY = "WAIT_GAME_READY"
    NOTIFY_GAME_READY = "NOTIFY_GAME_READY"
    WAIT_CLOSE_CMD = "WAIT_CLOSE_CMD"
    CLOSE_GAME = "CLOSE_GAME"
    LOGOUT = "LOGOUT"
    REQUEST_DELETE_PROFILE = "REQUEST_DELETE_PROFILE"
    DELETE_PROFILE = "DELETE_PROFILE"
    REPORT = "REPORT"


class ErrorCategory(str, Enum):
    NETWORK = "network"
    AUTH = "auth"
    CODE_INVALID = "code_invalid"
    CODE_USED = "code_used"
    GAME_STUCK = "game_stuck"
    STREAM_LOST = "stream_lost"
    UNKNOWN = "unknown"


class RedeemOutcome(str, Enum):
    REDEEMED = "redeemed"
    CODE_INVALID = "code_invalid"
    CODE_USED = "code_used"
    NETWORK = "network"
    PENDING = "pending"
    UNKNOWN = "unknown"


class Button(str, Enum):
    A = "A"
    B = "B"
    X = "X"
    Y = "Y"
    UP = "UP"
    DOWN = "DOWN"
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    GUIDE = "GUIDE"
    START = "START"  # the Menu button (three lines)
    BACK = "BACK"  # the View button (two windows)
    LB = "LB"
    RB = "RB"


@dataclass(frozen=True)
class Frame:
    """One captured browser-window frame (title bar and top strip already cropped)."""

    png: bytes
    digest: str
    mean_luma: float


@dataclass
class Command:
    id: str
    type: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class RedeemCode:
    id: str
    code: str  # normalized: 25 upper-case alphanumerics, no separators
    status: str = "pending"  # pending | redeemed | code_invalid | code_used | skipped | ...

    @property
    def masked(self) -> str:
        return "XXXXX-XXXXX-XXXXX-XXXXX-" + self.code[-5:] if self.code else "(malformed)"


_CODE_RE = re.compile(r"^[A-Z0-9]{25}$")


def normalize_code(raw: str) -> str | None:
    """Return the 25-char form of a redeem code, or None if the format is wrong."""
    cleaned = re.sub(r"[\s-]", "", raw or "").upper()
    return cleaned if _CODE_RE.match(cleaned) else None


def display_code(code: str) -> str:
    return "-".join(code[i : i + 5] for i in range(0, 25, 5))


@dataclass
class StepReport:
    """The one-line JSON the agent emits for every step (output contract)."""

    state: State
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    screenshot_id: str | None = None
    window_focused: bool = False
    message: str = ""
    need_confirmation: bool = False
    next_expected_event: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "action": self.action,
            "params": self.params,
            "screenshot_id": self.screenshot_id,
            "window_focused": self.window_focused,
            "message": self.message,
            "need_confirmation": self.need_confirmation,
            "next_expected_event": self.next_expected_event,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class Escalation(Exception):
    """Something is unclear or wrong: stop, report with a screenshot, wait for the admin."""

    def __init__(self, category: ErrorCategory, message: str):
        super().__init__(message)
        self.category = category
        self.message = message


class SessionAborted(Exception):
    """The admin aborted the session (or declined to continue after an escalation)."""
