from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

from xbox_agent.tests.fakes import Rig, RigTestCase

HAS_WEB = all(importlib.util.find_spec(m) for m in ("fastapi", "httpx", "multipart", "uvicorn"))
GOOD = "AAAAABBBBBCCCCCDDDDDEEEEE"
GOOD_TXT = "AAAAA-BBBBB-CCCCC-DDDDD-EEEEE"
AGENT, ADMIN = "agent-secret", "admin-secret"


@unittest.skipUnless(HAS_WEB, "fastapi/httpx/uvicorn not installed (pip install -r xbox_agent/requirements-dashboard.txt)")
class DashboardApi(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        from xbox_agent.dashboard.app import create_app

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.client = TestClient(create_app(AGENT, ADMIN, tmp.name))
        self.a = {"Authorization": f"Bearer {AGENT}"}
        self.m = {"X-Admin-Token": ADMIN}

    # helpers
    def event(self, kind, payload=None, image=None, sid=None):
        payload = dict(payload or {})
        if sid:
            payload["session_id"] = sid
        files = {"image": ("s.png", image, "image/png")} if image else None
        return self.client.post("/api/agent/events", data={"data": json.dumps({"kind": kind, "payload": payload})},
                                files=files, headers=self.a)

    def start(self, codes=GOOD_TXT):
        r = self.client.post("/api/admin/session", json={"codes": codes}, headers=self.m)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["session_id"]

    def state(self):
        return self.client.get("/api/admin/state", headers=self.m).json()

    def fetch(self, wait=0):
        return self.client.get(f"/api/agent/commands?wait={wait}", headers=self.a).json()

    def admin(self, type_):
        return self.client.post("/api/admin/command", json={"type": type_}, headers=self.m)

    # ---- auth + static
    def test_every_api_route_needs_its_own_token(self):
        for method, path in [("get", "/api/agent/commands"), ("post", "/api/agent/commands/1/ack"),
                             ("post", "/api/agent/events")]:
            self.assertEqual(getattr(self.client, method)(path).status_code, 401, path)
            self.assertEqual(getattr(self.client, method)(path, headers=self.m).status_code, 401, path)  # admin token != agent
        for method, path in [("get", "/api/admin/state"), ("post", "/api/admin/session"), ("post", "/api/admin/command"),
                             ("post", "/api/admin/delete/confirm"), ("post", "/api/admin/session/reset"),
                             ("get", "/api/admin/shots/" + "a" * 32)]:
            self.assertEqual(getattr(self.client, method)(path).status_code, 401, path)
            self.assertEqual(getattr(self.client, method)(path, headers=self.a).status_code, 401, path)  # agent token != admin

    def test_ui_is_served_with_a_strict_csp_and_no_inline_script(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("script-src 'self'", r.headers["content-security-policy"])
        self.assertNotIn("<script>", r.text)
        title = r.text.split("<title>")[1].split("</title>")[0]
        self.assertNotIn("xbox", title.lower())  # the agent looks for a window titled "Xbox"
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)
        self.assertNotIn("innerHTML", self.client.get("/static/app.js").text)

    # ---- session start
    def test_bad_and_duplicate_codes_are_rejected_with_line_numbers(self):
        r = self.client.post("/api/admin/session", json={"codes": f"{GOOD_TXT}\nnot-a-code\n{GOOD_TXT}"}, headers=self.m)
        self.assertEqual(r.status_code, 400)
        self.assertIn("2, 3", r.json()["detail"])
        self.assertIsNone(self.state()["session"])

    def test_start_hands_codes_to_the_agent_once_and_never_shows_them_again(self):
        sid = self.start(f"# comment\n{GOOD_TXT}\n")
        self.assertEqual(self.client.post("/api/admin/session", json={"codes": ""}, headers=self.m).status_code, 409)
        cmds = self.fetch()
        self.assertEqual([c["type"] for c in cmds], ["start_session"])
        self.assertEqual(cmds[0]["payload"], {"session_id": sid, "codes": [{"id": "c1", "code": GOOD_TXT}]})
        self.assertEqual(self.fetch(), [])  # handed out once
        self.assertNotIn(GOOD, json.dumps(self.state()).replace("-", ""))
        self.assertEqual(self.state()["session"]["codes"][0]["masked"], "XXXXX-XXXXX-XXXXX-XXXXX-EEEEE")

    def test_stop_after_is_validated_and_forwarded(self):
        bad = self.client.post("/api/admin/session", json={"codes": "", "stop_after": "LOGOUT"}, headers=self.m)
        self.assertEqual(bad.status_code, 400)
        self.assertIsNone(self.state()["session"])
        r = self.client.post("/api/admin/session", json={"codes": "", "stop_after": "DETECT_LOGGED_IN"}, headers=self.m)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.fetch()[0]["payload"]["stop_after"], "DETECT_LOGGED_IN")

    def test_generic_command_endpoint_cannot_issue_privileged_commands(self):
        self.start()
        for t in ("confirm_delete_profile", "start_session", "resume", "anything"):
            self.assertEqual(self.admin(t).status_code, 400, t)

    # ---- login code, game ready, errors
    def test_login_code_flow(self):
        sid = self.start()
        self.assertEqual(self.admin("admin_login_confirmed").status_code, 409)  # nothing to confirm yet
        self.event("login_code", {"code": "QX7M2K9P"}, image=b"\x89PNG", sid=sid)
        s = self.state()["session"]
        self.assertEqual(s["login_code"], "QX7M2K9P")
        self.assertTrue(s["login_shot"])
        self.fetch()
        self.assertEqual(self.admin("admin_login_confirmed").status_code, 200)
        self.assertIsNone(self.state()["session"]["login_code"])  # single use
        cmd = self.fetch()[0]
        self.assertEqual((cmd["type"], cmd["payload"]), ("admin_login_confirmed", {"session_id": sid}))
        self.assertEqual(self.admin("admin_login_confirmed").status_code, 409)

    def test_close_game_only_when_ready(self):
        sid = self.start()
        self.assertEqual(self.admin("close_game").status_code, 409)
        self.event("game_ready", {"message": "ready"}, sid=sid)
        self.assertEqual(self.admin("close_game").status_code, 200)

    def test_error_retry_skip_rules(self):
        sid = self.start()
        self.assertEqual(self.admin("retry").status_code, 409)
        self.event("error", {"category": "auth", "message": "password screen", "ocr_text": "Password", "state": "REACH_LOGIN",
                             "can_skip": False}, image=b"x", sid=sid)
        err = self.state()["session"]["error"]
        self.assertEqual((err["category"], err["ocr_text"]), ("auth", "Password"))
        self.assertEqual(self.admin("skip").status_code, 409)
        self.assertEqual(self.admin("retry").status_code, 200)
        self.assertIsNone(self.state()["session"]["error"])
        self.event("error", {"category": "network", "message": "x", "can_skip": True}, sid=sid)
        self.assertEqual(self.admin("skip").status_code, 200)

    # ---- profile deletion
    def test_delete_needs_the_typed_gamertag_and_never_exposes_the_token(self):
        sid = self.start()
        r = self.client.post("/api/admin/delete/confirm", json={"gamertag": "Tag99"}, headers=self.m)
        self.assertEqual(r.status_code, 409)
        self.event("delete_profile_request", {"gamertag": "Tag99", "confirm_token": "TOKEN-123"}, sid=sid)
        self.assertNotIn("TOKEN-123", json.dumps(self.state()))
        self.assertEqual(self.state()["session"]["delete_request"], {"gamertag": "Tag99"})
        bad = self.client.post("/api/admin/delete/confirm", json={"gamertag": "OtherUser"}, headers=self.m)
        self.assertEqual(bad.status_code, 400)
        self.fetch()
        ok = self.client.post("/api/admin/delete/confirm", json={"gamertag": "  tag99 "}, headers=self.m)
        self.assertEqual(ok.status_code, 200)
        cmd = self.fetch()[0]
        self.assertEqual((cmd["type"], cmd["payload"]), ("confirm_delete_profile", {"session_id": sid, "token": "TOKEN-123"}))
        self.assertIsNone(self.state()["session"]["delete_request"])

    def test_delete_can_be_denied(self):
        sid = self.start()
        self.event("delete_profile_request", {"gamertag": "Tag99", "confirm_token": "t"}, sid=sid)
        self.assertEqual(self.admin("deny_delete_profile").status_code, 200)
        self.assertEqual(self.fetch()[-1]["type"], "deny_delete_profile")
        self.assertEqual(self.admin("deny_delete_profile").status_code, 409)

    # ---- events, screenshots, lifecycle
    def test_stale_events_are_logged_but_do_not_touch_the_session(self):
        self.start()
        self.event("game_ready", {"message": "old"}, sid="some-other-session")
        self.assertFalse(self.state()["session"]["game_ready"])
        self.assertEqual(self.state()["events"][-1]["message"], "old")

    def test_event_validation(self):
        bad = self.client.post("/api/agent/events", data={"data": "{not json"}, headers=self.a)
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.event("who_knows").status_code, 400)
        big = self.event("step", {}, image=b"0" * (8 * 1024 * 1024 + 1))
        self.assertEqual(big.status_code, 413)

    def test_screenshots_roundtrip_and_path_traversal_is_impossible(self):
        self.event("step", {"state": "IDLE", "action": "idle"}, image=b"\x89PNG-bytes")
        shot = self.state()["events"][-1]["shot"]
        r = self.client.get(f"/api/admin/shots/{shot}", headers=self.m)
        self.assertEqual((r.status_code, r.content, r.headers["content-type"]), (200, b"\x89PNG-bytes", "image/png"))
        for bad in ("../../etc/passwd", "..%2F..%2Fetc%2Fpasswd", "a" * 32, "x"):
            self.assertEqual(self.client.get(f"/api/admin/shots/{bad}", headers=self.m).status_code, 404, bad)

    def test_report_ends_the_session_and_allows_a_new_one(self):
        sid = self.start()
        self.event("session_report", {"status": "completed", "codes": []}, sid=sid)
        s = self.state()["session"]
        self.assertEqual((s["status"], s["report"]["status"]), ("ended", "completed"))
        self.start()

    def test_reset_unblocks_a_dead_session_and_drops_undelivered_commands(self):
        self.start()
        self.client.post("/api/admin/session/reset", headers=self.m)
        self.assertEqual(self.fetch(), [])
        self.start()

    def test_long_poll_waits_then_returns_empty_and_marks_the_agent_online(self):
        self.assertFalse(self.state()["agent"]["online"])
        t0 = time.monotonic()
        self.assertEqual(self.fetch(wait=1), [])
        self.assertGreaterEqual(time.monotonic() - t0, 0.9)
        self.assertTrue(self.state()["agent"]["online"])

    def test_ack_moves_commands_to_consumed(self):
        self.start()
        cmd = self.fetch()[0]
        self.assertEqual(self.state()["commands"][-1]["status"], "delivered")
        self.client.post(f"/api/agent/commands/{cmd['id']}/ack", headers=self.a)
        self.assertEqual(self.state()["commands"][-1]["status"], "consumed")


@unittest.skipUnless(HAS_WEB, "fastapi/httpx/uvicorn not installed")
class DashboardLauncher(unittest.TestCase):
    def test_refuses_to_start_without_distinct_tokens(self):
        from xbox_agent.dashboard import __main__ as launcher

        with mock.patch.object(launcher, "load_env_file"):
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(launcher.main(), 2)
            with mock.patch.dict(os.environ, {"XBOX_AGENT_DASHBOARD_TOKEN": "x", "DASHBOARD_ADMIN_TOKEN": "x"}, clear=True):
                self.assertEqual(launcher.main(), 2)

    def test_create_app_requires_tokens(self):
        from xbox_agent.dashboard.app import create_app

        with self.assertRaises(ValueError):
            create_app("", "x")


@unittest.skipUnless(HAS_WEB, "fastapi/httpx/uvicorn not installed")
class RealAgentAgainstRealDashboard(RigTestCase):
    """The shipped agent + HttpDashboard client talking HTTP to the dashboard, with a scripted 'admin'."""

    def start_server(self):
        import uvicorn

        from xbox_agent.dashboard.app import create_app

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        server = uvicorn.Server(uvicorn.Config(create_app(AGENT, ADMIN, tmp.name), host="127.0.0.1", port=0, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(server.started)

        def stop():
            server.should_exit = True
            thread.join(5)

        self.addCleanup(stop)
        return f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"

    def test_full_session_driven_from_the_web_api(self):
        import httpx

        from xbox_agent.tools.dashboard import HttpDashboard

        url = self.start_server()
        dash = HttpDashboard(url, AGENT, retries=1)
        self.addCleanup(dash.close)
        rig = Rig(dashboard=dash)
        self.addCleanup(rig.close)
        rig.console.outcomes = {GOOD: "redeemed"}
        admin = httpx.Client(base_url=url, headers={"X-Admin-Token": ADMIN}, timeout=10)
        self.addCleanup(admin.close)
        admin_errors: list[str] = []
        final: dict = {}

        def wait_for(pred, what, timeout=45.0):
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                st = admin.get("/api/admin/state").json()
                if pred(st):
                    return st
                time.sleep(0.2)
            raise AssertionError(f"admin timed out waiting for {what}")

        def post(path, **body):
            r = admin.post(path, json=body)
            assert r.status_code == 200, (path, r.text)

        def admin_script():
            try:
                post("/api/admin/session", codes=GOOD_TXT)
                st = wait_for(lambda s: s["session"] and s["session"]["login_code"], "the sign-in code")
                assert st["session"]["login_code"] == "QX7M2K9P"
                rig.console.admin_login()  # the human signs in on their own device, then confirms
                post("/api/admin/command", type="admin_login_confirmed")
                wait_for(lambda s: s["session"]["game_ready"], "game ready")
                post("/api/admin/command", type="close_game")
                st = wait_for(lambda s: s["session"]["delete_request"], "the delete request")
                assert st["session"]["delete_request"]["gamertag"] == "AgentTest99"
                post("/api/admin/delete/confirm", gamertag="agenttest99")
                final.update(wait_for(lambda s: s["session"]["status"] == "ended", "the final report"))
            except Exception as e:  # never leave the agent blocked
                admin_errors.append(repr(e))
                admin.post("/api/admin/command", json={"type": "abort_session"})

        admin_thread = threading.Thread(target=admin_script, daemon=True)
        admin_thread.start()
        agent_errors: list[str] = []

        def run_agent():
            try:
                rig.agent.run_once()
            except Exception as e:
                agent_errors.append(repr(e))

        agent_thread = threading.Thread(target=run_agent, daemon=True)
        agent_thread.start()
        agent_thread.join(90)
        self.assertEqual(agent_errors, [])
        self.assertFalse(agent_thread.is_alive(), "agent did not finish")
        admin_thread.join(15)
        self.assertFalse(admin_thread.is_alive(), "admin script did not finish")
        self.assertEqual(admin_errors, [])

        session = final["session"]
        self.assertEqual(session["report"]["status"], "completed")
        self.assertEqual([c["status"] for c in session["codes"]], ["redeemed"])
        self.assertTrue(session["report"]["profile_deleted"])
        self.assertEqual(rig.console.accounts, ["OtherUser"])
        self.assertTrue(final["agent"]["online"])
        self.assertTrue(all(c["status"] == "consumed" for c in final["commands"]), final["commands"])
        self.assertNotIn(GOOD, json.dumps(final).replace("-", ""))
        self.assertTrue(any(e["shot"] for e in final["events"]))  # screenshots arrived over multipart


if __name__ == "__main__":
    unittest.main()
