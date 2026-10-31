"""The desks grant's limits and whether the grant in place holds them .

The limits are checked against the vendored @ekka-ai/contracts governance/grant-constraints/desks.v1 schema itself.
The policy digest below is the REAL `ekka-backend digest policy` over desk/policy.json, measured with an image built
from the signed ekka-backend v0.1.3 on 2026-10-19; the test of the decision uses Govern's grant rows as
`ekka gate grant list --json` prints them (verbatim GrantSummary rows).
"""
import copy
import datetime as dt
import io
import json
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import desk_grant as dg  # noqa: E402
import schema_check  # noqa: E402

with open(os.path.join(ROOT, "vendor", "ekka-contracts", "governance", "grant-constraints", "desks.v1.schema.json")) as f:
    SCHEMA = json.load(f)
with open(os.path.join(ROOT, "desk", "policy.json")) as f:
    POLICY = json.load(f)

IMAGE = "sha256:" + "d" * 64
POLICY_DIGEST = "sha256:031ef8cd84799343ba22c61dbaac709794a5408157c236c1e67cd3d4ad169cc1"
GITHUB = "sha256:" + "c" * 64
AGENT = "0a0b0c0d-1111-4222-8333-444455556666"
GATE = "enclave8e1d5a6aApi"
NOW = dt.datetime(2026, 10, 16, tzinfo=dt.timezone.utc)


def errors(value):
    return schema_check.schema_errors(value, SCHEMA)


def build(**over):
    args = dict(image_ids=[IMAGE], policy_digest=POLICY_DIGEST, provider_digests={"github": GITHUB},
                ceiling=dg.profile_hosts(), policy=POLICY)
    args.update(over)
    return dg.limits(**args)


def row(grant_id, constraints, days=200, agent=AGENT, gate=GATE, active=True):
    return {"grant_id": grant_id, "agent_id": agent, "is_active": active,
            "expires_at": (NOW + dt.timedelta(days=days)).isoformat().replace("+00:00", "Z"),
            "scope": {"capability": "api.write", "instance_id": gate, "resource_name": "apis/desks",
                      "constraints": constraints}}


class TheLimits(unittest.TestCase):
    def test_the_schema_check_is_real(self):
        with open(os.path.join(ROOT, "vendor", "ekka-contracts", "governance", "grant-constraints", "samples",
                               "desks.v1.json")) as f:
            sample = json.load(f)
        self.assertEqual(errors(sample), [])
        self.assertNotEqual(errors({**sample, "allow_all": True}), [])
        self.assertNotEqual(errors({**sample, "image_digests": []}), [])

    def test_this_machines_limits_are_what_the_contract_accepts(self):
        self.assertEqual(errors(build()), [])

    def test_the_ceiling_is_every_host_the_shipped_profiles_name(self):
        self.assertEqual(build()["allowed_hosts"], sorted({
            "api.anthropic.com", "api.openai.com", "auth.openai.com", "chatgpt.com", "ab.chatgpt.com",
            "api.github.com", "github.com"}))

    def test_the_baseline_is_the_kits_policy_and_nothing_wider(self):
        lim = build()
        self.assertEqual(lim["policy_baseline"]["must_equal"],
                         ["filesystem", "process", "user_namespaces", "enforcement", "driver", "runtime"])
        net = lim["policy_baseline"]["network"]
        self.assertEqual(net, {"allowed_hosts": ["github.com"], "allowed_ports": [443], "allowed_cidrs": [],
                               "provider_added": "listed_only"}, "desk/policy.json opens GitHub for git clone/fetch; port 443 only")
        self.assertEqual((lim["environment_keys"], lim["repo_hosts"], lim["requires_role"]), ([], ["github.com"], "admin"))

    def test_each_provider_is_listed_with_its_digest(self):
        lim = build(provider_digests={"github": GITHUB, "claude-code": "sha256:" + "e" * 64})
        self.assertEqual(lim["providers"], ["claude-code", "github"])
        self.assertEqual(sorted(lim["provider_config_digests"]), lim["providers"], "one digest per listed provider, no other")

    def test_planted_a_baseline_host_outside_the_ceiling_is_refused(self):
        wide = copy.deepcopy(POLICY)
        wide["network_policies"] = {"x": {"endpoints": [{"host": "evil.example.com", "port": 443}]}}
        with self.assertRaisesRegex(dg.Refused, "outside the ceiling"):
            build(policy=wide)

    def test_planted_a_digest_that_is_not_one_is_refused(self):
        for over in ({"image_ids": ["ekka-desk:local"]}, {"policy_digest": "sha256:ABC"},
                     {"provider_digests": {"github": "md5:1"}}, {"provider_digests": {"../x": GITHUB}}):
            with self.assertRaises(dg.Refused, msg=str(over)):
                build(**over)


class TheDecision(unittest.TestCase):
    def setUp(self):
        self.lim = build()

    def test_no_grant_yet_is_add(self):
        self.assertEqual(dg.decide([], AGENT, GATE, self.lim, NOW), "add")

    def test_the_same_limits_with_time_left_are_kept(self):
        self.assertEqual(dg.decide([row("g1", self.lim)], AGENT, GATE, self.lim, NOW), "keep g1")

    def test_the_same_limits_in_another_key_order_are_kept(self):
        reordered = json.loads(json.dumps(self.lim), object_pairs_hook=lambda kv: dict(reversed(kv)))
        self.assertEqual(dg.decide([row("g1", reordered)], AGENT, GATE, self.lim, NOW), "keep g1")

    def test_planted_new_limits_replace_the_grant(self):
        rebuilt = build(image_ids=["sha256:" + "f" * 64])   # the desk image was rebuilt
        self.assertEqual(dg.decide([row("g1", self.lim)], AGENT, GATE, rebuilt, NOW), "replace g1")

    def test_planted_a_grant_close_to_expiry_is_renewed(self):
        self.assertEqual(dg.decide([row("g1", self.lim, days=10)], AGENT, GATE, self.lim, NOW), "replace g1")

    def test_a_grant_with_no_expiry_and_the_same_limits_is_kept_run_after_run(self):
        # regression review: a missing expires_at was read as "about to expire", so every run replaced it.
        lasting = dict(row("g1", self.lim), expires_at=None)
        for _ in range(2):
            self.assertEqual(dg.decide([lasting], AGENT, GATE, self.lim, NOW), "keep g1")
        empty = dict(row("g1", self.lim), expires_at="")   # regression check: an empty value raised ValueError
        self.assertEqual(dg.decide([empty], AGENT, GATE, self.lim, NOW), "keep g1")
        rebuilt = build(image_ids=["sha256:" + "e" * 64])   # the desk image was rebuilt
        self.assertEqual(dg.decide([lasting], AGENT, GATE, rebuilt, NOW), "replace g1", "only different limits replace it")

    def test_planted_two_grants_on_the_same_resource_are_both_replaced(self):
        got = dg.decide([row("g1", self.lim), row("g2", self.lim)], AGENT, GATE, self.lim, NOW)
        self.assertEqual(got, "replace g1 g2", "never two grants with different limits on one resource")

    def test_other_agents_gates_and_inactive_grants_are_not_this_one(self):
        rows = [row("a", self.lim, agent="ffffffff-1111-4222-8333-444455556666"),
                row("b", self.lim, gate="enclave00000000Api"), row("c", self.lim, active=False)]
        self.assertEqual(dg.decide(rows, AGENT, GATE, self.lim, NOW), "add")

    def test_planted_a_gate_that_is_not_an_api_gate_is_refused(self):
        with self.assertRaisesRegex(dg.Refused, "API gate"):
            dg.decide([], AGENT, "enclave8e1d5a6aJob", self.lim, NOW)


class CommandLine(unittest.TestCase):
    def test_limits_then_decide_end_to_end(self):
        out = io.StringIO()
        sys_stdout, sys.stdout = sys.stdout, out
        try:
            rc = dg.main(["limits", "--image-id", IMAGE, "--policy-digest", POLICY_DIGEST, "--provider", f"github={GITHUB}"])
        finally:
            sys.stdout = sys_stdout
        self.assertEqual(rc, 0)
        lim = json.loads(out.getvalue())
        self.assertEqual(errors(lim), [])
        path = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"desk-grant-test-{os.getpid()}.json")
        with open(path, "w") as f:
            json.dump(lim, f)
        out = io.StringIO()
        sys_stdout, sys.stdout = sys.stdout, out
        try:
            rc = dg.main(["decide", AGENT, GATE, path], stdin=io.StringIO(json.dumps([row("g1", lim)])), now=NOW)
        finally:
            sys.stdout = sys_stdout
            os.unlink(path)
        self.assertEqual((rc, out.getvalue().strip()), (0, "keep g1"))


if __name__ == "__main__":
    unittest.main()


STUB = r"""#!/usr/bin/env python3
import hashlib, json, os, sys
a = sys.argv[1:]
log = lambda **k: open(os.environ["CALLS"], "a").write(json.dumps(k) + "\n")
if a[:1] == ["cp"]:   # a plan file into the Enclave, read here as it would land there
    with open(a[1]) as f: log(kind="cp", name=os.path.basename(a[1]), plan=json.load(f), to=a[2])
    sys.exit(0)
if a[:2] == ["image", "inspect"]:
    if a[-1] == "ekka-desk-dev:local":   # the development desk image: built only when the test says so
        if os.environ.get("DEV_ID"): print(os.environ["DEV_ID"]); sys.exit(0)
        print("Error: No such image: ekka-desk-dev:local", file=sys.stderr); sys.exit(1)
    print(os.environ["DESK_ID"]); sys.exit(0)
if a[:1] == ["run"] and "digest" in a:
    data = sys.stdin.read(); log(kind="digest", input_sha=hashlib.sha256(data.encode()).hexdigest(), argv=a)
    if os.environ.get("DIGEST_FAILS"): print("POLICY_INVALID", file=sys.stderr); sys.exit(1)
    print(os.environ["POLICY_DIGEST"]); sys.exit(0)
if a[:1] == ["compose"] and "ekka-backend" in a and "--from-gateway" in a:
    name = a[a.index("--from-gateway") + 1]; secret = sys.stdin.read()
    log(kind="provider", name=name, secret_sha=hashlib.sha256(secret.encode()).hexdigest(), argv=a)
    known = dict(x.split("=", 1) for x in os.environ.get("PROVIDER_DIGESTS", "").split(",") if x)
    if os.environ.get("PROVIDER_FAILS") == name: print("PROVIDER_UNREADABLE: PERMISSION_DENIED", file=sys.stderr); sys.exit(2)
    if name not in known: print(f"PROVIDER_NOT_FOUND: no provider {name}", file=sys.stderr); sys.exit(2)
    print(known[name]); sys.exit(0)
if a[:1] == ["compose"]:
    rest = a[a.index("ekka-enclave") + 1:]
    cmd = rest[1:]
    if cmd[:3] == ["gate", "grant", "add"]:
        with open(cmd[cmd.index("--constraint") + 1]) as f: limits = json.load(f)
        log(kind="add", limits=limits, agent=cmd[cmd.index("--agent") + 1], gate=cmd[cmd.index("--instance") + 1]); print("Granted: g-new"); sys.exit(0)
    if cmd == ["enclave", "status", "--json"]: print(json.dumps({"node_id": os.environ["NODE"]}))
    elif cmd == ["agent", "list", "--json"]: print(json.dumps({"agents": [{"agent_code": "desks", "agent_id": os.environ["AGENT"]}]}))
    elif cmd == ["gate", "list", "--json"]:
        print(json.dumps([{"connector_type": "job", "node_id": os.environ["NODE"], "instance_id": "enclave8e1d5a6aJob"},
                          {"connector_type": "api", "node_id": os.environ["NODE"], "instance_id": os.environ["GATE"]}]))
    elif cmd == ["gate", "grant", "list", "--json"]: print(os.environ["GRANTS"])
    elif cmd[:3] == ["gate", "grant", "revoke"]: log(kind="revoke", grant=cmd[3])
    elif cmd[:2] == ["plan", "create"]:
        name = os.path.basename(cmd[2])
        with open(cmd[2]) as f: log(kind="cp", name=name, plan=json.load(f), to=cmd[2])   # what the Enclave's user reads
        log(kind="plan", name=name)
        if os.environ.get("PLAN_FAILS") == name: print("validation_error: refused for the test"); sys.exit(1)
        print("Plan created")
    else: raise SystemExit(f"unexpected {cmd}")
    sys.exit(0)
raise SystemExit(f"unexpected docker {a}")
"""


def up_functions(*names, up=None):
    """Functions from up.sh by name; `up` is other text for up.sh, for a planted red."""
    if up is None:
        with open(os.path.join(ROOT, "up.sh"), encoding="utf-8") as f:
            up = f.read()
    text = up
    import re
    return "\n".join(re.search(rf"^{n}\(\) \{{.*?^\}}\n", text, re.S | re.M).group(0) for n in names)


SECRET = "test-only-desk-admin-secret"
GH = "sha256:" + "6" * 64
CC = "sha256:" + "7" * 64


def run_step(grants="[]", image=True, digest_fails=False, providers=f"github={GH},claude-code={CC}", provider_fails="",
             adapter_files=True, plan_fails="", up=None, dev_id="", pre_limits=None):
    import subprocess, tempfile
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "bin"))
    # By this step step_desk_admin has written and checked the adapter's configuration, and init the CA
    # (tools/backend_preflight.py); the adapter is run by name below, so its files are checked again.
    if adapter_files:
        for rel in ("backend/config.json", "tls/ca.crt"):
            os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
            with open(os.path.join(d, rel), "w") as f:
                f.write("{}")
    if pre_limits is not None:   # the limits an earlier run wrote, as the box holds them
        os.makedirs(os.path.join(d, "backend"), exist_ok=True)
        with open(os.path.join(d, "backend", "desks-grant.json"), "w") as f:
            json.dump(pre_limits, f)
    with open(os.path.join(d, "bin", "docker"), "w") as f:
        f.write(STUB)
    os.chmod(os.path.join(d, "bin", "docker"), 0o755)
    with open(os.path.join(d, "up.env"), "w") as f:
        f.write("DESKS_NAME=x\n" + ("EKKA_BACKEND_IMAGE=ghcr.io/ekka-ai/ekka-backend@sha256:" + "a" * 64 + "\n" if image else ""))
    calls = os.path.join(d, "calls.jsonl")
    script = f"""
set -euo pipefail
export DESKS_STATE={d}; DESKS_AGENT=desks   # exported, as up.sh:38 does: setup.sh reads it
say() {{ :; }}; ok() {{ echo "ok: $*"; }}; die() {{ echo "die: $*" >&2; exit 1; }}
kc() {{ [ "$1 $2" = "admin-client on" ] && printf %s "{SECRET}"; }}
profile_names() {{ printf '%s\\n' claude-code codex github; }}
ENCLAVE_HOME=$DESKS_STATE/enclave-home
ekka_in() {{ docker compose exec -T ekka-enclave ekka "$@"; }}   # the test's docker stub reads the ekka words after the marker
enclave_home_file() {{ mkdir -p "$(dirname "$ENCLAVE_HOME/$1")"; cat > "$ENCLAVE_HOME/$1"; }}
{up_functions("install_plans", "create_desk_admin_plans", "desk_provider_args", "step_desk_grant", up=up)}
step_desk_grant
"""
    env = dict(os.environ, PATH=f"{d}/bin:{os.environ['PATH']}", CALLS=calls, DESK_ID=IMAGE, POLICY_DIGEST=POLICY_DIGEST,
               NODE="8e1d5a6a-6df2-429a-8c3d-cc75c7682671", AGENT=AGENT, GATE=GATE, GRANTS=grants,
               PROVIDER_DIGESTS=providers, PROVIDER_FAILS=provider_fails, PLAN_FAILS=plan_fails,
               **({"DIGEST_FAILS": "1"} if digest_fails else {}), **({"DEV_ID": dev_id} if dev_id else {}))
    r = subprocess.run(["bash", "-c", script], cwd=ROOT, capture_output=True, text=True, env=env, timeout=60,
                       stdin=subprocess.DEVNULL)
    seen = []
    if os.path.exists(calls):
        with open(calls) as f:
            seen = [json.loads(x) for x in f]
    return r, seen, d


class UpShStep(unittest.TestCase):
    def test_planted_without_its_configuration_the_adapter_is_never_run(self):
        r, seen, _ = run_step(adapter_files=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("the backend adapter was not started", r.stderr)
        # The adapter's SERVICE, which mounts the configuration, is never run, and no grant changes. (The policy
        # digest before it runs the image alone, with no network and nothing mounted; it reads no configuration.)
        self.assertEqual([c["kind"] for c in seen if c["kind"] in ("provider", "add", "revoke")], [])

    def test_no_grant_yet_adds_one_whose_limits_the_contract_accepts(self):
        r, calls, d = run_step()
        self.assertEqual(r.returncode, 0, r.stderr)
        adds = [c for c in calls if c["kind"] == "add"]
        self.assertEqual(len(adds), 1)
        self.assertEqual((adds[0]["agent"], adds[0]["gate"]), ("desks", GATE), "this Enclave's API gate, never the job gate")
        self.assertEqual(errors(adds[0]["limits"]), [])
        self.assertEqual(adds[0]["limits"]["image_digests"], [IMAGE])
        self.assertEqual(adds[0]["limits"]["policy_digests"], [POLICY_DIGEST])
        import hashlib
        with open(os.path.join(ROOT, "desk", "policy.json"), "rb") as f:
            self.assertEqual([c["input_sha"] for c in calls if c["kind"] == "digest"], [hashlib.sha256(f.read()).hexdigest()],
                             "the digest is of desk/policy.json itself")
        self.assertIn("--network", [c for c in calls if c["kind"] == "digest"][0]["argv"])

    def test_unrelated_development_image_does_not_expand_the_grant(self):
        dev = "sha256:" + "e" * 64   # IMAGE is all d
        _, without, _ = run_step()
        r, calls, _ = run_step(dev_id=dev)
        self.assertEqual(r.returncode, 0, r.stderr)
        before = [c for c in without if c["kind"] == "add"][0]["limits"]
        after = [c for c in calls if c["kind"] == "add"][0]["limits"]
        self.assertEqual(before["image_digests"], [IMAGE], "no dev image built: the grant is what it was")
        self.assertEqual(after["image_digests"], [IMAGE], "only the installed base image is authorized")
        self.assertEqual({k: v for k, v in after.items() if k != "image_digests"},
                         {k: v for k, v in before.items() if k != "image_digests"}, "unrelated images do not change grant limits")
        self.assertEqual(errors(after), [])

    def test_the_same_limits_are_kept_and_nothing_is_added_or_revoked(self):
        _, first, d = run_step()
        lim = [c for c in first if c["kind"] == "add"][0]["limits"]
        r, calls, _ = run_step(grants=json.dumps([row("g1", lim)]))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("kept", r.stdout)
        self.assertEqual([c["kind"] for c in calls if c["kind"] in ("add", "revoke")], [])

    def test_planted_changed_limits_add_the_new_grant_then_revoke_the_old(self):
        r, calls, _ = run_step(grants=json.dumps([row("g-old", {"image_digests": ["sha256:" + "0" * 64]})]))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([(c["kind"], c.get("grant")) for c in calls if c["kind"] in ("add", "revoke")],
                         [("add", None), ("revoke", "g-old")], "add first, revoke after: never without a grant")

    def test_without_the_adapter_nothing_happens(self):
        r, calls, _ = run_step(image=False)
        self.assertEqual((r.returncode, calls), (0, []))

    def test_planted_a_policy_the_adapter_refuses_changes_no_grant(self):
        r, calls, _ = run_step(digest_fails=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no grant was changed", r.stderr)
        self.assertEqual([c for c in calls if c["kind"] in ("add", "revoke")], [])

    def test_e4b_the_grant_lists_each_connected_provider_by_the_digest_the_adapter_read(self):
        r, calls, _ = run_step()
        self.assertEqual(r.returncode, 0, r.stderr)
        lim = [c for c in calls if c["kind"] == "add"][0]["limits"]
        self.assertEqual(lim["provider_config_digests"], {"claude-code": CC, "github": GH})
        self.assertEqual(lim["providers"], ["claude-code", "github"], "codex is not connected here, so it is left out")
        self.assertEqual(errors(lim), [])

    def test_e4b_the_secret_reaches_the_adapter_on_stdin_only(self):
        import hashlib
        _, calls, _ = run_step()
        asks = [c for c in calls if c["kind"] == "provider"]
        self.assertEqual(sorted(c["name"] for c in asks), ["claude-code", "codex", "github"])
        for c in asks:
            self.assertEqual(c["secret_sha"], hashlib.sha256(SECRET.encode()).hexdigest(), "on stdin")
            self.assertNotIn(SECRET, " ".join(c["argv"]), "never on the command line")
            self.assertIn("--no-deps", c["argv"])
            self.assertEqual(c["argv"][c["argv"].index("--config") + 1], "/etc/ekka-backend/config.json")

    def test_e4b_planted_a_provider_the_adapter_cannot_read_changes_no_grant(self):
        r, calls, _ = run_step(provider_fails="github")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("PROVIDER_UNREADABLE", r.stderr)
        self.assertIn("no grant was changed", r.stderr)
        self.assertEqual([c for c in calls if c["kind"] in ("add", "revoke")], [])

    def test_e4b_planted_a_digest_that_is_not_a_digest_changes_no_grant(self):
        r, calls, _ = run_step(providers="github=sha256:short")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual([c for c in calls if c["kind"] in ("add", "revoke")], [])

    def test_e4b_no_provider_connected_still_grants_with_an_empty_list(self):
        r, calls, _ = run_step(providers="")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([c for c in calls if c["kind"] == "add"][0]["limits"]["providers"], [])

    # The walk on a clean box (regression coverage, 2026-10-19): no step created the seven desk
    # administration plans, so no plan declared api.write on the desks agent, and EKKA refused the grant:
    # "This agent does not declare 'api.write'".
    ADMIN_PLANS = {f"plan-desk-{op}.json": f"desk.{op}.v1"
                   for op in ("create", "delete", "stop", "start", "policy", "detach", "status")}

    def assert_admin_plans_made_before_any_add(self, calls):
        made = [c["name"] for c in calls if c["kind"] == "plan"]
        self.assertEqual(sorted(made), sorted(self.ADMIN_PLANS), "all seven, each once")
        copied = {c["name"]: c["plan"] for c in calls if c["kind"] == "cp"}
        for name, code in self.ADMIN_PLANS.items():
            plan = copied[name]
            self.assertEqual((plan["plan"]["code"], plan["plan"]["agent"]), (code, AGENT), "owned by the desks agent")
            self.assertEqual(plan["definition"]["operations"][0]["steps"][0]["target"], GATE, "this Enclave's API gate")
        kinds = [c["kind"] for c in calls]
        if "add" in kinds:
            self.assertLess(max(i for i, k in enumerate(kinds) if k == "plan"), kinds.index("add"),
                            "the plans exist before the grant is asked for")

    def test_the_seven_admin_plans_are_made_before_the_grant(self):
        r, calls, _ = run_step()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assert_admin_plans_made_before_any_add(calls)
        self.assertEqual(len([c for c in calls if c["kind"] == "add"]), 1)

    def test_a_rerun_with_the_grant_in_place_makes_the_plans_and_changes_no_grant(self):
        _, first, _ = run_step()
        lim = [c for c in first if c["kind"] == "add"][0]["limits"]
        r, calls, _ = run_step(grants=json.dumps([row("g1", lim)]))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("kept", r.stdout)
        self.assert_admin_plans_made_before_any_add(calls)
        self.assertEqual([c["kind"] for c in calls if c["kind"] in ("add", "revoke")], [])

    def test_planted_a_refused_admin_plan_stops_before_any_grant(self):
        r, calls, _ = run_step(plan_fails="plan-desk-stop.json")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("the plan ekka.desk.stop.v1 was not created", r.stderr)
        self.assertEqual([c for c in calls if c["kind"] in ("add", "revoke")], [])

    def test_planted_without_the_call_no_admin_plan_is_made(self):
        # The red this change fixes: the same step with its create_desk_admin_plans line taken out.
        with open(os.path.join(ROOT, "up.sh"), encoding="utf-8") as f:
            up = f.read()
        line = '  create_desk_admin_plans "$gate" "$id"\n'
        self.assertEqual(up.count(line), 1, "step_desk_grant calls it once")
        r, calls, _ = run_step(up=up.replace(line, ""))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([c for c in calls if c["kind"] == "plan"], [])
        with self.assertRaises(AssertionError):
            self.assert_admin_plans_made_before_any_add(calls)

    def test_up_sh_runs_it_after_the_agent_exists(self):
        with open(os.path.join(ROOT, "up.sh"), encoding="utf-8") as f:
            self.assertIn("step_grant; step_agent_open; step_desk_grant; step_people", f.read())


class TheGatewayNeverPullsADeskImage(unittest.TestCase):
    """The grant pins the content ID of the image this machine built. A pull could put another image under the
    same name, so the docker driver must use only what is already here (OpenShell ImagePullPolicy::Never)."""

    def test_the_docker_driver_never_pulls(self):
        from gateway_preflight import read_toml   # tomllib where Python has it, the plain reader on 3.9 and 3.10
        config = read_toml(os.path.join(ROOT, "deploy", "gateway", "gateway.toml.template"))
        self.assertEqual(config["openshell"]["drivers"]["docker"].get("image_pull_policy"), "never")
