"""Before the backend adapter starts: its configuration and the gateway's CA must be real files.

    python3 tools/backend_preflight.py <state folder> [--always]

The adapter (ADK-E1) bind-mounts two files, read-only: `backend/config.json` and `tls/ca.crt`. If either is
missing, Docker's short bind syntax CREATES AN EMPTY DIRECTORY in its place and the container starts on it.
compose.yml asks for the opposite (`create_host_path: false`), and Compose v2.38.2 and v5.5.1 honor it (measured),
but that is Compose's behavior, version by version, and an older Compose renders and treats it differently.
So this check does not lean on Compose: before any start, each file must exist, be a regular file and not a
link, or nothing is started and each reason is printed with its fix.

It runs only when the adapter would start: the `desk-admin` profile is on in up.env. `--always` checks anyway,
for a command that runs the adapter by name (a profile does not stop that).

Exit 0: the adapter may start (or is not going to). Exit 1: refused, nothing started.
"""
import os
import stat
import sys

FILES = (
    ("backend/config.json", "the adapter's configuration, written once this machine is enrolled: run sudo ./up.sh"),
    ("tls/ca.crt", "the gateway's CA, made by setup.sh init: run sudo ./up.sh"),
)


def profile_on(state):
    """The `desk-admin` profile is on in up.env."""
    try:
        with open(os.path.join(state, "up.env"), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return False
    for line in lines:
        if line.startswith("COMPOSE_PROFILES="):
            if "desk-admin" in [p.strip() for p in line.split("=", 1)[1].split(",")]:
                return True
    return False


def reasons(state):
    """Why the adapter must not start: one line per file that is not a real, regular file."""
    out = []
    for rel, fix in FILES:
        path = os.path.join(state, rel)
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            out.append(f"{path} does not exist; it is {fix}")
            continue
        except OSError as e:
            out.append(f"{path} cannot be read ({e.strerror}); it is {fix}")
            continue
        if stat.S_ISLNK(st.st_mode):
            out.append(f"{path} is a link, not the file itself; replace it with the file ({fix})")
        elif stat.S_ISDIR(st.st_mode):
            out.append(f"{path} is a directory, as a bind of a missing file leaves behind; remove it, then it is {fix}")
        elif not stat.S_ISREG(st.st_mode):
            out.append(f"{path} is not a regular file; it is {fix}")
    return out


def main(argv):
    args = [a for a in argv if a != "--always"]
    if len(args) != 1:
        print("usage: backend_preflight.py <state folder> [--always]", file=sys.stderr)
        return 2
    state = args[0]
    if "--always" not in argv and not profile_on(state):
        return 0
    found = reasons(state)
    for r in found:
        print(f"REFUSED: the backend adapter was not started: {r}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
