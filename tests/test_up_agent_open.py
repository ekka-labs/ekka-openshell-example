"""up.sh step_agent_open: every member may RUN the desks agent's shared plans, and nothing more .

Since Govern migration 233 a new agent is `listed`, so a member's run of a shared desk plan is refused. The step asks
EKKA for `ekka agent allow desks --everyone --as use` . The function runs here with `docker`
stubbed: `agent allow` answers as told, and `agent show --json` reports the mode Govern holds.
"""
import os
import re
import subprocess
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OPENED = "✓ desks: every member of your organization may now run its plans. Writing plans under it still needs --as author."


SHOWN = 'STAGING  a rehearsal\n{"agent_code": "desks", "access": {"use": "USE", "author": "listed", "people": [], "always": []}}\n'


def run(reply=OPENED, rc=0, done=False, text=None, use="open", shown=None):
    if text is None:
        with open(os.path.join(ROOT, "up.sh"), encoding="utf-8") as f:
            text = f.read()
    fn = re.search(r"^step_agent_open\(\) \{[^\n]*\n.*?^\}\n", text, re.S | re.M)
    assert fn, "step_agent_open is not in up.sh"
    state = tempfile.mkdtemp()
    calls = os.path.join(state, "calls")
    script = f"""
set -euo pipefail
DESKS_STATE={state}; DESKS_AGENT=desks; DONE={state}/done; MODE_ARGS="server desks.example.test"
ok() {{ echo "ok: $*"; }}
die() {{ echo "die: $*" >&2; exit 1; }}
done_() {{ mkdir -p "$DONE"; touch "$DONE/$1"; }}
is_done() {{ [[ -f $DONE/$1 ]]; }}
{'done_ agent_open' if done else ''}
docker() {{
  case "$*" in
    *"ekka agent allow "*) a="$*"; echo "${{a#*ekka-enclave ekka }}" >> {calls}; printf '%s\\n' "$REPLY"; return "$RC" ;;
    *"ekka agent show "*) a="$*"; echo "${{a#*ekka-enclave ekka }}" >> {calls}; printf '%s' "$SHOWN" ;;
    *) echo "unexpected: $*" >> {calls}; return 0 ;;
  esac
}}
{fn.group(0)}
step_agent_open
"""
    env = dict(os.environ, REPLY=reply, RC=str(rc), SHOWN=shown if shown is not None else SHOWN.replace("USE", use))
    out = subprocess.run(["bash", "-c", script], cwd=ROOT, capture_output=True, text=True, env=env)
    out.calls = open(calls).read().splitlines() if os.path.exists(calls) else []
    out.marked = os.path.exists(os.path.join(state, "done", "agent_open"))
    return out


class AgentOpen(unittest.TestCase):
    def test_an_ekka_with_allow_opens_use_only_and_marks_it_done(self):
        r = run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.calls, ["agent allow desks --everyone --as use", "agent show desks --json"],
                         "use only, never author, then read back from Govern")
        self.assertIn("ok: every member may run the plans of desks", r.stdout)
        self.assertTrue(r.marked)

    def test_an_ekka_without_the_command_stops_the_install(self):
        # 0.1.108 has no `agent allow` and its `agent show --json` has no access section (measured help; floor is 0.1.124)
        r = run(reply="error: unrecognized subcommand 'allow'", rc=2)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("die: EKKA refused to let every member run the plans of desks: error: unrecognized subcommand", r.stderr)
        self.assertFalse(r.marked)

    def test_a_refusal_stops_the_install_with_ekkas_reason(self):
        r = run(reply="  STAGING   a rehearsal\nYou need agents.access to change who may use desks.", rc=1)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("die: EKKA refused to let every member run the plans of desks: You need agents.access", r.stderr)
        self.assertNotIn("STAGING", r.stderr)
        self.assertFalse(r.marked)

    def test_an_accepted_change_that_govern_does_not_show_as_open_is_not_taken_as_one(self):
        for use in ("listed", "unknown (refused)"):
            r = run(use=use)
            self.assertNotEqual(r.returncode, 0, use)
            self.assertIn(f"die: EKKA accepted the change, but desks reads as '{use}', not open", r.stderr)
            self.assertFalse(r.marked)

    def test_a_show_without_an_access_section_is_not_open(self):
        r = run(text=None, use="open", shown='{"agent_code": "desks"}')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("die: EKKA accepted the change, but desks reads as 'nothing', not open", r.stderr)
        self.assertFalse(r.marked)

    def test_the_cli_sentence_is_not_what_decides(self):
        r = run(reply="✓ agent desks: done, in whatever words a later release uses")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.marked)

    def test_done_once_it_asks_nothing_again(self):
        r = run(done=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.calls, [])

    def test_it_runs_after_the_grant_in_the_install(self):
        with open(os.path.join(ROOT, "up.sh"), encoding="utf-8") as f:
            self.assertIn("step_grant; step_agent_open; step_desk_grant;", f.read())


if __name__ == "__main__":
    unittest.main()
