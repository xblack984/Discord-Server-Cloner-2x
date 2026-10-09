from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from xbox_agent.check import CheckResult, format_results, inspect_screen, run_checks
from xbox_agent.config import Settings, load_env_file
from xbox_agent.tools.browser import find_browser, new_window_command, title_matches
from xbox_agent.tests.fakes import FakeCapture, FakeOcr, FakeVision, Rig, RigTestCase


class EnvFile(unittest.TestCase):
    def write(self, text: str) -> str:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / ".env"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_parses_comments_quotes_and_never_overrides_the_real_environment(self):
        path = self.write('# comment\n\nA=1\nB = "two words"\nC=\'x\'\nBROKEN LINE\nTESSERACT_CMD=C:\\Program Files\\T\\t.exe\nKEEP=from_file\n')
        with mock.patch.dict(os.environ, {"KEEP": "from_env"}, clear=True):
            load_env_file(path)
            self.assertEqual((os.environ["A"], os.environ["B"], os.environ["C"]), ("1", "two words", "x"))
            self.assertEqual(os.environ["TESSERACT_CMD"], "C:\\Program Files\\T\\t.exe")
            self.assertEqual(os.environ["KEEP"], "from_env")
            self.assertNotIn("BROKEN LINE", os.environ)

    def test_a_repeated_key_takes_the_last_line_so_appending_overrides_the_template(self):
        path = self.write("XBOX_AGENT_PLAY_URL=https://www.xbox.com/de-DE/play/consoles\nOTHER=1\n"
                          "XBOX_AGENT_PLAY_URL=https://www.xbox.com/de-DE/play/consoles/launch/ABC\n")
        with mock.patch.dict(os.environ, {}, clear=True):
            load_env_file(path)
            self.assertEqual(os.environ["XBOX_AGENT_PLAY_URL"], "https://www.xbox.com/de-DE/play/consoles/launch/ABC")
            self.assertEqual(Settings.from_env().play_url, "https://www.xbox.com/de-DE/play/consoles/launch/ABC")
        with mock.patch.dict(os.environ, {"XBOX_AGENT_PLAY_URL": "https://from.env/"}, clear=True):
            load_env_file(path)
            self.assertEqual(os.environ["XBOX_AGENT_PLAY_URL"], "https://from.env/")  # the real environment still wins

    def test_missing_file_is_fine(self):
        load_env_file("/nonexistent/.env")  # must not raise

    def test_default_path_ignores_the_working_directorys_env_file(self):
        with tempfile.TemporaryDirectory() as cwd:
            (Path(cwd) / ".env").write_text("FROM_OTHER_PROJECT=1\n")
            before = os.getcwd()
            os.chdir(cwd)
            self.addCleanup(os.chdir, before)
            with mock.patch.dict(os.environ, {}, clear=True):
                load_env_file()
                self.assertNotIn("FROM_OTHER_PROJECT", os.environ)

    def test_settings_overrides(self):
        env = {"XBOX_AGENT_REDEEM_PATH": "store , redeem code", "XBOX_AGENT_ALLOW_DELETE": "1",
               "XBOX_AGENT_GAME_MATCH": "halo", "XBOX_AGENT_LOGIN_LABELS": ""}
        with mock.patch.dict(os.environ, env, clear=True):
            cfg = Settings.from_env()
        self.assertEqual(cfg.redeem_path, ("store", "redeem code"))
        self.assertTrue(cfg.allow_delete)
        self.assertEqual(cfg.game_match, "halo")
        self.assertEqual(cfg.login_labels, Settings().login_labels)  # empty value -> default

    def test_destructive_and_noisy_defaults_are_safe(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg = Settings.from_env()
        self.assertFalse(cfg.allow_delete)


class CheckTools(unittest.TestCase):
    def test_one_failing_check_does_not_stop_the_others(self):
        def boom():
            raise RuntimeError("ViGEmBus missing")

        results = run_checks([("a", lambda: "fine"), ("b", boom), ("c", lambda: "also fine")])
        self.assertEqual([r.ok for r in results], [True, False, True])
        self.assertEqual(results[1], CheckResult("b", False, "RuntimeError: ViGEmBus missing"))
        text = format_results(results)
        self.assertIn("[OK] a: fine", text)
        self.assertIn("[FAIL] b: RuntimeError: ViGEmBus missing", text)


class InspectTool(RigTestCase):
    def test_reports_screen_ocr_and_menu_without_pressing_anything(self):
        rig = self.make_rig(scene="sign_in_prompt")
        with tempfile.TemporaryDirectory() as out:
            report = inspect_screen(rig.window, FakeCapture(rig.console), FakeOcr(), FakeVision(), out)
            self.assertTrue(Path(report["screenshot"]).is_file())
        self.assertEqual(report["screen"], "sign_in_prompt")
        self.assertEqual([i["label"] for i in report["menu_items"]], ["Sign in", "Sign in on another device"])
        self.assertTrue(report["menu_items"][0]["focused"])
        self.assertIn("Sign in on another device", report["ocr_text"])
        self.assertEqual(rig.gamepad.taps, [])
        json.dumps(report)  # must be printable as JSON

    def test_fails_loudly_when_the_window_cannot_be_focused(self):
        rig = self.make_rig()
        rig.window.focusable = False
        with self.assertRaisesRegex(RuntimeError, "foreground"):
            inspect_screen(rig.window, FakeCapture(rig.console), FakeOcr(), FakeVision())


class BrowserLaunch(unittest.TestCase):
    ENV = {"ProgramFiles": r"C:\PF", "ProgramFiles(x86)": r"C:\PF86", "LOCALAPPDATA": r"C:\Users\me\AppData\Local"}

    def found(self, *paths):
        return lambda p: p in paths

    def test_auto_prefers_chrome_then_edge(self):
        chrome = r"C:\PF\Google\Chrome\Application\chrome.exe"
        edge = r"C:\PF86\Microsoft\Edge\Application\msedge.exe"
        self.assertEqual(find_browser("auto", self.ENV, self.found(chrome, edge)), chrome)
        self.assertEqual(find_browser("auto", self.ENV, self.found(edge)), edge)
        self.assertEqual(find_browser("edge", self.ENV, self.found(chrome, edge)), edge)
        local = r"C:\Users\me\AppData\Local\Google\Chrome\Application\chrome.exe"
        self.assertEqual(find_browser("chrome", self.ENV, self.found(local)), local)

    def test_explicit_path_and_helpful_errors(self):
        custom = r"D:\Browsers\brave.exe"
        self.assertEqual(find_browser(f'"{custom}"', self.ENV, self.found(custom)), custom)
        with self.assertRaisesRegex(FileNotFoundError, "does not exist"):
            find_browser(custom, self.ENV, self.found())
        with self.assertRaisesRegex(FileNotFoundError, "XBOX_AGENT_BROWSER"):
            find_browser("auto", self.ENV, self.found())

    def test_command_opens_a_new_window_and_only_for_http_urls(self):
        url = "https://www.xbox.com/de-DE/play/consoles"
        self.assertEqual(new_window_command("chrome.exe", url), ["chrome.exe", "--new-window", url])
        direct = "https://www.xbox.com/de-DE/play/consoles/launch/EXAMPLE0CONSOLE0ID"  # a console's direct-launch link
        self.assertEqual(new_window_command("msedge.exe", direct), ["msedge.exe", "--new-window", direct])
        for bad in ("file:///C:/secret.txt", "javascript:alert(1)", "--remote-debugging-port=1", "xbox.com"):
            with self.assertRaises(ValueError, msg=bad):
                new_window_command("chrome.exe", bad)

    def test_the_dashboard_window_is_never_mistaken_for_the_xbox_window(self):
        exclude = Settings().window_exclude
        self.assertFalse(title_matches("لوحة XboxAgent - Google Chrome", "Xbox", exclude))
        self.assertFalse(title_matches("XboxAgent dashboard", "xbox", exclude))
        self.assertTrue(title_matches("Konsolen - Xbox Cloud Gaming - Google Chrome", "Xbox", exclude))
        self.assertFalse(title_matches("Notepad", "Xbox", exclude))

    def test_chrome_profile_option_is_validated(self):
        url = "https://www.xbox.com/de-DE/play/consoles"
        self.assertEqual(new_window_command("chrome.exe", url, "Profile 1"),
                         ["chrome.exe", "--profile-directory=Profile 1", "--new-window", url])
        self.assertEqual(new_window_command("chrome.exe", url, ""), ["chrome.exe", "--new-window", url])
        for bad in ("x; calc", "..\\..\\Other", "C:\\Users\\me", "a=b", "p" * 41):
            with self.assertRaises(ValueError, msg=bad):
                new_window_command("chrome.exe", url, bad)

    def test_defaults_and_env_overrides(self):
        cfg = Settings()
        self.assertEqual(cfg.play_url, "https://www.xbox.com/de-DE/play/consoles")
        self.assertTrue(cfg.open_play_url)
        self.assertEqual((cfg.browser_profile, cfg.web_login_timeout_s), ("", 600.0))
        env = {"XBOX_AGENT_PLAY_URL": "https://www.xbox.com/en-US/play/consoles", "XBOX_AGENT_OPEN_PLAY_URL": "0",
               "XBOX_AGENT_BROWSER_PROFILE": "Profile 2", "XBOX_AGENT_WEB_LOGIN_TIMEOUT_S": "120",
               "XBOX_AGENT_BROWSER": "edge", "XBOX_AGENT_WINDOW_EXCLUDE": "XboxAgent, Dashboard",
               "XBOX_AGENT_CONSOLE_LABELS": "connect"}
        with mock.patch.dict(os.environ, env, clear=True):
            cfg = Settings.from_env()
        self.assertEqual((cfg.play_url, cfg.open_play_url, cfg.browser), (env["XBOX_AGENT_PLAY_URL"], False, "edge"))
        self.assertEqual((cfg.window_exclude, cfg.console_labels), (("XboxAgent", "Dashboard"), ("connect",)))
        self.assertEqual((cfg.browser_profile, cfg.web_login_timeout_s), ("Profile 2", 120.0))


class StartupFailures(unittest.TestCase):
    def run_main(self, argv, **patches):
        import io
        from contextlib import redirect_stderr

        from xbox_agent import __main__ as entry

        err = io.StringIO()
        with mock.patch.object(entry, "load_env_file"), mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(entry, "build_tools", **patches), redirect_stderr(err):
            code = entry.main(argv)
        return code, err.getvalue()

    def test_missing_driver_is_reported_in_plain_words_not_a_traceback(self):
        code, err = self.run_main(["--once"], side_effect=ModuleNotFoundError("No module named 'vgamepad'"))
        self.assertEqual(code, 1)
        self.assertIn("Could not start: ModuleNotFoundError", err)
        self.assertIn("--check", err)
        self.assertNotIn("Traceback", err)

    def test_dashboard_missing_tokens_names_the_file_to_edit(self):
        import io
        from contextlib import redirect_stderr

        from xbox_agent.dashboard import __main__ as launcher

        err = io.StringIO()
        with mock.patch.object(launcher, "load_env_file"), mock.patch.dict(os.environ, {}, clear=True), redirect_stderr(err):
            self.assertEqual(launcher.main(), 2)
        self.assertIn(".env", err.getvalue())


class LoginMode(unittest.TestCase):
    """`--login`: open the Xbox page in Chrome so a human can sign in; nothing is typed or pressed."""

    def run_login(self, window):
        import io
        from contextlib import redirect_stderr

        from xbox_agent import __main__ as entry

        err = io.StringIO()
        with mock.patch.object(entry, "load_env_file"), mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch("xbox_agent.tools.windows_input.window_from_settings", return_value=window), redirect_stderr(err):
            code = entry.main(["--login"])
        return code, err.getvalue()

    def test_opens_the_play_page_and_explains_what_to_do(self):
        calls = []
        window = mock.Mock(browser_path=r"C:\PF\Google\Chrome\Application\chrome.exe")
        window.ensure_open.side_effect = lambda url: calls.append(url) or True
        code, err = self.run_login(window)
        self.assertEqual((code, calls), (0, ["https://www.xbox.com/de-DE/play/consoles"]))
        self.assertIn("chrome.exe", err)
        self.assertIn("never types credentials", err)
        window.focus.assert_not_called()
        window.paste.assert_not_called()

    def test_reports_an_already_open_window_and_real_failures(self):
        code, err = self.run_login(mock.Mock(**{"ensure_open.return_value": False}))
        self.assertEqual(code, 0)
        self.assertIn("already open", err)
        code, err = self.run_login(mock.Mock(**{"ensure_open.side_effect": FileNotFoundError("could not find Chrome")}))
        self.assertEqual(code, 1)
        self.assertIn("could not find Chrome", err)


class BatchFiles(unittest.TestCase):
    """The .bat files cannot run here, so check the properties that make them fail silently on Windows."""

    scripts = sorted(Path(__file__).resolve().parents[1].glob("scripts/*.bat"))

    def test_there_are_three_scripts(self):
        self.assertEqual([p.name for p in self.scripts], ["run_agent.bat", "run_dashboard.bat", "setup.bat"])

    def test_crlf_line_endings(self):
        for p in self.scripts:
            raw = p.read_bytes()
            self.assertEqual(raw.count(b"\n"), raw.count(b"\r\n"), f"{p.name} has LF-only lines")

    def test_every_exit_path_pauses_so_the_window_stays_open(self):
        for p in self.scripts:
            text = p.read_text()
            self.assertGreaterEqual(text.count("pause"), 1, p.name)
            self.assertTrue(text.rstrip().endswith("pause") or text.rstrip().endswith("exit /b 1"), p.name)

    def test_run_scripts_check_the_setup_before_starting(self):
        for name in ("run_agent.bat", "run_dashboard.bat"):
            text = (self.scripts[0].parent / name).read_text()
            self.assertIn(r".venv-xbox\Scripts\python.exe", text)
            self.assertIn(r"xbox_agent\.env", text)


if __name__ == "__main__":
    unittest.main()
