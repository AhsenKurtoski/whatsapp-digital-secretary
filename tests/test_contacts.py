import unittest

import support  # noqa: F401  (sets the isolated environment)
import contacts


class ContactsTests(unittest.TestCase):
    def test_self_test_reads_real_history_shape(self):
        contacts.self_test()

    def test_empty_summary_mentions_business(self):
        text = contacts.summary_text([], {"business_name": "Officina Demo"})
        self.assertIn("Officina Demo", text)
        self.assertIn("Nessun contatto", text)

    def test_summary_ranks_hot_leads_first(self):
        rows = [
            {
                "phone": "+393331112222", "name": "Mario", "messages_in": 4, "messages_out": 3,
                "last_at": "2026-11-04T18:00:00+01:00", "score": 80, "intent": "preventivo",
                "last_messages": [{"text": "Vorrei un preventivo per il sito"}],
            },
            {
                "phone": "+393334445555", "name": "", "messages_in": 1, "messages_out": 1,
                "last_at": "2026-11-04T19:00:00+01:00", "score": 10, "intent": "info",
                "last_messages": [{"text": "Che orari avete?"}],
            },
        ]
        text = contacts.summary_text(rows, {"business_name": "Demo"}, {"+393331112222": 2})
        self.assertIn("Mario", text)
        self.assertLess(text.index("Mario"), text.index("+393334445555"))
        self.assertIn("2 da approvare", text)
        self.assertLessEqual(len(text), 1750)


if __name__ == "__main__":
    unittest.main()
