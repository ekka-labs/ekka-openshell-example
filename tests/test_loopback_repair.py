"""Installed repair security rules and actual Docker gateway-only recovery.

The Docker stand-in bypasses only root-file ownership (test runs unprivileged)
and gateway credential preflight (stand-in HTTP has no real credentials).
No systemd unit is installed and no shared Docker daemon is restarted.
"""
import contextlib
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from tests import test_server_loopback as fixture
ROOT = fixture.ROOT

spec = importlib.util.spec_from_file_location('loopback_repair', ROOT/'tools/loopback_repair.py')
repair = importlib.util.module_from_spec(spec); spec.loader.exec_module(repair)


class Security(unittest.TestCase):
    def test_rejects_untrusted_owner_mode_and_symlink_before_commands(self):
        for owner, mode in [(501, stat.S_IFREG | 0o600), (0, stat.S_IFREG | 0o666), (0, stat.S_IFLNK | 0o777)]:
            with self.subTest(owner=owner, mode=mode), patch.object(Path, 'lstat') as ls:
                ls.return_value.st_uid = owner; ls.return_value.st_mode = mode
                with self.assertRaisesRegex(RuntimeError, 'untrusted repair path'):
                    repair.trusted('/root/repair/compose.json')

    def test_user_writable_environment_or_parent_is_refused_before_compose(self):
        # Model root ownership while exercising the real mode/path traversal.
        # /tmp is intentionally excluded from these simulated trusted ancestors.
        for unsafe in ('env', 'parent'):
            def metadata(path):
                class S:
                    st_uid = 0
                    st_mode = stat.S_IFDIR | 0o755
                s = S()
                if str(path).endswith('up.env'):
                    s.st_mode = stat.S_IFREG | (0o666 if unsafe == 'env' else 0o600)
                elif str(path) == '/var/lib/desks' and unsafe == 'parent':
                    s.st_mode = stat.S_IFDIR | 0o777
                return s
            with self.subTest(unsafe=unsafe), patch.object(Path, 'lstat', metadata), \
                 patch.object(Path, 'is_file', return_value=True), patch.object(Path, 'is_dir', return_value=True), \
                 patch.object(repair.os, 'geteuid', return_value=0), patch.object(repair, 'command') as cmd:
                with self.assertRaisesRegex(RuntimeError, 'untrusted repair path'):
                    repair.install('/var/lib/desks', ROOT)
                cmd.assert_not_called()

    def test_non_shared_snapshot_refuses_before_docker_commands(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'compose.json').write_text(json.dumps({'services': {'gateway': {'network_mode': 'bridge'}}}))
            with patch.object(repair, 'trusted'), patch.object(repair, 'command') as cmd:
                with self.assertRaisesRegex(RuntimeError, 'not the shared-loopback topology'):
                    repair.repair(root, grace=0)
                cmd.assert_not_called()

    def test_installer_refuses_busy_shared_lock_before_writing_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            lock = Path(folder)/'lock'
            fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(repair, 'LOCK', lock), patch.object(repair, 'trusted'), \
                     patch.object(repair.time, 'monotonic', side_effect=[0, 6]):
                    with self.assertRaisesRegex(RuntimeError, 'repair is active'):
                        with repair.installation_lock():
                            self.fail('busy lock entered')
            finally:
                os.close(fd)

    def test_failure_is_journal_error_and_nonzero(self):
        output = io.StringIO()
        with patch.object(repair.sys, 'argv', ['repair', 'run']), \
             patch.object(repair, 'guarded_repair', side_effect=RuntimeError('gateway-only repair failed')), \
             contextlib.redirect_stderr(output):
            self.assertEqual(repair.main(), 1)
        self.assertIn('<3>GATEWAY_REPAIR_FAILED:', output.getvalue())

    def test_timer_targets_repeatable_same_oneshot_and_lock(self):
        unit = (ROOT/'deploy/ekka-desks-loopback-repair.service').read_text()
        for setting in ('After=docker.service', 'PartOf=docker.service', 'WantedBy=docker.service',
                        'Type=oneshot', '/usr/bin/flock -n', '/usr/bin/timeout 110',
                        'TimeoutStartSec=115', 'StandardError=journal',
                        'ExecCondition=/usr/bin/systemctl --quiet is-active docker.service'):
            self.assertIn(setting, unit)
        self.assertNotIn('RemainAfterExit=yes', unit)
        timer = (ROOT/'deploy/ekka-desks-loopback-repair.timer').read_text()
        self.assertIn('OnUnitInactiveSec=60s', timer)
        self.assertIn('Unit=ekka-desks-loopback-repair.service', timer)
        self.assertNotIn('Restart=', unit)


class FailureBudget(unittest.TestCase):
    def test_three_failures_latch_and_stop_timer_without_a_fourth_repair(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder); now = [1000]
            def failed(root, before_attempt):
                before_attempt()
                self.assertEqual(json.loads((state/'failures.json').read_text())['failures'][-1], now[0])
                raise RuntimeError('repair failed')
            with patch.object(repair, 'trusted'), patch.object(repair, 'repair', side_effect=failed) as attempt, \
                 patch.object(repair.time, 'time', side_effect=lambda: now[0]), patch.object(repair, 'command') as cmd:
                for timestamp in (1000, 1100, 1200):
                    now[0] = timestamp
                    with self.assertRaisesRegex(RuntimeError, 'repair failed'):
                        repair.guarded_repair('/root/snapshot', state)
                self.assertTrue(json.loads((state/'failures.json').read_text())['latched'])
                with self.assertRaisesRegex(RuntimeError, 'latched'):
                    repair.guarded_repair('/root/snapshot', state)
                self.assertEqual(attempt.call_count, 3)
                cmd.assert_called_with(['systemctl', 'stop', repair.TIMER])
                # A restart/reboot does not erase the latch, even after the window.
                now[0] = 10000
                with self.assertRaisesRegex(RuntimeError, 'latched'):
                    repair.guarded_repair('/root/snapshot', state)
                self.assertEqual(attempt.call_count, 3)

    def test_healthy_check_is_silent_read_only_and_old_failures_expire(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder); ledger = state/'failures.json'
            ledger.write_text(json.dumps({'failures': [100, 200], 'latched': False}))
            before = ledger.read_bytes(), ledger.stat().st_mtime_ns
            with patch.object(repair, 'trusted'), patch.object(repair, 'repair') as attempt, \
                 patch.object(repair.time, 'time', return_value=1000):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    repair.guarded_repair('/root/snapshot', state)
                self.assertEqual(output.getvalue(), '')
                self.assertEqual((ledger.read_bytes(), ledger.stat().st_mtime_ns), before)
                attempt.side_effect = RuntimeError('unavailable')
                with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                    repair.guarded_repair('/root/snapshot', state)
            self.assertEqual(json.loads(ledger.read_text()), {'failures': [1000], 'latched': False})

    def test_confirmed_success_is_not_counted_as_a_third_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder); ledger = state/'failures.json'
            ledger.write_text(json.dumps({'failures': [900, 950], 'latched': False}))
            with patch.object(repair, 'trusted'), patch.object(repair.time, 'time', return_value=1000), \
                 patch.object(repair, 'repair', side_effect=lambda root, before_attempt: before_attempt()), \
                 patch.object(repair, 'command') as cmd:
                repair.guarded_repair('/root/snapshot', state)
                self.assertEqual(json.loads(ledger.read_text()), {'failures': [900, 950], 'latched': False})
                cmd.assert_not_called()

    def test_corrupt_ledger_stops_timer_without_attempting_repair(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder); (state/'failures.json').write_text('broken')
            with patch.object(repair, 'trusted'), patch.object(repair, 'repair') as attempt, \
                 patch.object(repair, 'command') as cmd:
                with self.assertRaisesRegex(RuntimeError, 'invalid repair failure ledger'):
                    repair.guarded_repair('/root/snapshot', state)
                attempt.assert_not_called()
                cmd.assert_called_once_with(['systemctl', 'stop', repair.TIMER])

    def test_killed_attempt_reservations_count_before_another_attempt(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            # Each pending timestamp was fsynced before mutation; none was confirmed successful.
            (state/'failures.json').write_text(json.dumps({'failures': [900, 950, 990], 'latched': False}))
            with patch.object(repair, 'trusted'), patch.object(repair, 'repair') as attempt, \
                 patch.object(repair.time, 'time', return_value=1000), patch.object(repair, 'command'):
                with self.assertRaisesRegex(RuntimeError, 'latched'):
                    repair.guarded_repair('/root/snapshot', state)
                attempt.assert_not_called()

    def test_native_linux_comparison_uses_actual_namespace_inode_not_container_id(self):
        owner, gateway = {'State': {'Pid': 11}}, {'State': {'Pid': 22}}
        with patch.object(repair.os, 'stat') as metadata:
            metadata.return_value.st_ino = 7
            self.assertTrue(repair.same_namespace(owner, gateway))
            self.assertEqual([c.args[0] for c in metadata.call_args_list], ['/proc/11/ns/net', '/proc/22/ns/net'])
            metadata.side_effect = [type('S', (), {'st_ino': 7})(), type('S', (), {'st_ino': 8})()]
            self.assertFalse(repair.same_namespace(owner, gateway))


@unittest.skipUnless(fixture.have_image(), 'needs cached Python Docker image; no pull')
class RootInstall(unittest.TestCase):
    def test_root_snapshot_and_real_unsafe_env_modes_inside_disposable_container(self):
        # Root filesystem is the disposable container, never the host. No socket,
        # network or systemd; only Compose rendering/systemctl commands are faked.
        code = r"""
import importlib.util,json,os,pathlib
spec=importlib.util.spec_from_file_location('repair','/src/tools/loopback_repair.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.INSTALL=pathlib.Path('/installed')
state=pathlib.Path('/state');state.mkdir(mode=0o700)
for name in ('up.env','desks.env'):
 p=state/name;p.write_text('');p.chmod(0o600)
pathlib.Path('/etc/systemd/system').mkdir(parents=True,exist_ok=True)
rendered={'name':'existing-project','services':{
 'ekka-enclave':{'image':'owner-image'},
 'gateway':{'image':'gateway@sha256:pinned','network_mode':'service:ekka-enclave',
            'environment':{'TLS_IDENTITY':'unchanged'},'volumes':[],
            'depends_on':{'ekka-enclave':{'condition':'service_healthy'}}}}}
calls=[]
def lock_held():
 fd=os.open(m.LOCK,os.O_RDWR)
 try:
  try:m.fcntl.flock(fd,m.fcntl.LOCK_EX|m.fcntl.LOCK_NB)
  except BlockingIOError:return True
  return False
 finally:os.close(fd)
def command(args,**kw):
 assert lock_held() == (args[0]=='docker'), 'snapshot lock/release ordering'
 calls.append(args)
 return json.dumps(rendered) if args[0]=='docker' else ''
m.command=command
for kind in ('mode','owner','parent'):
 p=state/'up.env'
 if kind=='mode':p.chmod(0o666)
 if kind=='owner':os.chown(p,501,501)
 if kind=='parent':state.chmod(0o777)
 try:m.install(state,'/src')
 except RuntimeError as e:assert 'untrusted repair path' in str(e)
 else:raise AssertionError('unsafe '+kind+' accepted')
 assert not calls
 p.chmod(0o600);os.chown(p,0,0);state.chmod(0o700)
credentials=pathlib.Path('/credentials');credentials.mkdir(mode=0o700)
tls=credentials/'gateway';tls.mkdir(mode=0o700);os.chown(tls,1000,1000)
key=tls/'tls.key';key.write_text('fixture');key.chmod(0o600);os.chown(key,1000,1000)
m.trusted_gateway_data(tls)
os.chown(key,501,501)
try:m.trusted_gateway_data(tls)
except RuntimeError:pass
else:raise AssertionError('foreign credential owner accepted')
os.chown(key,1000,1000)
m.install(state,'/src')
snapshot=json.loads((m.INSTALL/'compose.json').read_text())
assert snapshot['name']==rendered['name']
expected=dict(rendered['services']['gateway']);expected.pop('depends_on')
assert snapshot['services']['gateway']==expected
assert snapshot['services']['ekka-enclave']=={'image':'owner-image'}
for relative in ('compose.json','tools/loopback_repair.py','tools/gateway_preflight.py','deploy/pins.json'):
 p=m.INSTALL/relative;assert p.stat().st_uid==0 and p.stat().st_mode & 0o777==0o600
assert (m.INSTALL/'compose.json').is_file()
assert calls[-1]==['systemctl','enable','--now',m.UNIT,m.TIMER]
assert m.STATE.stat().st_uid==0 and m.STATE.stat().st_mode & 0o777==0o700
assert m.LOCK.stat().st_uid==0 and m.LOCK.stat().st_mode & 0o777==0o600
"""
        r = subprocess.run(['docker', 'run', '--rm', '--pull', 'never', '--network', 'none', '-v', f'{ROOT}:/src:ro',
                            'python:3.12-alpine', 'python', '-c', code], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout+r.stderr)


class LifecycleRepair(fixture.DockerLifecycle):
    # Override inherited fixture scenario: the existing suite separately tests full up.sh repair.
    def test_first_port_migration_restart_detection_and_explicit_full_repair(self):
        self.new['services']['gateway']['command'] = ['python', '-c',
            'import http.server,signal,threading,time;'
            's=http.server.ThreadingHTTPServer(("",8080),http.server.SimpleHTTPRequestHandler);'
            'threading.Thread(target=s.serve_forever,daemon=True).start();'
            'signal.signal(signal.SIGUSR1,lambda *_:s.server_close());'
            'exec("while True: time.sleep(1)")']
        self.file.write_text(json.dumps(self.new))
        self.repair()
        installed = self.root/'installed'
        for folder in ('tools', 'deploy'):
            (installed/folder).mkdir(parents=True, exist_ok=True)
        (installed/'tools/gateway_preflight.py').write_text('fixture stand-in')
        (installed/'deploy/pins.json').write_text('{}')
        (installed/'compose.json').write_text(json.dumps(self.new))
        real_command = repair.command
        def command(args, **kwargs):
            if args[0] == 'python3':
                return ''  # only real gateway credential preflight is outside this HTTP fixture
            # Snapshot uses the fixture's project identity, as installed Compose JSON does.
            if args[:2] == ['docker', 'compose']:
                args = args[:2]+['-p', self.project]+args[2:]
            return real_command(args, **kwargs)
        def same_namespace(owner, gateway):
            return real_command(['docker', 'exec', owner['Id'], 'readlink', '/proc/1/ns/net']) == real_command(
                ['docker', 'exec', gateway['Id'], 'readlink', '/proc/1/ns/net'])
        with patch.object(repair, 'trusted'), patch.object(repair, 'command', side_effect=command), \
             patch.object(repair, 'same_namespace', side_effect=same_namespace):
            owner, gateway = self.container('ekka-enclave'), self.container('gateway')
            before = repair.inspect(owner)['State']['StartedAt']
            gateway_before = repair.inspect(gateway)['State']['StartedAt']
            failure_state = self.root/'failure-state'; failure_state.mkdir()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                repair.guarded_repair(installed, failure_state)
            self.assertEqual(output.getvalue(), '')
            self.assertEqual(list(failure_state.iterdir()), [])
            self.assertEqual(self.container('gateway'), gateway)
            self.assertEqual(repair.inspect(gateway)['State']['StartedAt'], gateway_before)
            # Listener failure in the SAME actual namespace is not permission to restart.
            subprocess.run(['docker', 'exec', gateway, 'kill', '-USR1', '1'], check=True, capture_output=True)
            for _ in range(20):
                if not self.reachable():
                    break
                time.sleep(0.1)
            self.assertFalse(self.reachable())
            with self.assertRaisesRegex(RuntimeError, 'shares the Enclave namespace'):
                repair.repair(installed, grace=0)
            self.assertEqual(self.container('gateway'), gateway)
            self.assertEqual(repair.inspect(gateway)['State']['StartedAt'], gateway_before)
            self.command('up', '-d', '--no-deps', '--force-recreate', 'gateway')
            gateway = self.container('gateway')
            # An intentional all-desks gateway stop must remain stopped.
            subprocess.run(['docker', 'stop', gateway], check=True, capture_output=True, timeout=10)
            repair.repair(installed, grace=0)
            self.assertFalse(repair.inspect(gateway)['State']['Running'])
            self.assertEqual(repair.inspect(owner)['State']['StartedAt'], before)
            subprocess.run(['docker', 'start', gateway], check=True, capture_output=True, timeout=10)
            subprocess.run(['docker', 'stop', owner], check=True, capture_output=True, timeout=10)
            repair.repair(installed, grace=0)
            self.assertFalse(repair.inspect(owner)['State']['Running'])
            self.assertEqual(self.container('gateway'), gateway)
            subprocess.run(['docker', 'start', owner], check=True, capture_output=True, timeout=10)
            subprocess.run(['docker', 'restart', owner], check=True, capture_output=True, timeout=10)
            self.assertFalse(self.reachable())
            for _ in range(30):
                if repair.inspect(owner)['State'].get('Health', {}).get('Status') == 'healthy':
                    break
                time.sleep(0.1)
            owner_started = repair.inspect(owner)['State']['StartedAt']
            repair.repair(installed, grace=0)
            self.assertTrue(self.reachable())
            self.assertEqual(self.container('ekka-enclave'), owner)
            self.assertEqual(repair.inspect(owner)['State']['StartedAt'], owner_started)
            self.assertNotEqual(self.container('gateway'), gateway)
            self.assertEqual((self.journal/'desk.lock').read_text(), 'held-attempt-1')
            self.assertEqual(json.loads((self.journal/'attempt.json').read_text()), {'state': 'held', 'outcome': 'unknown'})
