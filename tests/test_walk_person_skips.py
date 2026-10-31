"""A by-the-book SERVER.md install walks with no FAIL from a missing person sign-in .

A check run as the person (P, Pin) SKIPs with one hint when ekka says this server has no person sign-in. Only the
two exact messages skip: any other error from the same read still FAILs. The functions are taken from walk.sh itself.
"""
import os
import re
import subprocess
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
NO_BACKEND = "ekka desk profile list: no desk backend on this machine (/home/u/.ekka/desk-backend.json does not exist)"
NO_SIGN_IN = ("a sign-in is needed and this command has no terminal to ask in; run `ekka desk login` in a terminal, "
              "then run it again")


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def fn(text, name):
    """One function from walk.sh: a one-liner `name() { ...; }`, or the lines up to its closing brace."""
    one = re.search(rf"^{name}\(\) \{{[^\n]*\}}\n", text, re.M)
    return one.group(0) if one else re.search(rf"^{name}\(\) \{{.*?^\}}\n", text, re.S | re.M).group(0)


def bash(script, **env):
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30,
                         env=dict(os.environ, WHO="walker", **env))
    return out.stdout


class PersonChecksSkipWithOneHint(unittest.TestCase):
    def setUp(self):
        self.walk = read("tools", "walk.sh")
        self.helpers = "".join(fn(self.walk, n) for n in ("skip_leg", "t", "no")) + \
            "PASS=0; FAIL=0; SKIP=0; FAILED=(); SKIPPED=(); g=; r=; y=; d=; n=\n"

    def missing(self, output, walk=None):
        return bash(fn(walk or self.walk, "person_missing") + 'person_missing "$PROBE"', PROBE=output).strip()

    def test_only_the_two_setup_messages_give_a_hint(self):
        self.assertIn("docs/OPERATIONS.md, Administer desks from the server itself", self.missing(NO_BACKEND))
        self.assertIn("as walker run: ekka desk login", self.missing(NO_SIGN_IN))
        self.assertEqual(self.missing("  claude-code   api_key\n  github   token\n"), "")
        self.assertEqual(self.missing("ekka desk profile list: the gateway did not answer (connection refused)"), "",
                         "a real failure is never a missing sign-in")

    def run_check(self, no_person, check):
        script = self.helpers + 'P() { echo "error: gateway down"; return 1; }\n' + check + \
            '\necho "PASS=$PASS FAIL=$FAIL SKIP=$SKIP"'
        return bash(script, NO_PERSON=no_person)

    def test_person_checks_skip_and_everything_else_still_runs(self):
        hint = "no person sign-in on this server; set it up once: docs/OPERATIONS.md, Administer desks from the server itself"
        out = self.run_check(hint, 't "profiles" claude-code P desk profile list')
        self.assertIn("SKIP", out); self.assertIn(hint, out); self.assertIn("PASS=0 FAIL=0 SKIP=1", out)
        out = self.run_check(hint, 'no "upload is refused" "not available" P desk upload d f')
        self.assertIn("PASS=0 FAIL=0 SKIP=1", out)
        self.assertIn("PASS=0 FAIL=1 SKIP=0", self.run_check(hint, 't "the Enclave answers" org false'),
                      "a check not run as the person is never skipped")
        self.assertIn("PASS=0 FAIL=1 SKIP=0", self.run_check("", 't "profiles" claude-code P desk profile list'),
                      "with a person signed in, a failing read still FAILs")

    def test_the_probe_runs_before_the_first_person_check(self):
        probe = self.walk.index('NO_PERSON=$(person_missing "$(P desk profile list)")')
        self.assertLess(probe, self.walk.index('t "the person\'s ekka points at this server"'))

    def test_planted_reds_are_caught(self):
        loose = self.walk.replace("grep -q -F 'a sign-in is needed'", "grep -q -E 'error|sign-in'")
        self.assertNotEqual(self.missing("ekka desk profile list: error: connection refused", loose), "",
                            "PLANTED: a loose match turns a real failure into a SKIP")
        unguarded = self.helpers.replace('[[ -n ${NO_PERSON:-} && ( $1 == P || $1 == Pin ) ]]', '[[ -n ${NO_PERSON:-} ]]')
        self.assertNotEqual(unguarded, self.helpers, "the plant applied")
        script = unguarded + '\nt "the Enclave answers" org false\necho "SKIP=$SKIP"'
        self.assertIn("SKIP=1", bash(script, NO_PERSON="x"), "PLANTED: skipping every check would hide real failures")


class TheServerGuideCoversTheSignIn(unittest.TestCase):
    def test_server_md_links_the_exact_section(self):
        server = read("docs", "SERVER.md")
        self.assertIn("(OPERATIONS.md#administer-desks-from-the-server-itself)", server)
        self.assertIn("\n## Administer desks from the server itself\n", read("docs", "OPERATIONS.md"))
        self.assertIn("**You need a Linux server.**", server)
        self.assertIn("8 GB", server)

    def test_no_placeholder_clone_address_in_the_docs(self):
        for name in sorted(os.listdir(os.path.join(ROOT, "docs"))) + ["../README.md"]:
            if name.endswith(".md"):
                self.assertNotIn("<this repository>", read("docs", name), name)
