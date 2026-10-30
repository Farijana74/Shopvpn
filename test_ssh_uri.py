import base64
import unittest
from urllib.parse import urlsplit, unquote
from ssh_uri import build_ssh_uri


class SSHUriTests(unittest.TestCase):
    def test_uri_has_base64_credentials(self):
        uri = build_ssh_uri("root", "ZP9QUTS47qAg#l4U", "31.56.196.63", 24720, "Fff")
        self.assertEqual(uri, "ssh://cm9vdDpaUDlRVVRTNDdxQWcjbDRV@31.56.196.63:24720#Fff")
        parsed = urlsplit(uri)
        self.assertEqual(parsed.hostname, "31.56.196.63")
        self.assertEqual(parsed.port, 24720)
        self.assertEqual(base64.b64decode(parsed.username).decode(), "root:ZP9QUTS47qAg#l4U")
        self.assertEqual(unquote(parsed.fragment), "Fff")

    def test_ipv6_is_bracketed(self):
        parsed = urlsplit(build_ssh_uri("user", "password1234567890", "2001:db8::1", 2200, "v6"))
        self.assertEqual(parsed.hostname, "2001:db8::1")
        self.assertEqual(parsed.port, 2200)

    def test_missing_credentials_fail_closed(self):
        with self.assertRaises(ValueError):
            build_ssh_uri("user", "", "127.0.0.1", 22, "x")


if __name__ == "__main__":
    unittest.main()
