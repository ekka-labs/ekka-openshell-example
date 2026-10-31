"""The gateway's folder settings (host folders stay off) and the installer's retained settings.

Desk files live in separate storage; no host folder is ever attached. The local install that could opt in was
removed from this setup in 2026-10 ; `configure` keeps the gateway's attachment-label checks on.
"""
import os
from pathlib import Path
import re
import sys
import tempfile


def retained(state, key, fallback):
    if key not in ('EKKA_ENV', 'EKKA_VERSION'):
        raise ValueError('unsupported saved installer setting')
    p = Path(state) / 'up.env'
    values = re.findall(rf'^{key}=(.*)$', p.read_text(), re.M) if p.exists() else []
    if not values:
        return fallback
    if len(values) != 1 or not re.fullmatch(r'[A-Za-z0-9._-]+', values[0]):
        raise ValueError(f'invalid saved {key}; nothing changed')
    return values[0]


def configure(path, enabled, pending=None):
    p = Path(path)
    if p.is_symlink() or not p.is_file():
        raise ValueError('gateway configuration must be a real file, not a link')
    text = p.read_text()
    admission = re.search(r'^\s*\[openshell\.drivers\.docker\.resource_admission\]\s*\n(.*?)(?=^\s*\[|\Z)', text, re.M | re.S)
    if enabled and re.search(r'^\s*resource_admission\s*=', text, re.M):
        raise ValueError('inline attachment settings need operator review; configuration unchanged')
    if enabled and admission and re.search(r'^\s*enabled\s*=\s*false\b', admission.group(1), re.M):
        raise ValueError('host-folder mode requires attachment-label checks to stay enabled; configuration unchanged')
    header = '[openshell.drivers.docker]'
    if text.splitlines().count(header) != 1:
        raise ValueError('expected one Docker driver table; gateway configuration unchanged')
    start = text.index(header) + len(header)
    tail = text[start:]
    next_table = re.search(r'^\s*\[', tail, re.M)
    end = start + next_table.start() if next_table else len(text)
    section = text[start:end]
    for key in ('enable_bind_mounts', 'allow_driver_config'):
        pattern = rf'^{key}\s*=\s*(true|false)([^\n]*)$'
        if len(re.findall(pattern, section, re.M)) != 1:
            raise ValueError(f'expected one {key} boolean; gateway configuration unchanged')
        section = re.sub(pattern, lambda m: m.group(0) if m.group(1) == str(enabled).lower()
                         else f'{key} = {str(enabled).lower()}', section, flags=re.M)
    result = text[:start] + section + text[end:]
    if result == text:
        return False
    # Keep the reload request before changing the configuration. A crash between writes must not
    # leave the next run thinking a running gateway already loaded the new file.
    if pending:
        Path(pending).write_text(str(enabled).lower() + '\n')
    # Keep permissions and ownership of the existing operator-owned configuration.
    meta = p.stat()
    fd, tmp = tempfile.mkstemp(prefix='.workspace-policy-', dir=p.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(result)
            f.flush()
            os.fsync(f.fileno())
            os.fchmod(f.fileno(), meta.st_mode & 0o777)
            if os.geteuid() == 0:
                os.fchown(f.fileno(), meta.st_uid, meta.st_gid)
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return True


def main(args):
    try:
        if args[0] == 'retained':
            print(retained(args[1], args[2], args[3]))
        elif args[0] == 'configure' and args[2] in ('true', 'false'):
            print('changed' if configure(args[1], args[2] == 'true', args[3] if len(args) > 3 else None) else 'kept')
        else:
            raise ValueError('unknown workspace policy command')
    except (ValueError, OSError, IndexError) as e:
        print(f'workspace policy: {e}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
