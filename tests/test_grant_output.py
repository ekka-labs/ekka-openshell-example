"""A grant's success output must be drained; stopping at its first word can break the writer."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class GrantOutput(unittest.TestCase):
    def execute(self,failed=False,old_pipe=False):
        source=(ROOT/'up.sh').read_text()
        start=source.index('  local grant_out\n',source.index('step_desk_grant()'))
        end=source.index('  for g in ${decision#replace}',start)
        block=source[start:end]
        if old_pipe:
            command=block[block.index('grant_out=$(')+len('grant_out=$('):block.index(') || die')]
            block='('+command+') | grep -q "Granted" || die refused\n'
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);(p/'limits').write_text('{}')
            (p/'docker').write_text('''#!/usr/bin/env python3
import os,sys,time
sys.stdout.write('Granted api.write\\n');sys.stdout.flush()
time.sleep(.08)
try:
 sys.stdout.write('Grant detail\\n'*20000);sys.stdout.flush()
except BrokenPipeError: os._exit(141)
sys.exit(7 if os.environ.get('FAIL_GRANT') else 0)
''')
            (p/'docker').chmod(0o755)
            script='''set -euo pipefail
die() { echo "$*" >&2; exit 1; }
check_grant() {
local limits="$TEST_ROOT/limits" gate=testGate
'''+block+'''\necho confirmed
}
check_grant
'''
            env=dict(os.environ,PATH=str(p)+os.pathsep+os.environ['PATH'],TEST_ROOT=temp,
                     DESKS_STATE=temp,DESKS_AGENT='desks')
            if failed:env['FAIL_GRANT']='1'
            return subprocess.run(['bash','-c',script],cwd=ROOT,env=env,capture_output=True,text=True)

    def test_slow_multiline_success_is_confirmed(self):
        result=self.execute()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('confirmed',result.stdout)

    def test_failed_command_is_not_accepted_even_if_it_printed_granted(self):
        result=self.execute(failed=True)
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn('confirmed',result.stdout)

    def test_planted_old_grep_pipeline_reports_successful_write_as_failure(self):
        result=self.execute(old_pipe=True)
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn('confirmed',result.stdout)
