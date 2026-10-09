"""Windows-only: browser window focus + keyboard shortcuts + gamepad. ctypes/vgamepad only.

No mouse is ever used. The only keys sent are Ctrl+V (paste the redeem code) and F5 (reload the
stream page when the connection drops)."""
from __future__ import annotations

import ctypes
import subprocess
import time
from ctypes import wintypes
from typing import Sequence

from ..models import Button
from .browser import BROWSER_WINDOW_CLASS, find_browser, new_window_command, title_matches

_VK_CONTROL, _VK_V, _VK_F5, _VK_MENU = 0x11, 0x56, 0x74, 0x12
_KEYUP = 0x0002
_SW_RESTORE = 9


class Win32Window:
    """The Remote Play browser window. Once the agent has opened its own window it is pinned by handle,
    so no other window (e.g. the dashboard tab) can ever be mistaken for it."""

    def __init__(self, title_substring: str, crop_top_px: int = 80, exclude: Sequence[str] = ("XboxAgent",),
                 browser: str = "auto", open_timeout_s: float = 20.0, profile: str = ""):
        self._user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        try:  # physical pixels, so mss coordinates line up
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # type: ignore[attr-defined]
        except Exception:
            pass
        self._needle = title_substring
        self._exclude = list(exclude)
        self._crop = crop_top_px
        self._browser = browser
        self._open_timeout = open_timeout_s
        self._profile = profile
        self._pinned: int | None = None
        self.browser_path = ""

    def _windows(self) -> list[tuple[int, str, str]]:
        """Visible top-level windows with a title: (hwnd, title, class name)."""
        out: list[tuple[int, str, str]] = []
        proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def cb(hwnd, _):
            if self._user32.IsWindowVisible(hwnd):
                title = ctypes.create_unicode_buffer(512)
                cls = ctypes.create_unicode_buffer(256)
                self._user32.GetWindowTextW(hwnd, title, 512)
                self._user32.GetClassNameW(hwnd, cls, 256)
                if title.value:
                    out.append((hwnd, title.value, cls.value))
            return True

        self._user32.EnumWindows(proc(cb), 0)
        return out

    def _find(self) -> int | None:
        if self._pinned is not None:
            if self._user32.IsWindow(self._pinned) and self._user32.IsWindowVisible(self._pinned):
                return self._pinned
            self._pinned = None  # the user closed it
        for hwnd, title, cls in self._windows():
            # browsers only: the Xbox PC app and Game Bar are also titled "Xbox" but are not the stream
            if cls == BROWSER_WINDOW_CLASS and title_matches(title, self._needle, self._exclude):
                return hwnd
        return None

    def ensure_open(self, url: str) -> bool:
        """Open `url` in a new browser window unless a matching window already exists.
        Returns True if a window was opened (the page still needs time to load)."""
        if self._find() is not None:
            return False
        before = {hwnd for hwnd, _, _ in self._windows()}
        self.browser_path = find_browser(self._browser)
        subprocess.Popen(new_window_command(self.browser_path, url, self._profile), close_fds=True)
        deadline = time.monotonic() + self._open_timeout
        while time.monotonic() < deadline:
            time.sleep(0.4)
            new = [(h, t) for h, t, c in self._windows() if h not in before and c == BROWSER_WINDOW_CLASS]
            if new:
                self._pinned = new[0][0]
                return True
        raise RuntimeError(f"no new browser window appeared within {self._open_timeout:.0f}s after opening {url}")

    def is_foreground(self) -> bool:
        hwnd = self._find()
        return bool(hwnd) and self._user32.GetForegroundWindow() == hwnd

    def focus(self) -> bool:
        hwnd = self._find()
        if not hwnd:
            return False
        if self._user32.IsIconic(hwnd):
            self._user32.ShowWindow(hwnd, _SW_RESTORE)
        if self._user32.GetForegroundWindow() == hwnd:
            return True  # already in front: send no key at all (the user may be typing in it)
        # A tap on Alt lets SetForegroundWindow succeed from a background process.
        self._user32.keybd_event(_VK_MENU, 0, 0, 0)
        self._user32.keybd_event(_VK_MENU, 0, _KEYUP, 0)
        self._user32.SetForegroundWindow(hwnd)
        time.sleep(0.15)
        return self._user32.GetForegroundWindow() == hwnd

    def client_region(self) -> tuple[int, int, int, int]:
        """(left, top, width, height) of the client area minus the top strip (tabs/address bar)."""
        hwnd = self._find()
        if not hwnd:
            raise RuntimeError("browser window not found")
        rect = wintypes.RECT()
        self._user32.GetClientRect(hwnd, ctypes.byref(rect))
        origin = wintypes.POINT(0, 0)
        self._user32.ClientToScreen(hwnd, ctypes.byref(origin))
        return origin.x, origin.y + self._crop, rect.right, max(1, rect.bottom - self._crop)

    def _chord(self, *keys: int) -> None:
        for k in keys:
            self._user32.keybd_event(k, 0, 0, 0)
        for k in reversed(keys):
            self._user32.keybd_event(k, 0, _KEYUP, 0)
        time.sleep(0.2)

    def paste(self) -> None:
        self._chord(_VK_CONTROL, _VK_V)

    def reload(self) -> None:
        self._chord(_VK_F5)


def window_from_settings(cfg) -> Win32Window:
    return Win32Window(cfg.window_title, cfg.crop_top_px, cfg.window_exclude, cfg.browser,
                       profile=cfg.browser_profile)


# vgamepad.XUSB_BUTTON member for every Button (a test checks that none is missing)
VGAMEPAD_NAMES: dict[Button, str] = {
    Button.A: "XUSB_GAMEPAD_A", Button.B: "XUSB_GAMEPAD_B", Button.X: "XUSB_GAMEPAD_X", Button.Y: "XUSB_GAMEPAD_Y",
    Button.UP: "XUSB_GAMEPAD_DPAD_UP", Button.DOWN: "XUSB_GAMEPAD_DPAD_DOWN",
    Button.LEFT: "XUSB_GAMEPAD_DPAD_LEFT", Button.RIGHT: "XUSB_GAMEPAD_DPAD_RIGHT",
    Button.GUIDE: "XUSB_GAMEPAD_GUIDE", Button.START: "XUSB_GAMEPAD_START", Button.BACK: "XUSB_GAMEPAD_BACK",
    Button.LB: "XUSB_GAMEPAD_LEFT_SHOULDER", Button.RB: "XUSB_GAMEPAD_RIGHT_SHOULDER",
}


class VGamepad:
    """Virtual Xbox 360 pad (ViGEmBus). The browser sees it through the Gamepad API."""

    def __init__(self) -> None:
        import vgamepad as vg

        self._vg = vg
        self._pad = vg.VX360Gamepad()
        self._map = {button: getattr(vg.XUSB_BUTTON, name) for button, name in VGAMEPAD_NAMES.items()}

    def tap(self, button: Button) -> None:
        self._pad.press_button(button=self._map[button])
        self._pad.update()
        time.sleep(0.12)
        self._pad.release_button(button=self._map[button])
        self._pad.update()

    def stick(self, x: float, y: float, duration_s: float) -> None:
        self._pad.left_joystick_float(x_value_float=x, y_value_float=y)
        self._pad.update()
        time.sleep(duration_s)
        self._pad.left_joystick_float(x_value_float=0.0, y_value_float=0.0)
        self._pad.update()
