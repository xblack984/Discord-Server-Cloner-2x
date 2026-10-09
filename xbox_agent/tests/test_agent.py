from __future__ import annotations

import json
import unittest

from xbox_agent.config import Settings
from xbox_agent.models import Button, Command, Escalation, display_code
from xbox_agent.tests.fakes import Rig, RigTestCase

GOOD = "AAAAABBBBBCCCCCDDDDDEEEEE"
USED = "FFFFFGGGGGHHHHHJJJJJKKKKK"
BAD = "LLLLLMMMMMNNNNNPPPPPQQQQQ"
WEIRD = "RRRRRSSSSSTTTTTUUUUUVVVVV"
REPORT_KEYS = {"state", "action", "params", "screenshot_id", "window_focused", "message",
               "need_confirmation", "next_expected_event"}


def codes():
    return [{"id": "c1", "code": display_code(GOOD)}, {"id": "c2", "code": USED},
            {"id": "c3", "code": BAD}, {"id": "c4", "code": WEIRD},
            {"id": "c5", "code": "SHORT-CODE"}]


def answer_questions(rig: Rig, choice: str, session_id: str = "s1") -> None:
    """The admin picks `choice` whenever the agent asks a question on the dashboard."""
    rig.dash.on_event["question"] = lambda p: p["message"] and rig.dash.push("answer", session_id=session_id, choice=choice)


def abort_on(rig: Rig, *kinds: str, session_id: str = "s1") -> None:
    """End the run once one of these events is posted (so the abort queues up after any scripted answer)."""
    for kind in kinds:
        previous = rig.dash.on_event.get(kind)
        rig.dash.on_event[kind] = lambda p, previous=previous: (
            previous(p) if previous else None, rig.dash.push("abort_session", session_id=session_id))[0]


def script_full_session(rig: Rig) -> None:
    rig.console.outcomes = {GOOD: "redeemed", USED: "code_used", BAD: "code_invalid", WEIRD: "redeemed"}
    rig.console.weird = {WEIRD}
    rig.dash.push("start_session", session_id="s1", codes=codes())
    rig.dash.push("admin_login_confirmed", session_id="s1")
    rig.dash.push("close_game", session_id="s1")
    rig.dash.on_event["delete_profile_request"] = lambda p: rig.dash.push(
        "confirm_delete_profile", session_id="s1", token=p["confirm_token"])


class HappyPath(RigTestCase):
    def setUp(self):
        self.rig = self.make_rig()
        script_full_session(self.rig)
        self.rig.agent.run_once()

    def test_every_line_is_json_with_the_exact_contract(self):
        for report in self.rig.reports():
            self.assertEqual(set(report), REPORT_KEYS)

    def test_visits_the_states_in_order(self):
        order = []
        for r in self.rig.reports():
            if r["action"] == "enter_state" and (not order or order[-1] != r["state"]):
                order.append(r["state"])
        self.assertEqual(order, [
            "START_SESSION", "REACH_LOGIN", "EXTRACT_CODE", "SEND_CODE", "WAIT_ADMIN_LOGIN",
            "DETECT_LOGGED_IN", "REDEEM_CODES", "OPEN_LIBRARY", "LAUNCH_COD_MW", "WAIT_GAME_READY",
            "NOTIFY_GAME_READY", "WAIT_CLOSE_CMD", "CLOSE_GAME", "LOGOUT", "REQUEST_DELETE_PROFILE",
            "DELETE_PROFILE", "REPORT"])
        self.assertEqual(self.rig.reports()[0]["state"], "IDLE")

    def test_login_code_sent_once(self):
        sent = self.rig.dash.of("login_code")
        self.assertEqual([p["code"] for p in sent], ["QX7M2K9P"])

    def test_redeem_outcomes_and_masking(self):
        rep = self.rig.last_report()
        self.assertEqual({c["id"]: c["status"] for c in rep["codes"]}, {
            "c1": "redeemed", "c2": "code_used", "c3": "code_invalid", "c4": "redeemed", "c5": "code_invalid"})
        self.assertNotIn(GOOD, json.dumps(rep))
        self.assertNotIn(GOOD, "".join(self.rig.lines))

    def test_only_valid_codes_ever_hit_the_clipboard_and_it_is_cleared(self):
        copied = [c.replace("-", "") for c in self.rig.clipboard.history]
        self.assertEqual(copied, [GOOD, USED, BAD, WEIRD])
        self.assertEqual(self.rig.clipboard.text, "")
        self.assertEqual(self.rig.window.pastes, 4)

    def test_game_ready_notified_and_session_wound_down(self):
        self.assertEqual(len(self.rig.dash.of("game_ready")), 1)
        rep = self.rig.last_report()
        self.assertEqual((rep["status"], rep["game_ready"], rep["logged_out"]), ("completed", True, True))
        self.assertFalse(self.rig.console.game_running)

    def test_profile_removed_only_after_token_confirmation(self):
        self.assertEqual(self.rig.console.accounts, ["OtherUser"])
        self.assertTrue(self.rig.last_report()["profile_deleted"])
        request = self.rig.dash.of("delete_profile_request")[0]
        self.assertEqual((request["gamertag"], request["confirm_token"]), ("AgentTest99", "tok123"))

    def test_no_mouse_or_text_buttons_only_pad_buttons(self):
        self.assertTrue(set(self.rig.gamepad.taps) <= set(Button))

    def test_returns_to_idle_state(self):
        self.assertEqual(self.rig.agent.state.value, "REPORT")  # next run_once re-enters IDLE


class DeleteSafety(RigTestCase):
    def test_wrong_token_is_ignored_then_denied(self):
        rig = self.make_rig()
        rig.console.outcomes = {GOOD: "redeemed"}
        rig.dash.push("start_session", session_id="s1", codes=[{"id": "c1", "code": GOOD}])
        rig.dash.push("admin_login_confirmed", session_id="s1")
        rig.dash.push("close_game", session_id="s1")
        rig.dash.on_event["delete_profile_request"] = lambda p: (
            rig.dash.push("confirm_delete_profile", session_id="s1", token="forged"),
            rig.dash.push("deny_delete_profile", session_id="s1"))
        rig.agent.run_once()
        self.assertEqual(rig.console.accounts, ["AgentTest99", "OtherUser"])
        self.assertFalse(rig.last_report()["profile_deleted"])
        self.assertNotIn("DELETE_PROFILE", [r["state"] for r in rig.reports()])

    def test_no_confirmation_before_timeout_means_no_deletion(self):
        rig = self.make_rig(delete_confirm_timeout_s=30)
        rig.dash.push("start_session", session_id="s1", codes=[])
        rig.dash.push("admin_login_confirmed", session_id="s1")
        rig.dash.push("close_game", session_id="s1")
        rig.agent.run_once()
        self.assertEqual(rig.console.accounts, ["AgentTest99", "OtherUser"])
        self.assertIn("no confirmation", rig.last_report()["delete_note"])


class StopAfter(RigTestCase):
    def run_until(self, stop_after, codes=None):
        rig = self.make_rig()
        rig.console.outcomes = {GOOD: "redeemed"}
        rig.dash.push("start_session", session_id="s1", stop_after=stop_after, codes=codes or [])
        rig.dash.push("admin_login_confirmed", session_id="s1")
        rig.agent.run_once()
        return rig

    def visited(self, rig):
        return {r["state"] for r in rig.reports() if r["action"] == "enter_state"}

    def test_stops_cleanly_after_login_and_leaves_the_console_alone(self):
        rig = self.run_until("DETECT_LOGGED_IN", [{"id": "c1", "code": GOOD}])
        rep = rig.last_report()
        self.assertEqual(rep["status"], "stopped_early")
        self.assertIn("DETECT_LOGGED_IN", self.visited(rig))
        self.assertNotIn("REDEEM_CODES", self.visited(rig))
        self.assertEqual(rig.clipboard.history, [])
        self.assertTrue(rig.console.signed_in)  # no logout was attempted
        self.assertEqual(rep["codes"][0]["status"], "pending")
        self.assertEqual(rep["errors"][0]["category"], "stopped_early")

    def test_stops_after_redeem_without_touching_the_library(self):
        rig = self.run_until("REDEEM_CODES", [{"id": "c1", "code": GOOD}])
        self.assertEqual(rig.last_report()["codes"][0]["status"], "redeemed")
        self.assertNotIn("OPEN_LIBRARY", self.visited(rig))

    def test_stops_right_after_the_code_is_sent(self):
        rig = self.make_rig()
        rig.dash.push("start_session", session_id="s1", stop_after="SEND_CODE", codes=[])
        rig.agent.run_once()
        self.assertEqual(len(rig.dash.of("login_code")), 1)
        self.assertNotIn("WAIT_ADMIN_LOGIN", self.visited(rig))
        self.assertEqual(rig.last_report()["status"], "stopped_early")

    def test_unknown_or_reserved_values_are_ignored(self):
        for bad in ("NOPE", "IDLE", "REPORT", ""):
            rig = self.make_rig()
            script_full_session(rig)
            rig.dash.queue[0].payload["stop_after"] = bad
            rig.agent.run_once()
            self.assertEqual(rig.last_report()["status"], "completed", bad)


class GameOnScreen(RigTestCase):
    """The console was left on a game (e.g. Call of Duty at its title screen, no profile)."""

    def run_until_login_wait(self, scene, answer="keep"):
        rig = self.make_rig(scene=scene)
        answer_questions(rig, answer)
        abort_on(rig, "login_code", "error")
        rig.dash.push("start_session", session_id="s1", codes=[])
        rig.agent.run_once()
        return rig

    def test_it_opens_the_guide_over_the_game_and_signs_in_by_code_without_quitting_it(self):
        rig = self.run_until_login_wait("game_signed_out")
        self.assertEqual(rig.gamepad.taps, [Button.GUIDE, Button.A, Button.DOWN, Button.A])
        self.assertEqual([p["code"] for p in rig.dash.of("login_code")], ["QX7M2K9P"])
        self.assertIn("open_guide", [r["action"] for r in rig.reports()])
        self.assertEqual(rig.dash.of("error"), [])

    def test_a_game_whose_guide_offers_no_sign_in_escalates_without_pressing_anything_risky(self):
        rig = self.run_until_login_wait("game_main_menu")  # the fake guide has no sign-in entry here
        err = rig.dash.of("error")[0]
        self.assertEqual((err["category"], err["state"]), ("unknown", "REACH_LOGIN"))
        self.assertIn("not found", err["message"])
        self.assertNotIn(Button.A, rig.gamepad.taps)  # it only looked (Guide + scrolling), never confirmed anything
        self.assertEqual(rig.window.reloads, 0)  # and did not mistake the end of the menu for a lost stream


class AskAboutTheGame(RigTestCase):
    """A game is on the console screen: the agent asks start / close / keep instead of guessing."""

    def run_with_answer(self, scene, choice, **cfg):
        rig = self.make_rig(scene=scene, **cfg)
        answer_questions(rig, choice)
        abort_on(rig, "login_code", "error")
        rig.dash.push("start_session", session_id="s1", codes=[])
        rig.agent.run_once()
        return rig

    def test_it_asks_with_three_options_and_a_screenshot_before_pressing_anything(self):
        rig = self.make_rig(scene="game_signed_out")
        abort_on(rig, "question")  # nobody answers: the run ends at the question
        rig.dash.push("start_session", session_id="s1", codes=[])
        rig.agent.run_once()
        q = rig.dash.of("question")[0]
        self.assertEqual([o["id"] for o in q["options"]], ["start_game", "close_game", "keep"])
        self.assertIn("game", q["message"].lower())
        self.assertIsNotNone([img for k, p, img in rig.dash.events if k == "question"][0])
        self.assertEqual(rig.gamepad.taps, [])

    def test_start_the_game_presses_A_then_carries_on_with_the_guide(self):
        rig = self.run_with_answer("game_signed_out", "start_game")
        self.assertEqual(rig.gamepad.taps, [Button.A, Button.GUIDE, Button.A, Button.DOWN, Button.A])
        self.assertEqual([p["code"] for p in rig.dash.of("login_code")], ["QX7M2K9P"])

    def test_keep_goes_straight_to_the_guide(self):
        rig = self.run_with_answer("game_signed_out", "keep")
        self.assertEqual(rig.gamepad.taps, [Button.GUIDE, Button.A, Button.DOWN, Button.A])

    def test_close_the_game_quits_it_through_the_guide_and_continues_to_the_sign_in(self):
        rig = self.run_with_answer("game_quit_ok", "close_game")
        self.assertEqual(rig.gamepad.taps[:3], [Button.GUIDE, Button.DOWN, Button.A])  # Guide, move to Quit, confirm
        self.assertIn("game_closed", [r["action"] for r in rig.reports()])
        self.assertEqual([p["code"] for p in rig.dash.of("login_code")], ["QX7M2K9P"])
        self.assertEqual(rig.dash.of("error"), [])

    def test_close_when_the_guide_shows_no_quit_hands_over_to_the_joystick_instead_of_guessing(self):
        rig = self.run_with_answer("game_signed_out", "close_game")  # its Guide has only "Sign in"
        err = rig.dash.of("error")[0]
        self.assertIn("Quit", err["message"])
        self.assertNotIn(Button.A, rig.gamepad.taps)  # it looked, it did not confirm anything

    def test_an_answer_that_is_not_an_option_is_ignored_and_the_question_stays_open(self):
        rig = self.make_rig(scene="game_signed_out")
        rig.dash.on_event["question"] = lambda p: p["message"] and (
            rig.dash.push("answer", session_id="s1", choice="format_the_console"),
            rig.dash.push("answer", session_id="s1", choice="keep"))
        abort_on(rig, "login_code")
        rig.dash.push("start_session", session_id="s1", codes=[])
        rig.agent.run_once()
        self.assertEqual(rig.gamepad.taps, [Button.GUIDE, Button.A, Button.DOWN, Button.A])  # acted on "keep" only
        self.assertEqual([q["message"] for q in rig.dash.of("question")][-1], "")  # the card was cleared

    def test_the_guide_is_pressed_once_only_because_it_toggles(self):
        rig = self.run_with_answer("game_main_menu", "keep")  # its Guide is not the signed-out one: no sign-in entry
        self.assertEqual(rig.gamepad.taps.count(Button.GUIDE), 1)


class Joystick(RigTestCase):
    """Presses sent from the web joystick are done whenever the agent is waiting, even with no session."""

    def idle_run(self, rig, *commands):
        for kind, payload in commands:
            rig.dash.push(kind, **payload)
        abort_on(rig, "login_code", "error")
        rig.dash.push("start_session", session_id="s1", codes=[])
        rig.agent.run_once()

    def test_presses_work_in_idle_and_open_the_page_first_if_needed(self):
        rig = self.make_rig(scene="login_code")
        self.idle_run(rig, ("press", {"button": "A"}), ("press", {"button": "GUIDE"}), ("press", {"button": "START"}))
        self.assertEqual(rig.gamepad.taps[:3], [Button.A, Button.GUIDE, Button.START])
        self.assertEqual(rig.window.opened, ["https://www.xbox.com/de-DE/play/consoles"])
        self.assertTrue(set(rig.dash.acked) >= {"1", "2", "3"})  # every command was acknowledged

    def test_a_stick_push_and_a_screenshot_request(self):
        rig = self.make_rig(scene="login_code")
        self.idle_run(rig, ("stick", {"direction": "up"}), ("stick", {"direction": "left"}), ("snapshot", {}))
        self.assertEqual(rig.gamepad.sticks, [(0.0, 1.0, 0.3), (-1.0, 0.0, 0.3)])
        self.assertEqual(rig.gamepad.taps, [])
        frames = [(p, img) for k, p, img in rig.dash.events if k == "frame"]
        self.assertGreaterEqual(len(frames), 3)
        self.assertTrue(all(img for _, img in frames))

    def test_malformed_or_unknown_commands_are_ignored_without_crashing(self):
        rig = self.make_rig(scene="login_code")
        self.idle_run(rig, ("press", {"button": "FORMAT"}), ("press", {}), ("stick", {"direction": "sideways"}),
                      ("stick", {"direction": ["up"]}))
        self.assertEqual((rig.gamepad.taps, rig.gamepad.sticks), ([], []))
        self.assertEqual(rig.last_report()["status"], "aborted")  # and the session still ran normally

    def test_commands_of_another_session_are_dropped(self):
        rig = self.make_rig(scene="login_code")
        abort_on(rig, "login_code")
        rig.dash.push("start_session", session_id="s1", codes=[])
        rig.dash.push("press", session_id="OLD", button="A")
        rig.dash.push("press", session_id="s1", button="B")
        rig.agent.run_once()
        self.assertEqual(rig.gamepad.taps, [Button.B])

    def test_while_someone_is_driving_it_streams_frames_when_the_screen_changes(self):
        rig = self.make_rig(scene="sign_in_prompt")
        rig.dash.push("press", button="DOWN")
        rig.dash.push("press", button="A")  # moves to "another device" and opens the code screen
        abort_on(rig, "login_code", "error")
        rig.dash.push("start_session", session_id="s1", codes=[])
        rig.agent.run_once()
        digests = {img for k, p, img in rig.dash.events if k == "frame"}
        self.assertGreaterEqual(len(digests), 2)  # the picture changed between the two presses

    def test_a_failing_press_is_reported_as_a_step_not_raised(self):
        rig = self.make_rig()
        rig.window.focusable = False
        self.idle_run(rig, ("press", {"button": "A"}))
        self.assertIn("manual_failed", [r["action"] for r in rig.reports()])
        self.assertEqual(rig.gamepad.taps, [])


class DecidedInAdvance(RigTestCase):
    """XBOX_AGENT_ON_GAME_RUNNING / ON_SIGNED_IN: decisions made once in .env instead of stopping every time."""

    def run_until_login_wait(self, scene, **cfg):
        rig = self.make_rig(scene=scene, **cfg)
        abort_on(rig, "login_code", "error", "question")
        rig.dash.push("start_session", session_id="s1", codes=[])
        rig.agent.run_once()
        return rig

    def test_keep_never_asks_and_says_what_it_decided(self):
        rig = self.run_until_login_wait("game_signed_out", on_game_running="keep")
        self.assertEqual(rig.dash.of("question"), [])
        self.assertEqual(rig.gamepad.taps, [Button.GUIDE, Button.A, Button.DOWN, Button.A])
        auto = [r for r in rig.reports() if r["action"] == "auto_choice"][0]
        self.assertEqual(auto["params"], {"choice": "keep"})
        self.assertIn("ON_GAME_RUNNING=keep", auto["message"])

    def test_start_presses_A_and_close_quits_without_asking(self):
        rig = self.run_until_login_wait("game_signed_out", on_game_running="start")
        self.assertEqual(rig.gamepad.taps[0], Button.A)
        self.assertEqual(rig.dash.of("question"), [])
        rig = self.run_until_login_wait("game_quit_ok", on_game_running="close")
        self.assertEqual(rig.gamepad.taps[:3], [Button.GUIDE, Button.DOWN, Button.A])
        self.assertEqual(rig.dash.of("question"), [])

    def test_use_continues_with_an_already_signed_in_profile_without_stopping(self):
        rig = self.make_rig(scene="game_signed_in", on_game_running="keep", on_signed_in="use")
        rig.console.signed_in = True
        rig.dash.push("start_session", session_id="s1", stop_after="DETECT_LOGGED_IN", codes=[])
        rig.agent.run_once()  # no abort, no answer, no skip: nobody is needed
        self.assertEqual(rig.dash.of("error"), [])
        self.assertEqual(rig.last_report()["status"], "stopped_early")
        self.assertEqual(rig.last_report()["gamertag"], "AgentTest99")
        self.assertIn("ON_SIGNED_IN=use", [r["message"] for r in rig.reports() if r["action"] == "using_signed_in_profile"][0])

    def test_the_defaults_still_ask(self):
        cfg = Settings()
        self.assertEqual((cfg.on_game_running, cfg.on_signed_in), ("ask", "ask"))

    def test_deletion_cannot_be_decided_in_advance(self):
        # there is deliberately no setting that answers the profile-removal confirmation
        self.assertFalse([f for f in Settings.__dataclass_fields__ if "delete" in f and f not in ("allow_delete", "delete_path", "delete_confirm_timeout_s")])


class SecondLooks(RigTestCase):
    def test_an_unrecognised_screen_gets_extra_looks_before_giving_up(self):
        rig = self.make_rig(scene="connecting")  # shows "unknown" for the first two looks, then the sign-in screen
        frame = rig.capture.grab()
        self.assertEqual(rig.agent._classify(frame, retries=2), "sign_in_prompt")
        rig = self.make_rig(scene="connecting")
        self.assertEqual(rig.agent._classify(rig.capture.grab()), "unknown")  # no retries asked: unchanged behaviour
        rig = self.make_rig(scene="connecting")
        self.assertEqual(rig.agent._classify(rig.capture.grab(), retries=1), "unknown")  # one extra look is not enough

    def test_credential_screens_are_still_refused_after_the_extra_looks(self):
        rig = self.make_rig(scene="password_prompt")
        with self.assertRaises(Escalation):
            rig.agent._classify(rig.capture.grab(), retries=2)

    def test_going_home_from_inside_a_game_uses_the_guide_and_never_presses_B(self):
        rig = self.make_rig(scene="game_signed_in")
        rig.agent._go_home()
        self.assertEqual(rig.gamepad.taps, [Button.GUIDE, Button.A])  # Guide, then Home; B would have pressed inside the game
        self.assertEqual(rig.console.scene, "home")


class AlreadySignedIn(RigTestCase):
    """The console already has a profile signed in (its Guide shows it): the agent asks, then adopts it."""

    def adopt_rig(self, scene="game_signed_in", **cfg):
        rig = self.make_rig(scene=scene, **cfg)
        answer_questions(rig, "keep")
        rig.console.signed_in = True
        rig.dash.on_event["error"] = lambda p: rig.dash.push("skip", session_id="s1")  # the admin clicks "use this profile"
        return rig

    def test_the_admin_is_asked_and_the_error_card_offers_use_this_profile(self):
        rig = self.make_rig(scene="game_signed_in")
        answer_questions(rig, "keep")
        abort_on(rig, "error")
        rig.console.signed_in = True
        rig.dash.push("start_session", session_id="s1", codes=[])
        rig.agent.run_once()
        err = rig.dash.of("error")[0]
        self.assertEqual((err["category"], err["state"], err["can_skip"], err["skip_label"]),
                         ("auth", "REACH_LOGIN", True, "use_profile"))
        self.assertIn("AgentTest99", err["message"])  # the gamertag read from the Guide
        self.assertEqual(rig.dash.of("login_code"), [])  # no sign-in code is invented for a signed-in console

    def test_using_the_profile_skips_the_sign_in_states_and_goes_on(self):
        rig = self.adopt_rig()
        rig.dash.push("start_session", session_id="s1", stop_after="DETECT_LOGGED_IN", codes=[])
        rig.agent.run_once()
        rep = rig.last_report()
        self.assertEqual((rep["status"], rep["gamertag"]), ("stopped_early", "AgentTest99"))
        skipped = [r["state"] for r in rig.reports() if r["action"] == "skipped"]
        self.assertEqual(skipped, ["EXTRACT_CODE", "SEND_CODE", "WAIT_ADMIN_LOGIN"])
        self.assertEqual(rig.dash.of("login_code"), [])
        self.assertIn("using_signed_in_profile", [r["action"] for r in rig.reports()])

    def test_a_full_session_with_an_adopted_profile_never_signs_it_out_or_removes_it(self):
        rig = self.adopt_rig()
        rig.console.outcomes = {GOOD: "redeemed"}
        rig.dash.push("start_session", session_id="s1", codes=[{"id": "c1", "code": GOOD}])
        rig.dash.push("close_game", session_id="s1")
        # even a (forged or stale) delete confirmation could not reach a profile this session did not sign in
        rig.dash.on_event["delete_profile_request"] = lambda p: rig.dash.push(
            "confirm_delete_profile", session_id="s1", token=p["confirm_token"])
        rig.agent.run_once()
        rep = rig.last_report()
        self.assertEqual(rep["status"], "completed")
        self.assertEqual([c["status"] for c in rep["codes"]], ["redeemed"])  # it still did the actual work
        self.assertTrue(rep["game_ready"])
        self.assertFalse(rep["logged_out"])
        self.assertFalse(rep["profile_deleted"])
        self.assertIn("never removes", rep["delete_note"])
        self.assertTrue(rig.console.signed_in)
        self.assertEqual(rig.console.accounts, ["AgentTest99", "OtherUser"])
        self.assertEqual(rig.dash.of("delete_profile_request"), [])
        self.assertFalse(rig.console.game_running)  # the game it launched was closed again

    def test_the_home_screen_of_a_signed_in_console_asks_the_same_question(self):
        rig = self.make_rig(scene="home")
        rig.dash.push("start_session", session_id="s1", codes=[])
        rig.dash.push("abort_session", session_id="s1")
        rig.agent.run_once()
        err = rig.dash.of("error")[0]
        self.assertEqual((err["category"], err["can_skip"], err["skip_label"]), ("auth", True, "use_profile"))
        self.assertEqual(rig.gamepad.taps, [])


class DashboardDown(RigTestCase):
    """The dashboard is not running (WinError 10061): say so once, plainly, and recover by itself."""

    def flaky(self, rig_time, down_polls):
        from xbox_agent.tests.fakes import FakeDashboard
        from xbox_agent.tools.protocols import DashboardError

        class Flaky(FakeDashboard):
            polls = 0
            post_attempts = 0

            def fetch_commands(self, wait_s):
                Flaky.polls += 1
                if Flaky.polls <= down_polls:
                    raise DashboardError("GET /api/agent/commands failed: [WinError 10061] connection refused")
                return super().fetch_commands(wait_s)

            def post_event(self, kind, payload, image=None):
                Flaky.post_attempts += 1
                if Flaky.polls <= down_polls:
                    raise DashboardError("POST /api/agent/events failed: [WinError 10061] connection refused")
                return super().post_event(kind, payload, image)

        return Flaky(rig_time)

    def test_one_clear_message_then_recovery_and_the_session_still_runs(self):
        from xbox_agent.tests.fakes import FakeTime

        time_ = FakeTime()
        dash = self.flaky(time_, down_polls=3)
        rig = self.make_rig(dashboard=dash)
        rig.dash.time = rig.time  # one clock for the agent and the fake dashboard
        rig.dash.push("start_session", session_id="s1", codes=[])
        rig.dash.push("abort_session", session_id="s1")
        with self.assertLogs("xbox_agent", level="INFO") as logs:
            rig.agent.run_once()
        warnings = [r.getMessage() for r in logs.records if r.levelname == "WARNING"]
        self.assertEqual(len(warnings), 1, warnings)  # three failed polls, one message
        self.assertIn("run_dashboard.bat", warnings[0])
        self.assertIn(rig.cfg.dashboard_url, warnings[0])
        self.assertTrue(any("reachable again" in r.getMessage() for r in logs.records))
        self.assertEqual(rig.last_report()["status"], "aborted")  # it did run the session after the dashboard came up

    def test_repeats_are_limited_to_one_per_minute(self):
        rig = self.make_rig(dashboard=self.flaky(None, down_polls=130))
        rig.dash.time = rig.time
        rig.dash.push("start_session", session_id="s1", codes=[])
        rig.dash.push("abort_session", session_id="s1")
        with self.assertLogs("xbox_agent", level="INFO") as logs:
            rig.agent.run_once()
        warnings = [r.getMessage() for r in logs.records if r.levelname == "WARNING"]
        self.assertEqual(len(warnings), 3, warnings)  # first + after 60 s + after 120 s
        self.assertTrue(all("Still waiting" in w for w in warnings[1:]))

    def test_known_down_dashboard_is_not_hammered_with_events(self):
        rig = self.make_rig(dashboard=self.flaky(None, down_polls=10**6))
        rig.dash.time = rig.time
        with self.assertLogs("xbox_agent", level="WARNING"):
            rig.agent._post("step", {})
            rig.agent._post("step", {})
            rig.agent._post("step", {})
        self.assertEqual(rig.dash.post_attempts, 1)  # the others were skipped, not retried for seconds each

    def test_critical_notifications_are_still_attempted_and_escalate(self):
        rig = self.make_rig(dashboard=self.flaky(None, down_polls=10**6))
        rig.dash.time = rig.time
        with self.assertLogs("xbox_agent", level="WARNING"), self.assertRaises(Escalation):
            rig.agent._post_strict("login_code", {"code": "X"})


class OpenPlayPage(RigTestCase):
    URL = "https://www.xbox.com/de-DE/play/consoles"

    def run_until_login(self, scene="sign_in_prompt", **cfg):
        rig = self.make_rig(scene=scene, **cfg)
        rig.dash.push("start_session", session_id="s1", codes=[])
        rig.dash.push("abort_session", session_id="s1")  # ends the run at the first wait
        rig.agent.run_once()
        return rig

    def actions(self, rig):
        return [r["action"] for r in rig.reports()]

    def test_the_agent_opens_the_consoles_page_itself_once(self):
        rig = self.run_until_login()
        self.assertEqual(rig.window.opened, [self.URL])
        opened = [r for r in rig.reports() if r["action"] == "opened_url"]
        self.assertEqual(opened[0]["params"], {"url": self.URL})
        rig.dash.push("start_session", session_id="s2", codes=[])
        rig.dash.push("abort_session", session_id="s2")
        rig.agent.run_once()
        self.assertEqual(rig.window.opened, [self.URL])  # the second session reuses the window

    def test_the_url_comes_from_the_settings_and_opening_can_be_turned_off(self):
        rig = self.run_until_login(play_url="https://example.test/play")
        self.assertEqual(rig.window.opened, ["https://example.test/play"])
        rig = self.run_until_login(open_play_url=False)
        self.assertEqual(rig.window.opened, [])
        self.assertNotIn("opened_url", self.actions(rig))

    def test_it_presses_connect_on_the_consoles_page_and_waits_for_the_console(self):
        rig = self.run_until_login(scene="play_consoles")
        self.assertEqual(rig.gamepad.taps[:2], [Button.DOWN, Button.A])  # focus moves to "Verbinden", then A
        self.assertIn("connecting", self.actions(rig))
        ok = [r for r in rig.reports() if r["action"] == "stream_ok"][0]
        self.assertEqual(ok["params"]["screen"], "sign_in_prompt")
        self.assertEqual(rig.dash.of("error"), [])

    def test_console_labels_are_configurable(self):
        rig = self.run_until_login(scene="play_consoles", console_labels=("meine konsole",))
        self.assertEqual(rig.gamepad.taps[:1], [Button.A])  # already focused: no DOWN needed

    def test_a_microsoft_sign_in_page_makes_the_agent_wait_and_never_type(self):
        rig = self.make_rig(scene="web_sign_in", web_login_timeout_s=30)
        rig.dash.push("start_session", session_id="s1", codes=[{"id": "c1", "code": GOOD}])
        rig.dash.push("abort_session", session_id="s1")
        rig.agent.run_once()
        err = rig.dash.of("error")[0]  # nobody signed in: escalates only after the timeout
        self.assertEqual((err["category"], err["state"]), ("auth", "START_SESSION"))
        self.assertGreaterEqual(rig.time.now, 30)
        self.assertEqual(rig.gamepad.taps, [])
        self.assertEqual((rig.window.pastes, rig.clipboard.history), (0, []))
        notes = [n["message"] for n in rig.dash.of("notice")]
        self.assertTrue(notes[0] and notes[-1] == "")  # shown while waiting, cleared afterwards

    def test_when_you_sign_in_the_agent_notices_and_continues_by_itself(self):
        rig = self.make_rig(scene="web_sign_in")
        rig.console.web_login_after = 3
        focus_seen = []
        rig.capture.on_grab = lambda scene: focus_seen.append(rig.window.focus_calls) if scene == "web_sign_in" else None
        rig.dash.push("start_session", session_id="s1", codes=[])
        rig.dash.push("abort_session", session_id="s1")  # ends the run at the first later wait
        rig.agent.run_once()
        self.assertEqual(rig.dash.of("error"), [])  # no retry button needed
        acts = self.actions(rig)
        self.assertLess(acts.index("wait_web_login"), acts.index("web_login_done"))
        self.assertLess(acts.index("web_login_done"), acts.index("stream_ok"))
        notes = [n["message"] for n in rig.dash.of("notice")]
        self.assertEqual((bool(notes[0]), notes[-1]), (True, ""))
        # while you type, the agent must not touch the window: one focus before waiting, none during
        self.assertGreater(len(focus_seen), 2)
        self.assertEqual(len(set(focus_seen)), 1, focus_seen)
        # the only pad input is the normal next step on the console (choose "sign in on another device")
        self.assertEqual(rig.gamepad.taps, [Button.DOWN, Button.A])

    def test_other_credential_screens_still_stop_immediately(self):
        rig = self.make_rig(scene="password_prompt")
        rig.dash.push("start_session", session_id="s1", codes=[])
        rig.dash.push("abort_session", session_id="s1")
        rig.agent.run_once()
        self.assertEqual(rig.dash.of("error")[0]["category"], "auth")
        self.assertEqual(rig.dash.of("notice"), [])
        self.assertLess(rig.time.now, 10)

    def test_pressing_connect_but_never_getting_a_console_escalates_as_network(self):
        rig = self.make_rig(scene="play_consoles", connect_timeout_s=10)
        rig.console.connect_stuck = True
        rig.dash.push("start_session", session_id="s1", codes=[])
        rig.dash.push("abort_session", session_id="s1")
        rig.agent.run_once()
        err = rig.dash.of("error")[0]
        self.assertEqual((err["category"], err["state"]), ("network", "START_SESSION"))
        self.assertEqual(rig.gamepad.taps.count(Button.A), 1)  # it pressed connect once, not repeatedly

    def test_a_page_that_keeps_showing_an_error_escalates_quickly(self):
        rig = self.run_until_login(scene="error_page")
        err = rig.dash.of("error")[0]
        self.assertEqual(err["category"], "unknown")
        self.assertIn("error", err["message"])
        self.assertEqual(rig.gamepad.taps, [])
        self.assertLess(rig.time.now, 30)  # did not sit out the whole connect timeout


class DeleteDisabled(RigTestCase):
    def test_deletion_is_off_by_default_even_if_the_dashboard_would_confirm(self):
        rig = self.make_rig(allow_delete=False)
        script_full_session(rig)
        rig.agent.run_once()
        self.assertEqual(rig.dash.of("delete_profile_request"), [])
        self.assertEqual(rig.console.accounts, ["AgentTest99", "OtherUser"])
        self.assertIn("disabled", rig.last_report()["delete_note"])
        self.assertEqual(rig.last_report()["status"], "completed")


class AuthRule(RigTestCase):
    def test_password_screen_stops_everything_and_waits_for_admin(self):
        rig = self.make_rig(scene="password_prompt")
        rig.dash.push("start_session", session_id="s1", codes=[{"id": "c1", "code": GOOD}])
        rig.dash.push("abort_session", session_id="s1")
        rig.agent.run_once()
        self.assertEqual(rig.gamepad.taps, [])
        self.assertEqual(rig.clipboard.history, [])
        err = rig.dash.of("error")[0]
        self.assertEqual(err["category"], "auth")
        self.assertIsNotNone([img for k, _, img in rig.dash.events if k == "error"][0])
        self.assertEqual(rig.last_report()["status"], "aborted")

    def test_already_signed_in_profile_is_not_touched(self):
        rig = self.make_rig(scene="home")
        rig.dash.push("start_session", session_id="s1", codes=[])
        rig.dash.push("abort_session", session_id="s1")
        rig.agent.run_once()
        self.assertEqual(rig.dash.of("error")[0]["category"], "auth")
        self.assertEqual(rig.gamepad.taps, [])

    def test_admin_never_confirms_escalates_auth(self):
        rig = self.make_rig(admin_login_timeout_s=20)
        rig.dash.push("start_session", session_id="s1", codes=[])
        rig.dash.on_event["error"] = lambda p: rig.dash.push("abort_session", session_id="s1")
        rig.agent.run_once()
        err = rig.dash.of("error")[0]
        self.assertEqual((err["category"], err["state"]), ("auth", "WAIT_ADMIN_LOGIN"))
        self.assertGreaterEqual(rig.time.now, 20)  # it really waited out the timeout


class Escalations(RigTestCase):
    def test_black_frames_trigger_reload_then_continue(self):
        rig = self.make_rig()
        rig.console.black = 1
        rig.dash.push("start_session", session_id="s1", codes=[])
        rig.dash.push("abort_session", session_id="s1")  # ends the run once it reaches WAIT_ADMIN_LOGIN
        rig.agent.run_once()
        self.assertEqual(rig.window.reloads, 1)
        self.assertEqual(rig.dash.of("error"), [])  # recovered by itself, no admin needed
        self.assertIn("WAIT_ADMIN_LOGIN", [r["state"] for r in rig.reports()])

    def test_unfocusable_window_blocks_all_input(self):
        rig = self.make_rig()
        rig.window.focusable = False
        rig.dash.push("start_session", session_id="s1", codes=[])
        rig.dash.push("abort_session", session_id="s1")
        rig.agent.run_once()
        self.assertEqual(rig.gamepad.taps, [])
        self.assertFalse(all(r["window_focused"] for r in rig.reports()))
        self.assertEqual(rig.dash.of("error")[0]["category"], "unknown")

    def test_network_error_while_redeeming_can_be_skipped(self):
        rig = self.make_rig()
        rig.console.outcomes = {GOOD: "network", USED: "redeemed"}
        rig.dash.push("start_session", session_id="s1", codes=[{"id": "c1", "code": GOOD}, {"id": "c2", "code": USED}])
        rig.dash.push("admin_login_confirmed", session_id="s1")
        rig.dash.push("skip", session_id="s1")
        rig.dash.push("abort_session", session_id="s1")  # stop at WAIT_CLOSE_CMD? no: aborts the first later wait
        rig.agent.run_once()
        err = rig.dash.of("error")[0]
        self.assertEqual((err["category"], err["can_skip"]), ("network", True))
        statuses = {c["id"]: c["status"] for c in rig.last_report()["codes"]}
        self.assertEqual(statuses, {"c1": "skipped", "c2": "redeemed"})

    def test_paste_that_does_not_show_up_is_never_submitted(self):
        rig = self.make_rig()
        rig.console.paste_works = False
        rig.dash.push("start_session", session_id="s1", codes=[{"id": "c1", "code": GOOD}])
        rig.dash.push("admin_login_confirmed", session_id="s1")
        rig.dash.push("skip", session_id="s1")
        rig.dash.push("abort_session", session_id="s1")
        rig.agent.run_once()
        self.assertEqual(rig.console.pressed_a_on_redeem, 0)
        self.assertEqual(rig.clipboard.text, "")

    def test_game_not_owned_is_never_bought_or_installed(self):
        rig = self.make_rig()
        rig.console.has_play = False
        rig.dash.push("start_session", session_id="s1", codes=[])
        rig.dash.push("admin_login_confirmed", session_id="s1")
        rig.dash.push("abort_session", session_id="s1")
        rig.agent.run_once()
        err = rig.dash.of("error")[0]
        self.assertEqual(err["category"], "game_stuck")
        self.assertFalse(rig.console.game_running)
        self.assertEqual(rig.console.scene, "game_page")  # stopped in front of "Install", did not press it

    def test_commands_for_other_sessions_are_dropped(self):
        rig = self.make_rig()
        rig.dash.push("start_session", session_id="s1", codes=[])
        rig.dash.push("admin_login_confirmed", session_id="OTHER")
        rig.dash.push("abort_session", session_id="s1")
        rig.agent.run_once()
        self.assertEqual(rig.last_report()["status"], "aborted")
        self.assertFalse(rig.console.signed_in)

    def test_unexpected_exception_escalates_instead_of_crashing(self):
        rig = self.make_rig()
        rig.vision.ask = lambda *a, **k: (_ for _ in ()).throw(ValueError("vision backend returned unusable JSON"))
        rig.dash.push("start_session", session_id="s1", codes=[])
        rig.dash.push("abort_session", session_id="s1")
        rig.agent.run_once()
        self.assertEqual(rig.dash.of("error")[0]["category"], "unknown")


class CodeExtraction(RigTestCase):
    def run_to_admin_wait(self, login_text: str | None = None):
        rig = self.make_rig()
        if login_text:
            rig.console.login_text = login_text
        rig.dash.push("start_session", session_id="s1", codes=[])
        rig.dash.push("abort_session", session_id="s1")
        rig.agent.run_once()
        return rig

    def test_clean_ocr_read_does_not_call_vision(self):
        rig = self.run_to_admin_wait("Your code QX7M2K9P")
        done = [r for r in rig.reports() if r["action"] == "code_extracted"][0]
        self.assertEqual(done["params"]["source"], "tesseract")
        self.assertNotIn("code", rig.vision.calls)

    def test_ambiguous_ocr_falls_back_to_vision(self):
        rig = self.run_to_admin_wait()  # "microsoft.com/link" also looks like a code to the regex
        done = [r for r in rig.reports() if r["action"] == "code_extracted"][0]
        self.assertEqual(done["params"]["source"], "vision")
        self.assertEqual(rig.dash.of("login_code")[0]["code"], "QX7M2K9P")


class Recovery(RigTestCase):
    def test_unfinished_session_from_a_previous_run_is_reported_and_closed(self):
        rig = self.make_rig()
        rig.store.upsert_session("old", "REDEEM_CODES", "running")
        rig.dash.push("start_session", session_id="s1", codes=[])
        rig.dash.push("abort_session", session_id="s1")
        # run_forever loops; stop it after the first session by raising from the idle wait
        rig.dash.fetch_commands_orig = rig.dash.fetch_commands

        class Stop(Exception):
            pass

        calls = {"n": 0}

        def fetch(wait_s):
            calls["n"] += 1
            if not rig.dash.queue:
                raise Stop()
            return rig.dash.fetch_commands_orig(wait_s)

        rig.dash.fetch_commands = fetch
        with self.assertRaises(Stop):
            rig.agent.run_forever()
        self.assertEqual(rig.store.unfinished_sessions(), [])
        self.assertTrue(any(r["action"] == "recovered" for r in rig.reports()))


if __name__ == "__main__":
    unittest.main()
