"""The walk says which claude-code profile is in force, and checks the gateway holds it.

The shipped profile takes an Anthropic API key (owner's ruling, 2026-10-11). An install's own profile in
providers.local wins and is never replaced ; the walk names it and checks the gateway shows its credential.

This runs the walk's own lines, cut out of tools/walk.sh with its own `t`, against `ekka desk profile show`
answers in the shape the CLI prints them (`credential   <name>, sent as <style>, required`).
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WALK = (ROOT / "tools/walk.sh").read_text()
BLOCK = re.search(r"^# ── which claude-code profile is in force ──\n.*?^# ── end of the claude-code profile check ──\n",
                  WALK, re.M | re.S).group(0)
T = re.search(r"^t\(\) \{.*?^\}\n", WALK, re.M | re.S).group(0)


def shown(credential_line):
    return ("kind        claude-code\nname        Claude Code\ncategory    agent\n"
            f"credential  {credential_line}\nnetwork     an agent may reach api.anthropic.com:443 (read and write)\n")


API_KEY = shown("api_key, sent as header, required")
OTHER = shown("oauth_token, sent as bearer, required")
OWN = "id: claude-code\ncredentials:\n  - name: oauth_token\n    required: true\n"


def walk(gateway_says, own=None):
    with tempfile.TemporaryDirectory() as state:
        if own is not None:
            os.makedirs(f"{state}/providers.local")
            Path(f"{state}/providers.local/claude-code.yaml").write_text(own)
        script = ("set -uo pipefail\nPASS=0; FAIL=0; FAILED=(); g=; r=; d=; n=\n" + T +
                  'P() { printf "%s" "$GATEWAY_SAYS"; }\n' + BLOCK + 'echo "pass=$PASS fail=$FAIL"\n')
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                              env={**os.environ, "DESKS_STATE": state, "GATEWAY_SAYS": gateway_says}).stdout


class TheWalkNamesTheClaudeProfile(unittest.TestCase):
    def test_the_shipped_profile_takes_an_api_key(self):
        out = walk(API_KEY)
        self.assertIn("pass=1 fail=0", out)
        self.assertIn("an Anthropic API key", out)

    def test_planted_a_gateway_left_on_another_credential_fails(self):
        out = walk(OTHER)
        self.assertIn("pass=0 fail=1", out, out)

    def test_an_install_s_own_profile_is_named_and_checked_not_judged(self):
        out = walk(OTHER, own=OWN)
        self.assertIn("pass=1 fail=0", out, out)
        self.assertIn("this install's own profile", out)
        self.assertIn("providers.local/claude-code.yaml", out)
        self.assertIn("credential oauth_token", out)

    def test_planted_an_own_profile_the_gateway_does_not_hold_fails(self):
        out = walk(API_KEY, own=OWN)
        self.assertIn("pass=0 fail=1", out, out)

    def test_planted_an_own_profile_with_no_credential_fails(self):
        out = walk(API_KEY, own="id: claude-code\n")
        self.assertIn("pass=0 fail=1", out, out)
        self.assertIn("credential unreadable", out)


if __name__ == "__main__":
    unittest.main()
