"""Run the installer's provider discovery with the shell options it ships."""
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class ProfileNames(unittest.TestCase):
    def test_empty_missing_and_populated_local_folder(self):
        source = (ROOT / 'up.sh').read_text()
        start = source.index('profile_names() {')
        function = source[start:source.index('\n}\n', start) + 3]
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / 'providers').mkdir()
            (root / 'providers/claude-code.yaml').write_text('id: claude-code\n')
            state = root / 'state'
            state.mkdir()
            local = state / 'providers.local'
            for mode in ['missing', 'empty', 'populated']:
                if mode == 'empty':
                    local.mkdir()
                if mode == 'populated':
                    (local / 'claude-code.yaml').write_text('id: claude-code\n')
                    (local / 'internal.yaml').write_text('id: internal\n')
                # This is the real connect_cmd membership check under pipefail.
                script = 'set -uo pipefail\n' + function + '\nprofile_names | grep -qxF -- claude-code\n'
                out = subprocess.run(['bash', '-c', script], cwd=root,
                                     env={'PATH': '/usr/bin:/bin', 'DESKS_STATE': str(state)},
                                     capture_output=True, text=True)
                self.assertEqual(out.returncode, 0, (mode, out.stderr))
