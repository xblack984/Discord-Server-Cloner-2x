from __future__ import annotations

import json
import unittest

from xbox_agent.models import Button, Command, display_code
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
