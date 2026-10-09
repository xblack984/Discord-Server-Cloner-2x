from __future__ import annotations

import json
import tempfile
import unittest

from xbox_agent import vision_tasks as vt
from xbox_agent.config import Settings
from xbox_agent.models import (
    Button, ErrorCategory, Escalation, Frame, RedeemOutcome, State, StepReport, display_code,
    normalize_code,
)
from xbox_agent.navigation import Navigator, label_matcher, regex_matcher
from xbox_agent.redeem import classify_redeem_text, paste_looks_right
from xbox_agent.tools.store import SqliteStore


class CodeFormat(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_code("abcde-fghij-klmno-pqrst-uvwx2"), "ABCDEFGHIJKLMNOPQRSTUVWX2")
        self.assertEqual(display_code("ABCDEFGHIJKLMNOPQRSTUVWX2"), "ABCDE-FGHIJ-KLMNO-PQRST-UVWX2")

    def test_rejects_wrong_length_and_symbols(self):
        for bad in ("", "short", "A" * 24, "A" * 26, "ABCDE-FGHIJ-KLMNO-PQRST-UVW;2", "password123"):
            self.assertIsNone(normalize_code(bad), bad)


class RedeemText(unittest.TestCase):
    def test_classification(self):
        cases = {
            "Successfully redeemed! Enjoy your game": RedeemOutcome.REDEEMED,
            "This code has already been redeemed": RedeemOutcome.CODE_USED,
            "That code isn't valid. Check the code": RedeemOutcome.CODE_INVALID,
            "Something went wrong. Try again later": RedeemOutcome.NETWORK,
        }
        for text, want in cases.items():
            self.assertIs(classify_redeem_text(text), want, text)

    def test_ambiguous_or_empty_goes_to_vision(self):
        self.assertIsNone(classify_redeem_text("Hmm..."))
        self.assertIsNone(classify_redeem_text("already redeemed. Something went wrong"))

    def test_paste_check_tolerates_ocr_confusions(self):
        self.assertTrue(paste_looks_right("Enter code AAAAA-BBBBB-CCCCC-DDDDD-EEEEE", "AAAAABBBBBCCCCCDDDDDEEEEE"))
        self.assertTrue(paste_looks_right("c0de A1B2C-3D4E5-F6G7H-8J9K2-LMNPQ", "A1B2C3D4E5F6G7H8J9K2LMNPQ"))
        self.assertFalse(paste_looks_right("Enter code", "AAAAABBBBBCCCCCDDDDDEEEEE"))
        self.assertFalse(paste_looks_right("ZZZZZ-YYYYY-XXXXX-WWWWW-VVVVV", "AAAAABBBBBCCCCCDDDDDEEEEE"))


class Matchers(unittest.TestCase):
    def test_game_matcher_excludes_sequels_and_warzone(self):
        cfg = Settings()
        m = regex_matcher(cfg.game_match, cfg.game_exclude)
        self.assertTrue(m("Call of Duty: Modern Warfare"))
        self.assertTrue(m("Call of Duty®: Modern  Warfare®"))
        for no in ("Call of Duty: Modern Warfare II", "Call of Duty: Modern Warfare III",
                   "Call of Duty: Modern Warfare 2", "Call of Duty: Warzone", "Forza"):
            self.assertFalse(m(no), no)

    def test_label_matcher(self):
        m = label_matcher("sign out")
        self.assertTrue(m("  Sign   out "))
        self.assertFalse(m("Sign in"))


class Plumbing(unittest.TestCase):
    def test_step_report_has_exactly_the_contract_keys(self):
        d = json.loads(StepReport(State.IDLE, "idle").to_json())
        self.assertEqual(list(d), ["state", "action", "params", "screenshot_id", "window_focused",
                                   "message", "need_confirmation", "next_expected_event"])

    def test_store_roundtrip_persists_only_masked_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = SqliteStore(f"{tmp}/d.sqlite3", f"{tmp}/shots")
            s.upsert_session("a", "IDLE", "running")
            s.upsert_code("a", "c1", "XXXXX-XXXXX-XXXXX-XXXXX-EEEEE", "redeemed")
            self.assertEqual(s.unfinished_sessions(), ["a"])
            s.close_unfinished()
            self.assertEqual(s.unfinished_sessions(), [])
            shot = s.save_shot(b"png")
            self.assertTrue((__import__("pathlib").Path(tmp) / "shots" / f"{shot}.png").exists())
            rows = s._exec("SELECT masked FROM codes")
            self.assertEqual(rows, [("XXXXX-XXXXX-XXXXX-XXXXX-EEEEE",)])
            s.close()


class StubVision:
    def __init__(self, menus):
        self.menus = list(menus)

    def ask(self, png, task, required_keys, schema=None):
        return self.menus.pop(0) if len(self.menus) > 1 else self.menus[0]


def menu(*labels, focus=0, layout="vertical"):
    return {"layout": layout, "items": [{"label": l, "focused": i == focus, "row": i, "col": 0}
                                        for i, l in enumerate(labels)]}


class NavigatorTests(unittest.TestCase):
    def make(self, menus):
        self.pressed: list[Button] = []
        n = [0]

        def snap():
            n[0] += 1
            return Frame(b"x", f"f{n[0]}", 100.0)

        return Navigator(Settings(), StubVision(menus), self.pressed.append, snap, lambda s: None)

    def test_moves_down_then_confirms(self):
        nav = self.make([menu("A", "B", "Target", focus=0), menu("A", "B", "Target", focus=1),
                         menu("A", "B", "Target", focus=2)])
        self.assertEqual(nav.select(label_matcher("target"), "t"), "Target")
        self.assertEqual(self.pressed, [Button.DOWN, Button.DOWN, Button.A])

    def test_ambiguous_match_escalates_without_pressing(self):
        nav = self.make([menu("Sign out", "Sign out of all")])
        with self.assertRaises(Escalation) as cm:
            nav.select(label_matcher("sign out"), "sign out")
        self.assertEqual(cm.exception.category, ErrorCategory.UNKNOWN)
        self.assertEqual(self.pressed, [])

    def test_missing_focus_escalates(self):
        nav = self.make([menu("A", "B", focus=-1)])
        with self.assertRaises(Escalation):
            nav.select(label_matcher("b"), "b")
        self.assertEqual(self.pressed, [])

    def test_target_never_found_gives_up_after_bounded_scan(self):
        nav = self.make([menu("A", "B")])
        with self.assertRaises(Escalation):
            nav.select(label_matcher("zzz"), "zzz")
        self.assertLessEqual(len(self.pressed), Settings().nav_scan_limit + 1)

    def test_reaching_the_end_of_the_menu_is_not_found_not_a_lost_stream(self):
        self.pressed = []
        nav = Navigator(Settings(), StubVision([menu("A", "B", focus=1)]), self.pressed.append,
                        lambda: Frame(b"x", "static", 100.0), lambda s: None)  # pressing DOWN changes nothing
        with self.assertRaises(Escalation) as cm:
            nav.select(label_matcher("zzz"), "zzz")
        self.assertEqual(cm.exception.category, ErrorCategory.UNKNOWN)
        self.assertIn("not found", str(cm.exception))
        self.assertEqual(self.pressed, [Button.DOWN])

    def test_a_priority_list_uses_the_first_matcher_that_matches_anything(self):
        nav = self.make([menu("Sign in", "Sign in on another device", focus=0), menu("Sign in", "Sign in on another device", focus=1)])
        both = [label_matcher("another device"), label_matcher("sign in")]
        self.assertEqual(nav.select(both, "sign-in"), "Sign in on another device")  # not ambiguous with "Sign in"
        self.pressed.clear()
        only_plain = self.make([menu("Sign in", focus=0)])
        self.assertEqual(only_plain.select(both, "sign-in"), "Sign in")  # falls back to the second matcher
        self.assertEqual(self.pressed, [Button.A])

    def test_frozen_frames_raise_stream_lost(self):
        self.pressed = []
        nav = Navigator(Settings(), StubVision([menu("A", "B", "T", focus=0)]), self.pressed.append,
                        lambda: Frame(b"x", "same", 100.0), lambda s: None)
        with self.assertRaises(Escalation) as cm:
            nav.select(label_matcher("t"), "t")
        self.assertEqual(cm.exception.category, ErrorCategory.STREAM_LOST)


class GermanLabels(unittest.TestCase):
    """Defaults cover English and German consoles; unverified wording is tunable from .env."""

    def test_default_labels_match_german_menu_entries(self):
        cfg = Settings()
        cases = [
            (cfg.login_labels, "Auf einem anderen Gerät anmelden"), (cfg.signin_labels, "Anmelden"),
            (cfg.library_labels, "Meine Spiele & Apps"), (cfg.quit_labels, "Spiel beenden"),
            (cfg.signout_labels, "Abmelden"), (cfg.profile_labels, "Profil & System"),
            (cfg.play_labels, "Fortsetzen"), (cfg.redeem_path[1].split("|"), "Code einlösen"),
            (cfg.delete_path[1].split("|"), "Einstellungen"), (cfg.delete_path[3].split("|"), "Konten entfernen"),
        ]
        for needles, label in cases:
            self.assertTrue(label_matcher(*needles)(label), (needles, label))

    def test_english_labels_still_match_and_do_not_cross_match(self):
        cfg = Settings()
        self.assertTrue(label_matcher(*cfg.signout_labels)("Sign out"))
        self.assertTrue(label_matcher(*cfg.play_labels)("Play"))
        self.assertFalse(label_matcher(*cfg.signout_labels)("Sign in"))
        self.assertFalse(label_matcher(*cfg.quit_labels)("Settings"))


class ArabicAndSymbols(unittest.TestCase):
    """Labels seen on a real Arabic console screen (the Guide) plus the spelling variants a vision model may return."""

    def test_the_arabic_guide_entries_from_a_real_screen_match_the_defaults(self):
        cfg = Settings()
        self.assertTrue(label_matcher(*cfg.home_labels)("الصفحة الرئيسية"))
        self.assertTrue(label_matcher(*cfg.library_labels)("ألعابي وتطبيقاتي"))
        self.assertFalse(label_matcher(*cfg.home_labels)("ألعابي وتطبيقاتي"))
        self.assertFalse(label_matcher(*cfg.library_labels)("الصفحة الرئيسية"))

    def test_spelling_variants_and_diacritics_still_match(self):
        m = label_matcher("ألعابي")
        for variant in ("العابي وتطبيقاتي", "أَلْعَابِي", "ألعابـــي"):
            self.assertTrue(m(variant), variant)
        self.assertTrue(label_matcher("مكتبة")("المكتبه"))  # ta marbuta / ha
        self.assertTrue(label_matcher("قطع الاتصال")("  قطع   الاتصال "))

    def test_sign_in_and_sign_out_do_not_cross_match_in_arabic(self):
        cfg = Settings()
        self.assertTrue(label_matcher(*cfg.signin_labels)("تسجيل الدخول"))
        self.assertFalse(label_matcher(*cfg.signin_labels)("تسجيل الخروج"))
        self.assertTrue(label_matcher(*cfg.signout_labels)("تسجيل الخروج"))
        self.assertFalse(label_matcher(*cfg.quit_labels)("تسجيل الخروج"))  # "exit" must not pick "sign out"

    def test_the_game_list_from_the_real_arabic_guide_picks_exactly_one_title(self):
        cfg = Settings()
        pick = regex_matcher(cfg.game_match, cfg.game_exclude)
        titles = ["Call of Duty®", "Call of Duty®: Modern Warfare®", "Call of Duty®: Modern Warfare® III"]
        self.assertEqual([t for t in titles if pick(t)], ["Call of Duty®: Modern Warfare®"])
        for no in ("Call of Duty®: Modern Warfare® II", "Call of Duty®: Modern Warfare® 2", "Call of Duty®: Modern Warfare®III",
                   "Call of Duty®: Warzone™"):
            self.assertFalse(pick(no), no)

    def test_trademark_signs_are_ignored_by_every_matcher(self):
        self.assertTrue(label_matcher("modern warfare")("Modern Warfare®"))
        self.assertTrue(regex_matcher(r"modern\s*warfare")("Modern  Warfare™"))


class VisionValidation(unittest.TestCase):
    def test_unknown_screen_and_outcome_degrade_safely(self):
        v = StubVision([{"screen": "enter_your_password_here"}])
        self.assertEqual(vt.classify_screen(v, b""), "unknown")
        self.assertIs(vt.read_redeem_result(StubVision([{"outcome": "do_something_else"}]), b""), RedeemOutcome.UNKNOWN)
        self.assertEqual(vt.read_game_state(StubVision([{"state": "hacked"}]), b""), "unknown")

    def test_low_confidence_login_code_is_rejected(self):
        self.assertIsNone(vt.read_login_code(StubVision([{"code": "ABC12345", "confidence": 0.4}]), b""))
        self.assertEqual(vt.read_login_code(StubVision([{"code": "abc 12345", "confidence": 0.9}]), b""), "ABC12345")


if __name__ == "__main__":
    unittest.main()
