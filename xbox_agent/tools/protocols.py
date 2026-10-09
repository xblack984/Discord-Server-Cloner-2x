"""Interfaces the agent depends on. Real Windows implementations live next to this file;
tests inject fakes, so the state machine runs anywhere."""
from __future__ import annotations

from typing import Any, Protocol, Sequence

from ..models import Button, Command, Frame


class Gamepad(Protocol):
    def tap(self, button: Button) -> None: ...
    def stick(self, x: float, y: float, duration_s: float) -> None: ...


class BrowserWindow(Protocol):
    def focus(self) -> bool:
        """Bring the Remote Play window to the foreground; True only if it is now foreground."""

    def is_foreground(self) -> bool: ...
    def reload(self) -> None: ...
    def paste(self) -> None:
        """Send Ctrl+V to the (already focused) window."""


class Capture(Protocol):
    def grab(self) -> Frame: ...


class Ocr(Protocol):
    def read(self, png: bytes, *, psm: int = 6) -> str: ...


class Vision(Protocol):
    def ask(self, png: bytes, task: str, required_keys: Sequence[str]) -> dict[str, Any]:
        """Send an image + instructions to the model in JSON mode; return the parsed object."""


class Clipboard(Protocol):
    def copy(self, text: str) -> None: ...
    def clear(self) -> None: ...


class DashboardError(Exception):
    pass


class Dashboard(Protocol):
    def fetch_commands(self, wait_s: float) -> list[Command]:
        """Long-poll for new commands. Raise DashboardError on transport failure."""

    def ack(self, command: Command) -> None: ...
    def post_event(self, kind: str, payload: dict[str, Any], image: bytes | None = None) -> None:
        """Raise DashboardError if the event could not be delivered."""


class Store(Protocol):
    def save_shot(self, png: bytes) -> str: ...
    def log_event(self, session_id: str | None, state: str, kind: str, payload: dict[str, Any],
                  screenshot_id: str | None = None) -> None: ...
    def upsert_session(self, session_id: str, state: str, status: str) -> None: ...
    def upsert_code(self, session_id: str, code_id: str, masked: str, status: str) -> None: ...
    def unfinished_sessions(self) -> list[str]: ...
    def close_unfinished(self) -> None: ...
