"""Vision-guided menu navigation with the virtual gamepad (no mouse, no free typing)."""
from __future__ import annotations

import re
import time
from typing import Callable

from . import vision_tasks as vt
from .config import Settings
from .models import Button, ErrorCategory, Escalation, Frame
from .tools.protocols import Vision

Matcher = Callable[[str], bool]


def label_matcher(*needles: str, exclude: str | None = None) -> Matcher:
    """Case-insensitive 'contains any needle' matcher with an optional exclusion regex."""
    needles_l = [n.lower() for n in needles]
    ex = re.compile(exclude, re.I) if exclude else None

    def match(label: str) -> bool:
        text = re.sub(r"\s+", " ", label.lower()).strip()
        if ex and ex.search(text):
            return False
        return any(n in text for n in needles_l)

    return match


def regex_matcher(include: str, exclude: str | None = None) -> Matcher:
    inc = re.compile(include, re.I)
    ex = re.compile(exclude, re.I) if exclude else None
    return lambda label: bool(inc.search(label)) and not (ex and ex.search(label))


class Navigator:
    def __init__(
        self,
        cfg: Settings,
        vision: Vision,
        press: Callable[[Button], None],
        snap: Callable[[], Frame],
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.cfg = cfg
        self.vision = vision
        self.press = press
        self.snap = snap
        self.sleep = sleep

    def select(self, matcher: Matcher, what: str, *, press_confirm: bool = True) -> str:
        """Move focus to the single item matching `matcher`, then press A. Returns its label.

        Escalates (never guesses) on: no menu visible, several matches, focus not visible,
        target not found after a bounded scan, or a stalled stream.
        """
        scans = blanks = unchanged = 0
        last_digest: str | None = None
        for _ in range(self.cfg.nav_max_moves):
            frame = self.snap()
            unchanged = unchanged + 1 if frame.digest == last_digest else 0
            last_digest = frame.digest
            if unchanged >= self.cfg.stall_limit:
                raise Escalation(ErrorCategory.STREAM_LOST, f"frame unchanged while selecting {what}")

            menu = vt.read_menu(self.vision, frame.png)
            if not menu.items:
                blanks += 1
                if blanks >= 3:
                    raise Escalation(ErrorCategory.UNKNOWN, f"no menu visible while selecting {what}")
                self.sleep(self.cfg.settle_s)
                continue

            matches = [i for i in menu.items if matcher(i.label)]
            if len(matches) > 1:
                labels = [m.label for m in matches]
                raise Escalation(ErrorCategory.UNKNOWN, f"ambiguous {what}: {labels}")

            if not matches:
                scans += 1
                if scans > self.cfg.nav_scan_limit:
                    raise Escalation(ErrorCategory.UNKNOWN, f"{what} not found in menu")
                self._step(Button.RIGHT if menu.layout == "horizontal" else Button.DOWN)
                continue

            target = matches[0]
            if target.focused:
                if press_confirm:
                    self._step(Button.A)
                return target.label

            focused = next((i for i in menu.items if i.focused), None)
            if focused is None:
                raise Escalation(ErrorCategory.UNKNOWN, f"cannot see focus while selecting {what}")
            if target.row != focused.row:
                self._step(Button.DOWN if target.row > focused.row else Button.UP)
            else:
                self._step(Button.RIGHT if target.col > focused.col else Button.LEFT)
        raise Escalation(ErrorCategory.UNKNOWN, f"too many moves while selecting {what}")

    def _step(self, button: Button) -> None:
        self.press(button)
        self.sleep(self.cfg.settle_s)
