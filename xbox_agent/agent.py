"""XboxAgent: the session state machine.

Hard rules enforced in code (not just in the prompt):
  1. Credential / OTP / captcha screens are never typed into: they escalate to the admin.
  2. Only the Clipboard + Ctrl+V path types text, and only a format-validated redeem code.
  3. A profile is removed only after a `confirm_delete_profile` command carrying the one-time
     token issued in the request, and only for the exact gamertag read from the home screen.
  4. Redeem goes through the console's own Redeem screen.
  5. Anything unclear -> screenshot + OCR text + category to the Dashboard, then wait.
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from . import vision_tasks as vt
from .config import Settings
from .models import (
    Button, Command, ErrorCategory, Escalation, Frame, RedeemCode, RedeemOutcome,
    SessionAborted, State, StepReport, display_code, normalize_code,
)
from .navigation import Navigator, label_matcher, regex_matcher
from .redeem import classify_redeem_text, paste_looks_right
from .tools.protocols import (
    BrowserWindow, Capture, Clipboard, Dashboard, DashboardError, Gamepad, Ocr, Store, Vision,
)

log = logging.getLogger("xbox_agent")

BLACK_LUMA = 4.0
_SKIP = object()


@dataclass
class Tools:
    gamepad: Gamepad
    window: BrowserWindow
    capture: Capture
    ocr: Ocr
    vision: Vision
    clipboard: Clipboard
    dashboard: Dashboard
    store: Store


@dataclass
class Session:
    id: str
    codes: list[RedeemCode]
    login_code: str | None = None
    gamertag: str | None = None
    game_ready: bool = False
    logged_out: bool = False
    profile_deleted: bool | None = None  # None: never requested
    delete_note: str = ""
    errors: list[dict[str, Any]] = field(default_factory=list)
    reconnects: int = 0


class XboxAgent:
    def __init__(
        self,
        tools: Tools,
        cfg: Settings,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        out: Callable[[str], None] = lambda s: print(s, flush=True),
        token_factory: Callable[[], str] = lambda: uuid.uuid4().hex[:12],
    ):
        self.t = tools
        self.cfg = cfg
        self.sleep = sleep
        self.clock = clock
        self.out = out
        self.token_factory = token_factory
        self.state = State.IDLE
        self._focused = False
        self._last_frame: Frame | None = None
        self._pending: list[Command] = []
        self.nav = Navigator(cfg, tools.vision, self._press, self._snap, sleep)

    # ------------------------------------------------------------------ main loops

    def run_forever(self) -> None:
        stale = self.t.store.unfinished_sessions()
        if stale:
            self._emit(None, State.IDLE, "recovered", params={"abandoned_sessions": stale},
                       message="previous run ended mid-session; marked abandoned, console state unknown")
            self.t.store.close_unfinished()
        while True:
            self.run_once()

    def run_once(self) -> None:
        self.state = State.IDLE
        self._emit(None, State.IDLE, "idle", next_expected_event="start_session")
        cmd = self._wait_command(None, {"start_session"}, None)
        assert cmd is not None
        self.run_session(cmd)

    def run_session(self, start: Command) -> None:
        ctx = self._build_session(start)
        try:
            self._run(ctx, State.START_SESSION, lambda: self._start_session(ctx))
            self._run(ctx, State.REACH_LOGIN, lambda: self._reach_login(ctx))
            self._run(ctx, State.EXTRACT_CODE, lambda: self._extract_code(ctx))
            self._run(ctx, State.SEND_CODE, lambda: self._send_code(ctx))
            self._run(ctx, State.WAIT_ADMIN_LOGIN, lambda: self._wait_admin_login(ctx))
            self._run(ctx, State.DETECT_LOGGED_IN, lambda: self._detect_logged_in(ctx))
            self._enter(ctx, State.REDEEM_CODES)
            self._redeem_codes(ctx)
            self._run(ctx, State.OPEN_LIBRARY, lambda: self._open_library(ctx))
            self._run(ctx, State.LAUNCH_COD_MW, lambda: self._launch_cod(ctx))
            self._run(ctx, State.WAIT_GAME_READY, lambda: self._wait_game_ready(ctx))
            self._run(ctx, State.NOTIFY_GAME_READY, lambda: self._notify_game_ready(ctx))
            self._run(ctx, State.WAIT_CLOSE_CMD, lambda: self._wait_close(ctx))
            self._run(ctx, State.CLOSE_GAME, lambda: self._close_game(ctx))
            self._run(ctx, State.LOGOUT, lambda: self._logout(ctx))
            self._run(ctx, State.REQUEST_DELETE_PROFILE, lambda: self._request_delete(ctx))
            if ctx.delete_note == "confirmed":  # only an explicit, token-matched confirmation
                self._run(ctx, State.DELETE_PROFILE, lambda: self._delete_profile(ctx))
            status = "completed"
        except SessionAborted as e:
            status = "aborted"
            ctx.errors.append({"state": self.state.value, "category": "aborted", "message": str(e)})
        self._enter(ctx, State.REPORT)
        self._report(ctx, status)
        self.t.store.upsert_session(ctx.id, State.REPORT.value, status)

    def _build_session(self, start: Command) -> Session:
        codes: list[RedeemCode] = []
        for i, raw in enumerate(start.payload.get("codes") or []):
            code_id = str(raw.get("id", i))
            norm = normalize_code(str(raw.get("code", "")))
            if norm is None:  # malformed: never reaches the clipboard
                rc = RedeemCode(code_id, "", status="code_invalid")
            else:
                rc = RedeemCode(code_id, norm)
            codes.append(rc)
        ctx = Session(id=str(start.payload.get("session_id") or start.id), codes=codes)
        self.t.store.upsert_session(ctx.id, State.START_SESSION.value, "running")
        for c in codes:
            self.t.store.upsert_code(ctx.id, c.id, c.masked, c.status)
        return ctx

    # ------------------------------------------------------------------ plumbing

    def _enter(self, ctx: Session | None, state: State) -> None:
        self.state = state
        if ctx:
            ctx.reconnects = 0
            self.t.store.upsert_session(ctx.id, state.value, "running")
        self._emit(ctx, state, "enter_state")

    def _run(self, ctx: Session, state: State, fn: Callable[[], Any]) -> Any:
        self._enter(ctx, state)
        return self._guarded(ctx, state, fn)

    def _guarded(self, ctx: Session, state: State, fn: Callable[[], Any], *, allow_skip: bool = False) -> Any:
        """Run fn; on any problem stop, try stream reconnect if applicable, else ask the admin."""
        while True:
            try:
                return fn()
            except SessionAborted:
                raise
            except Escalation as esc:
                problem = esc
            except Exception as e:  # unexpected: never improvise, escalate
                problem = self._wrap(e)
            if problem.category is ErrorCategory.STREAM_LOST and ctx.reconnects < self.cfg.reconnect_attempts:
                ctx.reconnects += 1
                if self._reconnect(ctx):
                    continue
            if self._escalate(ctx, state, problem, allow_skip=allow_skip) == "skip":
                return _SKIP

    @staticmethod
    def _wrap(e: Exception) -> Escalation:
        name = type(e).__name__
        net = isinstance(e, (ConnectionError, TimeoutError)) or any(k in name for k in ("Timeout", "Connect", "Network"))
        return Escalation(ErrorCategory.NETWORK if net else ErrorCategory.UNKNOWN, f"{name}: {e}")

    def _ensure_focus(self) -> None:
        self._focused = bool(self.t.window.focus())
        if not self._focused:
            raise Escalation(ErrorCategory.UNKNOWN, "browser window could not be brought to the foreground")

    def _press(self, button: Button) -> None:
        self._ensure_focus()
        self.t.gamepad.tap(button)

    def _snap(self) -> Frame:
        self._ensure_focus()
        frame = self.t.capture.grab()
        self._last_frame = frame
        if frame.mean_luma < BLACK_LUMA:
            raise Escalation(ErrorCategory.STREAM_LOST, "black frame")
        return frame

    def _classify(self, frame: Frame) -> str:
        screen = vt.classify_screen(self.t.vision, frame.png)
        if screen in vt.AUTH_BLOCKING:
            raise Escalation(ErrorCategory.AUTH, f"{screen} screen: admin must handle it, agent will not type here")
        return screen

    def _reconnect(self, ctx: Session) -> bool:
        self._emit(ctx, self.state, "reconnect", message="stream lost or frozen; reloading the Remote Play page")
        try:
            self._ensure_focus()
            self.t.window.reload()
            self.sleep(self.cfg.reload_wait_s)
            return self._classify(self._snap()) != "unknown"
        except Exception:
            return False

    def _emit(
        self, ctx: Session | None, state: State, action: str, *, params: dict[str, Any] | None = None,
        message: str = "", need_confirmation: bool = False, next_expected_event: str = "",
        frame: Frame | None = None, kind: str = "step", send_image: bool = True,
    ) -> StepReport:
        shot_id = self.t.store.save_shot(frame.png) if frame else None
        report = StepReport(state, action, params or {}, shot_id, self._focused, message,
                            need_confirmation, next_expected_event)
        self.out(report.to_json())
        sid = ctx.id if ctx else None
        self.t.store.log_event(sid, state.value, kind, report.to_dict(), shot_id)
        self._post(kind, {"session_id": sid, **report.to_dict()}, frame.png if frame and send_image else None)
        return report

    def _post(self, kind: str, payload: dict[str, Any], image: bytes | None = None) -> None:
        try:
            self.t.dashboard.post_event(kind, payload, image)
        except DashboardError as e:
            log.warning("dashboard unreachable (%s); event %s kept in local store only", e, kind)

    def _post_strict(self, kind: str, payload: dict[str, Any], image: bytes | None = None) -> None:
        try:
            self.t.dashboard.post_event(kind, payload, image)
        except DashboardError as e:
            raise Escalation(ErrorCategory.NETWORK, f"could not deliver {kind} to the dashboard: {e}")

    def _wait_command(
        self, ctx: Session | None, types: Iterable[str], timeout: float | None, *,
        on_tick: Callable[[], None] | None = None,
    ) -> Command | None:
        """Wait for one of `types` (or an abort). Commands for other states are kept for later."""
        allowed = set(types)
        deadline = None if timeout is None else self.clock() + timeout
        last_tick = self.clock()
        while True:
            cmd = self._take_pending(ctx, allowed)
            if cmd:
                try:
                    self.t.dashboard.ack(cmd)
                except DashboardError:
                    log.warning("ack failed for command %s", cmd.id)
                if cmd.type == "abort_session":
                    raise SessionAborted("aborted by the dashboard")
                return cmd
            if deadline is not None and self.clock() >= deadline:
                return None
            wait = self.cfg.poll_interval_s
            if deadline is not None:
                wait = max(0.0, min(wait, deadline - self.clock()))
            try:
                self._pending.extend(self.t.dashboard.fetch_commands(wait))
            except DashboardError as e:
                log.warning("cannot poll dashboard: %s", e)
                self.sleep(self.cfg.poll_interval_s)
            if on_tick and self.clock() - last_tick >= self.cfg.heartbeat_s:
                last_tick = self.clock()
                on_tick()

    def _take_pending(self, ctx: Session | None, allowed: set[str]) -> Command | None:
        for cmd in list(self._pending):
            sid = cmd.payload.get("session_id")
            if ctx and sid and sid != ctx.id:  # stale command from another session
                self._pending.remove(cmd)
                continue
            if cmd.type == "abort_session":
                self._pending.remove(cmd)
                if ctx:
                    return cmd
                continue  # nothing to abort while idle; don't let it linger into the next session
            if cmd.type in allowed:
                self._pending.remove(cmd)
                return cmd
        return None

    def _escalate(self, ctx: Session, state: State, esc: Escalation, *, allow_skip: bool = False) -> str:
        """Report the problem (image + OCR text + category) and block until the admin answers."""
        frame = self._last_frame
        try:
            self._ensure_focus()
            frame = self.t.capture.grab()
        except Exception:
            pass
        text = ""
        if frame:
            try:
                text = self.t.ocr.read(frame.png)
            except Exception:
                pass
        ctx.errors.append({"state": state.value, "category": esc.category.value, "message": esc.message})
        payload = {"session_id": ctx.id, "state": state.value, "category": esc.category.value,
                   "message": esc.message, "ocr_text": text, "can_skip": allow_skip}
        self._post("error", payload, frame.png if frame else None)
        self._emit(ctx, state, "escalate", params={"category": esc.category.value, "can_skip": allow_skip},
                   message=esc.message, need_confirmation=True,
                   next_expected_event="retry|skip|abort_session" if allow_skip else "retry|abort_session",
                   frame=frame, send_image=False)  # the "error" event above already carries it
        while True:
            cmd = self._wait_command(ctx, {"retry", "resume", "skip"}, None)
            assert cmd is not None
            if cmd.type == "skip" and allow_skip:
                return "skip"
            if cmd.type in ("retry", "resume"):
                return "retry"

    # ------------------------------------------------------------------ states

    def _start_session(self, ctx: Session) -> None:
        frame = self._snap()
        screen = self._classify(frame)
        if screen == "unknown":
            raise Escalation(ErrorCategory.STREAM_LOST, "cannot recognise anything on the stream")
        self._emit(ctx, State.START_SESSION, "stream_ok", params={"screen": screen}, frame=frame)

    def _reach_login(self, ctx: Session) -> None:
        for _ in range(4):
            frame = self._snap()
            screen = self._classify(frame)
            if screen == "login_code":
                self._emit(ctx, State.REACH_LOGIN, "login_screen_reached", frame=frame)
                return
            if screen == "sign_in_prompt":
                self.nav.select(label_matcher(*self.cfg.login_labels), "sign-in-with-code option")
                self.sleep(self.cfg.settle_s)
                continue
            if screen == "home":
                raise Escalation(ErrorCategory.AUTH, "a profile is already signed in; admin must decide")
            raise Escalation(ErrorCategory.UNKNOWN, f"unexpected screen '{screen}' while reaching login")
        raise Escalation(ErrorCategory.UNKNOWN, "login-by-code screen not reached")

    def _extract_code(self, ctx: Session) -> None:
        rx = re.compile(self.cfg.login_code_regex)
        reads: list[list[str]] = []
        for _ in range(2):
            frame = self._snap()
            reads.append(sorted(set(rx.findall(self.t.ocr.read(frame.png).upper()))))
        source = "tesseract"
        code: str | None = None
        if len(reads[0]) == 1 and reads[0] == reads[1]:
            code = reads[0][0]
        else:
            source = "gemini"
            code = vt.read_login_code(self.t.vision, frame.png)
        if not code or not rx.fullmatch(code):
            raise Escalation(ErrorCategory.UNKNOWN, "sign-in code unreadable or implausible")
        ctx.login_code = code
        self._emit(ctx, State.EXTRACT_CODE, "code_extracted", params={"source": source}, frame=frame)

    def _send_code(self, ctx: Session) -> None:
        assert ctx.login_code
        frame = self._last_frame
        self._post_strict("login_code", {"session_id": ctx.id, "code": ctx.login_code},
                          frame.png if frame else None)
        self._emit(ctx, State.SEND_CODE, "code_sent", message="sign-in code delivered to the dashboard")

    def _wait_admin_login(self, ctx: Session) -> None:
        self._emit(ctx, State.WAIT_ADMIN_LOGIN, "wait_admin", need_confirmation=True,
                   message="waiting for the admin to complete sign-in with the code",
                   next_expected_event="admin_login_confirmed")
        cmd = self._wait_command(ctx, {"admin_login_confirmed"}, self.cfg.admin_login_timeout_s)
        if cmd is None:
            raise Escalation(ErrorCategory.AUTH, "admin did not confirm sign-in in time (code may have expired)")

    def _detect_logged_in(self, ctx: Session) -> None:
        deadline = self.clock() + self.cfg.login_detect_timeout_s
        while True:
            frame = self._snap()
            self._classify(frame)  # raises on password / OTP / captcha screens
            logged, tag = vt.detect_logged_in(self.t.vision, frame.png)
            if logged:
                ctx.gamertag = tag
                self._emit(ctx, State.DETECT_LOGGED_IN, "logged_in", params={"gamertag": tag}, frame=frame)
                return
            if self.clock() >= deadline:
                raise Escalation(ErrorCategory.AUTH, "admin confirmed sign-in but the console shows no signed-in user")
            self.sleep(self.cfg.settle_s * 2)

    # -- redeem

    def _redeem_codes(self, ctx: Session) -> None:
        for code in ctx.codes:
            if code.status != "pending":
                continue
            result = self._guarded(ctx, State.REDEEM_CODES, lambda c=code: self._redeem_one(ctx, c), allow_skip=True)
            if result is _SKIP:
                code.status = "skipped"
                self.t.store.upsert_code(ctx.id, code.id, code.masked, code.status)
                self._post("redeem_result", {"session_id": ctx.id, "code_id": code.id, "masked": code.masked,
                                             "outcome": "skipped"})
            elif self.cfg.pause_on_code_error and result in (RedeemOutcome.CODE_INVALID, RedeemOutcome.CODE_USED):
                self._escalate(ctx, State.REDEEM_CODES, Escalation(ErrorCategory(result.value), f"code {code.masked}: {result.value}"),
                               allow_skip=True)
            self._guarded(ctx, State.REDEEM_CODES, self._go_home)

    def _redeem_one(self, ctx: Session, code: RedeemCode) -> RedeemOutcome:
        self._open_via_guide(self.cfg.redeem_path, expected="redeem")
        self._paste_code(code)
        self._press(Button.A)
        deadline = self.clock() + self.cfg.redeem_result_timeout_s
        while True:
            self.sleep(self.cfg.settle_s)
            frame = self._snap()
            outcome, source = self._read_outcome(frame)
            if outcome not in (RedeemOutcome.PENDING, RedeemOutcome.UNKNOWN):
                break
            if self.clock() >= deadline:
                raise Escalation(ErrorCategory.UNKNOWN, f"redeem result unclear for {code.masked}")
        if outcome is RedeemOutcome.NETWORK:
            raise Escalation(ErrorCategory.NETWORK, f"network problem while redeeming {code.masked}")
        code.status = outcome.value
        self.t.store.upsert_code(ctx.id, code.id, code.masked, code.status)
        self._post("redeem_result", {"session_id": ctx.id, "code_id": code.id, "masked": code.masked,
                                     "outcome": outcome.value, "source": source}, frame.png)
        self._emit(ctx, State.REDEEM_CODES, "redeem_result",
                   params={"code_id": code.id, "masked": code.masked, "outcome": outcome.value, "source": source},
                   frame=frame)
        return outcome

    def _read_outcome(self, frame: Frame) -> tuple[RedeemOutcome, str]:
        outcome = classify_redeem_text(self.t.ocr.read(frame.png))
        if outcome is not None:
            return outcome, "tesseract"
        return vt.read_redeem_result(self.t.vision, frame.png), "gemini"

    def _paste_code(self, code: RedeemCode) -> None:
        if normalize_code(code.code) is None:  # defence in depth: nothing else is ever pasted
            raise Escalation(ErrorCategory.CODE_INVALID, f"refusing to paste malformed code {code.masked}")
        self._ensure_focus()
        self.t.clipboard.copy(display_code(code.code))
        try:
            self.t.window.paste()
            self.sleep(self.cfg.settle_s)
        finally:
            self.t.clipboard.clear()
        frame = self._snap()
        if not paste_looks_right(self.t.ocr.read(frame.png), code.code) and not paste_looks_right(
            vt.read_redeem_field(self.t.vision, frame.png), code.code
        ):
            raise Escalation(ErrorCategory.UNKNOWN, f"pasted text for {code.masked} not visible in the input box; not submitting")

    def _open_via_guide(self, path: Iterable[str], expected: str) -> None:
        self._press(Button.GUIDE)
        self.sleep(self.cfg.settle_s)
        for label in path:
            self.nav.select(label_matcher(label), label)
            self.sleep(self.cfg.settle_s)
        screen = self._classify(self._snap())
        if screen != expected:
            raise Escalation(ErrorCategory.UNKNOWN, f"expected '{expected}' screen, found '{screen}'")

    def _go_home(self) -> None:
        for _ in range(6):
            if self._classify(self._snap()) == "home":
                return
            self._press(Button.B)
            self.sleep(self.cfg.settle_s)
        self._press(Button.GUIDE)
        self.sleep(self.cfg.settle_s)
        self.nav.select(label_matcher("home"), "Home")
        self.sleep(self.cfg.settle_s)
        if self._classify(self._snap()) != "home":
            raise Escalation(ErrorCategory.UNKNOWN, "cannot return to the home screen")

    # -- library / game

    def _open_library(self, ctx: Session) -> None:
        self._go_home()
        self.nav.select(label_matcher(*self.cfg.library_labels), "My games & apps")
        self.sleep(self.cfg.settle_s)
        frame = self._snap()
        screen = self._classify(frame)
        if screen != "library":
            raise Escalation(ErrorCategory.UNKNOWN, f"expected library, found '{screen}'")
        self._emit(ctx, State.OPEN_LIBRARY, "library_open", frame=frame)

    def _launch_cod(self, ctx: Session) -> None:
        title = regex_matcher(self.cfg.game_match, self.cfg.game_exclude)
        self.nav.select(title, "Call of Duty: Modern Warfare")
        self.sleep(self.cfg.settle_s * 2)
        frame = self._snap()
        labels = [i.label for i in vt.read_menu(self.t.vision, frame.png).items]
        play = label_matcher("play", "launch")
        if not any(play(label) for label in labels) and any(
            re.search(r"install|buy|purchase|get\b|download", label, re.I) for label in labels
        ):
            raise Escalation(ErrorCategory.GAME_STUCK, "game is not installed/owned; agent never buys or installs")
        self.nav.select(play, "Play button")
        self._emit(ctx, State.LAUNCH_COD_MW, "game_launching", frame=frame)

    def _wait_game_ready(self, ctx: Session) -> None:
        deadline = self.clock() + self.cfg.game_ready_timeout_s
        while True:
            frame = self._snap()
            state = vt.read_game_state(self.t.vision, frame.png)
            if state == "main_menu":
                ctx.game_ready = True
                self._emit(ctx, State.WAIT_GAME_READY, "game_menu_detected", frame=frame)
                return
            if state in ("error", "update_required"):
                raise Escalation(ErrorCategory.GAME_STUCK, f"game reports '{state}'")
            if self.clock() >= deadline:
                raise Escalation(ErrorCategory.GAME_STUCK, "game main menu not reached before the timeout")
            self.sleep(self.cfg.poll_interval_s)

    def _notify_game_ready(self, ctx: Session) -> None:
        frame = self._last_frame
        self._post_strict("game_ready", {"session_id": ctx.id, "message": "اللعبة جاهزة"}, frame.png if frame else None)
        self._emit(ctx, State.NOTIFY_GAME_READY, "game_ready", message="اللعبة جاهزة",
                   next_expected_event="close_game", frame=frame)

    def _wait_close(self, ctx: Session) -> None:
        cmd = self._wait_command(ctx, {"close_game"}, None, on_tick=lambda: self._snap())
        assert cmd is not None

    def _close_game(self, ctx: Session) -> None:
        self._press(Button.GUIDE)
        self.sleep(self.cfg.settle_s)
        self.nav.select(label_matcher("quit", "exit"), "Quit")
        for _ in range(6):
            self.sleep(self.cfg.settle_s * 2)
            frame = self._snap()
            screen = self._classify(frame)
            if screen == "confirm_dialog":
                self.nav.select(label_matcher("quit", "yes", "confirm"), "quit confirmation")
            elif screen not in ("game_main_menu", "game_loading", "unknown"):
                self._emit(ctx, State.CLOSE_GAME, "game_closed", frame=frame)
                return
        raise Escalation(ErrorCategory.GAME_STUCK, "game still running after Quit")

    def _logout(self, ctx: Session) -> None:
        self._go_home()
        self._press(Button.GUIDE)
        self.sleep(self.cfg.settle_s)
        self.nav.select(label_matcher("profile"), "Profile")
        self.sleep(self.cfg.settle_s)
        self.nav.select(label_matcher("sign out"), "Sign out")
        for _ in range(5):
            self.sleep(self.cfg.settle_s * 2)
            frame = self._snap()
            if self._classify(frame) in ("sign_in_prompt", "login_code"):
                ctx.logged_out = True
                self._emit(ctx, State.LOGOUT, "signed_out", frame=frame)
                return
        raise Escalation(ErrorCategory.UNKNOWN, "sign-out not confirmed on screen")

    # -- profile deletion (explicit dashboard confirmation only)

    def _request_delete(self, ctx: Session) -> None:
        if not ctx.gamertag:
            ctx.profile_deleted = False
            ctx.delete_note = "skipped: gamertag unknown, cannot verify which profile to remove"
            self._emit(ctx, State.REQUEST_DELETE_PROFILE, "delete_skipped", message=ctx.delete_note)
            return
        token = self.token_factory()
        self._post_strict("delete_profile_request",
                          {"session_id": ctx.id, "gamertag": ctx.gamertag, "confirm_token": token})
        self._emit(ctx, State.REQUEST_DELETE_PROFILE, "request_confirmation", need_confirmation=True,
                   params={"gamertag": ctx.gamertag, "confirm_token": token},
                   message=f"profile '{ctx.gamertag}' will be removed only after explicit confirmation",
                   next_expected_event="confirm_delete_profile|deny_delete_profile")
        deadline = self.clock() + self.cfg.delete_confirm_timeout_s
        while True:
            remaining = deadline - self.clock()
            cmd = self._wait_command(ctx, {"confirm_delete_profile", "deny_delete_profile"}, max(0.0, remaining))
            if cmd is None:
                ctx.profile_deleted, ctx.delete_note = False, "skipped: no confirmation before timeout"
                return
            if cmd.type == "deny_delete_profile":
                ctx.profile_deleted, ctx.delete_note = False, "skipped: denied by the dashboard"
                return
            if cmd.payload.get("token") == token:
                ctx.delete_note = "confirmed"
                return
            log.warning("ignored delete confirmation with a wrong token")

    def _delete_profile(self, ctx: Session) -> None:
        tag = ctx.gamertag
        assert tag and ctx.delete_note == "confirmed"
        exact = re.compile(rf"^\W*{re.escape(tag)}\W*$", re.I)
        self._open_via_guide(self.cfg.delete_path, expected="accounts")
        self.nav.select(lambda label: bool(exact.match(label.strip())), f"account '{tag}'")
        self.sleep(self.cfg.settle_s)
        frame = self._snap()
        if not vt.dialog_mentions(self.t.vision, frame.png, tag):
            raise Escalation(ErrorCategory.UNKNOWN, f"confirmation dialog does not clearly name '{tag}'; nothing removed")
        self.nav.select(label_matcher("remove"), "Remove")
        self.sleep(self.cfg.settle_s * 2)
        frame = self._snap()
        still = [i for i in vt.read_menu(self.t.vision, frame.png).items if exact.match(i.label.strip())]
        if still:
            raise Escalation(ErrorCategory.UNKNOWN, f"'{tag}' still listed after removal")
        ctx.profile_deleted = True
        self._emit(ctx, State.DELETE_PROFILE, "profile_removed", params={"gamertag": tag}, frame=frame)

    # -- final report

    def _report(self, ctx: Session, status: str) -> None:
        summary = {
            "session_id": ctx.id, "status": status, "gamertag": ctx.gamertag,
            "codes": [{"id": c.id, "masked": c.masked, "status": c.status} for c in ctx.codes],
            "game_ready": ctx.game_ready, "logged_out": ctx.logged_out,
            "profile_deleted": ctx.profile_deleted, "delete_note": ctx.delete_note,
            "errors": ctx.errors,
        }
        self._post("session_report", summary)
        self._emit(ctx, State.REPORT, "final_report", params=summary, message=f"session {status}")
