import unittest

import support  # noqa: F401  (sets the isolated environment)
import quote_pdf


class QuoteTotalsTests(unittest.TestCase):
    def test_totals_are_computed_server_side(self):
        quote = quote_pdf.normalize_quote({
            "client_name": "Mario Rossi",
            "items": [{"description": "Sito vetrina", "qty": 2, "unit_price": "100,50"}],
            "discount_pct": 10,
            "vat_pct": 22,
        })
        self.assertEqual(quote["totals"]["subtotal"], 201.0)
        self.assertEqual(quote["totals"]["discount"], 20.1)
        self.assertEqual(quote["totals"]["vat"], 39.8)
        self.assertEqual(quote["totals"]["total"], 220.7)

    def test_iva_default_comes_from_config(self):
        quote = quote_pdf.normalize_quote({
            "items": [{"description": "Consulenza", "unit_price": 100}],
        })
        self.assertEqual(quote["vat_pct"], 22)
        self.assertEqual(quote["totals"]["total"], 122.0)

    def test_item_requires_description_and_price(self):
        with self.assertRaises(ValueError):
            quote_pdf.normalize_quote({"items": [{"qty": 1, "unit_price": 10}]})
        with self.assertRaises(ValueError):
            quote_pdf.normalize_quote({"items": [{"description": "x"}]})

    def test_empty_or_absurd_values_are_rejected(self):
        with self.assertRaises(ValueError):
            quote_pdf.normalize_quote({"items": []})
        with self.assertRaises(ValueError):
            quote_pdf.normalize_quote({"items": [{"description": "x", "unit_price": 10}], "discount_pct": 150})
        with self.assertRaises(ValueError):
            quote_pdf.normalize_quote({"items": [{"description": "x", "unit_price": -1}]})
        with self.assertRaises(ValueError):
            quote_pdf.normalize_quote({"items": [{"description": "x", "unit_price": 10}], "date": "not-a-date"})

    def test_validity_is_derived_from_days(self):
        quote = quote_pdf.normalize_quote({
            "date": "2026-01-01",
            "valid_days": 30,
            "items": [{"description": "x", "unit_price": 10}],
        })
        self.assertEqual(quote["valid_until"], "2026-01-31")


class QuotePdfTests(unittest.TestCase):
    def sample(self):
        return quote_pdf.normalize_quote({
            "number": "PREV-2026-001",
            "date": "2026-11-04",
            "client_name": "Mario Rossi",
            "client_company": "Ristorante Da Mario",
            "client_email": "mario@example.com",
            "client_address": "Via Roma 1\n20100 Milano",
            "items": [
                {"description": "Sito web vetrina con menu e prenotazioni", "qty": 1, "unit_price": 890, "unit": "forfait"},
                {"description": "Manutenzione mensile", "qty": 12, "unit_price": 35, "unit": "mese"},
            ],
            "discount_pct": 5,
            "notes": "Città e zone limitrofe. Preventivo valido per un sito in italiano.",
        })

    def test_pdf_is_a_valid_document(self):
        data = quote_pdf.render_quote_pdf(self.sample())
        self.assertTrue(data.startswith(b"%PDF-1.4"))
        self.assertTrue(data.rstrip().endswith(b"%%EOF"))
        self.assertIn(b"/Type /Catalog", data)
        self.assertIn(b"/Type /Pages", data)
        self.assertIn(b"PREVENTIVO", data)
        self.assertIn(b"xref", data)
        self.assertEqual(data.count(b"/Type /Page "), 1)

    def test_brand_and_totals_are_rendered(self):
        data = quote_pdf.render_quote_pdf(self.sample())
        self.assertIn(b"Attivit\xe0 Demo", data)  # à in WinAnsi
        self.assertIn("€".encode("cp1252"), data)
        self.assertIn(b"TOTALE", data)
        self.assertIn(b"PREV-2026-001", data)

    def test_long_quotes_flow_on_multiple_pages(self):
        items = [
            {"description": f"Voce {index} con una descrizione abbastanza lunga da andare a capo", "qty": 1, "unit_price": 10}
            for index in range(1, 35)
        ]
        quote = quote_pdf.normalize_quote({"client_name": "Cliente", "items": items})
        data = quote_pdf.render_quote_pdf(quote)
        self.assertGreaterEqual(data.count(b"/Type /Page "), 2)
        self.assertIn(b"Pagina 2 di", data)

    def test_filename_is_safe(self):
        name = quote_pdf.quote_filename({"number": "PREV/2026 001", "client_name": "Mario <Rossi>"})
        self.assertTrue(name.startswith("Preventivo-"))
        self.assertTrue(name.endswith(".pdf"))
        self.assertNotIn("/", name)
        self.assertNotIn("<", name)

    def test_demo_quote_has_a_real_email(self):
        demo = quote_pdf.demo_quote()
        self.assertEqual(len(demo["items"]), 3)
        self.assertEqual(demo["client_email"], "demo@example.com")
        self.assertGreater(demo["totals"]["total"], 0)
        self.assertTrue(quote_pdf.render_quote_pdf(demo).startswith(b"%PDF-1.4"))


if __name__ == "__main__":
    unittest.main()
