"""Linux Docker-start oneshot: repair only an already-running shared gateway.

Installed code/config are root-owned snapshots. No enrollment, keys, grants,
Enclave restart, task replay or recovery of deliberately stopped services.
"""
import json
import contextlib
import fcntl
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time

INSTALL = Path('/usr/local/libexec/ekka-desks-loopback')
UNIT = 'ekka-desks-loopback-repair.service'
TIMER = 'ekka-desks-loopback-repair.timer'
STATE = Path('/var/lib/ekka-desks-loopback')
LOCK = Path('/run/ekka-desks-loopback-repair.lock')


def trusted(path, directory=False):
    """Every path component must be root-owned, non-symlink and not writable by others."""
    path = Path(path).absolute()
    for item in [path, *path.parents]:
        s = item.lstat()
        if stat.S_ISLNK(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
            raise RuntimeError('untrusted repair path: '+str(item))
    if not (path.is_dir() if directory else path.is_file()):
        raise RuntimeError('unexpected repair file type: '+str(path))


def trusted_gateway_data(path):
    # setup.sh deliberately gives these read-only credential trees to gateway
    # uid1000. Their root-owned parent anchors the path; they are data, never
    # code/config executed by this root service. Preserve that existing trust.
    path = Path(path)
    trusted(path.parent, directory=True)
    if not path.is_dir():
        raise RuntimeError('gateway credential mount must be a directory')
    for item in [path, *path.rglob('*')]:
        s = item.lstat()
        if (s.st_uid not in (0, 1000) or s.st_mode & 0o022
                or not (stat.S_ISDIR(s.st_mode) or stat.S_ISREG(s.st_mode))):
            raise RuntimeError('untrusted gateway credential path: '+str(item))


def command(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=25, **kwargs).stdout.strip()


def inspect(container):
    return json.loads(command(['docker', 'inspect', container]))[0]


def compose(root, *args):
    return ['docker', 'compose', '--env-file', '/dev/null', '-f', str(root/'compose.json'), *args]


def probe(root):
    try:
        command(compose(root, 'exec', '-T', 'ekka-enclave', 'sh', '-c', 'nc -z -w 3 127.0.0.1 8080 < /dev/null'))
        return True
    except subprocess.SubprocessError:
        return False


def running_pair(root):
    ids = [command(compose(root, 'ps', '-a', '-q', service)) for service in ('ekka-enclave', 'gateway')]
    if not all(ids):
        return None
    owner, gateway = [inspect(i) for i in ids]
    if not all(c['State']['Running'] for c in (owner, gateway)):
        return None  # never undo an intentional stop, including the all-desks kill switch
    if not gateway['HostConfig']['NetworkMode'].startswith('container:'):
        raise RuntimeError('gateway is not already configured with a shared namespace')
    return owner, gateway


def same_namespace(owner, gateway):
    # Native Linux host namespace identity, not container ID or a failed TCP
    # probe. A restarted owner can retain its ID while changing this inode.
    return os.stat(f"/proc/{owner['State']['Pid']}/ns/net").st_ino == os.stat(
        f"/proc/{gateway['State']['Pid']}/ns/net").st_ino


def save_failures(path, data):
    trusted(path.parent, directory=True)
    if path.exists() or path.is_symlink():
        trusted(path)
    temporary = path.with_suffix('.new')
    if temporary.exists() or temporary.is_symlink():
        trusted(temporary)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(data, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def guarded_repair(root, state=STATE):
    # The service and timer enter through the same flock. A reservation is
    # durable BEFORE replacement: timeout/SIGKILL cannot erase a failed attempt.
    path = Path(state)/'failures.json'
    trusted(path.parent, directory=True)
    data = {'failures': [], 'latched': False}
    try:
        if path.exists() or path.is_symlink():
            trusted(path)
            data = json.loads(path.read_text())
            if (not isinstance(data, dict) or set(data) != {'failures', 'latched'} or type(data['latched']) is not bool
                    or not isinstance(data['failures'], list)
                    or any(type(t) not in (int, float) or not math.isfinite(t) or t < 0 for t in data['failures'])):
                raise ValueError('invalid ledger')
    except (ValueError, TypeError, RuntimeError):
        command(['systemctl', 'stop', TIMER])
        raise RuntimeError('invalid repair failure ledger; timer stopped; operator intervention required') from None
    data['failures'] = [t for t in data['failures'] if t >= time.time()-600]
    if data['latched'] or len(data['failures']) >= 3:
        data['latched'] = True
        save_failures(path, data)
        command(['systemctl', 'stop', TIMER])
        raise RuntimeError('repair latched after 3 failures in 10 minutes; timer stopped; explicit reset required')
    reserved = False
    def reserve():
        nonlocal reserved
        data['failures'].append(time.time())
        save_failures(path, data)
        reserved = True
    try:
        repair(root, before_attempt=reserve)
    except BaseException:
        if not reserved:
            reserve()
        if len(data['failures']) >= 3:
            data['latched'] = True
            save_failures(path, data)
            command(['systemctl', 'stop', TIMER])
        raise
    if reserved:
        data['failures'].pop()  # only this confirmed success; retain prior failures
        save_failures(path, data)


def repair(root, grace=30, before_attempt=lambda: None):
    root = Path(root)
    for file in ('compose.json', 'tools/gateway_preflight.py', 'deploy/pins.json'):
        trusted(root/file)
    config = json.loads((root/'compose.json').read_text())
    gateway = config['services']['gateway']
    if 'env_file' in gateway:
        raise RuntimeError('repair snapshot may not read mutable env files')
    if gateway.get('network_mode') != 'service:ekka-enclave':
        raise RuntimeError('repair snapshot is not the shared-loopback topology')
    # Revalidate privileged mounted inputs before any Compose start. Never trust
    # an env file or arbitrary development checkout on an unattended root path.
    for mount in gateway.get('volumes', []):
        if mount['type'] == 'bind' and mount['target'] == '/var/run/docker.sock' and mount['source'] != '/var/run/docker.sock':
            raise RuntimeError('unexpected Docker socket source')
        if mount['type'] == 'bind' and mount['target'] != '/var/run/docker.sock':
            source = Path(mount['source'])
            if mount['target'] in ('/tls/gateway', '/tls/jwt'):
                if not mount.get('read_only'):
                    raise RuntimeError('gateway credential mounts must remain read-only')
                trusted_gateway_data(source)
                continue
            trusted(source, directory=source.is_dir())
            if source.is_dir():
                for child in source.rglob('*'):
                    trusted(child, directory=child.is_dir())
    # Docker starts restart-policy containers asynchronously. Absence/stop is
    # never permission to start either service, even on a timer invocation.
    until = time.monotonic()+grace
    pair = running_pair(root)
    while pair is None and time.monotonic() < until:
        time.sleep(2)
        pair = running_pair(root)
    if pair is None:
        return
    if same_namespace(*pair):
        while not probe(root) and time.monotonic() < until:
            time.sleep(2)
        if probe(root):
            return  # healthy checks are read-only and silent
        raise RuntimeError('gateway shares the Enclave namespace but is unavailable; no restart attempted')
    if pair[0]['State'].get('Health', {}).get('Status', 'healthy') != 'healthy':
        raise RuntimeError('Enclave is not healthy; gateway was not replaced')
    command(['python3', str(root/'tools/gateway_preflight.py')], input=json.dumps(config))
    # Last possible check after preflight: do not undo an operator stop, and
    # never repair a pair another invocation already brought into one namespace.
    pair = running_pair(root)
    if pair is None:
        return
    if same_namespace(*pair):
        if probe(root):
            return
        raise RuntimeError('gateway shares the Enclave namespace but is unavailable; no restart attempted')
    before_attempt()
    command(compose(root, 'up', '-d', '--no-deps', '--no-build', '--pull', 'never', '--force-recreate', 'gateway'))
    until = time.monotonic()+30
    while not probe(root) and time.monotonic() < until:
        time.sleep(2)
    if not probe(root):
        raise RuntimeError('gateway loopback remains unavailable after gateway-only repair')
    print('gateway namespace repaired; Enclave and durable attempts retained', flush=True)


@contextlib.contextmanager
def installation_lock():
    trusted(LOCK.parent, directory=True)
    if LOCK.exists() or LOCK.is_symlink():
        trusted(LOCK)
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        until = time.monotonic()+5
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= until:
                    raise RuntimeError('gateway repair is active; stop service/timer before maintenance')
                time.sleep(0.1)
        yield
    finally:
        os.close(fd)


def install(state, source):
    if os.geteuid() != 0:
        raise RuntimeError('install requires root')
    state = Path(state).absolute()
    trusted(state, directory=True)
    for name in ('up.env', 'desks.env'):
        trusted(state/name)
    # Share the service/timer lock while replacing all code/config/unit files.
    # Release BEFORE starting the unit, whose ExecStart acquires this same lock.
    with installation_lock():
        install_snapshot(state, source)
    command(['systemctl', 'daemon-reload'])
    command(['systemctl', 'enable', '--now', UNIT, TIMER])


def install_snapshot(state, source):
    if os.geteuid() != 0:
        raise RuntimeError('install requires root')
    state, source = Path(state).absolute(), Path(source).absolute()
    trusted(state, directory=True)
    envs = [state/'up.env', state/'desks.env']
    for env in envs:
        trusted(env)
    args = ['docker', 'compose', '-f', str(source/'deploy/compose.yml')]
    for env in envs:
        args += ['--env-file', str(env)]
    rendered = json.loads(command([*args, 'config', '--format', 'json']))
    if rendered['services']['gateway'].get('network_mode') != 'service:ekka-enclave':
        raise RuntimeError('only an installed shared-loopback topology can enable repair')
    # No mutable env/checkout is read at boot. The sole target is gateway;
    # --no-deps means this namespace reference never starts the Enclave.
    rendered['services'] = {'gateway': rendered['services']['gateway'],
                            'ekka-enclave': {'image': rendered['services']['ekka-enclave']['image']}}
    rendered['services']['gateway'].pop('depends_on', None)
    INSTALL.mkdir(mode=0o700, parents=True, exist_ok=True)
    trusted(INSTALL, directory=True)
    for folder in ('tools', 'deploy'):
        (INSTALL/folder).mkdir(mode=0o700, exist_ok=True)
        trusted(INSTALL/folder, directory=True)
    for relative in ('tools/loopback_repair.py', 'tools/gateway_preflight.py', 'deploy/pins.json'):
        destination = INSTALL/relative
        if destination.exists() or destination.is_symlink():
            trusted(destination)
        shutil.copyfile(source/relative, destination)
        destination.chmod(0o600)
    config = INSTALL/'compose.json'
    if config.exists() or config.is_symlink():
        trusted(config)
    config.write_text(json.dumps(rendered))
    config.chmod(0o600)
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    trusted(STATE, directory=True)
    # Unit contents are fixed in the reviewed kit, never generated from env values.
    for name in (UNIT, TIMER):
        unit = Path('/etc/systemd/system')/name
        trusted(unit.parent, directory=True)
        if unit.exists() or unit.is_symlink():
            trusted(unit)
        shutil.copyfile(source/'deploy'/name, unit)
        unit.chmod(0o644)


def main():
    try:
        if sys.argv[1:] == ['run']:
            guarded_repair(INSTALL)
        elif len(sys.argv) == 4 and sys.argv[1] == 'install':
            install(sys.argv[2], sys.argv[3])
        else:
            raise RuntimeError('expected run or install <state> <source>')
    except Exception as error:
        # stderr is retained by journald and exit1 marks the oneshot failed.
        # Do not print subprocess output or config: either may contain credentials.
        print('<3>GATEWAY_REPAIR_FAILED: '+str(error).splitlines()[0] if isinstance(error, RuntimeError)
              else '<3>GATEWAY_REPAIR_FAILED: command or configuration failed; run up.sh status and inspect this unit journal', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
