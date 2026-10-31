"""No file carries anybody's email address: only RFC 2606 reserved ones (example.com, .net, .org) and the product's own domain."""
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
MAIL = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.I)
ALLOWED = re.compile(r"@(example\.(com|net|org)|ekka\.ai|users\.noreply\.github\.com)$", re.I)


class NoPrivateAddresses(unittest.TestCase):
    def test_every_address_is_reserved_or_ours(self):
        found = {}
        for top, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d != ".git"]
            for name in files:
                path = os.path.join(top, name)
                try:
                    with open(path, encoding="utf-8") as f:
                        text = f.read()
                except (UnicodeDecodeError, OSError):
                    continue
                for m in MAIL.findall(text):
                    if not ALLOWED.search(m):
                        found.setdefault(m, os.path.relpath(path, ROOT))
        self.assertEqual(found, {}, "use an @example.com address (address: file)")


if __name__ == "__main__":
    unittest.main()
