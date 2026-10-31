"""The desks grant's limits, and whether the grant in place already holds them .

    python3 tools/desk_grant.py limits --image-id sha256:<hex> --policy-digest sha256:<hex> \\
        [--provider <name>=sha256:<hex> ...] > limits.json
    <`ekka gate grant list --json`> | python3 tools/desk_grant.py decide <agent id> <api gate> limits.json

The limits are @ekka-ai/contracts governance/grant-constraints/desks.v1 for this machine:

  image_digests            the content ID of the desk image this machine built (`docker image inspect .Id`). The
                           owner rules out a published desk image (Claude Code is installed by Anthropic's own
                           installer when the customer builds it), so a rebuild is a grant update.
  policy_digests           `ekka-backend digest policy` over desk/policy.json, the reviewed baseline, from the
                           pinned adapter image: the adapter's own code, so the two never disagree.
  provider_config_digests  `ekka-backend digest provider` for each provider this gateway holds.
  allowed_hosts            the ceiling: every host the shipped provider profiles name. A policy change may open
                           no other host.
  policy_baseline          all six must_equal parts; the shipped policy allows GitHub clone/fetch for git; port 443 only; no
                           address ranges; a provider adds network access only when it is listed.
  providers, environment_keys, repo_hosts, requires_role   as the kit installs them.

`decide` prints one line: `keep <grant id>` when an active grant for this agent on this gate's apis/desks already
holds exactly these limits and lives more than 30 days more; `add` when there is none; `replace <id> ...` otherwise:
the new grant is added first and the old ones revoked after, so desks are never left without one.
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
HOST = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
INSTANCE = re.compile(r"^enclave[0-9a-f]{8}Api$")
MUST_EQUAL = ["filesystem", "process", "user_namespaces", "enforcement", "driver", "runtime"]
PORTS = [443]
REPO_HOSTS = ["github.com"]
RESOURCE = "apis/desks"
CAPABILITY = "api.write"
RENEW_BEFORE = dt.timedelta(days=30)


class Refused(Exception):
    pass


def profile_hosts(providers_dir=os.path.join(ROOT, "providers")):
    """Every endpoint host the shipped provider profiles name, sorted, each checked."""
    hosts = set()
    for path in sorted(glob.glob(os.path.join(providers_dir, "*.yaml"))):
        with open(path, encoding="utf-8") as f:
            for m in re.finditer(r"^\s*-?\s*host:\s*([^\s#]+)\s*$", f.read(), re.M):
                host = m.group(1).strip("'\"")
                if not HOST.match(host):
                    raise Refused(f"{os.path.basename(path)} names a host the limits cannot hold: {host!r}")
                hosts.add(host)
    if not hosts:
        raise Refused("no provider profile names a host; the ceiling would be empty")
    return sorted(hosts)


def baseline_hosts(policy):
    """The hosts desk/policy.json itself opens (its network_policies), which sit inside the ceiling."""
    hosts = set()
    for rule in (policy.get("network_policies") or policy.get("networkPolicies") or {}).values():
        for ep in rule.get("endpoints", []):
            if ep.get("host"):
                hosts.add(ep["host"])
    return sorted(hosts)


def limits(image_ids, policy_digest, provider_digests, ceiling, policy):
    for d in list(image_ids) + [policy_digest] + list(provider_digests.values()):
        if not DIGEST.match(d or ""):
            raise Refused(f"{d!r} is not sha256:<64 lowercase hex>")
    for name in provider_digests:
        if not NAME.match(name):
            raise Refused(f"{name!r} is not a provider name")
    base = baseline_hosts(policy)
    outside = [h for h in base if h not in ceiling]
    if outside:
        raise Refused(f"desk/policy.json opens {', '.join(outside)}, outside the ceiling the profiles set")
    return {
        "image_digests": sorted(set(image_ids)),
        "policy_digests": [policy_digest],
        "allowed_hosts": ceiling,
        "providers": sorted(provider_digests),
        "provider_config_digests": dict(sorted(provider_digests.items())),
        "environment_keys": [],
        "repo_hosts": REPO_HOSTS,
        "policy_baseline": {
            "must_equal": MUST_EQUAL,
            "network": {"allowed_hosts": base, "allowed_ports": PORTS, "allowed_cidrs": [],
                        "provider_added": "listed_only"},
        },
        "requires_role": "admin",
    }


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def decide(grants, agent_id, instance, wanted, now):
    """keep <id> | add | replace <id>..., for this agent's grants on this gate's apis/desks."""
    if not INSTANCE.match(instance):
        raise Refused(f"{instance!r} is not this Enclave's API gate")
    mine = [g for g in grants
            if g.get("agent_id") == agent_id and g.get("is_active", True)
            and (g.get("scope") or {}).get("capability") == CAPABILITY
            and (g.get("scope") or {}).get("instance_id") == instance
            and (g.get("scope") or {}).get("resource_name") == RESOURCE]
    for g in mine:
        expires = g.get("expires_at")
        # A grant with no expiry lasts; one with an expiry is renewed once it is within RENEW_BEFORE of it. So two
        # runs with the same limits change nothing (regression review). An empty expiry is no expiry.
        lasts = not expires or dt.datetime.fromisoformat(expires.replace("Z", "+00:00")) - now > RENEW_BEFORE
        if lasts and canonical((g["scope"] or {}).get("constraints")) == canonical(wanted) and len(mine) == 1:
            return f"keep {g['grant_id']}"
    if not mine:
        return "add"
    return "replace " + " ".join(sorted(g["grant_id"] for g in mine))


def main(argv, stdin=sys.stdin, now=None):
    p = argparse.ArgumentParser(prog="desk_grant.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    lim = sub.add_parser("limits")
    lim.add_argument("--image-id", action="append", required=True)
    lim.add_argument("--policy-digest", required=True)
    lim.add_argument("--provider", action="append", default=[])
    dec = sub.add_parser("decide")
    dec.add_argument("agent_id")
    dec.add_argument("instance")
    dec.add_argument("limits_file")
    a = p.parse_args(argv)
    try:
        if a.cmd == "limits":
            providers = {}
            for item in a.provider:
                name, sep, digest = item.partition("=")
                if not sep:
                    raise Refused(f"--provider {item!r} is not <name>=<digest>")
                providers[name] = digest
            with open(os.path.join(ROOT, "desk", "policy.json"), encoding="utf-8") as f:
                policy = json.load(f)
            print(json.dumps(limits(a.image_id, a.policy_digest, providers, profile_hosts(), policy), indent=2))
        else:
            with open(a.limits_file, encoding="utf-8") as f:
                wanted = json.load(f)
            grants = json.loads(stdin.read() or "[]")
            if not isinstance(grants, list):
                raise Refused("the grant list is not a list")
            print(decide(grants, a.agent_id, a.instance, wanted, now or dt.datetime.now(dt.timezone.utc)))
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
