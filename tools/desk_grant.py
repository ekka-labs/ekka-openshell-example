"""The desks grant's limits, and whether the grant in place already holds them .

    python3 tools/desk_grant.py limits --image-id sha256:<hex> --policy-digest sha256:<hex> \\
        [--provider <name>=sha256:<hex> ...] [--image-id <registry digest> --policy-digest <digest> \\
        --allowed-cidr 0.0.0.0/0 --allowed-port 80 ...] > limits.json
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
  providers, environment_keys, repo_hosts, requires_role   as this setup installs them.

A DISCOVERY window (onboarding a project, ekka-ai/.github#784): the same limits with one more image (the project's,
by registry digest), one more policy (desk/policy-discovery.json, host-less `allowedIps` over `0.0.0.0/0`), the address
range and the ports that policy needs. Everything a desk then reaches is in OpenShell's own log; the window ends with
`sudo ekka desk grant restore`, which writes the shipped limits again.

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


CIDR = re.compile(r"^((25[0-5]|2[0-4][0-9]|1?[0-9]{1,2})\.){3}(25[0-5]|2[0-4][0-9]|1?[0-9]{1,2})/([0-9]|[12][0-9]|3[0-2])$")


def limits(image_ids, policy_digests, provider_digests, ceiling, policy, cidrs=(), ports=(443,), hosts=()):
    policy_digests = [policy_digests] if isinstance(policy_digests, str) else list(policy_digests)
    for d in list(image_ids) + policy_digests + list(provider_digests.values()):
        if not DIGEST.match(d or ""):
            raise Refused(f"{d!r} is not sha256:<64 lowercase hex>")
    for h in hosts:
        if not HOST.match(h.removeprefix("*.")) or (h.startswith("*.") and h.count(".") < 2):
            raise Refused(f"{h!r} is not a host name a grant can hold (a wildcard needs three labels, *.example.com)")
    ceiling = sorted(set(ceiling) | set(hosts))
    for c in cidrs:
        if not CIDR.match(c or ""):
            raise Refused(f"{c!r} is not an IPv4 range a.b.c.d/n")
    ports = [int(p) for p in ports]
    if not ports or len(ports) > 16 or len(set(ports)) != len(ports) or any(not 1 <= p <= 65535 for p in ports):
        raise Refused("ports: 1 to 16 distinct ports, each 1 to 65535")
    for name in provider_digests:
        if not NAME.match(name):
            raise Refused(f"{name!r} is not a provider name")
    base = baseline_hosts(policy)
    outside = [h for h in base if h not in ceiling]
    if outside:
        raise Refused(f"desk/policy.json opens {', '.join(outside)}, outside the ceiling the profiles set")
    return {
        "image_digests": sorted(set(image_ids)),
        "policy_digests": sorted(set(policy_digests)),
        "allowed_hosts": ceiling,
        "providers": sorted(provider_digests),
        "provider_config_digests": dict(sorted(provider_digests.items())),
        "environment_keys": [],
        "repo_hosts": REPO_HOSTS,
        "policy_baseline": {
            "must_equal": MUST_EQUAL,
            "network": {"allowed_hosts": base, "allowed_ports": sorted(set(ports)), "allowed_cidrs": sorted(set(cidrs)),
                        "provider_added": "listed_only"},
        },
        "requires_role": "admin",
    }


PACK_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
RULE_NAME = re.compile(r"^[a-z0-9][a-z0-9_]{0,40}$")
READ_ONLY_DIRS = ("/usr/", "/bin/", "/sbin/", "/lib/")   # read-only on every desk (desk/policy.json), so a program
#                                                           there cannot be planted by the agent: a glob is safe only there
# What two rules that can match the same host and port must agree on, or NVIDIA OpenShell refuses the desk
# (openshell-policy/src/ambiguity.rs, v0.1.4). Access, rules and binaries may differ: they only authorize.
MUST_AGREE = ("protocol", "tls", "allowedIps", "enforcement", "allowEncodedSlash")


def pack_dirs(state=None):
    dirs = [os.path.join(ROOT, "desk", "packs")]
    if state:
        dirs.append(os.path.join(state, "packs.local"))   # this install's own packs, root-owned (checked by up.sh)
    return dirs


def load_pack(name, dirs):
    """One network pack by name: a set of named rules, each with a reason. A pack of this install wins over a shipped one."""
    if not PACK_NAME.match(name or ""):
        raise Refused(f"{name!r} is not a pack name")
    for d in reversed(dirs):
        path = os.path.join(d, f"{name}.json")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                pack = json.load(f)
            if pack.get("pack") != name:
                raise Refused(f"{path} says it is pack {pack.get('pack')!r}, not {name!r}")
            return pack
    raise Refused(f"no pack {name!r}; the packs are: {', '.join(sorted(available_packs(dirs)))}")


def available_packs(dirs):
    names = set()
    for d in dirs:
        if os.path.isdir(d):
            names |= {f[:-5] for f in os.listdir(d) if f.endswith(".json")}
    return names


def host_patterns_overlap(a, b):
    """OpenShell's rule: an empty host is every host; *.x.y matches one label under x.y."""
    if not a or not b or a == b:
        return True
    for w, h in ((a, b), (b, a)):
        if w.startswith("*.") and h.endswith(w[1:]) and h.count(".") == w.count("."):
            return True
    return False


def check_pack_rule(pack, rule_name, rule):
    where = f"pack {pack} rule {rule_name}"
    if not RULE_NAME.match(rule_name):
        raise Refused(f"{where}: a rule name is lowercase letters, digits and _")
    if not (rule.get("why") or "").strip():
        raise Refused(f"{where} says nothing about why it exists")
    if not rule.get("endpoints"):
        raise Refused(f"{where} opens nothing")
    if not rule.get("binaries"):
        raise Refused(f"{where} names no program; OpenShell matches no program for a rule without one")
    for b in rule["binaries"]:
        if not b.startswith("/") or ("*" in b and not b.startswith(READ_ONLY_DIRS)):
            raise Refused(f"{where}: {b!r}: a program is an absolute path, and a pattern only under {', '.join(READ_ONLY_DIRS)}")
    for ep in rule["endpoints"]:
        host = ep.get("host", "")
        if not host or ep.get("allowedIps"):
            raise Refused(f"{where}: a pack names hosts, never addresses")
        bare = host.removeprefix("*.")
        if not HOST.match(bare) or (host.startswith("*.") and host.count(".") < 2):
            raise Refused(f"{where}: {host!r} is not a host name (a wildcard needs three labels, *.example.com)")
        ports = set(ep.get("ports") or [ep.get("port")])
        if not ports or ports - {80, 443}:
            raise Refused(f"{where}: ports 443 and 80 only")
        if ep.get("enforcement") != "NETWORK_ENFORCEMENT_MODE_ENFORCE":
            raise Refused(f"{where}: enforcement must be on")


def compose(base, names, dirs):
    """The one policy a desk gets: desk/policy.json with every rule of the named packs. Refused when two rules that can
    match the same host and port disagree on what OpenShell needs to agree on, before any desk is made."""
    policy = json.loads(json.dumps(base))
    rules = policy.setdefault("networkPolicies", {})
    for name in names:
        pack = load_pack(name, dirs)
        for rule_name, rule in sorted(pack.get("rules", {}).items()):
            check_pack_rule(name, rule_name, rule)
            key = f"{name}_{rule_name}"
            if key in rules:
                raise Refused(f"two rules are both named {key}")
            rules[key] = {"name": key, "endpoints": rule["endpoints"], "binaries": [{"path": b} for b in rule["binaries"]]}
    eps = [(k, i, ep) for k, r in rules.items() for i, ep in enumerate(r.get("endpoints", []))]
    for x in range(len(eps)):
        for y in range(x + 1, len(eps)):
            (ka, ia, a), (kb, ib, b) = eps[x], eps[y]
            pa = set(a.get("ports") or [a.get("port")]); pb = set(b.get("ports") or [b.get("port")])
            if not (pa & pb) or not host_patterns_overlap(a.get("host", ""), b.get("host", "")):
                continue
            differ = [f for f in MUST_AGREE if a.get(f) != b.get(f)]
            if differ:
                raise Refused(f"{ka} and {kb} both reach {a.get('host') or 'any host'} on {sorted(pa & pb)} but disagree on "
                              f"{', '.join(differ)}; OpenShell refuses such a desk")
    return policy


def window(policy, image_digest, policy_digest, image_ref, policy_file, reason, since):
    """The extras a discovery window adds to the shipped limits, read from the discovery policy itself: every address
    range and every port its endpoints name, plus the project image and the policy, both by digest."""
    cidrs, ports = set(), set()
    for rule in (policy.get("network_policies") or policy.get("networkPolicies") or {}).values():
        for ep in rule.get("endpoints", []):
            for ip in ep.get("allowedIps") or ep.get("allowed_ips") or []:
                cidrs.add(ip if "/" in ip else ip + "/32")
            for p in ep.get("ports") or ([ep["port"]] if ep.get("port") else []):
                ports.add(int(p))
    hosts = set()
    for rule in (policy.get("network_policies") or policy.get("networkPolicies") or {}).values():
        for ep in rule.get("endpoints", []):
            if ep.get("host"):
                hosts.add(ep["host"])
    if not cidrs and not ports:
        raise Refused("the discovery policy names no address range and no port; nothing to open")
    for d in (image_digest, policy_digest):
        if not DIGEST.match(d or ""):
            raise Refused(f"{d!r} is not sha256:<64 lowercase hex>")
    return {"images": [image_digest], "policy_digests": [policy_digest], "cidrs": sorted(cidrs), "ports": sorted(ports),
            "hosts": sorted(hosts),
            "image_ref": image_ref, "policy_file": policy_file, "reason": reason, "since": since}


def extras_args(extras):
    """The limits options a window adds, as command words (validated again when limits() runs)."""
    words = []
    for d in extras.get("images", []): words += ["--image-id", d]
    for d in extras.get("policy_digests", []): words += ["--policy-digest", d]
    for c in extras.get("cidrs", []): words += ["--allowed-cidr", c]
    for h in extras.get("hosts", []): words += ["--allowed-host", h]
    for p in extras.get("ports", []):
        if int(p) != 443: words += ["--allowed-port", str(int(p))]
    return words


def show(lim, extras=None):
    net = lim["policy_baseline"]["network"]
    lines = [f"images (by digest):   {', '.join(lim['image_digests'])}",
             f"policies (by digest): {', '.join(lim['policy_digests'])}",
             f"hosts the policy opens: {', '.join(net['allowed_hosts']) or 'none'}",
             f"ports: {', '.join(map(str, net['allowed_ports']))}   address ranges: {', '.join(net['allowed_cidrs']) or 'none'}",
             f"providers: {', '.join(lim['providers']) or 'none'}   host ceiling: {', '.join(lim['allowed_hosts'])}"]
    if extras:
        lines.append(f"DISCOVERY WINDOW open since {extras.get('since')}: image {extras.get('image_ref')}, policy {extras.get('policy_file')}"
                     f" ({extras.get('reason')}). End it: sudo ekka desk grant restore")
    else:
        lines.append("no discovery window: the shipped limits")
    return "\n".join(lines)


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
    lim.add_argument("--policy-digest", action="append", required=True)
    lim.add_argument("--provider", action="append", default=[])
    lim.add_argument("--allowed-cidr", action="append", default=[], help="an address range the baseline allows (a discovery window)")
    lim.add_argument("--allowed-port", action="append", type=int, default=[], help="a port beside 443 the baseline allows")
    lim.add_argument("--allowed-host", action="append", default=[], help="a host a discovery window's packs name")
    cmp_ = sub.add_parser("compose", help="desk/policy.json with the named packs' rules, checked")
    cmp_.add_argument("packs"); cmp_.add_argument("--state", default=None)
    sub.add_parser("packs", help="the packs this install has").add_argument("--state", default=None)
    dec = sub.add_parser("decide")
    dec.add_argument("agent_id")
    dec.add_argument("instance")
    dec.add_argument("limits_file")
    win = sub.add_parser("window", help="the extras file for a discovery window, from the discovery policy")
    win.add_argument("policy_file"); win.add_argument("--image-digest", required=True); win.add_argument("--policy-digest", required=True)
    win.add_argument("--image-ref", required=True); win.add_argument("--reason", default="onboarding a project (ekka-ai/.github#784)")
    win.add_argument("--since", default=None)
    ex = sub.add_parser("extras-args", help="the limits options a window adds, one per line"); ex.add_argument("extras_file")
    sh = sub.add_parser("show"); sh.add_argument("limits_file"); sh.add_argument("extras_file", nargs="?")
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
            print(json.dumps(limits(a.image_id, a.policy_digest, providers, profile_hosts(), policy,
                                    cidrs=a.allowed_cidr, ports=[443, *a.allowed_port], hosts=a.allowed_host), indent=2))
        elif a.cmd == "compose":
            with open(os.path.join(ROOT, "desk", "policy.json"), encoding="utf-8") as f:
                base = json.load(f)
            names = [n.strip() for n in a.packs.split(",") if n.strip()]
            if not names:
                raise Refused("name at least one pack")
            print(json.dumps(compose(base, names, pack_dirs(a.state)), indent=2))
        elif a.cmd == "packs":
            dirs = pack_dirs(a.state)
            for n in sorted(available_packs(dirs)):
                p = load_pack(n, dirs)
                print(f"{n:12} {p.get('about', '')}")
        elif a.cmd == "window":
            with open(a.policy_file, encoding="utf-8") as f:
                policy = json.load(f)
            since = a.since or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            print(json.dumps(window(policy, a.image_digest, a.policy_digest, a.image_ref, a.policy_file, a.reason, since), indent=2))
        elif a.cmd == "extras-args":
            with open(a.extras_file, encoding="utf-8") as f:
                print("\n".join(extras_args(json.load(f))))
        elif a.cmd == "show":
            with open(a.limits_file, encoding="utf-8") as f:
                lim = json.load(f)
            extras = None
            if a.extras_file and os.path.exists(a.extras_file):
                with open(a.extras_file, encoding="utf-8") as f:
                    extras = json.load(f)
            print(show(lim, extras))
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
