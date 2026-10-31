"""The judgements behind the walk's desk administration legs .

The walk (tools/walk.sh) runs on the installed machine and hands each check what it read there: `docker inspect`,
the grant listing, the sign-in client's scopes, a command's output, the adapter's state and logs. Kept here, not
inline in the walk, so each check is tested with a planted bad case in CI (tests/test_desk_admin_checks.py), where
no machine is needed. Each returns the problems it found; none means the leg passes.

    python3 tools/desk_admin_checks.py adapter  <docker-inspect.json>
    python3 tools/desk_admin_checks.py grant    <grant-list.json>
    python3 tools/desk_admin_checks.py scopes   <scope,scope,...>
    python3 tools/desk_admin_checks.py refused  <CODE>            < the command's output
    python3 tools/desk_admin_checks.py outcome  <outcome>         < the command's output
    python3 tools/desk_admin_checks.py exited                     < the desk's containers, as JSON [[id, state, sandbox]]
    python3 tools/desk_admin_checks.py clean    <path>...         (the planted value on standard input, never argv)

Prints each problem and exits 1, or prints "ok" and exits 0. Standard library only.
"""
import json
import re
import os
import sys

# What the adapter's container must be (ADK-E1), as `docker inspect` reports it: compose.yml's four volumes, the two
# files read-only (regression review).
ADAPTER_MOUNTS = {"/run/ekka-backend", "/var/lib/ekka-backend", "/etc/ekka-backend/config.json", "/etc/ekka-backend/ca.pem"}
ADAPTER_READ_ONLY = {"/etc/ekka-backend/config.json", "/etc/ekka-backend/ca.pem"}
ADAPTER_NETWORK_SUFFIX = "_backend"           # compose prefixes the project name: ekka-desks_backend
# What a person's sign-in may hold (ADK-E2): reading only.
PEOPLE_SCOPES = {"sandbox:read", "provider:read", "workspace:read", "config:read"}
# Keycloak's built-in identity scopes are not OpenShell permissions. Unknown scope names still fail.
IDENTITY_SCOPES = {"openid", "offline_access", "acr", "basic", "email", "profile", "roles", "web-origins"}
# The limits every desks grant carries: the vendored contract's own required list (grant-constraints desks.v1).
DESKS_LIMITS_SCHEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vendor", "ekka-contracts",
                                   "governance", "grant-constraints", "desks.v1.schema.json")
# The adapter contract's fixed words (@ekka-ai/contracts backend/backend-v1.schema.json).
OUTCOMES = {"ready", "refused", "unknown", "running"}


def adapter(inspect):
    """The adapter container, from `docker inspect <container>` (a one-item list or the item)."""
    c = inspect[0] if isinstance(inspect, list) and inspect else inspect
    if not isinstance(c, dict) or "Config" not in c:
        return ["no adapter container in the inspect output"]
    problems = []
    state = c.get("State") or {}
    if (state.get("Health") or {}).get("Status") != "healthy":
        problems.append(f"not healthy: {(state.get('Health') or {}).get('Status') or state.get('Status')}")
    if c["Config"].get("User") != "2000:1000":
        problems.append(f"runs as {c['Config'].get('User')!r}, not the Enclave's 2000:1000")
    host = c.get("HostConfig") or {}
    if host.get("ReadonlyRootfs") is not True:
        problems.append("its root filesystem is writable")
    if [x.upper() for x in host.get("CapDrop") or []] not in (["ALL"], ["CAP_ALL"]):
        problems.append(f"drops {host.get('CapDrop')}, not ALL")
    if host.get("CapAdd"):
        problems.append(f"adds capabilities {host['CapAdd']}")
    if host.get("Privileged"):
        problems.append("is privileged")
    if "no-new-privileges:true" not in (host.get("SecurityOpt") or []):
        problems.append("may gain privileges (no-new-privileges is not set)")
    if any(v for v in (host.get("PortBindings") or {}).values()):
        problems.append(f"publishes ports {sorted(host['PortBindings'])}")
    # compose's `tmpfs: [/tmp]` is memory, not a mount of anything on the machine.
    mounts = [m for m in c.get("Mounts") or [] if not (m.get("Type") == "tmpfs" and m.get("Destination") == "/tmp")]
    targets = {m.get("Destination") for m in mounts}
    if targets != ADAPTER_MOUNTS:
        problems.append(f"mounts {sorted(t for t in targets if t)}, not exactly {sorted(ADAPTER_MOUNTS)}")
    for m in mounts:
        if m.get("Destination") in ADAPTER_READ_ONLY and m.get("RW") is not False:
            problems.append(f"can write {m['Destination']}, which is read-only")
    if any("docker.sock" in (m.get("Source") or "") for m in c.get("Mounts") or []):
        problems.append("holds the Docker socket")
    nets = list(((c.get("NetworkSettings") or {}).get("Networks") or {}))
    if len(nets) != 1 or not nets[0].endswith(ADAPTER_NETWORK_SUFFIX):
        problems.append(f"is on networks {nets}, not exactly the internal backend network")
    return problems


def grant(rows):
    """Exactly one active api.write grant on apis/desks, from `ekka gate grant list --json`."""
    if isinstance(rows, dict):
        rows = rows.get("grants")
    if not isinstance(rows, list):
        return ["the grant listing could not be read"]
    desks = [g for g in rows if isinstance(g, dict) and isinstance(g.get("scope"), dict)
             and g["scope"].get("resource_name") == "apis/desks" and g.get("is_active", True)]
    if len(desks) != 1:
        return [f"{len(desks)} active grants on apis/desks, not exactly one"]
    g = desks[0]
    problems = []
    if g["scope"].get("capability") != "api.write":
        problems.append(f"the desks grant is {g['scope'].get('capability')!r}, not api.write")
    # Govern keeps a grant's limits in its scope (GrantSummary.scope.constraints), as desk_grant.py reads them.
    limits = g["scope"].get("constraints")
    if not isinstance(limits, dict) or not limits:
        problems.append("the desks grant carries no limits")
    else:
        missing = [k for k in desks_limits_required() if k not in limits]
        if missing:
            problems.append(f"the desks grant's limits lack {', '.join(missing)}")
    return problems


def desks_limits_required():
    with open(DESKS_LIMITS_SCHEMA, encoding="utf-8") as f:
        return json.load(f)["required"]


def scopes(held):
    """A person's sign-in client holds reading scopes only."""
    held = {s for s in held if s}
    problems = []
    extra = sorted(held - PEOPLE_SCOPES - IDENTITY_SCOPES)
    if extra:
        problems.append(f"a person's sign-in holds {', '.join(extra)}")
    missing = sorted(PEOPLE_SCOPES - held)
    if missing:
        problems.append(f"a person's sign-in lacks {', '.join(missing)}")
    return problems


def refused(output, code):
    """A desk command was refused, and the refusal names the limit or rule by its contract code."""
    problems = []
    if "refused" not in output:
        problems.append("the output does not say refused")
    if code not in output:
        problems.append(f"the refusal does not name {code}")
    if "ready" in output.split():
        problems.append("the output also says ready")
    return problems


def words(output):
    """The words of an answer, whatever punctuation sits around them: ekka 0.1.111 prints `stop completed (ready)`.
    Whole words only, so `already` is never `ready`."""
    return set(re.findall(r"[a-z_]+", output.lower()))


def outcome(output, want):
    """The command's answer carries the contract outcome `want` (or, for a repeat, `duplicate`)."""
    if want not in OUTCOMES | {"duplicate"}:
        return [f"{want!r} is not one of the contract's words"]
    return [] if want in words(output) else [f"the answer does not say {want}"]


def exited(rows):
    """Every container of the desk has exited, and there is at least one (plan step 17c): a stop that settle waited
    for is finished, not still running beside an exited one."""
    if not isinstance(rows, list) or not rows:
        return ["no container of the desk was found"]
    problems = []
    for r in rows:
        if isinstance(r, list) and len(r) == 3 and r[1] == "exited":
            continue
        if isinstance(r, list) and len(r) == 3:
            problems.append(f"container {str(r[0])[:12]} is {r[1]}, not exited")
        else:
            problems.append("a container row could not be read")
    return problems


def sealed(text, run):
    """A run's receipt (`ekka receipts show <run>`) is THAT run's, and EKKA sealed it. ekka 0.1.111 prints who ran a
    change nowhere on a receipt, so the walk proves what a receipt does carry and never claims an identity."""
    if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f-]{27}", run or ""):
        return ["no run id to look for"]
    if f"run {run[:8]}" not in text:
        return [f"the receipt is not run {run[:8]}'s"]
    return [] if "the record was sealed" in text else ["the receipt does not say the record was sealed"]


def grant_denied(diag, receipts, run, command_exit):
    """The SAME run was denied for its grant before a step; an arbitrary failure is never a pass."""
    valid_run = isinstance(run, str) and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", run)
    if (command_exit == 0 or not valid_run or not isinstance(diag, dict)
            or diag.get("run_id") != run or diag.get("run_status") != "failed"
            or not isinstance(diag.get("error"), dict)
            or diag["error"].get("code") != "RESOURCE_GRANT_DENIED"
            or not isinstance(receipts, dict) or receipts.get("entries") != []
            or receipts.get("malformed_lines") != 0):
        return ["no same-run RESOURCE_GRANT_DENIED before work"]
    return []


def completed_in_order(rows, job, change):
    """Both observed tasks completed, and the queued change completed after the job."""
    try:
        from datetime import datetime
        if not job or job == change or not isinstance(rows, dict) or not isinstance(rows.get("runs"), list):
            raise ValueError()
        selected=[]
        for identity in (job, change):
            matches=[r for r in rows["runs"] if isinstance(r, dict) and r.get("id") == identity]
            if len(matches) != 1 or matches[0].get("status") != "completed":
                raise ValueError()
            stamp=matches[0].get("completed_at")
            if not isinstance(stamp,str): raise ValueError()
            end=datetime.fromisoformat(stamp.replace("Z","+00:00"))
            if end.tzinfo is None: raise ValueError()
            selected.append(end)
        if selected[0] >= selected[1]: raise ValueError()
    except (ValueError, TypeError):
        return ["the same job and queued change did not complete in that order"]
    return []


def pinned_id(shown, desk):
    """Read a healthy Enclave pin, not an unverified gateway name."""
    if not isinstance(shown, dict) or shown.get("state") != "pinned":
        raise ValueError("no healthy pin")
    record = shown.get("desk")
    if not isinstance(record, dict) or record.get("desk") != desk:
        raise ValueError("wrong desk")
    identity = record.get("backend_id")
    if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identity):
        raise ValueError("no backend identity")
    return identity


def provider_usage(rows, provider):
    """Read the credential-free CLI answer strictly; an unreadable list is never isolation."""
    if not isinstance(rows, dict) or not isinstance(rows.get("connections"), list):
        raise ValueError("invalid connections")
    matches = [r for r in rows["connections"] if isinstance(r, dict) and r.get("name") == provider]
    if len(matches) != 1:
        raise ValueError("provider not uniquely listed")
    row = matches[0]
    desks, others = row.get("desks"), row.get("other_backend_desks")
    if (not isinstance(desks, list) or any(not isinstance(d, str) for d in desks)
            or len(set(desks)) != len(desks) or type(others) is not int or others < 0):
        raise ValueError("invalid provider users")
    return sorted(desks), others


RUN_ID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def same_run(text):
    """`plan run`'s id at dispatch is the id the run completed under . Runner 0.1.113's
    first draft printed Govern's request correlation id there, which names no run: every later read went to nothing."""
    dispatched = re.findall(rf"\u2192 Run ({RUN_ID}) dispatched\.", text)
    completed = re.findall(rf"Plan completed \(run ({RUN_ID})\)", text)
    if len(dispatched) != 1:
        return None, [f"expected one dispatched run id, found {len(dispatched)}"]
    if len(completed) != 1:
        return None, [f"expected one completed run id, found {len(completed)}"]
    if dispatched[0] != completed[0]:
        return None, [f"dispatched as run {dispatched[0]} but completed as run {completed[0]}"]
    return completed[0], []


def grant_id(rows):
    """The ONE active api.write grant on apis/desks (the same choice as `grant`): its `grant_id`, so the walk can
    revoke it (step 6)."""
    problems = grant(rows)
    if problems:
        return None, problems
    rows = rows.get("grants") if isinstance(rows, dict) else rows
    g = [g for g in rows if isinstance(g, dict) and isinstance(g.get("scope"), dict)
         and g["scope"].get("resource_name") == "apis/desks" and g.get("is_active", True)][0]
    return (g["grant_id"], []) if g.get("grant_id") else (None, ["the desks grant has no grant_id"])


def clean(paths, planted):
    """No file under `paths` holds the planted value or a bearer header: the adapter keeps no credential."""
    if not planted:
        return ["no planted value to search for"]
    needles = [planted.encode(), b"Bearer "]
    problems = []
    for root in paths:
        files = [root] if os.path.isfile(root) else [os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs]
        for path in files:
            try:
                with open(path, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            for needle in needles:
                if needle in data:
                    problems.append(f"{os.path.relpath(path, os.path.dirname(root))} holds "
                                    f"{'the planted value' if needle == needles[0] else 'a bearer header'}")
    return problems


def main(argv):
    if not argv:
        print(__doc__.strip().splitlines()[0])
        return 2
    cmd, args = argv[0], argv[1:]
    if cmd == "adapter":
        with open(args[0]) as f:
            problems = adapter(json.load(f))
    elif cmd == "grant":
        try:
            with open(args[0]) as f:
                problems = grant(json.load(f))
        except ValueError:
            problems = ["the grant listing could not be read"]
    elif cmd == "scopes":
        problems = scopes(args[0].split(",") if args else [])
    elif cmd == "refused":
        problems = refused(sys.stdin.read(), args[0])
    elif cmd == "outcome":
        problems = outcome(sys.stdin.read(), args[0])
    elif cmd == "exited":
        try:
            problems = exited(json.loads(sys.stdin.read()))
        except ValueError:
            problems = ["the container listing could not be read"]
    elif cmd == "clean":
        problems = clean(args, sys.stdin.read().strip())
    elif cmd == "sealed":
        problems = sealed(sys.stdin.read(), args[0] if args else "")
    elif cmd == "grant-denied":
        try:
            with open(args[0]) as f:
                diag = json.load(f)
            with open(args[1]) as f:
                problems = grant_denied(diag, json.load(f), args[2], int(args[3]))
        except (ValueError, OSError):
            problems = ["grant refusal diagnostics could not be read"]
    elif cmd == "completed-in-order":
        try:
            with open(args[0]) as f:
                problems = completed_in_order(json.load(f), args[1], args[2])
        except (ValueError, OSError):
            problems = ["task completion order could not be read"]
    elif cmd in ("pinned-id", "new-pin"):
        try:
            with open(args[0]) as f:
                before = pinned_id(json.load(f), args[1])
            if cmd == "pinned-id":
                print(before)
                return 0
            with open(args[2]) as f:
                after = pinned_id(json.load(f), args[1])
            problems = [] if before != after else ["the new desk still names the old backend id"]
        except (ValueError, OSError):
            problems = ["a healthy desk pin could not be read"]
    elif cmd in ("provider-scope", "provider-preserved"):
        try:
            with open(args[0]) as f:
                before = provider_usage(json.load(f), args[1])
            if cmd == "provider-scope":
                print("isolated" if before == ([args[2]], 0) else "shared")
                return 0
            with open(args[2]) as f:
                after = provider_usage(json.load(f), args[1])
            problems = [] if before == after else ["provider attachments changed"]
        except (ValueError, OSError):
            problems = ["provider attachments could not be read"]
    elif cmd == "same-run":
        run, problems = same_run(sys.stdin.read())
        if run:
            print(run)
            return 0
    elif cmd == "grant-id":
        try:
            with open(args[0]) as f:
                gid, problems = grant_id(json.load(f))
        except ValueError:
            gid, problems = None, ["the grant listing could not be read"]
        if gid:
            print(gid)
            return 0
    else:
        print(f"unknown check {cmd!r}", file=sys.stderr)
        return 2
    for p in problems:
        print(p)
    if not problems:
        print("ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
