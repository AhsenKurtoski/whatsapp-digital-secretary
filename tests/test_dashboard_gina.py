"""Endpoint della dashboard Gina: ruoli, conversazioni, statistiche, GDPR."""
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import support  # noqa: F401

import approval_store
import dashboard
import identity
import memory

ROOT = Path(__file__).resolve().parents[1]


class Server(HTTPServer):
    allow_reuse_address = True


class GinaEndpointsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dashboard.HTML_FILE = str(ROOT / "dashboard.html")
        dashboard.approval_store = approval_store
        cls.server = Server(("127.0.0.1", 0), dashboard.Handler)
        dashboard.PORT = cls.server.server_port
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.admin = dashboard.DASH_TOKEN
        cls.viewer = identity.create_user("Lettura", "viewer")
        cls.operator = identity.create_user("Operatore", "operator")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def request(self, path, token=None, method="GET", body=None):
        headers = {}
        if token:
            headers["Authorization"] = "Bearer " + token
        data = None
        if method == "POST":
            data = json.dumps(body or {}).encode()
            headers["Content-Type"] = "application/json"
            headers["X-Requested-With"] = "dashboard"
            headers["Origin"] = self.base
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, json.loads(response.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            with exc:
                raw = exc.read().decode()
                try:
                    return exc.code, json.loads(raw or "{}")
                except Exception:
                    return exc.code, {"raw": raw}

    def test_requires_auth(self):
        status, _ = self.request("/api/me")
        self.assertEqual(status, 401)

    def test_me_reports_role(self):
        status, data = self.request("/api/me", self.viewer["token"])
        self.assertEqual(status, 200)
        self.assertEqual(data["user"]["role"], "viewer")
        self.assertFalse(data["user"]["can_write"])

        status, data = self.request("/api/me", self.operator["token"])
        self.assertTrue(data["user"]["can_write"])

    def test_conversations_and_handoff_permissions(self):
        phone = "+393331112222"
        memory.record(phone, "user", "Ciao, vorrei un preventivo", intent="quote", score=70)
        memory.record(phone, "assistant", "Certo, a che email?", name="Mario")

        status, data = self.request("/api/conversations", self.viewer["token"])
        self.assertEqual(status, 200)
        self.assertTrue(any(row["phone"] == phone for row in data["data"]))

        status, data = self.request(f"/api/conversations/{phone}", self.viewer["token"])
        self.assertEqual(status, 200)
        self.assertIn("[telefono]", data["data"]["messages"][0]["text"] + "[telefono]")

        status, _ = self.request("/api/conversations/handoff", self.viewer["token"], "POST",
                                 {"phone": phone, "active": True})
        self.assertEqual(status, 403)

        status, data = self.request("/api/conversations/handoff", self.operator["token"], "POST",
                                    {"phone": phone, "active": True})
        self.assertEqual(status, 200)
        self.assertTrue(data["handoff"]["active"])
        self.assertTrue(memory.is_handoff(phone))

        status, data = self.request("/api/conversations/handoff", self.operator["token"], "POST",
                                    {"phone": phone, "active": False})
        self.assertFalse(data["handoff"]["active"])

    def test_stats_and_usage(self):
        status, data = self.request("/api/stats", self.viewer["token"])
        self.assertEqual(status, 200)
        self.assertIn("kpis", data["data"])
        self.assertEqual(len(data["data"]["daily"]), 14)

        status, data = self.request("/api/usage", self.viewer["token"])
        self.assertEqual(status, 200)
        self.assertIn("providers", data["data"])

    def test_admin_only_sections(self):
        status, _ = self.request("/api/users", self.viewer["token"])
        self.assertEqual(status, 403)
        status, data = self.request("/api/users", self.admin)
        self.assertEqual(status, 200)

        status, _ = self.request("/api/gdpr/inventory", self.operator["token"])
        self.assertEqual(status, 403)
        status, data = self.request("/api/gdpr/inventory", self.admin)
        self.assertEqual(status, 200)
        self.assertIn("where", data["data"])

    def test_create_and_revoke_user(self):
        status, data = self.request("/api/users", self.admin, "POST", {"name": "Temporaneo", "role": "viewer"})
        self.assertEqual(status, 200)
        token = data["user"]["token"]
        status, me = self.request("/api/me", token)
        self.assertEqual(me["user"]["name"], "Temporaneo")
        status, data = self.request("/api/users/revoke", self.admin, "POST", {"name": "Temporaneo"})
        self.assertTrue(data["ok"])
        status, _ = self.request("/api/me", token)
        self.assertEqual(status, 401)

    def test_gdpr_delete_requires_confirmation(self):
        phone = "+393339998888"
        memory.record(phone, "user", "ciao")
        status, _ = self.request("/api/gdpr/delete", self.admin, "POST", {"phone": phone})
        self.assertEqual(status, 400)
        status, data = self.request("/api/gdpr/delete", self.admin, "POST",
                                    {"phone": phone, "confirm": "DELETE"})
        self.assertEqual(status, 200)
        self.assertEqual(data["deleted"]["memory"], 1)

    def test_handoff_appears_in_audit(self):
        phone = "+393337776666"
        memory.record(phone, "user", "ciao")
        self.request("/api/conversations/handoff", self.operator["token"], "POST",
                     {"phone": phone, "active": True})
        rows = identity.recent_audit(50)
        self.assertTrue(any(row.get("action") == "handoff" and row.get("user") == "Operatore" for row in rows))


if __name__ == "__main__":
    unittest.main()
