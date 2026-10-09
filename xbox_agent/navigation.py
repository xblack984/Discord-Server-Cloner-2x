"""Vision-guided menu navigation with the virtual gamepad (no mouse, no free typing)."""
from __future__ import annotations

import re
import time
from typing import Callable, Sequence

from . import vision_tasks as vt
from .config import Settings
from .models import Button, ErrorCategory, Escalation, Frame
from .tools.protocols import Vision

Matcher = Callable[[str], bool]


_AR_MARKS = re.compile("[\u064B-\u065F\u0670\u0640]")  # tashkeel, dagger alef, tatweel
_AR_LETTERS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه"})


def normalize(text: str) -> str:
    """What labels are compared as: lower case, no ® ™ © signs, Arabic spelling variants unified
    (hamza forms, ya/alef maqsura, ta marbuta, diacritics), single spaces."""
    t = re.sub("[®™©]", "", text.lower())
    t = _AR_MARKS.sub("", t).translate(_AR_LETTERS)
    return re.sub(r"\s+", " ", t).strip()


def label_matcher(*needles: str, exclude: str | None = None) -> Matcher:
    """'Contains any needle' matcher over normalised text (English, German, Arabic), with an optional
    exclusion regex."""
    needles_n = [normalize(n) for n in needles if n]
    ex = re.compile(exclude, re.I) if exclude else None

    def match(label: str) -> bool:
        text = normalize(label)
        if ex and ex.search(text):
            return False
        return any(n in text for n in needles_n)

    return match


def regex_matcher(include: str, exclude: str | None = None) -> Matcher:
    inc = re.compile(include, re.I)
    ex = re.compile(exclude, re.I) if exclude else None

    def match(label: str) -> bool:
        text = normalize(label)  # "Modern Warfare® III" must read as "modern warfare iii"
        return bool(inc.search(text)) and not (ex and ex.search(text))

    return match


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

    def select(self, matcher: Matcher | Sequence[Matcher], what: str, *, press_confirm: bool = True) -> str:
        """Move focus to the single item matching `matcher`, then press A. Returns its label.
        A list of matchers is a priority list: the first one that matches anything decides.

        Escalates (never guesses) on: no menu visible, several matches, focus not visible,
        target not found after a bounded scan, or a stalled stream.
        """
        matchers = [matcher] if callable(matcher) else list(matcher)
        scans = blanks = unchanged = 0
        last_digest: str | None = None
        scanned = False  # the previous step was a "look further" press, not a move toward a target
        for _ in range(self.cfg.nav_max_moves):
            frame = self.snap()
            same = frame.digest == last_digest
            if same and scanned:  # pressing further changed nothing: the end of the menu, not a frozen stream
                raise Escalation(ErrorCategory.UNKNOWN, f"{what} not found in menu (reached its end)")
            scanned = False
            unchanged = unchanged + 1 if same else 0
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

            matches: list = []
            for m in matchers:
                matches = [i for i in menu.items if m(i.label)]
                if matches:
                    break
            if len(matches) > 1:
                raise Escalation(ErrorCategory.UNKNOWN, f"ambiguous {what}: {[i.label for i in matches]}")

            if not matches:
                scans += 1
                if scans > self.cfg.nav_scan_limit:
                    raise Escalation(ErrorCategory.UNKNOWN, f"{what} not found in menu")
                self._step(Button.RIGHT if menu.layout == "horizontal" else Button.DOWN)
                scanned = True
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
