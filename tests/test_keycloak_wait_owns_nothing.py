"""Waiting for Keycloak never starts a container the running stack unit owns.

The stack unit runs Compose from its installed copy, the installer from the checkout, so `edge` (which mounts
./edge/nginx.conf) has a different config hash from each. An `up` of `edge` from the installer recreated it, and the
unit's --abort-on-container-exit stopped the whole stack and every desk (an upgrade on a box, 5 Nov 2026). The real
`wait_keycloak` runs here with systemctl, Compose, curl and python3 stubbed.
"""
import os
import re
import subprocess
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def function(name, path="deploy/setup.sh"):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return re.search(rf"^{name}\(\) \{{.*?^\}}\n", f.read(), re.S | re.M).group(0)


def wait_keycloak():
    return function("start_signin") + function("wait_keycloak")


def run(unit_state, fn=None, call="wait_keycloak"):
    calls = tempfile.mktemp()
    script = "\n".join([
        "set -euo pipefail", "DESKS_STATE=/nonexistent; T=/nonexistent",
        f'systemctl() {{ [[ "$*" == "is-active ekka-desks.service" ]] || return 9; echo {unit_state}; '
        f'[[ {unit_state} == active ]]; }}',
        f'P="record"; record() {{ echo "compose $*" >> {calls}; }}',
        "curl() { return 0; }", "python3() { return 0; }",
        fn or wait_keycloak(), call])
    r = subprocess.run(["/bin/bash", "-c", script], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    started = ""
    if os.path.exists(calls):
        with open(calls) as f:
            started = f.read()
    return r, started


class WaitingForKeycloakOwnsNothing(unittest.TestCase):
    def test_a_running_or_starting_unit_is_only_waited_for(self):
        for state in ("active", "activating", "reloading"):
            r, started = run(state)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(started, "", f"{state}: started {started!r}, which recreates the unit's edge")

    def test_a_stopped_failed_or_masked_unit_has_keycloak_started_here(self):
        for state in ("inactive", "failed", "masked"):
            r, started = run(state)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("up -d --wait keycloak-db keycloak edge", started, state)

    def test_the_init_step_starts_nothing_under_a_running_unit_either(self):
        for state, expect in (("active", ""), ("inactive", "compose --env-file /nonexistent/desks.env up -d keycloak-db keycloak edge\n")):
            r, started = run(state, call="start_signin")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(started, expect, state)

    def test_no_other_line_starts_keycloak_or_edge_from_the_checkout(self):
        with open(os.path.join(ROOT, "deploy/setup.sh"), encoding="utf-8") as f:
            code = [l for l in f if not l.lstrip().startswith("#")]
        ups = [l.strip() for l in code if re.search(r"\bup -d\b.*\bedge\b", l)]
        self.assertEqual(ups, ['$P --env-file "$DESKS_STATE/desks.env" up -d "$@" keycloak-db keycloak edge'], ups)

    def test_the_wait_before_this_fix_fails_the_first_case(self):
        old = subprocess.run(["git", "show", "ccdbca3:deploy/setup.sh"], cwd=ROOT, capture_output=True, text=True)
        if old.returncode != 0:
            self.skipTest("no history here")
        fn = re.search(r"^wait_keycloak\(\) \{.*?^\}\n", old.stdout, re.S | re.M).group(0)
        _, started = run("active", fn)
        self.assertIn("edge", started, "the planted old wait must be caught starting edge under a running unit")


if __name__ == "__main__":
    unittest.main()
