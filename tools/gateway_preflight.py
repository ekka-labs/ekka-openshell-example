"""Before NVIDIA's gateway starts: refuse anything that would let it hand the desks' credentials out.

    docker compose -f compose.yml --env-file <env> config --format json | python3 tools/gateway_preflight.py

A desk's agent never holds a credential: the gateway keeps it. OpenShell 0.1.4 can also export real credential
values to an "operator", meaning any caller with an OU=operator client certificate under a client CA the gateway
trusts. This setup signs everyone in through OIDC and never needs either. So the gateway does not start, and each
reason is named, when:

  - the gateway.toml compose mounts sets [openshell.gateway.tls] client_ca_path, or [openshell.gateway.mtls_auth]
    operator_enabled or enabled;
  - the gateway's environment sets OPENSHELL_TLS_CLIENT_CA, OPENSHELL_ENABLE_OPERATOR_AUTH or
    OPENSHELL_ENABLE_MTLS_AUTH, to any value, or points OPENSHELL_GATEWAY_CONFIG anywhere but that gateway.toml;
  - the gateway is given any command or entrypoint (this setup runs it with none; every setting is in gateway.toml);
  - the supervisor binary compose mounts is not the one deploy/pins.json pins (a new gateway with an old
    supervisor is how a pin bump goes half done).

The running Enclave checks the live gateway as well and refuses desk work if it asks for a client certificate
. This is the setup's own door; neither replaces the other.
"""
import hashlib
import json
import os
import re
import sys

try:
    import tomllib
except ImportError:  # Python 3.9 and 3.10. Amazon Linux 2023's own python3 is 3.9.25 (measured); see plain_toml
    tomllib = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PINS = os.path.join(ROOT, "deploy", "pins.json")
CONFIG_IN_CONTAINER = "/etc/openshell/gateway.toml"
REFUSED_ENV = ("OPENSHELL_TLS_CLIENT_CA", "OPENSHELL_ENABLE_OPERATOR_AUTH", "OPENSHELL_ENABLE_MTLS_AUTH")


# The plain TOML this setup writes (deploy/gateway/gateway.toml.template, tools/workspace_policy.py), read where
# Python has no tomllib. It reads only: comments, [table], [[table]], and `key = value` where the value is a "string"
# with no backslash, an integer, true or false, or a one-line array of those. Anything else is refused by line, never
# guessed at: dotted or quoted keys, inline tables, literal and multi-line strings, a key or table given twice. So
# what it returns is what tomllib returns for the same file (tests/test_gateway_preflight.py holds that).
BARE = r"[A-Za-z0-9_-]+"
HEADER = re.compile(rf"^(\[\[?)({BARE}(?:\.{BARE})*)(\]\]?)[ \t]*(?:#.*)?$")
PAIR = re.compile(rf"^({BARE})[ \t]*=[ \t]*")
SCALAR = re.compile(r'"([^"\\\x00-\x08\x0a-\x1f\x7f]*)"|(true|false|[+-]?(?:0|[1-9][0-9]*))(?=[ \t,\]#]|$)')
REST = re.compile(r"^[ \t]*(?:#.*)?$")


class PlainTOMLError(ValueError):
    pass


def plain_toml(text):
    """gateway.toml as tomllib would read it, for the plain subset only; PlainTOMLError on anything else."""
    root, defined, arrays = {}, set(), set()   # arrays: id() of each list a [[table]] made, the only ones it extends
    table = root
    for n, raw in enumerate(text.split("\n"), 1):
        line = (raw[:-1] if raw.endswith("\r") else raw).strip(" \t")
        if not line or line.startswith("#"):
            continue
        m = HEADER.match(line)
        if m:
            name, many = m.group(2), m.group(1) == "[["
            if many != (m.group(3) == "]]"):
                raise PlainTOMLError(f"line {n}: a table header this check does not read")
            keys = name.split(".")
            parent = root
            for k in keys[:-1]:
                parent = parent.setdefault(k, {})
                if isinstance(parent, list) and id(parent) in arrays:
                    parent = parent[-1]
                if not isinstance(parent, dict):
                    raise PlainTOMLError(f"line {n}: {k} is a value, not a table")
            last, path = keys[-1], tuple(keys)
            if many:
                rows = parent.get(last)
                if rows is None:
                    rows = parent[last] = []
                    arrays.add(id(rows))
                elif not (isinstance(rows, list) and id(rows) in arrays):
                    raise PlainTOMLError(f"line {n}: [[{name}]] names something that is not a table array")
                table = {}
                rows.append(table)
                defined = {d for d in defined if d[:len(path)] != path}   # a new row starts its own tables
            else:
                if path in defined or not isinstance(parent.setdefault(last, {}), dict):
                    raise PlainTOMLError(f"line {n}: [{name}] is given twice, or is not a table")
                defined.add(path)
                table = parent[last]
            continue
        m = PAIR.match(line)
        if not m:
            raise PlainTOMLError(f"line {n}: not a plain `key = value` this check reads")
        key = m.group(1)
        if key in table:
            raise PlainTOMLError(f"line {n}: {key} is given twice")
        value, i = plain_value(line, m.end(), n, key)
        if not REST.match(line[i:]):
            raise PlainTOMLError(f"line {n}: something follows {key}'s value")
        table[key] = value
    return root


def plain_value(line, i, n, key):
    """The value at line[i:] and where it ends: one scalar, or a one-line array of them."""
    if not line.startswith("[", i):
        v = SCALAR.match(line, i)
        if not v:
            raise PlainTOMLError(f"line {n}: {key} holds a value this check does not read")
        return scalar(v), v.end()
    values, i = [], i + 1
    while True:
        i += len(line[i:]) - len(line[i:].lstrip(" \t"))
        if line.startswith("]", i):                 # empty, or after a trailing comma
            return values, i + 1
        v = SCALAR.match(line, i)
        if not v:
            raise PlainTOMLError(f"line {n}: {key} holds an array this check does not read")
        values.append(scalar(v))
        i = v.end() + len(line[v.end():]) - len(line[v.end():].lstrip(" \t"))
        if line.startswith("]", i):
            return values, i + 1
        if not line.startswith(",", i):
            raise PlainTOMLError(f"line {n}: {key} holds an array this check does not read")
        i += 1


def scalar(m):
    if m.group(1) is not None:
        return m.group(1)
    return {"true": True, "false": False}[m.group(2)] if m.group(2) in ("true", "false") else int(m.group(2))


def read_toml(path):
    with open(path, "rb") as f:
        if tomllib:
            return tomllib.load(f)
        return plain_toml(f.read().decode("utf-8"))


def toml_reasons(cfg):
    """Every reason a parsed gateway.toml would let a client certificate in; empty when it would not."""
    reasons = []

    def table(parent, key, name):
        # A list where a table belongs ([[openshell.gateway.tls]]) is not read past: refused, by name.
        got = parent.get(key, {})
        if isinstance(got, dict):
            return got
        reasons.append(f"gateway.toml's {name} is not a table, so this check cannot read it. Write it as {name}")
        return {}

    gw = table(table(cfg, "openshell", "[openshell]"), "gateway", "[openshell.gateway]")
    if table(gw, "tls", "[openshell.gateway.tls]").get("client_ca_path"):
        reasons.append("gateway.toml sets [openshell.gateway.tls] client_ca_path: the gateway would trust client "
                       "certificates, and an operator certificate can export desk credentials. Remove the line")
    mtls = table(gw, "mtls_auth", "[openshell.gateway.mtls_auth]")
    for key in ("operator_enabled", "enabled"):
        if mtls.get(key) is not False and key in mtls:
            reasons.append(f"gateway.toml sets [openshell.gateway.mtls_auth] {key} = {json.dumps(mtls[key])}: "
                           f"this setup signs people in through OIDC only. Set it to false")
    return reasons


def mounted_source(service, target):
    """The host path compose binds at `target` inside the service, or None."""
    for v in service.get("volumes") or []:
        if isinstance(v, dict) and v.get("target") == target and v.get("type") == "bind":
            return v.get("source")
    return None


def service_reasons(service):
    """Every reason the gateway service's own settings would let a client certificate in, or check another file."""
    reasons = []
    env = service.get("environment") or {}
    for name in REFUSED_ENV:
        if name in env:
            reasons.append(f"the gateway's environment sets {name}: this setup never sets it. Remove it")
    if env.get("OPENSHELL_GATEWAY_CONFIG") != CONFIG_IN_CONTAINER:
        reasons.append(f"the gateway's OPENSHELL_GATEWAY_CONFIG is {env.get('OPENSHELL_GATEWAY_CONFIG')!r}, not "
                       f"{CONFIG_IN_CONTAINER}: this check reads only the file mounted there")
    if service.get("command"):
        reasons.append("the gateway is given a command: this setup runs it with none, every setting in gateway.toml")
    if service.get("entrypoint"):
        reasons.append("the gateway is given an entrypoint: this setup runs NVIDIA's own")
    return reasons


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def reasons(compose, pins):
    """Every reason the gateway must not start, from compose's resolved config; empty when it may."""
    service = (compose.get("services") or {}).get("gateway")
    if not service:
        return ["compose has no gateway service"]
    out = service_reasons(service)
    toml_path = mounted_source(service, CONFIG_IN_CONTAINER)
    if not toml_path:
        out.append(f"compose mounts no gateway.toml at {CONFIG_IN_CONTAINER}")
    else:
        try:
            out += toml_reasons(read_toml(toml_path))
        except (OSError, ValueError) as e:  # tomllib's TOMLDecodeError, PlainTOMLError and a bad UTF-8 byte included
            out.append(f"{toml_path} cannot be read as TOML: {e}")
    sup = (service.get("environment") or {}).get("OPENSHELL_DOCKER_SUPERVISOR_BIN")
    sup_path = mounted_source(service, sup) if sup else None
    want = pins["supervisor"]["binary_sha256"]
    if not sup_path:
        out.append("compose mounts no supervisor binary at OPENSHELL_DOCKER_SUPERVISOR_BIN")
    else:
        try:
            got = sha256_file(sup_path)
        except OSError as e:
            got = f"unreadable ({e.strerror})"
        if got != want:
            out.append(f"the supervisor binary {sup_path} is not OpenShell {pins['version']}'s (sha256 {got[:12]}, "
                       f"pinned {want[:12]}). Run: sudo ./deploy/setup.sh supervisor")
    return out


def main():
    with open(PINS) as f:
        pins = json.load(f)["openshell"]
    try:
        compose = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"gateway preflight: compose's config is not JSON: {e}", file=sys.stderr)
        return 1
    found = reasons(compose, pins)
    for r in found:
        print(f"gateway preflight: {r}", file=sys.stderr)
    if found:
        print("gateway preflight: the gateway was not started", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
