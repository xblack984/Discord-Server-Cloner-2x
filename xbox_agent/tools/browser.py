"""Finding Chrome/Edge and building the command that opens a URL in a NEW window of it.

Pure logic (no Windows API) so it can be tested anywhere. The default browser profile is used on
purpose: the Microsoft sign-in needed by xbox.com/play lives there, and the agent never types
credentials itself."""
from __future__ import annotations

import os
from typing import Callable, Mapping, Sequence

# Both Chrome and Edge create their top-level windows with this class name.
BROWSER_WINDOW_CLASS = "Chrome_WidgetWin_1"


def _candidates(env: Mapping[str, str]) -> dict[str, list[str]]:
    pf = env.get("ProgramFiles", r"C:\Program Files")
    pf86 = env.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = env.get("LOCALAPPDATA", "")
    chrome = [rf"{pf}\Google\Chrome\Application\chrome.exe", rf"{pf86}\Google\Chrome\Application\chrome.exe"]
    edge = [rf"{pf86}\Microsoft\Edge\Application\msedge.exe", rf"{pf}\Microsoft\Edge\Application\msedge.exe"]
    if local:
        chrome.append(rf"{local}\Google\Chrome\Application\chrome.exe")
    return {"chrome": chrome, "edge": edge}


def find_browser(preference: str = "auto", env: Mapping[str, str] = os.environ,
                 exists: Callable[[str], bool] = os.path.isfile) -> str:
    """Path of the browser executable. `preference`: auto (Chrome, else Edge) | chrome | edge | a full path."""
    pref = (preference or "auto").strip().strip('"')
    if pref.lower() not in ("auto", "chrome", "edge"):
        if exists(pref):
            return pref
        raise FileNotFoundError(f"XBOX_AGENT_BROWSER points to a file that does not exist: {pref}")
    found = _candidates(env)
    order: Sequence[str] = ("chrome", "edge") if pref.lower() == "auto" else (pref.lower(),)
    for name in order:
        for path in found[name]:
            if exists(path):
                return path
    raise FileNotFoundError(
        f"could not find {'Chrome or Edge' if pref.lower() == 'auto' else pref} in the usual folders; "
        "set XBOX_AGENT_BROWSER to the full path of chrome.exe or msedge.exe")


def title_matches(title: str, needle: str, exclude: Sequence[str] = ()) -> bool:
    """A window is the Remote Play window if its title has `needle` and none of the `exclude` words
    (the dashboard tab is called ...XboxAgent... and must never be picked)."""
    low = title.lower()
    return needle.lower() in low and not any(x.lower() in low for x in exclude if x)


def new_window_command(executable: str, url: str) -> list[str]:
    if not url.lower().startswith(("https://", "http://")):
        raise ValueError(f"refusing to open a non-http(s) address: {url!r}")
    return [executable, "--new-window", url]
