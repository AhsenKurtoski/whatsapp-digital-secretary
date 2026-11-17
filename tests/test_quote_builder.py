"""Test del preventivo creato dalla dashboard, senza passare dal modello."""
import shutil
import unittest

import support  # noqa: F401  (sets the isolated environment)

import approval_store
import dashboard


class QuoteBuilderTests(unittest.TestCase):
    def setUp(self):
        if approval_store.QUEUE.exists():
            approval_store.QUEUE.unlink()
        shutil.rmtree(approval_store.QUOTES_DIR, ignore_errors=True)

    def payload(self, **overrides):
        data = {
            "client_name": "Cliente Demo",
            "client_email": "cliente@example.com",
            "client_company": "Demo Srl",
            "client_phone": "+393331112222",
            "client_address": "Via Roma 1, Milano",
            "items": [
                {"description": "Sito web vetrina", "qty": "1", "unit": "forfait", "unit_price": "900"},
                {"description": "Manutenzione", "qty": "12", "unit": "mese", "unit_price": "35,50"},
            ],
            "discount_pct": "10",
            "vat_pct": "22",
            "valid_days": "30",
            "notes": "Preventivo creato a mano dalla dashboard.",
            "subject": "Preventivo per il sito",
        }
        data.update(overrides)
        return data

    def test_manual_quote_creates_pdf_and_pending_request(self):
        result = dashboard.create_manual_quote(self.payload())
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(str(result["number"]).startswith("PREV-"))
        request = approval_store.get_request(result["id"])
        self.assertIsNotNone(request)
        self.assertEqual(request["type"], "quote")
        self.assertEqual(request["status"], "pending")
        self.assertEqual(request["payload"]["totals"]["subtotal"], 1326.0)
        self.assertEqual(request["payload"]["totals"]["total"], 1455.95)
        path = approval_store.quote_path(request)
        self.assertIsNotNone(path)
        self.assertTrue(path.read_bytes().startswith(b"%PDF-1.4"))

    def test_manual_quote_can_be_duplicated_with_a_new_number(self):
        first = dashboard.create_manual_quote(self.payload())
        second = dashboard.create_manual_quote(self.payload())
        self.assertTrue(first["ok"] and second["ok"])
        self.assertNotEqual(first["id"], second["id"])
        self.assertNotEqual(first["number"], second["number"])

    def test_manual_quote_requires_items(self):
        result = dashboard.create_manual_quote(self.payload(items=[]))
        self.assertFalse(result["ok"])
        self.assertIn("voce", result["error"])

    def test_manual_quote_requires_a_valid_email(self):
        result = dashboard.create_manual_quote(self.payload(client_email="non-una-email"))
        self.assertFalse(result["ok"])
        self.assertIn("email", result["error"].lower())

    def test_quotes_data_reports_expiry_and_items(self):
        created = dashboard.create_manual_quote(self.payload(valid_days="30"))
        data = dashboard.quotes_data()
        row = next(item for item in data["quotes"] if item["id"] == created["id"])
        self.assertTrue(row["has_pdf"])
        self.assertFalse(row["expired"])
        self.assertGreaterEqual(row["days_left"], 29)
        self.assertEqual(len(row["items"]), 2)

    def test_quotes_csv_escapes_formulas(self):
        dashboard.create_manual_quote(self.payload(client_name="=SUM(A1)"))
        text = dashboard.quotes_csv()
        self.assertIn("numero;creato;cliente", text)
        self.assertIn("'=SUM(A1)", text)

    def test_email_preview_is_generated_locally(self):
        created = dashboard.create_manual_quote(self.payload())
        preview = dashboard.quote_email_preview(created["id"])
        self.assertTrue(preview["ok"], preview.get("error"))
        self.assertIn("<html", preview["html"].lower())
        self.assertEqual(preview["number"], created["number"])
        self.assertEqual(preview["to"], "cliente@example.com")

    def test_capabilities_are_read_from_the_installed_module(self):
        caps = dashboard.bot_capabilities()
        self.assertTrue(caps["ok"], caps)
        self.assertIn("request_quote", caps["tools"])


if __name__ == "__main__":
    unittest.main()
