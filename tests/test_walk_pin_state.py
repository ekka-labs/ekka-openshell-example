"""The real walk classifier must understand runner 0.1.112's desk states."""
from pathlib import Path
import subprocess
import unittest

class DeskPinState(unittest.TestCase):
    def test_actual_classifier_accepts_only_a_pin_or_named_replacement(self):
        text=(Path(__file__).resolve().parent.parent/'tools/walk.sh').read_text()
        start=text.index('pin_state() {')
        body=text[start:text.index('until_state() {', start)]
        cases={
            'pinned':'pinned',
            'not pinned yet':'waiting',
            'pinned, last check DESK_NOT_READY':'waiting',
            'REPLACED (DESK_REPLACED)':'replaced',
            'REPLACED (DESK_SANDBOX_REPLACED)':'replaced',
            'REPLACED (DESK_POLICY_MISMATCH)':'replaced',
        }
        for state, expected in cases.items():
            with self.subTest(state=state):
                script=body+'\nE() { printf "%s\\n" "$LINE"; }\nDESK=walk-desk; pin_state'
                import os
                env=dict(os.environ, LINE='  • desks/walk-desk backend name walk-desk '+state)
                r=subprocess.run(['bash','-c',script],env=env,capture_output=True,text=True,timeout=10)
                self.assertEqual(r.returncode,0,r.stderr)
                self.assertEqual(r.stdout.strip(),expected)
