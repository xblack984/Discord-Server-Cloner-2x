"""Redeem-screen helpers: OCR result classification and paste verification."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from .models import RedeemOutcome

# Order matters only for readability; classification requires exactly one family to match.
_PATTERNS: dict[RedeemOutcome, re.Pattern[str]] = {
    RedeemOutcome.CODE_USED: re.compile(
        r"already (been )?(redeemed|used)|has been (redeemed|used)|previously redeemed", re.I
    ),
    RedeemOutcome.REDEEMED: re.compile(
        r"successfully redeemed|code redeemed|you'?re all set|added to your (library|account)|"
        r"enjoy your",
        re.I,
    ),
    RedeemOutcome.CODE_INVALID: re.compile(
        r"isn'?t valid|not valid|invalid code|can'?t find (that|this) code|"
        r"doesn'?t look right|check the code|couldn'?t redeem",
        re.I,
    ),
    RedeemOutcome.NETWORK: re.compile(
        r"can'?t connect|no network|network (error|problem)|connection (lost|error)|"
        r"try again later|something went wrong",
        re.I,
    ),
}


def classify_redeem_text(text: str) -> RedeemOutcome | None:
    """Return an outcome only when exactly one family matches; otherwise None (ambiguous)."""
    hits = [outcome for outcome, rx in _PATTERNS.items() if rx.search(text or "")]
    return hits[0] if len(hits) == 1 else None


_CONFUSABLE = str.maketrans({"O": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2"})


def _squash(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper()).translate(_CONFUSABLE)


def paste_looks_right(ocr_text: str, code: str, threshold: float = 0.85) -> bool:
    """Fuzzy check that the pasted code is visible in OCR text (tolerates O/0, I/1, ...)."""
    want = _squash(code)
    have = _squash(ocr_text)
    if not have:
        return False
    if want in have:
        return True
    n = len(want)
    best = 0.0
    for i in range(0, max(1, len(have) - n + 1)):
        best = max(best, SequenceMatcher(None, want, have[i : i + n]).ratio())
    return best >= threshold
