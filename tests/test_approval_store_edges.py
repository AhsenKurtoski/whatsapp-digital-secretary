"""Casi limite della coda approvazioni: validazioni, numerazione e stati."""
import unittest

import support  # noqa: F401

import approval_store


class ApprovalStoreEdgeTests(unittest.TestCase):
    def test_email_validation_trims_and_rejects_bad_payloads(self):
        clean = approval_store.validate_email({
            "to": "  Anna@Example.com, secondo@example.com ",
            "subject": "  Preventivo  ",
            "body": "  Ciao Anna,\n\nEcco il preventivo.  ",
        })
        self.assertEqual(clean["to"], "Anna@Example.com, secondo@example.com")
        with self.assertRaises(ValueError):
            approval_store.validate_email({
                "to": "anna@example.com, non-valida", "subject": "x", "body": "y",
            })
        self.assertEqual(clean["subject"], "Preventivo")
        self.assertTrue(clean["body"].startswith("Ciao Anna,"))
        for payload in (
            {"to": "", "subject": "x", "body": "y"},
            {"to": "a@example.com", "subject": "", "body": "y"},
            {"to": "a@example.com", "subject": "x", "body": ""},
        ):
            with self.assertRaises(ValueError):
                approval_store.validate_email(payload)

    def test_quote_number_skips_numbers_already_used(self):
        rows = [
            {"type": "quote", "payload": {"number": "PREV-2026-001"}},
            {"type": "quote", "payload": {"number": "PREV-2026-002"}},
            {"type": "email", "payload": {"number": "PREV-2026-003"}},
        ]
        number = approval_store._next_quote_number(rows, "PREV")
        self.assertTrue(number.startswith("PREV-"))
        self.assertNotIn(number, {"PREV-2026-001", "PREV-2026-002"})

    def test_quote_path_stays_inside_the_private_folder(self):
        self.assertIsNone(approval_store.quote_path({"payload": {}}))
        self.assertIsNone(approval_store.quote_path({"payload": {"pdf": "../fuori.pdf"}}))
        folder = approval_store._quote_dir()
        pdf = folder / "prova.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")
        self.assertEqual(approval_store.quote_path({"payload": {"pdf": "prova.pdf"}}), pdf)

    def test_duplicate_requests_reuse_the_same_id(self):
        payload = {"to": "cliente@example.com", "subject": "Ciao", "body": "Testo"}
        first = approval_store.append_request("email", payload, client_name="Cliente")
        second = approval_store.append_request("email", payload, client_name="Cliente")
        self.assertEqual(first["id"], second["id"])

    def test_status_transitions_and_errors(self):
        request = approval_store.append_request(
            "email", {"to": "stati@example.com", "subject": "Ciao", "body": "Testo"},
        )
        with self.assertRaises(ValueError):
            approval_store.set_status(request["id"], "inviata")
        with self.assertRaises(ValueError):
            approval_store.set_status("inesistente", "approved")
        approved = approval_store.set_status(request["id"], "approved", result="inviata")
        self.assertEqual(approved["status"], "approved")
        with self.assertRaises(ValueError):
            approval_store.set_status(request["id"], "rejected")

    def test_calendar_validation_accepts_valid_and_rejects_invalid_windows(self):
        clean = approval_store.validate_calendar({
            "summary": " Call conoscitiva ",
            "start": "2030-05-01T10:00:00+02:00",
            "end": "2030-05-01T11:00:00+02:00",
            "attendees": ["cliente@example.com"],
            "location": "Meet",
            "description": "Prima call",
        })
        self.assertEqual(clean["summary"], "Call conoscitiva")
        self.assertEqual(clean["attendees"], ["cliente@example.com"])
        self.assertEqual(clean["timezone"], "Europe/Rome")
        with self.assertRaises(ValueError):
            approval_store.validate_calendar({"summary": "", "start": "2030-05-01T10:00:00+02:00",
                                              "end": "2030-05-01T11:00:00+02:00"})
        with self.assertRaises(ValueError):
            approval_store.validate_calendar({"summary": "X", "start": "2030-05-01T12:00:00+02:00",
                                              "end": "2030-05-01T11:00:00+02:00"})
        with self.assertRaises(ValueError):
            approval_store.validate_calendar({"summary": "X", "start": "2020-01-01T10:00:00+01:00",
                                              "end": "2020-01-01T11:00:00+01:00"})
        with self.assertRaises(ValueError):
            approval_store.validate_calendar({"summary": "X", "start": "2030-05-01T10:00:00+02:00",
                                              "end": "2030-05-20T11:00:00+02:00"})

    def test_listing_limits_and_pending_count(self):
        listing = approval_store.list_requests(1)
        self.assertLessEqual(len(listing["pending"]), 1)
        self.assertLessEqual(len(listing["recent"]), 1)
        self.assertIn("max_pending", listing["limits"])
        self.assertGreaterEqual(approval_store.pending_count(), 0)
        self.assertIsNone(approval_store.get_request(""))
        self.assertIsNone(approval_store.get_request("sconosciuta"))


if __name__ == "__main__":
    unittest.main()
