"""Server shared-loopback topology, private config migration and real Docker lifecycle.

The optional Docker fixture uses only a local Python image and a unique Compose project.
It runs the kit's actual step_start/wait functions; no gateway, credentials or live desk.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
MODE = 'network_mode: service:ekka-enclave'


def function(path, name):
    return re.search(rf'^{name}\(\) \{{.*?^\}}\n', (ROOT/path).read_text(), re.S | re.M).group(0)


class Migration(unittest.TestCase):
    def migrate(self, text):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d)/'enclave.env'; f.write_text(text); f.chmod(0o600)
            r = subprocess.run(['sh', str(ROOT/'deploy/migrate-loopback.sh'), str(f)], capture_output=True, text=True)
            return r, f.read_text(), f.stat().st_mode & 0o777

    def test_preserves_enrollment_tls_and_private_values(self):
        # Fresh install: there is no enrolled file until enclave-config writes one.
        with tempfile.TemporaryDirectory() as d:
            missing = Path(d)/'enclave.env'
            r = subprocess.run(['sh', str(ROOT/'deploy/migrate-loopback.sh'), str(missing)], capture_output=True)
            self.assertEqual(r.returncode, 0)
            self.assertFalse(missing.exists())
        before = ('NODE_ID=unchanged\nPRIVATE_TEST_VALUE=not-for-logs\n'
                  'EKKA_OPENSHELL_EXECUTOR_TLS_DOMAIN=desks.example.test\n'
                  'EKKA_OPENSHELL_EXECUTOR_CA_PEM_PATH=/tls/ca.crt\n'
                  'EKKA_OPENSHELL_EXECUTOR_URL=https://gateway:8080\n')
        r, after, mode = self.migrate(before)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(after, before.replace('https://gateway:8080', 'https://127.0.0.1:8080'))
        self.assertEqual(mode, 0o600)
        self.assertNotIn('not-for-logs', r.stdout+r.stderr)
        again, same, _ = self.migrate(after)
        self.assertEqual((again.returncode, same), (0, after))

    def test_refuses_unknown_or_duplicate_urls_without_replacing_the_file(self):
        for text in ['PRIVATE_TEST_VALUE=hidden\n',
                     'EKKA_OPENSHELL_EXECUTOR_URL=https://remote.example:8080\n',
                     'EKKA_OPENSHELL_EXECUTOR_URL=https://gateway:8080\n'*2]:
            with self.subTest(text=text):
                r, after, _ = self.migrate(text)
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual(after, text)
                self.assertNotIn('hidden', r.stdout+r.stderr)
                self.assertNotIn('remote.example', r.stdout+r.stderr)

    def test_unknown_url_refuses_before_supervisor_or_other_startup_work(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root/'deploy').mkdir()
            env = root/'enclave.env'; env.write_text('EKKA_OPENSHELL_EXECUTOR_URL=https://custom.example:8080\n')
            calls = root/'calls'
            (root/'deploy/setup.sh').write_text(
                '#!/bin/bash\necho "$1" >> '+repr(str(calls))+'\n'
                'if [[ $1 == loopback-config ]]; then\n'
                '  sh '+repr(str(ROOT/'deploy/migrate-loopback.sh'))+' '+repr(str(env))+'\n'
                'else exit 0; fi\n')
            script = ('set -euo pipefail\ndie() { echo "$*" >&2; exit 1; }\nedge_conf_sha() { :; }; edge_current() { :; }\nis_done() { [[ $1 == started ]]; }\n'
                      + function('up.sh', 'step_start')+'\nstep_start\n')
            r = subprocess.run(['bash', '-c', script], cwd=root, capture_output=True, text=True, timeout=5)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(calls.read_text().splitlines(), ['loopback-config'])
            self.assertIn('no configuration was changed', r.stderr)
            self.assertEqual(env.read_text(), 'EKKA_OPENSHELL_EXECUTOR_URL=https://custom.example:8080\n')


    def test_a_first_install_does_not_migrate_an_executor_url_it_has_not_written_yet(self):
        # Measured on the integration test server 2026-10-22: enrollment writes enclave.env with no executor URL; the migration
        # refused it and `up.sh server` stopped on every fresh server. enclave-config writes the URL instead.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root/'deploy').mkdir()
            env = root/'enclave.env'; env.write_text('EKKA_TENANT_ID=t\nEKKA_NODE_ID=n\n')
            calls = root/'calls'
            (root/'deploy/setup.sh').write_text(
                '#!/bin/bash\necho "$1" >> '+repr(str(calls))+'\n'
                'if [[ $1 == loopback-config ]]; then\n'
                '  sh '+repr(str(ROOT/'deploy/migrate-loopback.sh'))+' '+repr(str(env))+'\n'
                'else exit 0; fi\n')
            (root/'deploy/certs.sh').write_text('#!/bin/bash\nexit 0\n')
            script = ('set -euo pipefail\ndie() { echo "$*" >&2; exit 1; }\nedge_conf_sha() { :; }; edge_current() { :; }\nis_done() { return 1; }\n'
                      'say() { :; }\nok() { :; }\ndone_() { :; }\nwait_gateway_loopback() { :; }\nEKKA_VERSION=x\nDESKS_STATE=$PWD\n'
                      + function('up.sh', 'step_start')+'\nstep_start\n')
            r = subprocess.run(['bash', '-c', script], cwd=root, capture_output=True, text=True, timeout=20)
            self.assertEqual(r.returncode, 0, r.stderr)
            seen = calls.read_text().splitlines()
            self.assertNotIn('loopback-config', seen)
            self.assertIn('enclave-config', seen)

    def test_a_new_enclave_forgets_what_the_old_one_had_and_a_kept_one_does_not(self):
        # Measured on the integration test server 2026-10-24: after `ekka enclave reset`, enrollment wrote a URL-less enclave.env and the
        # kept `started` mark ran the migration on it ("could not be migrated; nothing started").
        for already in (False, True):
            with self.subTest(already_enrolled=already), tempfile.TemporaryDirectory() as d:
                done = Path(d)/'done'; done.mkdir()
                for m in ('init', 'first-admin', 'started', 'admin', 'grant', 'agent_open'):
                    (done/m).write_text('x')
                stubs = ('set -euo pipefail\ndie() { echo "$*" >&2; exit 1; }\nok() { :; }\nsay() { :; }\n'
                         'stop_for_person() { die "$*"; }\nclaim_gone() { :; }\nsleep() { :; }\n'
                         f'DONE={d}/done; DESKS_STATE={d}; EKKA_ORG=walk\n'
                         f'enrolled() {{ [[ -f {d}/claimed ]]; }}\n'
                         + ('touch '+d+'/claimed\n' if already else '') +
                         'ekka_in() { case "$1 ${2:-}" in\n'
                         '  "whoami ") echo "Signed in as p";; "org list") echo "  * walk  Walk";;\n'
                         '  "enclave create") echo "Enclave 5dff7e46-3718-47c5-ba22-0128851bf369";;\n'
                         f'  "enclave start") touch {d}/claimed;; esac; }}\n')
                r = subprocess.run(['bash', '-c', stubs + function('up.sh', 'step_enroll') + 'step_enroll\n'],
                                   capture_output=True, text=True, timeout=20)
                self.assertEqual(r.returncode, 0, r.stderr)
                left = sorted(p.name for p in done.iterdir())
                if already:
                    self.assertEqual(left, ['admin', 'agent_open', 'first-admin', 'grant', 'init', 'started'])
                else:
                    self.assertEqual(left, ['agent_open', 'first-admin', 'init'])


class Topology(unittest.TestCase):
    def test_shared_namespace_keeps_credentials_and_docker_control_separate(self):
        text = (ROOT/'deploy/compose.yml').read_text()
        owner = text.split('  ekka-enclave:\n', 1)[1].split('  gateway-init:\n', 1)[0]
        gateway = text.split('  gateway:\n', 1)[1].split('  ekka-backend:\n', 1)[0]
        self.assertIn(MODE, gateway)
        self.assertNotIn('\n    networks:', gateway)
        self.assertNotIn('\n    ports:', gateway)
        self.assertIn('8080:8080', owner)
        self.assertIn('aliases: [host.openshell.internal]', owner)
        self.assertNotIn('/var/run/docker.sock', owner)
        self.assertIn('/var/run/docker.sock:/var/run/docker.sock', gateway)
        self.assertIn('restart: true', gateway)
        setup = (ROOT/'deploy/setup.sh').read_text()
        self.assertIn('EKKA_OPENSHELL_EXECUTOR_URL=https://127.0.0.1:8080', setup)
        self.assertIn('EKKA_OPENSHELL_EXECUTOR_TLS_DOMAIN=$DESKS_NAME', setup)


def have_image():
    return shutil.which('docker') and subprocess.run(
        ['docker', 'image', 'inspect', 'python:3.12-alpine'], capture_output=True).returncode == 0


@unittest.skipUnless(have_image(), 'needs Docker and local python:3.12-alpine image; no image is pulled')
class DockerLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); (self.root/'deploy').mkdir()
        self.project = 'codex-loopback-'+uuid.uuid4().hex[:12]
        self.file = self.root/'compose.json'
        self.prefix = ['docker', 'compose', '-p', self.project, '-f', str(self.file)]
        self.addCleanup(lambda: subprocess.run(self.prefix+['down', '--volumes', '--remove-orphans'], capture_output=True))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); self.port = sock.getsockname()[1]
        self.mapping = f'127.0.0.1:{self.port}:8080'
        image = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', 'python:3.12-alpine'], text=True).strip()
        self.journal = self.root/'home'; self.journal.mkdir()
        (self.journal/'desk.lock').write_text('held-attempt-1')
        (self.journal/'attempt.json').write_text('{"state":"held","outcome":"unknown"}')
        self.old = {'services': {
            'ekka-enclave': {'image': image, 'command': ['python', '-c', 'import time;time.sleep(3600)'], 'stop_grace_period': '1s',
                             'volumes': [f'{self.journal}:/journal'],
                             'healthcheck': {'test': ['CMD', 'true'], 'interval': '1s', 'retries': 20}},
            'gateway': {'image': image, 'command': ['python', '-m', 'http.server', '8080'], 'stop_grace_period': '1s',
                        'ports': [self.mapping], 'depends_on': {'ekka-enclave': {'condition': 'service_healthy'}}},
        }}
        self.file.write_text(json.dumps(self.old))
        self.command('up', '-d', '--wait')
        self.owner_before = self.container('ekka-enclave')
        self.gateway_before = self.container('gateway')
        self.new = copy.deepcopy(self.old)
        self.new['services']['ekka-enclave']['ports'] = [self.mapping]
        gw = self.new['services']['gateway']; gw.pop('ports')
        gw['network_mode'] = 'service:ekka-enclave'
        gw['depends_on']['ekka-enclave']['restart'] = True
        self.file.write_text(json.dumps(self.new))
        self.env_file = self.root/'enclave.env'
        self.env_file.write_text('NODE_ID=kept\nEKKA_OPENSHELL_EXECUTOR_URL=https://gateway:8080\n')
        (self.root/'deploy/certs.sh').write_text('exit 0\n')
        (self.root/'desks.env').write_text('')
        # Real setup loopback check; only credentials/preflight and the gateway executable are fixture stubs.
        script = ('#!/bin/bash\nset -euo pipefail\nP='+repr(' '.join(self.prefix))+'\n'
                  'die() { echo "$*" >&2; exit 1; }\n'+function('deploy/setup.sh', 'stage_loopback_check')+'\n'+function('deploy/setup.sh', 'stage_loopback_current')+
                  '\ncase "$1" in\n'
                  'loopback-check) stage_loopback_check ;;\n'
                  'loopback-current) stage_loopback_current ;;\n'
                  'loopback-config) sh '+repr(str(ROOT/'deploy/migrate-loopback.sh'))+' '+repr(str(self.env_file))+' ;;\n'
                  'compose) shift; exec $P "$@" ;;\n'
                  'preflight|supervisor|backend-preflight) exit 0 ;;\n'
                  '*) exit 2 ;;\nesac\n')
        (self.root/'deploy/setup.sh').write_text(script)

    def command(self, *args):
        return subprocess.run(self.prefix+list(args), capture_output=True, text=True, check=True, timeout=40)

    def container(self, service):
        return self.command('ps', '-q', service).stdout.strip()

    def repair(self):
        script = ('set -euo pipefail\n'
                  'die() { echo "$*" >&2; exit 1; }; ok() { :; }; say() { :; }; is_done() { return 0; }; '
                  'edge_conf_sha() { :; }; edge_current() { :; }\n'
                  'EKKA_VERSION=fixture\n'+function('up.sh', 'wait_gateway_loopback')+'\n'+function('up.sh', 'step_start')+'\nstep_start\n')
        r = subprocess.run(['bash', '-c', script], cwd=self.root,
                           env=dict(os.environ, DESKS_STATE=str(self.root)), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout+r.stderr)
        self.assertEqual((self.journal/'desk.lock').read_text(), 'held-attempt-1')
        self.assertEqual(json.loads((self.journal/'attempt.json').read_text()), {'state': 'held', 'outcome': 'unknown'})

    def reachable(self):
        return subprocess.run(self.prefix+['exec', '-T', 'ekka-enclave', 'sh', '-c',
                              'nc -z -w 3 127.0.0.1 8080 < /dev/null'], capture_output=True, timeout=8).returncode == 0

    def test_first_port_migration_restart_detection_and_explicit_full_repair(self):
        self.repair()
        self.assertNotEqual(self.owner_before, self.container('ekka-enclave'))
        self.assertNotEqual(self.gateway_before, self.container('gateway'))
        self.assertTrue(self.reachable())
        self.assertEqual(self.env_file.read_text(), 'NODE_ID=kept\nEKKA_OPENSHELL_EXECUTOR_URL=https://127.0.0.1:8080\n')
        # The same owner ID restarted outside Compose strands the gateway in the old namespace.
        owner = self.container('ekka-enclave')
        subprocess.run(['docker', 'restart', owner], check=True, capture_output=True, timeout=20)
        self.assertFalse(self.reachable(), 'the test must actually reproduce a stale namespace')
        stale = subprocess.run(['bash', 'deploy/setup.sh', 'loopback-check'], cwd=self.root,
                               env=dict(os.environ, DESKS_STATE=str(self.root)), capture_output=True, text=True, timeout=8)
        self.assertNotEqual(stale.returncode, 0)
        self.assertIn('repair the whole stack', stale.stderr)
        self.repair()
        self.assertTrue(self.reachable())
        # A healthy no-op rerun keeps both containers and their running processes.
        stable = [self.container(s) for s in ('ekka-enclave', 'gateway')]
        started = subprocess.check_output(['docker', 'inspect', '--format', '{{.State.StartedAt}}', *stable], text=True)
        self.repair()
        self.assertEqual(stable, [self.container(s) for s in ('ekka-enclave', 'gateway')])
        self.assertEqual(started, subprocess.check_output(
            ['docker', 'inspect', '--format', '{{.State.StartedAt}}', *stable], text=True))
        self.assertTrue(self.reachable())
        # A real owner config upgrade still applies and reattaches the gateway.
        self.new['services']['ekka-enclave']['environment'] = {'FIXTURE_REVISION': '2'}
        self.file.write_text(json.dumps(self.new))
        self.repair()
        self.assertNotEqual(stable[0], self.container('ekka-enclave'))
        self.assertNotEqual(stable[1], self.container('gateway'))
        self.assertTrue(self.reachable())


if __name__ == '__main__':
    unittest.main()
