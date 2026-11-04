import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401
import approval_setup


class ApprovalSetupTests(unittest.TestCase):
    def test_quote_tool_is_exposed_to_the_bot(self):
        tmp = Path(tempfile.mkdtemp(prefix="approval-setup-"))
        config_path = tmp / "openclaw.json"
        base = tmp / "base"
        base.mkdir()
        (base / "approval_mcp.py").write_text("# fixture\n", encoding="utf-8")
        (base / "secrets.env").write_text("", encoding="utf-8")
        config_path.write_text(json.dumps({"mcp": {"servers": {}}}), encoding="utf-8")

        with mock.patch.object(approval_setup, "CONFIG", Path(config_path)), \
                mock.patch.object(approval_setup, "BASE", Path(base)), \
                mock.patch.object(approval_setup, "CFG", {}):
            approval_setup.main()

        data = json.loads(config_path.read_text(encoding="utf-8"))
        server = data["mcp"]["servers"]["secretary"]
        self.assertIn("request_quote", server["toolFilter"]["include"])
        self.assertIn("secretary__request_quote", data["tools"]["alsoAllow"])
        self.assertIn("request_email", server["toolFilter"]["include"])

    def test_tool_names_cover_every_approval(self):
        self.assertIn("secretary__request_quote", approval_setup.TOOL_NAMES)
        self.assertIn("secretary__request_email", approval_setup.TOOL_NAMES)
        self.assertIn("secretary__request_calendar", approval_setup.TOOL_NAMES)


if __name__ == "__main__":
    unittest.main()
