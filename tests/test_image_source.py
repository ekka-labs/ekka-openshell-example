"""EKKA installs the separate pinned image source before building it."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


class SeparateImageSource(unittest.TestCase):
    def run_images(self, fail=False):
        text = (ROOT / 'up.sh').read_text()
        function = re.search(r'^step_images\(\) \{.*?^\}', text, re.M | re.S).group(0)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / 'external/desk-base/desk/image'
            image.mkdir(parents=True)
            (image / 'Dockerfile').write_text('FROM scratch\n')
            calls = root / 'calls'
            script = '''set -euo pipefail
say() { :; }; ok() { :; }; die() { echo "$*" >&2; exit 1; }
git() { printf 'git %s\\n' "$*" >> "$CALLS"; return "$GIT_STATUS"; }
docker() { printf 'docker %s\\n' "$*" >> "$CALLS"; return 0; }
''' + function + '\nstep_images\n'
            result = subprocess.run(['bash', '-c', script], capture_output=True, text=True,
                env=dict(os.environ, ROOT=str(root), CALLS=str(calls), GIT_STATUS='1' if fail else '0',
                         OPENSHELL_TAG='v0.1.4', EKKA_VERSION='test', DESKS_STATE=str(root)))
            return result, calls.read_text()

    def test_build_uses_initialized_separate_source(self):
        result, calls = self.run_images()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('submodule update --init --checkout -- external/desk-base', calls)
        self.assertLess(calls.index('git '), calls.index('docker build'))
        self.assertRegex(calls, r'docker build .*external/desk-base/desk/image')

    def test_initialization_failure_prevents_any_build(self):
        result, calls = self.run_images(fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('docker ', calls)
        self.assertIn('could not initialize', result.stderr)
