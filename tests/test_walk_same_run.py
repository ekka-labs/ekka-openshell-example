"""A run's id at dispatch is the id it completed under .

Runner 0.1.113's first draft printed Govern's request correlation id as "→ Run <id> dispatched". The walk took the
first id it saw, so every later read went to a run that does not exist. The walk now takes a run's id only when the
dispatch line and the completion line agree.
"""
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import desk_admin_checks as c  # noqa: E402

RUN = "70c0905d-7e36-41f4-ad33-851c9ae5ec35"
CORRELATION = "435dfae2-a280-44d5-9c1e-d12c3a6fb0b9"


def output(dispatched, completed):
    lines = ["→ Dispatching plan `ekka.example.desk.ask.walkDesk.v1@1.0.0` through your Enclave..."]
    if dispatched:
        lines.append(f"→ Run {dispatched} dispatched. Its outcome: `ekka run show {dispatched}`.")
    lines.append("→ Waiting for the run to finish...")
    if completed:
        lines.append(f"✓ Plan completed (run {completed}).")
    return "\n".join(lines) + "\n"


class SameRun(unittest.TestCase):
    def test_agreeing_ids_give_the_run(self):
        self.assertEqual(c.same_run(output(RUN, RUN)), (RUN, []))

    def test_planted_the_correlation_id_printed_at_dispatch_fails_naming_both(self):
        run, problems = c.same_run(output(CORRELATION, RUN))
        self.assertIsNone(run)
        self.assertEqual(problems, [f"dispatched as run {CORRELATION} but completed as run {RUN}"])

    def test_no_dispatch_line_or_no_completion_is_never_a_run(self):
        for text in (output(None, RUN), output(RUN, None), "", output(RUN, RUN) + output(RUN, RUN)):
            with self.subTest(text=text):
                run, problems = c.same_run(text)
                self.assertIsNone(run)
                self.assertTrue(problems)


class TheWalkUsesIt(unittest.TestCase):
    def run_of(self, text):
        walk = (ROOT / "tools" / "walk.sh").read_text()
        body = walk[walk.index("run_of() {"):walk.index("run_of \"setup\"")]
        script = ("set -u\nOUT=''\n"
                  't() { local want=$2; shift 2; OUT=$("$@"); local rc=$?; '
                  '(( rc == 0 )) && grep -q -E -- "$want" <<<"$OUT" && echo PASS || echo FAIL; }\n'
                  + body + 'run_of "ask" "$1"\nprintf "RUN_OF=%s\\n" "$RUN_OF"\n')
        r = subprocess.run(["bash", "-c", script, "_", text], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_the_real_helper_takes_the_id_only_when_both_lines_agree(self):
        self.assertIn(f"PASS\nRUN_OF={RUN}\n", self.run_of(output(RUN, RUN)))

    def test_planted_a_mismatch_fails_the_leg_and_gives_no_id(self):
        self.assertIn("FAIL\nRUN_OF=\n", self.run_of(output(CORRELATION, RUN)))

    def test_every_plan_run_id_in_the_walk_comes_from_run_of(self):
        walk = (ROOT / "tools" / "walk.sh").read_text()
        for leg in ('run_of "setup" "$OUT"; run_id=$RUN_OF', 'run_of "ask" "$OUT"; ASKED=$RUN_OF',
                    'run_of "the observed job" "$(cat "$TMP/held-job.out")"; job_run=$RUN_OF'):
            self.assertIn(leg, walk)
        # The first-id shortcut this replaced (a full run id taken first from a plan run's output) must not come back.
        self.assertNotRegex(walk, r"\{12\}' (<<<\"\$OUT\"|\"\$TMP/held-job\.out\") \| head -1\)")


if __name__ == "__main__":
    unittest.main()
