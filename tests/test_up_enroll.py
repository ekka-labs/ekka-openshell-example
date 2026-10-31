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


def run(create_reply, create_rc, mode="normal", unit_before="none", signed_in=True, interactive="", admin_email="admin@example.com", login_works=True):
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
    if signed_in:
        open(os.path.join(state, "signed"), "w").close()
    script = f"""
set -euo pipefail
DESKS_STATE={state}; DONE={state}/done; EKKA_ORG=ekkawalk; EKKA_ENV=prod; MODE_ARGS="server desks.example.test"
INTERACTIVE="$INTERACTIVE"; ADMIN_EMAIL="$ADMIN_EMAIL"
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
    whoami) if [[ -f {state}/signed ]]; then echo "Signed in as someone@example.com"; else echo "ekka whoami: not signed in"; return 1; fi ;;
    login*) [[ "$LOGIN_WORKS" == 1 ]] && touch {state}/signed; : ;;
    "org list") echo "  * ekkawalk  (walk)  owner" ;;
    "org switch "*) : ;;
    "enclave create "*) printf '%s' "$CREATE_REPLY"; return "$CREATE_RC" ;;
    *) echo "unexpected: $*" >&2; return 1 ;;
  esac
}}
{functions(text)}
step_enroll
"""
    env = dict(os.environ, CREATE_REPLY=create_reply, CREATE_RC=str(create_rc), MODE=mode, INTERACTIVE=interactive,
               ADMIN_EMAIL=admin_email, LOGIN_WORKS="1" if login_works else "0")
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


class SignIn(unittest.TestCase):
    """ekka-labs/ekka-openshell-example#6: in a terminal the installer asks for the EKKA sign-in and runs it, then goes on in the
    same run; without one it stops with numbered steps and the administrator's real email, never `<you>`."""

    def test_without_a_terminal_it_stops_with_numbered_steps_and_the_real_email(self):
        r = run(CREATED, 0, signed_in=False, interactive="")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("stop: Sign in to EKKA, as the person who owns organization ekkawalk", r.stderr)
        self.assertIn("2. ekka login --email admin@example.com", r.stderr)
        self.assertNotIn("<you>", r.stderr)
        self.assertNotIn("login", " ".join(r.calls), "nothing ran the sign-in for the person")
        self.assertNotIn("claimed", r.stdout)

    def test_in_a_terminal_it_runs_the_sign_in_and_continues(self):
        r = run(CREATED, 0, signed_in=False, interactive="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("login --email admin@example.com", r.calls)
        self.assertLess(r.calls.index("login --email admin@example.com"), next(i for i, c in enumerate(r.calls) if c.startswith("enclave create")))
        self.assertIn("ok: this machine is claimed", r.stdout, "the same run finished; no rerun asked")
        self.assertNotIn("stop:", r.stderr)

    def test_in_a_terminal_without_an_email_it_asks_the_sign_in_to_ask(self):
        r = run(CREATED, 0, signed_in=False, interactive="1", admin_email="")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("login", r.calls)
        self.assertIn("ok: this machine is claimed", r.stdout)

    def test_a_sign_in_that_does_not_complete_is_a_refusal_not_a_claim(self):
        r = run(CREATED, 0, signed_in=False, interactive="1", login_works=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("sign-in did not complete", r.stderr)
        self.assertNotIn("enclave create", " ".join(r.calls))


class TerminalDetection(unittest.TestCase):
    """The terminal check must not run inside a command substitution: there stdout is a pipe and `-t 1` lies (measured)."""

    def detect(self):
        with open(UP_SH, encoding="utf-8") as f:
            text = f.read()
        m = re.search(r"^detect_interactive\(\) \{.*?^\}\n", text, re.S | re.M)
        assert m, "detect_interactive is not in up.sh"
        return m.group(0) + 'detect_interactive; printf "INTERACTIVE=[%s]" "$INTERACTIVE"'

    def test_a_person_at_a_terminal_is_detected(self):
        import pty
        out = bytearray()
        def read(fd):
            data = os.read(fd, 1024); out.extend(data); return data
        env = {k: v for k, v in os.environ.items() if k != "INTERACTIVE"}
        pid, fd = pty.fork()
        if pid == 0:
            os.execve("/bin/bash", ["bash", "-c", self.detect()], env)
        while True:
            try:
                data = os.read(fd, 1024)
            except OSError:
                break
            if not data:
                break
            out.extend(data)
        os.waitpid(pid, 0)
        self.assertIn(b"INTERACTIVE=[1]", bytes(out))

    def test_pipes_are_not_a_terminal(self):
        env = {k: v for k, v in os.environ.items() if k != "INTERACTIVE"}
        r = subprocess.run(["bash", "-c", self.detect()], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
        self.assertEqual(r.stdout, "INTERACTIVE=[]")

    def test_an_explicit_setting_wins(self):
        r = subprocess.run(["bash", "-c", self.detect()], capture_output=True, text=True, env=dict(os.environ, INTERACTIVE=""), stdin=subprocess.DEVNULL)
        self.assertEqual(r.stdout, "INTERACTIVE=[]")
        r = subprocess.run(["bash", "-c", self.detect()], capture_output=True, text=True, env=dict(os.environ, INTERACTIVE="1"), stdin=subprocess.DEVNULL)
        self.assertEqual(r.stdout, "INTERACTIVE=[1]")


if __name__ == "__main__":
    unittest.main()
