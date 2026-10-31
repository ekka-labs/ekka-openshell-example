"""Which NVIDIA openshell commands `sudo ./up.sh openshell` runs as the gateway's administrator (ADK-E8, runner#1042).

    python3 tools/openshell_allow.py <log file> <openshell arguments...>

Owner, 2026-10-15: "Nothing happens on a desk except through EKKA." So this machine's own door to NVIDIA's CLI keeps
exactly three kinds of command, each named in full below, and refuses every other by name before the CLI runs:

  - reads, which change nothing;
  - `sandbox stop <desk>`, the on-box kill switch for when EKKA's cloud is unreachable (D8). The desk is always named:
    with no name NVIDIA stops the last one used;
  - provider setup: providers and their keys are set at install, on this machine (D7), and so is their refresh.

A secret never travels as an argument, where `ps` and this log would show it: a credential is named and read from the
environment (`--credential KEY`, `--secret-material-env`), never written as `KEY=VALUE`. Refresh material goes only
through `--secret-material-env`: NVIDIA's `--material KEY=VALUE` is refused, so no value can be sent the wrong way. A
provider never takes the administrator's own sign-in as its key (`--from-oidc-token`).

The call always goes to this machine's gateway. NVIDIA's gateway flags are global, so one placed after the command
would still win over the `-g ekka-agent-desks` up.sh puts before it; `-g`, `--gateway`, `--gateway-endpoint` and
`--gateway-insecure` are refused in every spelling, and up.sh clears their environment twins (OPENSHELL_GATEWAY*).
`--workspace` stays: it picks a workspace on this same gateway.

Every allowed call is appended to the log, with the command and the person who ran sudo, before it runs.
Exit 0: allowed and logged. Exit 1: refused, nothing logged and nothing run.
"""
import datetime as dt
import json
import os
import re
import sys

READS = {("status",), ("sandbox", "list"), ("sandbox", "get"), ("provider", "list"), ("provider", "get"),
         ("provider", "list-profiles"), ("provider", "refresh", "status")}
STOP = ("sandbox", "stop")
PROVIDER_SETUP = {("provider", "create"), ("provider", "update"), ("provider", "delete"),
                  ("provider", "refresh", "configure"), ("provider", "refresh", "rotate"),
                  ("provider", "refresh", "delete")}
ALLOWED = READS | PROVIDER_SETUP | {STOP}
DESK = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
EKKA = "desk changes run through EKKA: ekka desk <op>"


class Refused(Exception):
    pass


def command(argv):
    """The command words at the front, exactly as NVIDIA names them: no alias, no flag before them."""
    for n in (3, 2, 1):
        if tuple(argv[:n]) in ALLOWED:
            return tuple(argv[:n])
    words = []
    for a in argv[:3]:
        if a.startswith("-"):
            break
        words.append(a)
    raise Refused(f"openshell {' '.join(words) or '(no command)'}: {EKKA}")


def flag_values(rest, name):
    """Every value given to `--name`, in either spelling: `--name v` or `--name=v`."""
    out = []
    for i, a in enumerate(rest):
        if a == name and i + 1 < len(rest):
            out.append(rest[i + 1])
        elif a.startswith(name + "="):
            out.append(a[len(name) + 1:])
    return out


def given(rest, name):
    """True when `--name` appears, in either spelling: `--name v` or `--name=v`."""
    return any(a == name or a.startswith(name + "=") for a in rest)


GATEWAY_FLAGS = ("--gateway", "--gateway-endpoint", "--gateway-insecure")


def short_g(a):
    """`-g`, `-gNAME`, `-g=NAME`, or `g` inside a cluster of short flags such as `-vg`."""
    return a.startswith("-") and not a.startswith("--") and "g" in a[1:].split("=", 1)[0]


def check(argv):
    """The command these arguments name, or Refused with the line to print."""
    cmd = command(argv)
    rest = argv[len(cmd):]
    if cmd == STOP:
        if len(rest) != 1 or not DESK.match(rest[0]):
            raise Refused("openshell sandbox stop: name exactly one desk, e.g. sudo ./up.sh openshell sandbox stop code-desk")
    if any(a == "--from-oidc-token" for a in rest):
        raise Refused("openshell --from-oidc-token: the gateway administrator's own sign-in never becomes a desk's key")
    if any("=" in v for v in flag_values(rest, "--credential")):
        raise Refused("openshell --credential KEY=VALUE: a secret is never an argument; name the key and pass it in the "
                      "environment: sudo --preserve-env=KEY ./up.sh openshell ... --credential KEY")
    for name in ("--material", "--secret-material-key"):
        if given(rest, name):
            raise Refused(f"openshell {name}: refresh material is never an argument; pass each value in the "
                          "environment: sudo --preserve-env=ENVVAR ./up.sh openshell ... --secret-material-env KEY=ENVVAR")
    for name in GATEWAY_FLAGS:
        if given(rest, name):
            raise Refused(f"openshell {name}: the call always goes to this machine's gateway; "
                          "drop the flag: sudo ./up.sh openshell <command>")
    if any(short_g(a) for a in rest):
        raise Refused("openshell -g: the call always goes to this machine's gateway; "
                      "drop the flag: sudo ./up.sh openshell <command>")
    return cmd


def log_line(argv, now=None, env=os.environ):
    who = env.get("SUDO_USER") or env.get("USER") or "unknown"
    at = (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return json.dumps({"at": at, "user": who, "command": ["openshell", *argv]}, separators=(",", ":"))


def append(path, line):
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, (line + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def main(argv):
    if len(argv) < 1:
        print("usage: openshell_allow.py <log file> <openshell arguments...>", file=sys.stderr)
        return 2
    path, args = argv[0], argv[1:]
    try:
        check(args)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 1
    try:
        append(path, log_line(args))
    except OSError as e:
        print(f"REFUSED: the call could not be logged ({e.strerror}); nothing was run", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
