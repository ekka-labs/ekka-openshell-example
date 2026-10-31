"""up.sh step_enroll: a refused Enclave creation stops the install with EKKA's own reason, never silently.

Measured 2026-10-31 on a fresh server: the organization had no Enclave slot left, EKKA said so, and the installer
exited 1 with no message, because the id was looked for in a pipeline whose grep found nothing, and `set -e` ended
the script before the `die` line. The function runs here with `ekka_in` stubbed to answer as EKKA does.
"""
import os
import re
import subprocess
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
REFUSED = ("ekka enclave: this organization has no Enclave slots left.\n"
           "  see what holds them:  ekka enclave list\n  free one:             ekka enclave revoke <id>\n")
CREATED = "✓ Enclave created: 0d71efa2-91d3-418a-9c44-7cb734107dcf (desks walk)\n"


def run(create_reply, create_rc):
    with open(os.path.join(ROOT, "up.sh"), encoding="utf-8") as f:
        text = f.read()
    fn = re.search(r"^step_enroll\(\) \{[^\n]*\n.*?^\}\n", text, re.S | re.M)
    assert fn, "step_enroll is not in up.sh"
    state = tempfile.mkdtemp()
    calls = os.path.join(state, "calls")
    script = f"""
set -euo pipefail
DESKS_STATE={state}; DONE={state}/done; EKKA_ORG=ekkawalk; EKKA_ENV=prod; MODE_ARGS="server desks.example.test"
ENCLAVE_HOME={state}/enclave-home
mkdir -p "$DONE"
ok() {{ echo "ok: $*"; }}
say() {{ :; }}
die() {{ echo "die: $*" >&2; exit 1; }}
stop_for_person() {{ echo "stop: $1" >&2; exit 0; }}
n=0
enrolled() {{ n=$((n+1)); [[ -f {state}/claimed ]]; }}
systemd-run() {{ echo "systemd-run $*" >> {calls}; touch {state}/claimed; }}   # the transient claim unit, stubbed: claimed at once
systemctl() {{ echo "systemctl $*" >> {calls}; case "$*" in "is-active --quiet ekka-enroll.service") return 1 ;; *) return 0 ;; esac; }}
journalctl() {{ echo "journal of the claim"; }}
ekka_in() {{
  echo "$*" >> {calls}
  case "$*" in
    whoami) echo "Signed in as someone@example.com" ;;
    "org list") echo "  * ekkawalk  (walk)  owner" ;;
    "org switch "*) : ;;
    "enclave create "*) printf '%s' "$CREATE_REPLY"; return "$CREATE_RC" ;;
    *) echo "unexpected: $*" >&2; return 1 ;;
  esac
}}
{fn.group(0)}
step_enroll
"""
    env = dict(os.environ, CREATE_REPLY=create_reply, CREATE_RC=str(create_rc))
    out = subprocess.run(["bash", "-c", script], cwd=ROOT, capture_output=True, text=True, env=env, timeout=60)
    out.calls = open(calls).read().splitlines() if os.path.exists(calls) else []
    return out


class Enroll(unittest.TestCase):
    def test_a_refused_creation_stops_with_ekkas_reason(self):
        r = run(REFUSED, 1)
        self.assertEqual(r.returncode, 1)
        self.assertIn("die: EKKA did not create an Enclave", r.stderr)
        self.assertIn("no Enclave slots left", r.stderr, "EKKA's own sentence reaches the person")
        self.assertIn("ekka enclave revoke", r.stderr, "and so does its remedy")
        self.assertNotIn("systemd-run", " ".join(r.calls), "nothing was started")

    def test_a_created_enclave_is_claimed(self):
        r = run(CREATED, 0)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ok: Enclave 0d71efa2-91d3-418a-9c44-7cb734107dcf created", r.stdout)
        self.assertTrue(any(c.startswith("systemd-run --unit=ekka-enroll --uid=ekka --gid=ekka") and
                            c.endswith("/usr/local/bin/ekka enclave start 0d71efa2-91d3-418a-9c44-7cb734107dcf --custody file") for c in r.calls), r.calls)
        self.assertIn("systemctl stop ekka-enroll.service", r.calls, "stopped by name once claimed")
        self.assertIn("ok: this machine is claimed", r.stdout)


if __name__ == "__main__":
    unittest.main()
