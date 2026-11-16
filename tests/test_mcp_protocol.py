"""Test del protocollo MCP reale, come lo usa OpenClaw.

Il bot parlava di "tool mancante" perché nessun test verificava l'handshake
JSON-RPC: qui il server viene avviato su stdio e interrogato davvero.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class McpProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mcp-protocol-"))
        self.base = self.tmp / "base"
        self.base.mkdir()
        self.config = self.tmp / "config.json"
        self.config.write_text(json.dumps({
            "business_name": "Officina Demo",
            "owner_name": "Mario Rossi",
            "bot_name": "Gina",
            "owner_number": "+393331112222",
            "google_account": "demo@example.com",
            "vat_rate": 22,
            "quote_prefix": "TEST",
            "payment_terms": "50% alla conferma, saldo alla consegna.",
        }), encoding="utf-8")
        self.env = dict(os.environ)
        self.env.update({
            "SECRETARY_BASE": str(self.base),
            "SECRETARY_CONFIG": str(self.config),
            "PYTHONPATH": str(ROOT),
        })
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "approval_mcp.py")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self.env,
            cwd=str(ROOT),
        )

    def tearDown(self):
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)

    def request(self, message):
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        self.assertTrue(line, "nessuna risposta dal server MCP")
        return json.loads(line)

    def initialize(self):
        return self.request({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        })

    def test_initialize_negotiates_protocol(self):
        reply = self.initialize()
        self.assertEqual(reply["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(reply["result"]["serverInfo"]["name"], "secretary-approvals")
        self.assertIn("tools", reply["result"]["capabilities"])

    def test_tools_list_exposes_all_three_approvals(self):
        self.initialize()
        reply = self.request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = reply["result"]["tools"]
        self.assertEqual(
            {tool["name"] for tool in tools},
            {"request_email", "request_calendar", "request_quote"},
        )
        quote = next(tool for tool in tools if tool["name"] == "request_quote")
        required = set(quote["inputSchema"]["required"])
        self.assertEqual(required, {"client_name", "client_email", "items"})

    def test_request_quote_writes_a_real_pdf_and_queue_row(self):
        self.initialize()
        reply = self.request({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {
                "name": "request_quote",
                "arguments": {
                    "client_name": "Anna Verdi",
                    "client_email": "anna@example.com",
                    "items": [
                        {"description": "Sito web vetrina", "qty": 1, "unit_price": 900, "unit": "forfait"},
                    ],
                    "reason": "Il cliente ha chiesto un preventivo",
                },
            },
        })
        result = reply["result"]
        self.assertFalse(result["isError"])
        structured = result["structuredContent"]
        self.assertEqual(structured["status"], "pending")
        self.assertTrue(structured["pdf"])
        self.assertTrue(structured["number"].startswith("TEST-"))

        pdfs = list((self.base / "quotes").glob("*.pdf"))
        self.assertEqual(len(pdfs), 1)
        self.assertTrue(pdfs[0].read_bytes().startswith(b"%PDF-1.4"))

        rows = (self.base / "approvals.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(rows), 1)
        row = json.loads(rows[0])
        self.assertEqual(row["type"], "quote")
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["payload"]["totals"]["total"], 1098.0)

    def test_invalid_quote_returns_a_clean_error(self):
        reply = self.request({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {
                "name": "request_quote",
                "arguments": {"client_name": "X", "client_email": "x@example.com", "items": []},
            },
        })
        self.assertIn("error", reply)
        self.assertEqual(reply["error"]["code"], -32602)
        self.assertIn("voce", reply["error"]["message"])

    def test_unknown_tool_is_rejected(self):
        reply = self.request({
            "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "send_email", "arguments": {}},
        })
        self.assertIn("error", reply)

    def test_selftest_cli_used_by_the_installer(self):
        outcome = subprocess.run(
            [sys.executable, str(ROOT / "approval_mcp.py"), "--selftest"],
            capture_output=True, text=True, env=self.env, cwd=str(ROOT), timeout=60,
        )
        self.assertEqual(outcome.returncode, 0, outcome.stderr)
        self.assertIn("MCP SELF-TEST OK", outcome.stdout)
        self.assertIn("request_quote", " ".join(str(t) for t in [outcome.stdout]) + " request_quote")


if __name__ == "__main__":
    unittest.main()
