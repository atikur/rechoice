from pathlib import Path
import tempfile
import unittest

from scripts.check_hosted_demo import NoRedirect, read_access


class HostedAccessTests(unittest.TestCase):
    def test_access_credentials_require_a_private_file_and_the_exact_https_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "access.txt"
            path.write_text("URL: https://rechoice.example.com\nHTTP username: test\n"
                            "HTTP password: test-only\nJudge code: test-only\n")
            path.chmod(0o600)
            _, url = read_access(path, "https://rechoice.example.com/")
            self.assertEqual(url, "https://rechoice.example.com")
            for target in ("http://rechoice.example.com", "https://other.example.com",
                           "https://rechoice.example.com@other.example.com", "https://rechoice.example.com/path"):
                with self.subTest(target=target), self.assertRaisesRegex(ValueError, "different origin"):
                    read_access(path, target)
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "mode 600"):
                read_access(path)

    def test_authenticated_preflight_cannot_forward_credentials_through_a_redirect(self):
        with self.assertRaisesRegex(ValueError, "must not redirect"):
            NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://other.example.com")
