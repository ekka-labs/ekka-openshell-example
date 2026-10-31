"""The desk image's Claude Code download : pinned binary, retried through 429, checked against a pinned hash.

On 2026-10-22 Anthropic's download server answered 429 for over 20 minutes and claude.ai/install.sh gave up on the
first one, so no server could install. The Dockerfile is read as text; the real build was measured on the integration test server.
"""
import os
import re
import unittest

DOCKERFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "external", "desk-base", "desk", "image", "Dockerfile")


def dockerfile():
    with open(DOCKERFILE, encoding="utf-8") as f:
        return f.read()


class ClaudeCodeIsPinnedRetriedAndChecked(unittest.TestCase):
    def test_no_install_script(self):
        self.assertNotIn("claude.ai/install.sh", dockerfile())

    def test_each_architecture_has_a_pinned_sha256(self):
        text = dockerfile()
        for arch in ("X64", "ARM64"):
            self.assertRegex(text, rf"ARG CLAUDE_CODE_SHA256_{arch}=[0-9a-f]{{64}}\n")

    def test_the_download_retries_through_429(self):
        run = re.search(r"downloads\.claude\.ai/claude-code-releases/\$\{CLAUDE_CODE_VERSION\}/\$\{platform\}/claude", dockerfile())
        self.assertIsNotNone(run)
        text = dockerfile()
        for flag in ("--retry 30", "--retry-all-errors", "--retry-delay 10"):
            self.assertIn(flag, text)

    def test_the_hash_decides_what_is_installed(self):
        text = dockerfile()
        check = text.index('sha256sum -c -')
        self.assertLess(check, text.index("install -m 0755 /tmp/claude /usr/local/bin/claude"))

    def test_planted_reds_are_caught(self):
        text = dockerfile()
        for plant in (text.replace(" --retry-all-errors", ""), text.replace('echo "${want}  /tmp/claude" | sha256sum -c -; \\\n', "")):
            self.assertNotEqual(plant, text)
            failed = ("--retry-all-errors" not in plant) or ("sha256sum -c -" not in plant)
            self.assertTrue(failed)


if __name__ == "__main__":
    unittest.main()
