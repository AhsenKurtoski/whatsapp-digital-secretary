import base64
import json
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import support  # noqa: F401

import approval_store
import dashboard
import identity
import live
import memory

ROOT = Path(__file__).resolve().parents[1]


class SessionTests(unittest.TestCase):
    def test_sessions_expire_and_revoke_every_device(self):
        dashboard.SESSIONS.clear()
        sid = dashboard.create_session("owner", "admin")
        self.assertIn(sid, dashboard._live_sessions())
        dashboard.SESSIONS[sid]["exp"] = time.time() - 1
        self.assertNotIn(sid, dashboard._live_sessions())
        dashboard.create_session("owner", "admin")
        self.assertEqual(dashboard.revoke_sessions(), 1)
        self.assertEqual(dashboard.SESSIONS, {})


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DashboardHTTPServer(HTTPServer):
    """Server di test sequenziale, senza thread concorrenti sul loopback."""

    allow_reuse_address = True


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dashboard.HTML_FILE = str(ROOT / "dashboard.html")
        # La cartella base dei test è temporanea: collega esplicitamente la coda.
        dashboard.approval_store = approval_store
        cls.server = DashboardHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        dashboard.PORT = cls.server.server_port
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def get(self, path, token=None):
        headers = {"Authorization": "Bearer " + token} if token else {}
        req = urllib.request.Request(self.base + path, headers=headers)
        return urllib.request.urlopen(req, timeout=10)

    def expect_http_error(self, code, action):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            action()
        error = ctx.exception
        self.addCleanup(error.close)
        self.assertEqual(error.code, code)
        return error

    def post_live(self, path, payload, token=""):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Gina-Live"] = token
        req = urllib.request.Request(
            self.base + path, data=json.dumps(payload).encode("utf-8"), headers=headers,
        )
        return urllib.request.urlopen(req, timeout=10)

    def test_live_ingest_requires_the_plugin_token(self):
        self.expect_http_error(
            401,
            lambda: self.post_live("/api/live/inbound", {"phone": "+393331237777", "text": "ciao"}),
        )

    def test_live_ingest_updates_memory_and_cache(self):
        token = live.token()
        phone = "+393331237777"
        with self.post_live(
            "/api/live/inbound", {"phone": phone, "text": "Avete una garanzia?", "name": "Sara"}, token,
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())["data"]["intent"], "info")
        with self.post_live(
            "/api/live/outbound",
            {"phone": phone, "text": "La garanzia copre due anni.", "question": "Avete una garanzia?"},
            token,
        ) as response:
            self.assertTrue(json.loads(response.read())["data"]["cached"])
        profile = memory.load(phone)
        self.assertEqual(profile["messages_out"], 1)
        self.assertIn("Sara", profile["name"])
        self.assertEqual(live.brief("Avete una garanzia?")["source"], "cache")

    def test_live_ingest_rejects_an_empty_payload(self):
        self.expect_http_error(
            400, lambda: self.post_live("/api/live/inbound", {"phone": "", "text": ""}, live.token()),
        )

    def test_live_summary_is_admin_only(self):
        with self.get("/api/live/summary", dashboard.DASH_TOKEN) as response:
            data = json.loads(response.read())["data"]
        self.assertIn("cache_entries", data)

    def test_index_renders_business_name(self):
        with self.get("/") as response:
            body = response.read().decode("utf-8")
        self.assertEqual(response.status, 200)
        self.assertIn("Attività Demo", body)
        self.assertNotIn("{{BUSINESS_NAME}}", body)
        self.assertNotIn("{{I18N_TEXT}}", body)
        self.assertIn('"Panoramica": "Overview"', body)

    def test_api_requires_token(self):
        self.expect_http_error(401, lambda: self.get("/api/leads"))

    def test_api_accepts_bearer(self):
        with self.get("/api/leads", dashboard.DASH_TOKEN) as response:
            self.assertEqual(response.status, 200)
            self.assertTrue(json.loads(response.read())["ok"])

    def test_health_endpoint_requires_token(self):
        self.expect_http_error(401, lambda: self.get("/api/health"))

    def test_health_endpoint_returns_cached_state(self):
        with self.get("/api/health", dashboard.DASH_TOKEN) as response:
            self.assertEqual(response.status, 200)
            payload = json.loads(response.read())
            self.assertTrue(payload["ok"])
            self.assertIn("available", payload["data"])

    def test_post_without_csrf_is_rejected(self):
        req = urllib.request.Request(
            self.base + "/api/restart",
            data=b"{}",
            method="POST",
            headers={"Authorization": "Bearer " + dashboard.DASH_TOKEN, "Content-Type": "application/json"},
        )
        error = self.expect_http_error(403, lambda: urllib.request.urlopen(req, timeout=10))
        self.assertIn("csrf", error.read().decode())

    def test_next_elapse_reads_json_timers(self):
        original = dashboard.run
        payload = json.dumps([
            {"unit": "other.timer", "next": 0},
            {"unit": "openclaw-secretary-health.timer", "next": 1793568077264633},
        ])
        dashboard.run = lambda *a, **k: (0, payload, "")
        try:
            value = dashboard.next_elapse("openclaw-secretary-health.timer")
        finally:
            dashboard.run = original
        self.assertRegex(value, r"^\d{2}/\d{2} \d{2}:\d{2}$")

    def test_next_elapse_falls_back_when_json_unavailable(self):
        original = dashboard.run
        calls = []

        def fake(cmd, **kwargs):
            calls.append(cmd)
            if "list-timers" in cmd:
                return 1, "", "unknown option"
            return 0, "Sun 2026-11-01 22:21:17 CET", ""

        dashboard.run = fake
        try:
            value = dashboard.next_elapse("openclaw-secretary-health.timer")
        finally:
            dashboard.run = original
        self.assertIn("22:21", value)

    def post_json(self, path, payload, token=None, origin=None):
        headers = {"X-Requested-With": "dashboard", "Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        if origin:
            headers["Origin"] = origin
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode(),
            method="POST",
            headers=headers,
        )
        return urllib.request.urlopen(req, timeout=10)

    def test_loopback_tunnel_origin_passes_csrf(self):
        # Il tunnel SSH conserva l'URL loopback: il browser invia
        # Origin http://127.0.0.1:<porta>, che deve essere accettato.
        with self.post_json(
            "/api/approvals/approve",
            {"id": "richiesta-inesistente"},
            dashboard.DASH_TOKEN,
            origin=self.base,
        ) as response:
            payload = json.loads(response.read())
        self.assertFalse(payload["ok"])
        self.assertIn("non trovata", payload["error"])

    def test_foreign_origin_is_rejected_even_with_csrf_header(self):
        error = self.expect_http_error(
            403,
            lambda: self.post_json(
                "/api/approvals/approve",
                {"id": "richiesta-inesistente"},
                dashboard.DASH_TOKEN,
                origin="https://evil.example",
            ),
        )
        self.assertIn("csrf", error.read().decode())

    def test_index_has_demo_quote_email_button(self):
        with self.get("/") as response:
            body = response.read().decode("utf-8")
        self.assertIn("testQuoteEmailBtn", body)
        self.assertIn("Preventivo di prova", body)

    def test_demo_quote_email_route_is_wired(self):
        original = dashboard.send_test_quote_email
        dashboard.send_test_quote_email = lambda: {"ok": True, "result": "demo", "pdf": True}
        dashboard.ACTION_LIMIT.pop("test-email", None)
        try:
            with self.post_json("/api/email/test-quote", {}, dashboard.DASH_TOKEN) as response:
                payload = json.loads(response.read())
        finally:
            dashboard.send_test_quote_email = original
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"], "demo")
        self.assertTrue(payload["pdf"])

    def test_google_renew_requires_token(self):
        self.expect_http_error(401, lambda: self.post_json("/api/google/renew/start", {}))

    def test_google_renew_rejects_foreign_redirect(self):
        with self.post_json(
            "/api/google/renew/finish",
            {"auth_url": "https://evil.example/callback?code=abc"},
            dashboard.DASH_TOKEN,
        ) as response:
            payload = json.loads(response.read())
        self.assertFalse(payload["ok"])
        self.assertIn("non valido", payload["error"])

    def test_google_renew_start_without_cli_reports_the_problem(self):
        original = dashboard.GOG
        dashboard.GOG = "/nonexistent/gog"
        try:
            result = dashboard.google_renew_start()
        finally:
            dashboard.GOG = original
        self.assertFalse(result["ok"])
        self.assertIn("gog", result["error"])

    def test_health_summary_includes_problem_tokens(self):
        original = dashboard.HEALTH_STATE
        path = ROOT / "tests" / "_health_tmp.json"
        path.write_text(
            json.dumps(
                {
                    "available": True,
                    "overall": "critical",
                    "counts": {"critical": 1, "warn": 0, "ok": 2},
                    "generated_at": "2026-11-01T20:00:00+01:00",
                    "tokens": [
                        {"id": "google_oauth", "label": "Google OAuth", "status": "critical", "expires": "01/11/2026 15:53"},
                        {"id": "dashboard_token", "label": "Token dashboard", "status": "ok", "expires": "nessuna scadenza"},
                    ],
                    "expiring": [{"id": "google_oauth", "label": "Google OAuth", "days_left": -1, "expired": True}],
                }
            ),
            encoding="utf-8",
        )
        dashboard.HEALTH_STATE = str(path)
        try:
            summary = dashboard.health_summary()
        finally:
            dashboard.HEALTH_STATE = original
            path.unlink(missing_ok=True)
        self.assertEqual([t["id"] for t in summary["tokens"]], ["google_oauth"])
        self.assertEqual(len(summary["expiring"]), 1)

    def test_interval_seconds_reads_monotonic_props(self):
        props = "{ OnUnitActiveUSec=2min ; next_elapse=26min 1.913766s }"
        self.assertEqual(dashboard.interval_seconds(props), 120)
        self.assertEqual(dashboard.interval_seconds("{ OnUnitActiveUSec=1h 30min }"), 5400)
        self.assertEqual(dashboard.interval_seconds("{ next_elapse=30min }"), 0)
        self.assertEqual(dashboard.interval_seconds(""), 0)

    def test_next_elapse_rebuilds_when_systemd_has_no_next(self):
        unit = "openclaw-watchdog.timer"
        original = dashboard.run

        def fake(cmd, **kwargs):
            if "list-timers" in cmd:
                return 0, json.dumps([{"unit": unit, "next": 0}]), ""
            if "NextElapseUSecRealtime" in cmd:
                return 0, "", ""
            if "TimersMonotonic" in cmd:
                return 0, "{ OnUnitActiveUSec=2min ; next_elapse=0 }", ""
            if "LastTriggerUSec" in cmd:
                return 0, "Mon 2026-11-02 13:29:25 CET", ""
            return 1, "", ""

        dashboard.run = fake
        try:
            value = dashboard.next_elapse(unit)
        finally:
            dashboard.run = original
        self.assertEqual(value, "02/11 13:31")

    def test_next_elapse_accepts_any_timezone_abbreviation(self):
        # strptime %Z accetta solo UTC/GMT/local time: CET e CEST devono funzionare
        # comunque, su ogni versione di Python.
        unit = "openclaw-secretary-health.timer"
        original = dashboard.run

        for stamp in ("Mon 2026-11-02 13:29:25 CET", "Mon 2026-11-02 13:29:25 CEST", "Mon 2026-11-02 13:29:25"):
            def fake(cmd, **kwargs):
                if "list-timers" in cmd:
                    return 0, json.dumps([{"unit": unit, "next": 0}]), ""
                if "NextElapseUSecRealtime" in cmd:
                    return 0, "", ""
                if "TimersMonotonic" in cmd:
                    return 0, "{ OnUnitActiveUSec=2min }", ""
                if "LastTriggerUSec" in cmd:
                    return 0, stamp, ""
                return 1, "", ""

            dashboard.run = fake
            try:
                value = dashboard.next_elapse(unit)
            finally:
                dashboard.run = original
            self.assertEqual(value, "02/11 13:31", stamp)

    def test_quotes_endpoint_requires_token(self):
        self.expect_http_error(401, lambda: self.get("/api/quotes"))

    def test_contacts_endpoint_requires_token(self):
        self.expect_http_error(401, lambda: self.get("/api/contacts"))

    def test_quote_pdf_download_requires_token(self):
        self.expect_http_error(401, lambda: self.get("/api/quotes/apr-20260101-000000-abcd.pdf"))

    def test_demo_quote_preview_requires_token(self):
        self.expect_http_error(401, lambda: self.get("/api/quotes/demo/preview"))

    def test_demo_quote_preview_is_local_and_returns_a_pdf(self):
        before = len(approval_store.list_requests(200).get("recent") or [])
        with self.get("/api/quotes/demo/preview", dashboard.DASH_TOKEN) as response:
            self.assertEqual(response.status, 200)
            payload = json.loads(response.read())
        after = len(approval_store.list_requests(200).get("recent") or [])
        self.assertTrue(payload["ok"], payload)
        self.assertFalse(payload["sent"])
        self.assertIn("PDF ALLEGATO", payload["html"])
        self.assertTrue(base64.b64decode(payload["pdf_base64"]).startswith(b"%PDF-1.4"))
        self.assertEqual(before, after)

    def test_demo_quote_pdf_is_inline_and_does_not_queue(self):
        before = len(approval_store.list_requests(200).get("recent") or [])
        with self.get("/api/quotes/demo.pdf", dashboard.DASH_TOKEN) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get("Content-Type"), "application/pdf")
            self.assertIn("inline", response.headers.get("Content-Disposition", ""))
            data = response.read()
        after = len(approval_store.list_requests(200).get("recent") or [])
        self.assertTrue(data.startswith(b"%PDF-1.4"))
        self.assertEqual(before, after)

    def test_index_wires_demo_preview_without_creating_a_request(self):
        body = (ROOT / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn('id="previewDemoBtn"', body)
        self.assertIn('id="demoPreviewCard"', body)
        self.assertIn("/api/quotes/demo/preview", body)
        self.assertIn("nessuna email inviata", body)

    def test_hidden_banners_are_not_forced_visible_by_flex_layout(self):
        body = (ROOT / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn("[hidden]{display:none!important}", body)
        self.assertIn("#capabilitiesBanner[hidden]{display:none}", body)

    def test_demo_quote_creates_a_downloadable_pdf(self):
        with self.post_json("/api/quotes/demo", {}, dashboard.DASH_TOKEN) as response:
            payload = json.loads(response.read())
        self.assertTrue(payload["ok"], payload)
        quote_id = payload["id"]
        self.assertTrue(payload["number"].startswith("PREV-"))
        with self.get(f"/api/quotes/{quote_id}.pdf", dashboard.DASH_TOKEN) as response:
            data = response.read()
            self.assertEqual(response.headers.get("Content-Type"), "application/pdf")
            self.assertIn("attachment", response.headers.get("Content-Disposition", ""))
        self.assertTrue(data.startswith(b"%PDF-1.4"))
        self.assertIn(b"PREVENTIVO", data)

    def test_quote_email_includes_the_pdf_attachment(self):
        row = approval_store.append_request(
            "quote",
            {
                "client_name": "Mario Rossi",
                "client_email": "cliente@example.com",
                "items": [{"description": "Sito vetrina", "unit_price": 900}],
            },
        )
        captured = {}
        original_run = dashboard.run
        original_gog = dashboard.GOG
        dashboard.GOG = "/usr/bin/true"

        def fake(cmd, **kwargs):
            captured["cmd"] = cmd
            return 0, '{"id":"msg-1"}', ""

        dashboard.run = fake
        try:
            result = dashboard._execute_email(row["payload"])
        finally:
            dashboard.run = original_run
            dashboard.GOG = original_gog
        self.assertIn("--attach", captured["cmd"])
        self.assertIn("PDF", result)

    def test_login_sets_cookie_and_rejects_bad_token(self):
        opener = urllib.request.build_opener(NoRedirect)
        body = urllib.parse.urlencode({"token": dashboard.DASH_TOKEN}).encode()
        req = urllib.request.Request(self.base + "/login", data=body, method="POST")
        error = self.expect_http_error(302, lambda: opener.open(req, timeout=10))
        cookie = error.headers.get("Set-Cookie", "")
        self.assertIn("ocdash=", cookie)
        # Il cookie deve restare privato anche quando il browser arriva dal
        # tunnel SSH: niente lettura da JavaScript e nessun invio cross-site.
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)

        bad = urllib.parse.urlencode({"token": "wrong"}).encode()
        self.expect_http_error(
            401,
            lambda: opener.open(
                urllib.request.Request(self.base + "/login", data=bad, method="POST"),
                timeout=10,
            ),
        )

    def login(self, token, code=None):
        opener = urllib.request.build_opener(NoRedirect)
        payload = {"token": token}
        if code is not None:
            payload["code"] = code
        req = urllib.request.Request(
            self.base + "/login",
            data=urllib.parse.urlencode(payload).encode(),
            method="POST",
        )
        return opener.open(req, timeout=10)

    @staticmethod
    def session_id(error):
        cookie = error.headers.get("Set-Cookie", "")
        return cookie.split("=", 1)[1].split(";", 1)[0] if "=" in cookie else ""

    def test_login_with_2fa_requires_the_code_over_http(self):
        self.addCleanup(identity.disable_totp)
        self.addCleanup(dashboard.SESSIONS.clear)
        enrolled = identity.enable_totp()
        dashboard.SESSIONS.clear()

        error = self.expect_http_error(401, lambda: self.login(dashboard.DASH_TOKEN))
        self.assertIn("2FA", error.read().decode())
        self.assertEqual(dashboard.SESSIONS, {})

        wrong = "000000" if identity.totp_code(enrolled["secret"]) != "000000" else "111111"
        self.expect_http_error(401, lambda: self.login(dashboard.DASH_TOKEN, wrong))
        self.assertEqual(dashboard.SESSIONS, {})

        error = self.expect_http_error(
            302, lambda: self.login(dashboard.DASH_TOKEN, identity.totp_code(enrolled["secret"])),
        )
        sid = self.session_id(error)
        self.assertTrue(sid)
        self.assertIn(sid, dashboard.SESSIONS)
        self.assertEqual(dashboard.SESSIONS[sid]["role"], "admin")
        # La sessione nata dal login 2FA vale davvero sulle API.
        with self.get("/api/leads", sid) as response:
            self.assertEqual(response.status, 200)
        actions = [row["action"] for row in identity.recent_audit(20)]
        self.assertIn("login.2fa", actions)
        self.assertTrue(identity.verify_audit()["ok"])

    def revoke_request(self, session_id):
        req = urllib.request.Request(
            self.base + "/api/sessions/revoke",
            data=b"{}",
            method="POST",
            headers={
                "X-Requested-With": "dashboard",
                "Content-Type": "application/json",
                "Cookie": f"ocdash={session_id}",
            },
        )
        return urllib.request.urlopen(req, timeout=10)

    def test_sessions_revoke_over_http_ends_sessions_and_rotates_the_token(self):
        self.addCleanup(dashboard.SESSIONS.clear)
        original_token = dashboard.DASH_TOKEN

        def restore_token():
            dashboard.DASH_TOKEN = original_token
            Path(dashboard.TOKEN_FILE).write_text(original_token, encoding="utf-8")

        self.addCleanup(restore_token)

        error = self.expect_http_error(
            302, lambda: self.login(dashboard.DASH_TOKEN),
        )
        sid = self.session_id(error)
        self.assertIn(sid, dashboard.SESSIONS)
        with self.get("/api/leads", sid) as response:
            self.assertEqual(response.status, 200)

        # Solo un admin revoca: un viewer riceve 403 e la sessione resta.
        viewer_sid = dashboard.create_session("Lettura", "viewer")
        self.expect_http_error(403, lambda: self.revoke_request(viewer_sid))
        self.assertIn(viewer_sid, dashboard.SESSIONS)

        with self.revoke_request(sid) as response:
            payload = json.loads(response.read())
            cookie = response.headers.get("Set-Cookie", "")
        self.assertTrue(payload["ok"], payload)
        self.assertGreaterEqual(payload["revoked"], 2)
        self.assertIn("ocdash=;", cookie)
        self.assertIn("Max-Age=0", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertEqual(dashboard.SESSIONS, {})
        self.expect_http_error(401, lambda: self.get("/api/leads", sid))
        self.assertNotEqual(dashboard.DASH_TOKEN, original_token)
        self.expect_http_error(401, lambda: self.get("/api/leads", original_token))
        with self.get("/api/leads", dashboard.DASH_TOKEN) as response:
            self.assertEqual(response.status, 200)
        session_actions = [row["action"] for row in identity.recent_audit(20)]
        self.assertIn("sessions.revoke", session_actions)


if __name__ == "__main__":
    unittest.main()
