import json
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import support  # noqa: F401
import approval_store
import dashboard

ROOT = Path(__file__).resolve().parents[1]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dashboard.HTML_FILE = str(ROOT / "dashboard.html")
        # La cartella base dei test è temporanea: collega esplicitamente la coda.
        dashboard.approval_store = approval_store
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        dashboard.PORT = cls.server.server_port
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def get(self, path, token=None):
        headers = {"Authorization": "Bearer " + token} if token else {}
        req = urllib.request.Request(self.base + path, headers=headers)
        return urllib.request.urlopen(req, timeout=10)

    def test_index_renders_business_name(self):
        with self.get("/") as response:
            body = response.read().decode("utf-8")
        self.assertEqual(response.status, 200)
        self.assertIn("Attività Demo", body)
        self.assertNotIn("{{BUSINESS_NAME}}", body)

    def test_api_requires_token(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/leads")
        self.assertEqual(ctx.exception.code, 401)

    def test_api_accepts_bearer(self):
        with self.get("/api/leads", dashboard.DASH_TOKEN) as response:
            self.assertEqual(response.status, 200)
            self.assertTrue(json.loads(response.read())["ok"])

    def test_health_endpoint_requires_token(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/health")
        self.assertEqual(ctx.exception.code, 401)

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
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 403)
        self.assertIn("csrf", ctx.exception.read().decode())

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

    def post_json(self, path, payload, token=None):
        headers = {"X-Requested-With": "dashboard", "Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode(),
            method="POST",
            headers=headers,
        )
        return urllib.request.urlopen(req, timeout=10)

    def test_google_renew_requires_token(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post_json("/api/google/renew/start", {})
        self.assertEqual(ctx.exception.code, 401)

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
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/quotes")
        self.assertEqual(ctx.exception.code, 401)

    def test_contacts_endpoint_requires_token(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/contacts")
        self.assertEqual(ctx.exception.code, 401)

    def test_quote_pdf_download_requires_token(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/quotes/apr-20260101-000000-abcd.pdf")
        self.assertEqual(ctx.exception.code, 401)

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
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            opener.open(req, timeout=10)
        self.assertEqual(ctx.exception.code, 302)
        self.assertIn("ocdash=", ctx.exception.headers.get("Set-Cookie", ""))

        bad = urllib.parse.urlencode({"token": "wrong"}).encode()
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            opener.open(urllib.request.Request(self.base + "/login", data=bad, method="POST"), timeout=10)
        self.assertEqual(ctx.exception.code, 401)


if __name__ == "__main__":
    unittest.main()
