"""up.sh step_enroll: a refused Enclave creation stops the install with EKKA's own reason, never silently, and the
claim's transient unit must STOP, with the stop's exit status and the unit's end state both read (Codex review of #4).

Measured 2026-10-31 on a fresh server: the organization had no Enclave slot left, EKKA said so, and the installer
exited 1 with no message, because the id was looked for in a pipeline whose grep found nothing, and `set -e` ended
the script before the `die` line. Codex then ran the extracted function with `systemctl stop` and the status query
both failing after the claim appeared, and watched it print "this machine is claimed" with exit 0.

The function runs here with `ekka_in` stubbed to answer as EKKA does, and `systemctl` stubbed as systemd 255 answers:
`systemctl show` on a unit systemd no longer holds prints LoadState=not-found, ActiveState=inactive and exits 0.
Set UP_SH_UNDER_TEST to run these against another up.sh (the previous one fails the stop and query cases).
"""
import os
import re
import subprocess
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
UP_SH = os.environ.get("UP_SH_UNDER_TEST", os.path.join(ROOT, "up.sh"))
REFUSED = ("ekka enclave: this organization has no Enclave slots left.\n"
           "  see what holds them:  ekka enclave list\n  free one:             ekka enclave revoke <id>\n")
CREATED = "✓ Enclave created: 0d71efa2-91d3-418a-9c44-7cb734107dcf (desks walk)\n"


def functions(text):
    """step_enroll, and the two claim functions that sit together after enrolled() in up.sh."""
    import re as _re
    m = _re.search(r"^step_enroll\(\) \{[^\n]*\n.*?^\}\n", text, _re.S | _re.M)
    assert m, "step_enroll is not in up.sh"
    claim = ""
    if "\nclaim_state() {" in text:
        start = text.index("\nclaim_state() {") + 1
        end = text.index("\n}\n", text.index("claim_gone() {", start)) + 3
        claim = text[start:end]
    return claim + "\n" + m.group(0)


def run(create_reply, create_rc, mode="normal", unit_before="none"):
    """mode: how the stubbed systemd behaves.
    normal            the claim unit runs, is claimed at once, stops on request and is then garbage-collected (not-found)
    failed-exit       as normal, but the stopped unit lingers in state failed until reset-failed
    stop-fails        `systemctl stop` exits 5 (Codex's reproduction)
    show-fails        every `systemctl show` of a loaded unit fails with no output
    still-active      `systemctl stop` exits 0 but the unit stays active
    never-claims      the claim unit exits (inactive) without ever writing the enrolled file
    """
    with open(UP_SH, encoding="utf-8") as f:
        text = f.read()
    state = tempfile.mkdtemp()
    calls = os.path.join(state, "calls")
    unit = os.path.join(state, "unit")
    with open(unit, "w") as f:
        f.write(unit_before)
    script = f"""
set -euo pipefail
DESKS_STATE={state}; DONE={state}/done; EKKA_ORG=ekkawalk; EKKA_ENV=prod; MODE_ARGS="server desks.example.test"
ENCLAVE_HOME={state}/enclave-home
mkdir -p "$DONE"
ok() {{ echo "ok: $*"; }}
say() {{ :; }}
die() {{ echo "die: $*" >&2; exit 1; }}
stop_for_person() {{ echo "stop: $1" >&2; exit 0; }}
sleep() {{ :; }}
enrolled() {{ [[ -f {state}/claimed ]]; }}
systemd-run() {{ echo "systemd-run $*" >> {calls}
  case "$MODE" in
    never-claims) echo stopped > {unit} ;;
    *) echo running > {unit}; touch {state}/claimed ;;
  esac; }}
systemctl() {{ echo "systemctl $*" >> {calls}
  local u; u=$(cat {unit})
  if [[ $MODE == show-fails && $1 == show && $u != none ]]; then return 1; fi   # (bash 3.2 on macOS has no ;;&)
  case "$MODE:$*" in
    *:"show -p LoadState --value ekka-enroll.service") [[ $u == none ]] && echo not-found || echo loaded ;;
    *:"show -p ActiveState --value ekka-enroll.service") case $u in running) echo active ;; failed) echo failed ;; *) echo inactive ;; esac ;;
    stop-fails:"stop ekka-enroll.service") return 5 ;;
    still-active:"stop ekka-enroll.service") return 0 ;;
    failed-exit:"stop ekka-enroll.service") echo failed > {unit} ;;
    *:"stop ekka-enroll.service") echo none > {unit} ;;
    *:"reset-failed ekka-enroll.service") echo none > {unit} ;;
    *) return 0 ;;
  esac; }}
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
{functions(text)}
step_enroll
"""
    env = dict(os.environ, CREATE_REPLY=create_reply, CREATE_RC=str(create_rc), MODE=mode)
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

    def test_a_refusal_with_an_id_is_never_claimed(self):
        r = run(REFUSED + "request: 0d71efa2-91d3-418a-9c44-7cb734107dcf\n", 1)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no Enclave slots left", r.stderr)
        self.assertNotIn("systemd-run", " ".join(r.calls))
        self.assertNotIn("claimed", r.stdout)

    def test_a_created_enclave_is_claimed(self):
        r = run(CREATED, 0)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ok: Enclave 0d71efa2-91d3-418a-9c44-7cb734107dcf created", r.stdout)
        self.assertTrue(any(c.startswith("systemd-run --unit=ekka-enroll --uid=ekka --gid=ekka") and
                            c.endswith("/usr/local/bin/ekka enclave start 0d71efa2-91d3-418a-9c44-7cb734107dcf --custody file") for c in r.calls), r.calls)
        self.assertIn("systemctl stop ekka-enroll.service", r.calls, "stopped by name once claimed")
        self.assertIn("ok: this machine is claimed", r.stdout)

    def test_a_stopped_unit_that_lingers_as_failed_is_still_a_stopped_unit(self):
        r = run(CREATED, 0, mode="failed-exit")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ok: this machine is claimed", r.stdout)
        self.assertIn("systemctl reset-failed ekka-enroll.service", r.calls)

    def test_a_claim_unit_left_by_an_earlier_run_is_stopped_first(self):
        r = run(CREATED, 0, unit_before="running")
        self.assertEqual(r.returncode, 0, r.stderr)
        stops = [i for i, c in enumerate(r.calls) if c == "systemctl stop ekka-enroll.service"]
        start = next(i for i, c in enumerate(r.calls) if c.startswith("systemd-run"))
        self.assertTrue(stops and stops[0] < start, r.calls)

    def test_a_stop_that_fails_is_a_refusal_not_a_claimed_machine(self):
        """Codex's reproduction: stop exits 5 after the claim appeared; the previous up.sh said 'claimed' and exited 0."""
        r = run(CREATED, 0, mode="stop-fails")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("could not be stopped", r.stderr)
        self.assertNotIn("claimed", r.stdout)

    def test_a_state_query_that_fails_is_a_refusal(self):
        r = run(CREATED, 0, mode="show-fails")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("could not report the claim", r.stderr)
        self.assertNotIn("claimed", r.stdout)

    def test_a_unit_still_active_after_stop_is_a_refusal(self):
        r = run(CREATED, 0, mode="still-active")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("still 'active' after the stop", r.stderr)
        self.assertNotIn("claimed", r.stdout)

    def test_a_claim_that_exits_without_enrolling_is_a_refusal(self):
        r = run(CREATED, 0, mode="never-claims")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("did not finish enrolling", r.stderr)
        self.assertNotIn("claimed", r.stdout)


if __name__ == "__main__":
    unittest.main()
