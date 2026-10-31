"""Execute the actual lock observer with a late admin claim and a simulated clock."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parent.parent
CLAIM='admin:11111111-aaaa-4aaa-8aaa-aaaaaaaaaaaa'

class QueueWatch(unittest.TestCase):
    def observe(self, fast=False, legacy=False):
        text=(ROOT/'tools/walk.sh').read_text()
        a=text.index('    watch_deadline=')
        body=text[a:text.index('    wait "$job_pid"',a)]
        if legacy: body=body.replace('while :; do','for _ in $(seq 1 300); do')
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/'counter').write_text('0\n')
            setup='''set -u
FAIL=0; FAILED=(); r=''; n=''; job_pid=111; stop_pid=222; saw_admin=''; SECONDS=0
lock_holder() {
  local number; read -r number < "$T/counter"
  number=$((number+1)); printf '%s\\n' "$number" > "$T/counter"
  if (( number >= 306 )); then echo admin:11111111-aaaa-4aaa-8aaa-aaaaaaaaaaaa; else echo job-attempt; fi
}
kill() { local number; read -r number < "$T/counter"; [[ $FAST == 1 ]] || (( number < 310 )); }
sleep() {
  local number; read -r number < "$T/counter"
  if [[ $FAST == 1 ]]; then SECONDS=$((number*100)); else SECONDS=$((number/5)); fi
}
'''
            script=setup+body+'\nprintf "RESULT=%s|%s|%s\\n" "$saw_admin" "$watch_expired" "$FAIL"\n'
            result=subprocess.run(['bash','-c',script],env=dict(os.environ,T=folder,FAST='1' if fast else '0'),
                capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            return result.stdout

    def test_claim_after_sixty_one_seconds_is_still_observed(self):
        self.assertIn('RESULT='+CLAIM+'|0|0',self.observe())

    def test_deadline_records_failure(self):
        self.assertIn('RESULT=|1|1',self.observe(fast=True))

    def test_planted_old_iteration_limit_misses_the_late_claim(self):
        self.assertIn('RESULT=|0|0',self.observe(legacy=True))
