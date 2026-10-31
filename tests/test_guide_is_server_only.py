"""The customer guide is server-only (owner, 2026-10-22: "we are taking out local mac version completely from this
guide; we will work on local mac version at some later date").

No guide page shows a local install, the Mac helper or a mounted checkout, and no page links one of the removed
pages. "Mac" appears only where a person on a Mac connects to the server (docs/SERVER.md, step 6) and where the guide
says a Mac cannot be the server. The local-install code itself was removed .
"""
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
GUIDE = ["README.md"] + [os.path.join("docs", f) for f in sorted(os.listdir(os.path.join(ROOT, "docs"))) if f.endswith(".md")]
REMOVED = ("docs/MACOS.md", "docs/ON-ONE-MACHINE.md", "docs/LOCAL-FOLDERS.md", "docs/TEAM-LOCAL-TEST.md")
FORBIDDEN = re.compile(
    r"up\.sh local\b|local[- ]install|local-desk|--host-mount|--no-host-mount|macos_local|Mac helper|"
    r"desks\.localhost|mounted (checkout|project|local)|developer checkout|DESKS_HOST_MOUNTS|"
    r"MACOS\.md|ON-ONE-MACHINE|LOCAL-FOLDERS|TEAM-LOCAL-TEST|\[On one machine\]",
    re.I)
MAC = re.compile(r"\bmac(os|book)?\b", re.I)
# The only places a Mac belongs in a server-only guide: a person's own computer connecting to the server.
MAC_ALLOWED = {
    "docs/SERVER.md": ("A Mac or Windows computer cannot be the server", "# macOS",
                       "| macOS | `security add-trusted-cert -r trustRoot -p ssl -s "),
    "docs/OPERATIONS.md": ("| macOS | `security add-trusted-cert -r trustRoot -p ssl -s ",),
}


def problems(pages):
    found = []
    for name, text in pages:
        for number, line in enumerate(text.splitlines(), 1):
            if FORBIDDEN.search(line):
                found.append(f"{name}:{number}: {FORBIDDEN.search(line).group(0)}")
            elif MAC.search(line) and not any(a in line for a in MAC_ALLOWED.get(name, ())):
                found.append(f"{name}:{number}: {MAC.search(line).group(0)}")
    return found


def guide():
    pages = []
    for name in GUIDE:
        with open(os.path.join(ROOT, name), encoding="utf-8") as f:
            pages.append((name, f.read()))
    return pages


class TheGuideIsServerOnly(unittest.TestCase):
    def test_no_page_shows_a_local_install_or_the_mac_helper(self):
        self.assertEqual(problems(guide()), [])

    def test_the_removed_pages_are_gone(self):
        for name in REMOVED:
            self.assertFalse(os.path.exists(os.path.join(ROOT, name)), name)

    def test_a_person_on_a_mac_can_still_connect_to_the_server(self):
        server = dict(guide())["docs/SERVER.md"]
        self.assertIn("brew install cosign          # macOS", server)
        # Trusted for websites under the server's name, as the person: never every purpose, system-wide .
        self.assertIn("| macOS | `security add-trusted-cert -r trustRoot -p ssl -s <server name> -k ~/Library/Keychains/"
                      "login.keychain-db desks-ca.pem`", server)
        self.assertNotIn("-k /Library/Keychains/System.keychain", server)

    def test_planted_reds_are_caught(self):
        for planted in ("Install it on your Mac with `sudo ./up.sh local`.",
                        "See [On one machine](ON-ONE-MACHINE.md).",
                        "Mounted checkouts keep the preserving prompt.",
                        "Your MacBook runs the desks."):
            self.assertTrue(problems([("docs/X.md", planted)]), planted)
        self.assertTrue(problems([("docs/SERVER.md", "Run the desks on macOS too.")]),
                        "a Mac line in SERVER.md outside the allowed client rows")
        removal = "## Removing an earlier Mac installation\npython3 tools/macos_local.py destroy-local --confirm\n"
        self.assertTrue(problems([("docs/OPERATIONS.md", removal)]), "the Mac helper is gone; no page may name it")


if __name__ == "__main__":
    unittest.main()
