"""Execute the real walk leg: shared or unreadable connections must cause no disconnect."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parent.parent

class ScopedDisconnect(unittest.TestCase):
    def run_leg(self, rows, unguarded=False):
        text=(ROOT/'tools/walk.sh').read_text()
        a=text.index('  covers "ekka desk disconnect"')
        body=text[a:text.index('  docker cp "$ADAPTER',a)]
        if unguarded:
            body=body.replace('[[ $provider_scope == isolated ]]','true')
        with tempfile.TemporaryDirectory() as folder:
            env=dict(os.environ, TMP=folder, DESK='walk-desk', CONNECTIONS_JSON=json.dumps(rows))
            script='''set -u
FAIL=0; FAILED=()
covers() { :; }
skip_leg() { echo skip >> "$TMP/skips"; }
E() {
  if [[ "$1 $2" == 'desk connections' ]]; then printf '%s\\n' "$CONNECTIONS_JSON";
  elif [[ "$1 $2" == 'desk disconnect' ]]; then echo disconnect >> "$TMP/calls"; return 1;
  else return 2; fi
}
judged() { shift 3; "$@" || true; }
t() { shift 2; "$@"; }
'''+body+'\nprintf "failures=%s\\n" "$FAIL"\n'
            r=subprocess.run(['bash','-c',script],cwd=ROOT,env=env,capture_output=True,text=True,timeout=10)
            calls=(Path(folder)/'calls').read_text() if (Path(folder)/'calls').exists() else ''
            return r,calls

    @staticmethod
    def rows(desks, others=0):
        return {'connections':[{'name':'claude-code','desks':desks,'other_backend_desks':others}]}

    def test_only_the_isolated_walk_desk_reaches_disconnect(self):
        r,calls=self.run_leg(self.rows(['walk-desk']))
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(calls,'disconnect\n')
        self.assertIn('failures=0',r.stdout)
        for rows in [self.rows(['walk-desk','customer-desk']),self.rows(['walk-desk'],1)]:
            r,calls=self.run_leg(rows)
            self.assertEqual(calls,'',r.stderr)
            self.assertIn('failures=0',r.stdout)

    def test_unreadable_data_fails_without_a_disconnect(self):
        r,calls=self.run_leg({})
        self.assertEqual(calls,'')
        self.assertIn('failures=1',r.stdout)

    def test_planted_missing_guard_would_touch_a_customer_desk(self):
        _,calls=self.run_leg(self.rows(['walk-desk','customer-desk']),unguarded=True)
        self.assertEqual(calls,'disconnect\n','the preceding shared-desk test catches removing the actual guard')
