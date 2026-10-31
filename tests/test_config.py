import os
import unittest

import support  # noqa: F401  (sets the isolated environment)
import config


class ConfigTests(unittest.TestCase):
    def test_reads_business_config(self):
        data = config.load()
        self.assertEqual(data["business_name"], "Attività Demo")
        self.assertEqual(data["owner_name"], "Mario Rossi")
        self.assertEqual(data["dashboard_port"], 8787)

    def test_env_overrides_file(self):
        os.environ["BUSINESS_NAME"] = "Override Srl"
        try:
            self.assertEqual(config.load()["business_name"], "Override Srl")
        finally:
            os.environ.pop("BUSINESS_NAME", None)

    def test_whatsapp_link_uses_digits(self):
        self.assertEqual(config.wa_link(), "https://wa.me/393331112222")

    def test_timezone_is_valid(self):
        self.assertEqual(config.timezone_name(), "Europe/Rome")

    def test_node_and_cli_are_strings(self):
        self.assertTrue(isinstance(config.node_bin(), str) and config.node_bin())
        self.assertTrue(isinstance(config.openclaw_cli(), str) and config.openclaw_cli())


if __name__ == "__main__":
    unittest.main()
