import json
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401
import gateway_policy


class GatewayPolicyTests(unittest.TestCase):
    def test_self_test(self):
        gateway_policy.self_test()

    def test_creates_backup_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "openclaw.json"
            path.write_text(json.dumps({"agents": {"defaults": {}}}), encoding="utf-8")
            first = gateway_policy.apply(str(path))
            self.assertTrue(first["changed"])
            self.assertTrue(path.with_name(path.name + ".bak-secretary").exists())
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["messages"]["inbound"]["debounceMs"], 1200)
            self.assertEqual(saved["messages"]["inbound"]["byChannel"]["whatsapp"], 1800)
            self.assertEqual(saved["messages"]["queue"]["byChannel"]["whatsapp"], "collect")
            second = gateway_policy.apply(str(path))
            self.assertFalse(second["changed"])
            self.assertEqual(second["added"], [])

    def test_never_overrides_user_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "openclaw.json"
            path.write_text(json.dumps({
                "messages": {
                    "inbound": {"byChannel": {"whatsapp": 5000}},
                    "queue": {"mode": "steer", "byChannel": {"whatsapp": "followup"}},
                }
            }), encoding="utf-8")
            gateway_policy.apply(str(path))
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["messages"]["inbound"]["byChannel"]["whatsapp"], 5000)
            self.assertEqual(saved["messages"]["queue"]["byChannel"]["whatsapp"], "followup")
            self.assertEqual(saved["messages"]["queue"]["mode"], "steer")

    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "openclaw.json"
            original = json.dumps({"messages": {}})
            path.write_text(original, encoding="utf-8")
            result = gateway_policy.apply(str(path), dry_run=True)
            self.assertTrue(result["changed"])
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_missing_file_is_reported(self):
        with self.assertRaises(ValueError):
            gateway_policy.apply("/nonexistent/openclaw.json")


if __name__ == "__main__":
    unittest.main()
