"""The desks grant's limits, and whether the grant in place already holds them .

    python3 tools/desk_grant.py limits --image-id sha256:<hex> --policy-digest sha256:<hex> \\
        [--provider <name>=sha256:<hex> ...] [--window <window file>] > limits.json
    <`ekka gate grant list --json`> | python3 tools/desk_grant.py decide <agent id> <api gate> limits.json

The limits are @ekka-ai/contracts governance/grant-constraints/desks.v1 for this machine:

  image_digests            the content ID of the desk image this machine built (`docker image inspect .Id`). The
                           owner rules out a published desk image (Claude Code is installed by Anthropic's own
                           installer when the customer builds it), so a rebuild is a grant update.
  policy_digests           `ekka-backend digest policy` over desk/policy.json, the reviewed baseline, from the
                           pinned adapter image: the adapter's own code, so the two never disagree.
  provider_config_digests  `ekka-backend digest provider` for each provider this gateway holds.
  allowed_hosts            the ceiling: every host the profiles this install puts in the gateway name (--profile,
                           from up.sh; every shipped profile when none is given). A policy change may open no
                           other host.
  policy_baseline          all six must_equal parts; the shipped policy allows GitHub clone/fetch for git; port 443 only; no
                           address ranges; a provider adds network access only when it is listed.
  providers, environment_keys, repo_hosts, requires_role   as this setup installs them.

A DISCOVERY WINDOW (onboarding a project): the same limits, the project's image added by its registry digest, and the
grant's `discovery` block {image_digests, providers, name_prefix "discover-", expires_at}. The desk is created with the
shipped policy; no policy, address range or port is added. While the window is open, the backend adapter may switch a
desk named discover-*, made from that image with only those providers, to NVIDIA OpenShell's auto approval mode, and
writes a permit for it; the Enclave's gateway interceptor lets that desk's proposals through only while the permit
holds. Without the block no desk may enter auto mode. `window` writes the window file:

    python3 tools/desk_grant.py window --image-ref <registry>/<name>@sha256:<hex> --providers <a,b> \\
        [--minutes 60] [--limits <the limits in force>] > window.json

and `sudo ekka desk grant restore` removes it, so the shipped limits are written again.

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


def profile_hosts(providers_dir=os.path.join(ROOT, "providers"), files=None):
    """Every endpoint host the profiles name, sorted, each checked: `files`, the very profiles this install puts in
    the gateway (up.sh passes them, an install's own providers.local file in place of a shipped one), or else every
    shipped profile. One source: a profile the gateway holds can never sit outside the ceiling."""
    hosts = set()
    for path in (files if files else sorted(glob.glob(os.path.join(providers_dir, "*.yaml")))):
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



IMAGE_REF = re.compile(r"^[a-z0-9.-]+(:[0-9]+)?(/[a-z0-9._-]+)+@sha256:[0-9a-f]{64}$")   # the same rule as up.sh's
UTC_Z = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
NAME_PREFIX = "discover-"      # contracts desks.v1 `discovery.name_prefix`, a constant
DEFAULT_MINUTES = 60
MAX_MINUTES = 240
MAX_WINDOW_IMAGES = 4          # contracts desks.v1 `discovery.image_digests`: 1 to 4
MAX_WINDOW_PROVIDERS = 8       # contracts desks.v1 `discovery.providers`: 0 to 8
DISCOVERY_KEYS = ("image_digests", "providers", "name_prefix", "expires_at")


def utc_z(when):
    return when.astimezone(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc_z(text):
    if not isinstance(text, str) or not UTC_Z.match(text):
        raise Refused(f"{text!r} is not a UTC time written YYYY-MM-DDTHH:MM:SSZ")
    try:
        return dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        raise Refused(f"{text!r} is not a real time") from None


def check_discovery(block):
    """The grant's `discovery` block, held to its contract shape (closed) before it reaches a grant."""
    if not isinstance(block, dict) or sorted(block) != sorted(DISCOVERY_KEYS):
        raise Refused(f"a discovery block holds exactly {', '.join(DISCOVERY_KEYS)}")
    images, providers = block["image_digests"], block["providers"]
    if not isinstance(images, list) or not 1 <= len(images) <= MAX_WINDOW_IMAGES or len(set(images)) != len(images):
        raise Refused(f"a discovery window names 1 to {MAX_WINDOW_IMAGES} distinct images")
    for d in images:
        if not DIGEST.match(d if isinstance(d, str) else ""):
            raise Refused(f"{d!r} is not sha256:<64 lowercase hex>")
    if not isinstance(providers, list) or len(providers) > MAX_WINDOW_PROVIDERS or len(set(providers)) != len(providers):
        raise Refused(f"a discovery window names at most {MAX_WINDOW_PROVIDERS} distinct providers")
    for name in providers:
        if not NAME.match(name if isinstance(name, str) else ""):
            raise Refused(f"{name!r} is not a provider name")
    if block["name_prefix"] != NAME_PREFIX:
        raise Refused(f"a discovery desk's name starts with {NAME_PREFIX!r}, nothing else")
    parse_utc_z(block["expires_at"])
    return {"image_digests": sorted(images), "providers": sorted(providers), "name_prefix": NAME_PREFIX,
            "expires_at": block["expires_at"]}


def limits(image_ids, policy_digests, provider_digests, ceiling, policy, discovery=None):
    policy_digests = [policy_digests] if isinstance(policy_digests, str) else list(policy_digests)
    for d in list(image_ids) + policy_digests + list(provider_digests.values()):
        if not DIGEST.match(d or ""):
            raise Refused(f"{d!r} is not sha256:<64 lowercase hex>")
    for name in provider_digests:
        if not NAME.match(name):
            raise Refused(f"{name!r} is not a provider name")
    base = baseline_hosts(policy)
    outside = [h for h in base if h not in ceiling]
    if outside:
        raise Refused(f"desk/policy.json opens {', '.join(outside)}, outside the ceiling the profiles set")
    images = set(image_ids)
    if discovery is not None:
        discovery = check_discovery(discovery)
        images |= set(discovery["image_digests"])   # a desk is created from the window's image, so the grant names it
    out = {
        "image_digests": sorted(images),
        "policy_digests": sorted(set(policy_digests)),
        "allowed_hosts": sorted(set(ceiling)),
        "providers": sorted(provider_digests),
        "provider_config_digests": dict(sorted(provider_digests.items())),
        "environment_keys": [],
        "repo_hosts": REPO_HOSTS,
        "policy_baseline": {
            "must_equal": MUST_EQUAL,
            "network": {"allowed_hosts": base, "allowed_ports": list(PORTS), "allowed_cidrs": [], "provider_added": "listed_only"},
        },
        "requires_role": "admin",
    }
    if discovery is not None:
        out["discovery"] = discovery
    return out


def window(image_ref, providers, minutes, now, granted_providers=None):
    """The window file `sudo ekka desk grant discovery` writes: the grant's `discovery` block, and the image's own
    name and the opening time for `show`. `granted_providers`, the providers the grant in force holds: a window naming
    any other is refused, since a desk holding it could never be created."""
    if not IMAGE_REF.match(image_ref or ""):
        raise Refused(f"{image_ref!r} is not <registry>/<name>@sha256:<64 hex>, the exact image the desk is created from "
                      "(a tag can move; a digest cannot)")
    if not providers:
        raise Refused("name the providers the discovery desk may hold, for example github,bitbucket")
    for name in providers:
        if not NAME.match(name or ""):
            raise Refused(f"{name!r} is not a provider name")
    if len(set(providers)) != len(providers) or len(providers) > MAX_WINDOW_PROVIDERS:
        raise Refused(f"name 1 to {MAX_WINDOW_PROVIDERS} distinct providers")
    if granted_providers is not None:
        missing = sorted(set(providers) - set(granted_providers))
        if missing:
            raise Refused(f"{', '.join(missing)} is not connected on this server, so no desk may hold it; "
                          "connect it first: <the key> | sudo ekka desk connect <provider>")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= MAX_MINUTES:
        raise Refused(f"--minutes is a whole number from 1 to {MAX_MINUTES}")
    block = check_discovery({"image_digests": [image_ref.rsplit("@", 1)[1]], "providers": sorted(providers),
                             "name_prefix": NAME_PREFIX, "expires_at": utc_z(now + dt.timedelta(minutes=minutes))})
    return {"image_ref": image_ref, "since": utc_z(now), "discovery": block}


def window_block(record):
    """The `discovery` block a window file holds. A file from before this shape (a policy window) is refused, so its
    address ranges and policy never reach a grant again."""
    if not isinstance(record, dict) or "discovery" not in record:
        raise Refused("the open discovery window was written by an older installer (a policy window, which the gateway "
                      "now refuses); end it: sudo ekka desk grant restore")
    return check_discovery(record["discovery"])


def show(lim, extras=None, profiles=(), now=None):
    """`sudo ekka desk grant show`: what a desk made through EKKA may use on this server, in words. Each image digest
    carries its name: the shipped one is what the install built (ekka-desk:local), and a discovery window's is named
    from its own record."""
    net = lim["policy_baseline"]["network"]
    extras = extras or {}
    disc = lim.get("discovery")
    win_images = set((disc or {}).get("image_digests", []))
    ref = extras.get("image_ref") or "(not recorded)"
    connected = list(lim["providers"])
    waiting = [p for p in profiles if p not in connected]
    lines = ["The desks grant: what a desk made through EKKA may use on this server", "",
             "DESK IMAGE (a desk runs only this image)"]
    lines += [f"  {f'the discovery image {ref}' if d in win_images else 'ekka-desk:local, built by the install'}\n      {d}"
              for d in lim["image_digests"]]
    lines += ["", "DESK POLICY (what a desk may reach on the network)"]
    lines += [f"  desk/policy.json, the shipped policy\n      {d}" for d in lim["policy_digests"]]
    lines += [f"  opens: {', '.join(net['allowed_hosts']) or 'no host'}, on port {', '.join(map(str, net['allowed_ports']))}",
              f"  address ranges: {', '.join(net['allowed_cidrs']) or 'none'}",
              f"  read the shipped policy:  cat {os.path.abspath(os.path.join(ROOT, 'desk', 'policy.json'))}",
              "", "PROVIDERS (the keys a desk may use)"]
    if connected:
        lines.append(f"  connected: {', '.join(connected)}")
    else:
        lines.append("  none connected yet, so a desk can use no key.")
    if waiting:
        lines.append(f"  on this server, not connected: {', '.join(waiting)}")
        lines.append("  connect one, the key piped in and never typed:  <the key> | sudo ekka desk connect <provider>")
    if profiles:
        lines.append(f"  what each provider may reach:  cat {os.path.abspath(os.path.join(ROOT, 'providers'))}/<provider>.yaml")
    lines += ["", "HOSTS A PROVIDER MAY EVER REACH (the ceiling; a provider's own profile picks from these)",
              f"  {', '.join(lim['allowed_hosts'])}", ""]
    if disc:
        until = disc["expires_at"]
        providers = ", ".join(disc["providers"]) or "none"
        what = (f"image {ref}, providers {providers}; a desk named {disc['name_prefix']}* made from that image with "
                "only those providers may use NVIDIA OpenShell's auto approval mode")
        if (now or dt.datetime.now(dt.timezone.utc)) < parse_utc_z(until):
            lines.append(f"DISCOVERY WINDOW open until {until} (opened {extras.get('since') or 'at an unrecorded time'}): {what}."
                         " End it: sudo ekka desk grant restore")
        else:
            lines.append(f"DISCOVERY WINDOW closed at {until} (expired): no desk may enter auto approval mode. "
                         f"The image stays in the grant until: sudo ekka desk grant restore")
    else:
        lines.append("no discovery window: the shipped limits are in force")
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
    lim.add_argument("--profile", action="append", default=[], help="a profile file this install puts in the gateway; the ceiling is their hosts")
    lim.add_argument("--window", default=None, help="a discovery window's file: its image and the grant's discovery block")
    dec = sub.add_parser("decide")
    dec.add_argument("agent_id")
    dec.add_argument("instance")
    dec.add_argument("limits_file")
    win = sub.add_parser("window", help="the file for a discovery window: the grant's discovery block")
    win.add_argument("--image-ref", required=True)
    win.add_argument("--providers", required=True, help="the providers the discovery desk may hold, comma separated")
    win.add_argument("--minutes", type=int, default=DEFAULT_MINUTES, help=f"how long it is open, 1 to {MAX_MINUTES}")
    win.add_argument("--limits", default=None, help="the limits in force; a provider they do not hold is refused")
    sh = sub.add_parser("show"); sh.add_argument("limits_file"); sh.add_argument("extras_file", nargs="?")
    sh.add_argument("--profiles", default="", help="the profiles this server has, comma separated")
    a = p.parse_args(argv)
    when = now or dt.datetime.now(dt.timezone.utc)
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
            discovery = None
            if a.window:
                with open(a.window, encoding="utf-8") as f:
                    discovery = window_block(json.load(f))
            print(json.dumps(limits(a.image_id, a.policy_digest, providers, profile_hosts(files=a.profile), policy,
                                    discovery=discovery), indent=2))
        elif a.cmd == "window":
            granted = None
            if a.limits:
                with open(a.limits, encoding="utf-8") as f:
                    granted = json.load(f).get("providers", [])
            names = [n.strip() for n in a.providers.split(",") if n.strip()]
            print(json.dumps(window(a.image_ref, names, a.minutes, when, granted), indent=2))
        elif a.cmd == "show":
            with open(a.limits_file, encoding="utf-8") as f:
                lim = json.load(f)
            extras = None
            if a.extras_file and os.path.exists(a.extras_file):
                with open(a.extras_file, encoding="utf-8") as f:
                    extras = json.load(f)
            print(show(lim, extras, [p for p in a.profiles.split(",") if p], now=when))
        else:
            with open(a.limits_file, encoding="utf-8") as f:
                wanted = json.load(f)
            grants = json.loads(stdin.read() or "[]")
            if not isinstance(grants, list):
                raise Refused("the grant list is not a list")
            print(decide(grants, a.agent_id, a.instance, wanted, when))
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
