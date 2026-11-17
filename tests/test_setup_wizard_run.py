"""Il wizard interattivo, pilotato con risposte scriptate: nessun input reale."""
import unittest
from unittest import mock

import support  # noqa: F401

import setup_wizard


def scripted(answers):
    values = list(answers)

    def ask(_prompt=""):
        if not values:
            raise EOFError
        return values.pop(0)

    return ask


class WizardRunTests(unittest.TestCase):
    def test_declining_without_whatsapp_writes_nothing(self):
        saved = []
        out = []
        with mock.patch.object(setup_wizard, "whatsapp_status", return_value={"ok": False, "detail": "gateway spento"}):
            ok = setup_wizard.run({"google_account": ""}, saved.append,
                                  ask=scripted(["", "", "n"]), out=out.append)
        self.assertFalse(ok)
        self.assertEqual(saved, [])
        self.assertTrue(any("WhatsApp non pronto" in line for line in out))
        self.assertTrue(any("annullata" in line for line in out))

    def test_valid_key_is_saved_only_after_confirmation(self):
        saved = []
        out = []
        with mock.patch.object(setup_wizard, "whatsapp_status", return_value={"ok": True}), \
                mock.patch.object(setup_wizard, "validate_ai_key", return_value={"ok": True, "models": 3}), \
                mock.patch.object(setup_wizard, "send_test_email", return_value={"ok": True}), \
                mock.patch.object(setup_wizard, "_write_secret") as write_secret:
            ok = setup_wizard.run({"google_account": "titolare@example.com"}, saved.append,
                                  ask=scripted(["chiave-di-prova", "", "", ""]), out=out.append)
        self.assertTrue(ok)
        self.assertEqual(len(saved), 1)
        write_secret.assert_called_once()
        self.assertTrue(any("Email inviata" in line for line in out))
        self.assertTrue(any("Configurazione salvata" in line for line in out))

    def test_invalid_key_is_reported_and_skipped(self):
        saved = []
        out = []
        with mock.patch.object(setup_wizard, "whatsapp_status", return_value={"ok": True}), \
                mock.patch.object(setup_wizard, "validate_ai_key", return_value={"ok": False, "error": "401"}), \
                mock.patch.object(setup_wizard, "_write_secret") as write_secret:
            ok = setup_wizard.run({}, saved.append, ask=scripted(["chiave-sbagliata", "", "", "no"]), out=out.append)
        self.assertFalse(ok)
        self.assertEqual(saved, [])
        write_secret.assert_not_called()
        self.assertTrue(any("401" in line for line in out))

    def test_eof_during_prompts_is_safe(self):
        out = []
        with mock.patch.object(setup_wizard, "whatsapp_status", return_value={"ok": True}):
            ok = setup_wizard.run({"google_account": ""}, lambda _data: None, ask=scripted([]), out=out.append)
        self.assertTrue(ok)
        self.assertTrue(any("Nessun indirizzo" in line for line in out))


if __name__ == "__main__":
    unittest.main()
