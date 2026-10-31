import json
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import support  # noqa: F401
import dashboard

ROOT = Path(__file__).resolve().parents[1]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dashboard.HTML_FILE = str(ROOT / "dashboard.html")
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
