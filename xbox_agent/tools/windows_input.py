"""Windows-only: browser window focus + keyboard shortcuts + gamepad. ctypes/vgamepad only.

No mouse is ever used. The only keys sent are Ctrl+V (paste the redeem code) and F5 (reload the
stream page when the connection drops)."""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from ..models import Button

_VK_CONTROL, _VK_V, _VK_F5, _VK_MENU = 0x11, 0x56, 0x74, 0x12
_KEYUP = 0x0002
_SW_RESTORE = 9


class Win32Window:
    def __init__(self, title_substring: str, crop_top_px: int = 80):
        self._user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        try:  # physical pixels, so mss coordinates line up
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # type: ignore[attr-defined]
        except Exception:
            pass
        self._needle = title_substring.lower()
        self._crop = crop_top_px

    def _find(self) -> int | None:
        found: list[int] = []
        proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def cb(hwnd, _):
            if self._user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(512)
                self._user32.GetWindowTextW(hwnd, buf, 512)
                if self._needle in buf.value.lower():
                    found.append(hwnd)
            return True

        self._user32.EnumWindows(proc(cb), 0)
        return found[0] if found else None

    def is_foreground(self) -> bool:
        hwnd = self._find()
        return bool(hwnd) and self._user32.GetForegroundWindow() == hwnd

    def focus(self) -> bool:
        hwnd = self._find()
        if not hwnd:
            return False
        if self._user32.IsIconic(hwnd):
            self._user32.ShowWindow(hwnd, _SW_RESTORE)
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


class VGamepad:
    """Virtual Xbox 360 pad (ViGEmBus). The browser sees it through the Gamepad API."""

    def __init__(self) -> None:
        import vgamepad as vg

        self._vg = vg
        self._pad = vg.VX360Gamepad()
        b = vg.XUSB_BUTTON
        self._map = {
            Button.A: b.XUSB_GAMEPAD_A, Button.B: b.XUSB_GAMEPAD_B,
            Button.X: b.XUSB_GAMEPAD_X, Button.Y: b.XUSB_GAMEPAD_Y,
            Button.UP: b.XUSB_GAMEPAD_DPAD_UP, Button.DOWN: b.XUSB_GAMEPAD_DPAD_DOWN,
            Button.LEFT: b.XUSB_GAMEPAD_DPAD_LEFT, Button.RIGHT: b.XUSB_GAMEPAD_DPAD_RIGHT,
            Button.GUIDE: b.XUSB_GAMEPAD_GUIDE,
        }

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
