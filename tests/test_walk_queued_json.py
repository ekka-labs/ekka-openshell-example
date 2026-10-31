"""The real queued-stop invocation keeps progress off the JSON result stream."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parent.parent

class QueuedJSON(unittest.TestCase):
    def test_actual_command_keeps_stderr_progress_separate(self):
        line=next(line for line in (ROOT/'tools/walk.sh').read_text().splitlines()
                  if 'desk stop "$DESK" --json > "$TMP/queued-stop.out"' in line)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            up=root/'up.sh'
            up.write_text("""#!/bin/bash
echo "Dispatching through the Enclave" >&2
printf '%s\\n' '{"run":"11111111-aaaa-4aaa-8aaa-aaaaaaaaaaaa","outcome":"ready"}'
""")
            up.chmod(0o700)
            r=subprocess.run(['bash','-c',line+'\nwait'],cwd=root,
                env=dict(os.environ,TMP=folder,DESK='walk-desk'),capture_output=True,text=True,timeout=10)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual(json.loads((root/'queued-stop.out').read_text())['outcome'],'ready')
            self.assertIn('Dispatching',(root/'queued-stop.progress').read_text())
