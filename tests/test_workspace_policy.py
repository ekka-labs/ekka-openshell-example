"""Exercise fresh installs, saved choices, refused flags and configuration changes without Docker."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import workspace_policy as policy


class WorkspaceChoice(unittest.TestCase):
    def test_changing_folder_mode_keeps_the_saved_environment_and_release(self):
        with tempfile.TemporaryDirectory() as state:
            (Path(state) / 'up.env').write_text('EKKA_ENV=staging\nEKKA_VERSION=0.1.108\n')
            self.assertEqual(policy.retained(state, 'EKKA_ENV', 'prod'), 'staging')
            self.assertEqual(policy.retained(state, 'EKKA_VERSION', '0.1.105'), '0.1.108')
            (Path(state) / 'up.env').write_text('EKKA_ENV=staging\nEKKA_ENV=prod\n')
            with self.assertRaises(ValueError): policy.retained(state, 'EKKA_ENV', 'prod')

    def test_a_fresh_gateway_has_no_bind_mounts_or_driver_config(self):
        with tempfile.TemporaryDirectory() as state, tempfile.TemporaryDirectory() as work:
            gateway = Path(work) / 'gateway.toml'
            gateway.write_text((ROOT / 'deploy' / 'gateway' / 'gateway.toml.template').read_text())
            before = gateway.read_text()
            self.assertFalse(policy.configure(gateway, False))
            self.assertEqual(gateway.read_text(), before)
            for key in ('enable_bind_mounts', 'allow_driver_config'):
                self.assertRegex(before, rf'(?m)^{key} = false\b')

    def test_an_extra_install_argument_stops_before_the_installer_mutates_anything(self):
        with tempfile.TemporaryDirectory() as state:
            r = subprocess.run(['bash', 'up.sh', 'server', 'example.test', '--host-mount'],
                               cwd=ROOT, capture_output=True, text=True,
                               env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'DESKS_STATE': state})
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('usage:', r.stderr)
            self.assertEqual(list(Path(state).iterdir()), [])


class GatewayPolicy(unittest.TestCase):
    def test_changes_only_folder_support_and_preserves_security_tables(self):
        original = (ROOT / 'deploy/gateway/gateway.toml.template').read_text()
        with tempfile.TemporaryDirectory() as state:
            p = Path(state) / 'gateway.toml'; p.write_text(original); p.chmod(0o640)
            self.assertFalse(policy.configure(p, False))
            self.assertTrue(policy.configure(p, True))
            result = p.read_text()
            self.assertIn('enable_bind_mounts = true', result)
            self.assertIn('allow_driver_config = true', result)
            self.assertEqual(result.split('[[openshell.gateway.interceptors]]')[1:],
                             original.split('[[openshell.gateway.interceptors]]')[1:])
            self.assertNotIn('enabled = false', result.split('[openshell.drivers.docker]')[1].split('[[openshell.gateway.interceptors]]')[0])
            self.assertEqual(p.stat().st_mode & 0o777, 0o640)
            self.assertFalse(policy.configure(p, True))
            self.assertTrue(policy.configure(p, False))
            self.assertIn('enable_bind_mounts = false', p.read_text())

    def test_refusal_preserves_file_bytes(self):
        with tempfile.TemporaryDirectory() as state:
            p = Path(state) / 'gateway.toml'
            for text in ['[openshell.drivers.docker]\nenable_bind_mounts = false\n',
                         '[openshell.drivers.docker]\nenable_bind_mounts = false\nallow_driver_config = false\n'
                         '[openshell.drivers.docker.resource_admission]\nenabled = false\n']:
                p.write_text(text)
                with self.assertRaises(ValueError): policy.configure(p, True)
                self.assertEqual(p.read_text(), text)

    def test_reload_is_still_pending_after_an_interrupted_config_write(self):
        with tempfile.TemporaryDirectory() as state:
            p = Path(state) / 'gateway.toml'
            original = (ROOT / 'deploy/gateway/gateway.toml.template').read_text()
            p.write_text(original); pending = Path(state) / 'pending'
            with patch.object(policy.os, 'replace', side_effect=OSError('simulated interruption')):
                with self.assertRaises(OSError): policy.configure(p, True, pending)
            self.assertEqual(p.read_text(), original)
            self.assertTrue(pending.exists())
            self.assertTrue(policy.configure(p, True, pending))
            self.assertFalse(policy.configure(p, True, pending))
            self.assertTrue(pending.exists(), 'a kept config does not clear a reload that never finished')

if __name__ == '__main__': unittest.main()
