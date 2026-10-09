"""Clipboard wrapper (pyperclip). Text reaches the console only via copy + Ctrl+V."""
from __future__ import annotations


class PyperclipClipboard:
    def __init__(self) -> None:
        import pyperclip

        self._pc = pyperclip

    def copy(self, text: str) -> None:
        self._pc.copy(text)

    def clear(self) -> None:
        self._pc.copy("")
