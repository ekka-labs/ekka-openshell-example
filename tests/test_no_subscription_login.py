"""No shipped file teaches a personal Claude subscription sign-in.

The owner's ruling of 2026-10-11: "The agent's model is connected by API key ONLY in docs and up.sh; never show a
personal Claude subscription login." The Mac path taught one until it moved to the API key. This reads every file
git tracks, so a doc, a profile, a script or a test that brings it back fails here, by file and line.

An install may still keep its own sign-in in /var/lib/ekka-agent-desks/providers.local/ . That folder is
the install's private configuration and is never in this repository.

The words are built from pieces, so this file does not find itself.
"""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = [
    "setup" + "-token",                 # the command that makes a subscription token
    "CLAUDE_CODE_" + "OAUTH_TOKEN",     # the variable it is read from
    "claude-" + "subscription",         # the profile the Mac path shipped
]
# The vendored contracts are another repository's text, pinned; nothing there is shipped instructions.
SKIP = ("vendor/",)


def tracked():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [p for p in out.decode().split("\0") if p and not p.startswith(SKIP)]


def findings(files, read=lambda p: (ROOT / p).read_bytes()):
    found = []
    for path in files:
        try:
            text = read(path).decode("utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for word in FORBIDDEN:
                if word.lower() in line.lower():
                    found.append(f"{path}:{n}: {word}")
    return found


class NoSubscriptionLogin(unittest.TestCase):
    def test_no_tracked_file_teaches_a_subscription_sign_in(self):
        self.assertEqual(findings(tracked()), [])

    def test_planted_each_word_is_found(self):
        for word in FORBIDDEN:
            self.assertEqual(findings(["docs/X.md"], read=lambda p: f"run claude {word} first".encode()),
                             [f"docs/X.md:1: {word}"])

    def test_planted_a_word_in_another_case_is_found(self):
        self.assertTrue(findings(["a.sh"], read=lambda p: FORBIDDEN[1].lower().encode()))

    def test_the_scan_sees_the_whole_repository(self):
        files = tracked()
        self.assertIn("docs/SERVER.md", files)
        self.assertIn("tools/desk_plans.py", files)
        self.assertIn("up.sh", files)


if __name__ == "__main__":
    unittest.main()
