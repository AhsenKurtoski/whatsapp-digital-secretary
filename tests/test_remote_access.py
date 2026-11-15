import unittest

import support  # noqa: F401
import remote_access


class RemoteAccessTests(unittest.TestCase):
    def test_builds_private_local_forward_without_a_shell(self):
        command = remote_access.build_ssh_command(
            "host.tailnet.ts.net", user="owner", port=8787
        )
        self.assertEqual(command[0], "ssh")
        self.assertIn("-N", command)
        self.assertIn("-T", command)
        self.assertEqual(
            command[command.index("-L") + 1],
            "127.0.0.1:8787:127.0.0.1:8787",
        )
        self.assertEqual(command[-1], "owner@host.tailnet.ts.net")
        self.assertNotIn("-R", command)
        self.assertNotIn("0.0.0.0", command)

    def test_rejects_option_injection_and_bad_ports(self):
        for host in ("", "-oProxyCommand=evil", "host name", "host\nname", "host/path"):
            with self.assertRaises(ValueError, msg=host):
                remote_access.build_ssh_command(host)
        for port in (0, 65536, "abc"):
            with self.assertRaises(ValueError, msg=str(port)):
                remote_access.build_ssh_command("host.tailnet.ts.net", port=port)
        with self.assertRaises(ValueError):
            remote_access.build_ssh_command("host.tailnet.ts.net", user="-oProxyCommand=evil")

    def test_dashboard_url_is_loopback_only(self):
        self.assertEqual(remote_access.dashboard_url(), "http://127.0.0.1:8787")
        self.assertEqual(remote_access.dashboard_url(9000), "http://127.0.0.1:9000")

    def test_self_test(self):
        self.assertEqual(remote_access.main(["--self-test"]), 0)


if __name__ == "__main__":
    unittest.main()
