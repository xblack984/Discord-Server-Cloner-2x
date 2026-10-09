"""Dashboard state: one session at a time, a command queue for the agent, an event log.

Pure Python (no web framework) so the rules are easy to test. Rules enforced here, not in the UI:
  * only a well-formed redeem code is queued; full codes are dropped from history once delivered;
  * profile deletion is confirmed by typing the gamertag; the one-time token never reaches the browser;
  * admin buttons are refused when there is nothing for them to act on.
"""
from __future__ import annotations

import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..models import display_code, normalize_code

MAX_CODES = 50
AGENT_ONLINE_WITHIN_S = 20
EVENT_KINDS = {"step", "login_code", "redeem_result", "error", "game_ready", "notice",
               "delete_profile_request", "session_report"}
ADMIN_COMMANDS = {"admin_login_confirmed", "close_game", "retry", "skip", "abort_session",
                  "deny_delete_profile"}
# Trial aid: stop the session cleanly after one of these states (the agent leaves the console as is)
STOP_AFTER = ("SEND_CODE", "DETECT_LOGGED_IN", "REDEEM_CODES", "WAIT_GAME_READY")
_SHOT_ID = re.compile(r"^[0-9a-f]{32}$")


class Refused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


@dataclass
class Session:
    id: str
    created: float
    state: str = "QUEUED"
    status: str = "starting"  # starting | running | ended
    codes: list[dict[str, Any]] = field(default_factory=list)  # {id, masked, status}
    login_code: str | None = None
    login_shot: str | None = None
    game_ready: bool = False
    delete_request: dict[str, Any] | None = None  # {gamertag, token}; token stays server-side
    error: dict[str, Any] | None = None
    notice: dict[str, Any] | None = None  # "waiting for you" (e.g. sign in to Microsoft in the browser)
    report: dict[str, Any] | None = None


class DashboardState:
    def __init__(self, shots_dir: str | Path, *, max_events: int = 300, max_shots: int = 300,
                 clock: Callable[[], float] = time.time):
        self.clock = clock
        self.shots_dir = Path(shots_dir)
        self.shots_dir.mkdir(parents=True, exist_ok=True)
        self.max_shots = max_shots
        self.events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self.commands: list[dict[str, Any]] = []
        self.session: Session | None = None
        self.last_seen: float | None = None
        self._next_cmd = 0

    # ---------------------------------------------------------------- agent side

    def touch_agent(self) -> None:
        self.last_seen = self.clock()

    def take_queued(self) -> list[dict[str, Any]]:
        """Commands for the agent. Each is handed out once; full codes are scrubbed afterwards."""
        self.touch_agent()
        out = []
        for c in self.commands:
            if c["status"] == "queued":
                c["status"] = "delivered"
                out.append({"id": c["id"], "type": c["type"], "payload": dict(c["payload"])})
                if c["type"] == "start_session":
                    c["payload"] = {**c["payload"], "codes": [
                        {"id": x["id"], "code": "(delivered)"} for x in c["payload"]["codes"]]}
        return out

    def ack(self, command_id: str) -> None:
        self.touch_agent()
        for c in self.commands:
            if c["id"] == command_id and c["status"] == "delivered":
                c["status"] = "consumed"

    def save_shot(self, png: bytes) -> str:
        shot_id = uuid.uuid4().hex
        (self.shots_dir / f"{shot_id}.png").write_bytes(png)
        files = sorted(self.shots_dir.glob("*.png"), key=lambda p: p.stat().st_mtime)
        for old in files[: max(0, len(files) - self.max_shots)]:
            old.unlink(missing_ok=True)
        return shot_id

    def shot_path(self, shot_id: str) -> Path | None:
        if not _SHOT_ID.match(shot_id):
            return None
        path = self.shots_dir / f"{shot_id}.png"
        return path if path.is_file() else None

    def ingest(self, kind: str, payload: dict[str, Any], image: bytes | None) -> None:
        if kind not in EVENT_KINDS:
            raise Refused(400, f"unknown event kind '{kind}'")
        self.touch_agent()
        shot = self.save_shot(image) if image else None
        sid = payload.get("session_id")
        self.events.append({
            "ts": self.clock(), "kind": kind, "session_id": sid, "shot": shot,
            "state": str(payload.get("state") or "")[:40],
            "action": str(payload.get("action") or kind)[:60],
            "message": str(payload.get("message") or "")[:500],
        })
        s = self.session
        if not s or sid != s.id:  # idle heartbeats / stale events are only logged
            return
        if s.status == "starting":
            s.status = "running"
        if kind == "step":
            s.state = str(payload.get("state") or s.state)[:40]
        elif kind == "login_code":
            s.login_code, s.login_shot = str(payload.get("code") or "")[:32] or None, shot
        elif kind == "redeem_result":
            for c in s.codes:
                if c["id"] == str(payload.get("code_id")):
                    c["status"] = str(payload.get("outcome"))[:30]
        elif kind == "error":
            s.error = {"category": str(payload.get("category"))[:30], "message": str(payload.get("message"))[:500],
                       "ocr_text": str(payload.get("ocr_text") or "")[:2000], "state": str(payload.get("state"))[:40],
                       "can_skip": bool(payload.get("can_skip")), "skip_label": str(payload.get("skip_label") or "")[:30],
                       "shot": shot, "ts": self.clock()}
        elif kind == "notice":
            text = str(payload.get("message") or "")[:400]
            s.notice = {"message": text, "shot": shot} if text else None
        elif kind == "game_ready":
            s.game_ready = True
        elif kind == "delete_profile_request":
            tag, token = str(payload.get("gamertag") or ""), str(payload.get("confirm_token") or "")
            s.delete_request = {"gamertag": tag[:60], "token": token} if tag and token else None
        elif kind == "session_report":
            s.report, s.status, s.notice = payload, "ended", None

    # ---------------------------------------------------------------- admin side

    def _enqueue(self, type_: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._next_cmd += 1
        cmd = {"id": str(self._next_cmd), "type": type_, "payload": payload, "status": "queued", "ts": self.clock()}
        self.commands.append(cmd)
        del self.commands[:-100]
        return cmd

    def _active(self) -> Session:
        if not self.session or self.session.status == "ended":
            raise Refused(409, "there is no active session")
        return self.session

    def start_session(self, codes_text: str, stop_after: str | None = None) -> Session:
        if self.session and self.session.status != "ended":
            raise Refused(409, "a session is already running (abort or reset it first)")
        codes, bad, seen = [], [], set()
        for n, line in enumerate(codes_text.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            norm = normalize_code(line)
            if norm is None:
                bad.append(n)
            elif norm in seen:
                bad.append(n)
            else:
                seen.add(norm)
                codes.append(norm)
        if stop_after and stop_after not in STOP_AFTER:
            raise Refused(400, f"stop_after must be one of {', '.join(STOP_AFTER)}")
        if bad:
            raise Refused(400, f"invalid or duplicate redeem code on line(s): {', '.join(map(str, bad))}")
        if len(codes) > MAX_CODES:
            raise Refused(400, f"at most {MAX_CODES} codes per session")
        sid = "s" + uuid.uuid4().hex[:8]
        self.session = Session(sid, self.clock(), codes=[
            {"id": f"c{i}", "masked": "XXXXX-XXXXX-XXXXX-XXXXX-" + c[-5:], "status": "pending"}
            for i, c in enumerate(codes, 1)])
        payload: dict[str, Any] = {"session_id": sid, "codes": [
            {"id": f"c{i}", "code": display_code(c)} for i, c in enumerate(codes, 1)]}
        if stop_after:
            payload["stop_after"] = stop_after
        self._enqueue("start_session", payload)
        return self.session

    def command(self, type_: str) -> dict[str, Any]:
        if type_ not in ADMIN_COMMANDS:
            raise Refused(400, f"unknown command '{type_}'")
        s = self._active()
        if type_ == "admin_login_confirmed":
            if not s.login_code:
                raise Refused(409, "no sign-in code is waiting for confirmation")
            s.login_code = s.login_shot = None  # codes are single use
        elif type_ == "close_game" and not s.game_ready:
            raise Refused(409, "the game is not ready yet")
        elif type_ in ("retry", "skip"):
            if not s.error:
                raise Refused(409, "there is no error waiting for an answer")
            if type_ == "skip" and not s.error["can_skip"]:
                raise Refused(409, "this error cannot be skipped")
            s.error = None
        elif type_ == "abort_session":
            s.error = s.notice = None
        elif type_ == "deny_delete_profile":
            if not s.delete_request:
                raise Refused(409, "no profile deletion was requested")
            s.delete_request = None
        return self._enqueue(type_, {"session_id": s.id})

    def confirm_delete(self, typed_gamertag: str) -> dict[str, Any]:
        s = self._active()
        req = s.delete_request
        if not req:
            raise Refused(409, "no profile deletion was requested")
        if typed_gamertag.strip().casefold() != req["gamertag"].strip().casefold():
            raise Refused(400, "the typed gamertag does not match the profile that would be removed")
        s.delete_request = None
        return self._enqueue("confirm_delete_profile", {"session_id": s.id, "token": req["token"]})

    def reset(self) -> None:
        """Escape hatch for a dead agent: drop the session and any undelivered commands."""
        if self.session:
            self.session.status = "ended"
        for c in self.commands:
            if c["status"] == "queued":
                c["status"] = "dropped"

    def snapshot(self) -> dict[str, Any]:
        now = self.clock()
        s = self.session
        return {
            "agent": {"online": self.last_seen is not None and now - self.last_seen < AGENT_ONLINE_WITHIN_S,
                      "seconds_ago": None if self.last_seen is None else round(now - self.last_seen)},
            "session": None if s is None else {
                "id": s.id, "state": s.state, "status": s.status, "codes": s.codes,
                "login_code": s.login_code, "login_shot": s.login_shot, "game_ready": s.game_ready,
                "delete_request": None if not s.delete_request else {"gamertag": s.delete_request["gamertag"]},
                "error": s.error, "notice": s.notice, "report": s.report},
            "commands": [{"id": c["id"], "type": c["type"], "status": c["status"], "ts": c["ts"]}
                         for c in self.commands[-15:]],
            "events": list(self.events)[-120:],
        }
