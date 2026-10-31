"""Network packs: reusable sets of named rules a discovery desk's policy is built from (ekka-ai/.github#784).

NVIDIA OpenShell (v0.1.4, measured on a desks server 2026-10-31) cannot open "every host" to a desk's programs: its
DNS answers only names a rule names, and a rule without a host works only through an explicit proxy, which OpenShell
strips from every program. So a discovery desk starts from named hosts, grouped by what they are for, and whatever
the build still needs shows up as a denial with its name and program. Packs are combined with desk/policy.json into
one policy, and the combination is refused when two rules that can match the same host and port disagree on what
OpenShell needs them to agree on (openshell-policy/src/ambiguity.rs): the clash that refused a desk the same night.
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import desk_grant as dg  # noqa: E402
import schema_check  # noqa: E402

with open(os.path.join(ROOT, "desk", "policy.json")) as f:
    BASE = json.load(f)
with open(os.path.join(ROOT, "vendor", "ekka-contracts", "governance", "grant-constraints", "desks.v1.schema.json")) as f:
    SCHEMA = json.load(f)
DIRS = dg.pack_dirs()
SHIPPED = sorted(dg.available_packs(DIRS))


class EveryShippedPack(unittest.TestCase):
    def test_each_pack_is_well_formed(self):
        self.assertTrue(SHIPPED, "packs ship in desk/packs")
        for name in SHIPPED:
            pack = dg.load_pack(name, DIRS)
            self.assertTrue(pack.get("about"), name)
            for rule_name, rule in pack["rules"].items():
                dg.check_pack_rule(name, rule_name, rule)

    def test_registries_are_download_only(self):
        """The owner's rule (2026-10-30): registries are read only; a publish is refused by the desk itself."""
        for name in ("rust", "node"):
            for rule in dg.load_pack(name, DIRS)["rules"].values():
                for ep in rule["endpoints"]:
                    self.assertEqual(ep.get("access"), "NETWORK_ACCESS_PRESET_READ_ONLY", (name, ep["host"]))

    def test_git_may_fetch_and_never_push(self):
        (ep,) = dg.load_pack("github", DIRS)["rules"]["git"]["endpoints"]
        allowed = [(r["allow"]["method"], r["allow"]["path"]) for r in ep["rules"]]
        self.assertIn(("POST", "/**/git-upload-pack"), allowed)
        self.assertFalse(any("receive-pack" in p for _, p in allowed), "push is git-receive-pack")
        self.assertFalse(any(m in ("PUT", "PATCH", "DELETE") for m, _ in allowed))

    def test_every_combination_of_shipped_packs_is_one_policy_openshell_accepts(self):
        import itertools
        for n in range(1, len(SHIPPED) + 1):
            for combo in itertools.combinations(SHIPPED, n):
                policy = dg.compose(BASE, list(combo), DIRS)
                for k in ("filesystem", "landlock", "process"):
                    self.assertEqual(policy[k], BASE[k], f"{combo}: the shipped {k} is untouched")
                self.assertIn("github_clone", policy["networkPolicies"], "the shipped rule stays")


class TheCombination(unittest.TestCase):
    def plant(self, d, name, pack):
        with open(os.path.join(d, f"{name}.json"), "w") as f:
            json.dump({"pack": name, "about": "planted", **pack}, f)
        return DIRS + [d]

    def rule(self, **ep):
        e = {"host": "github.com", "port": 443, "protocol": "rest", "enforcement": "NETWORK_ENFORCEMENT_MODE_ENFORCE"}
        e.update(ep)
        return {"rules": {"x": {"why": "planted", "endpoints": [e], "binaries": ["/usr/bin/git"]}}}

    def test_planted_a_rule_that_disagrees_with_the_shipped_github_rule_is_refused(self):
        """Tonight's clash, by name: the same host and port with TLS relayed against TLS opened."""
        with tempfile.TemporaryDirectory() as d:
            dirs = self.plant(d, "clash", self.rule(tls="NETWORK_TLS_MODE_SKIP"))
            with self.assertRaisesRegex(dg.Refused, "disagree on tls"):
                dg.compose(BASE, ["clash"], dirs)
            dirs = self.plant(d, "slash", self.rule(allowEncodedSlash=True))
            with self.assertRaisesRegex(dg.Refused, "allowEncodedSlash"):
                dg.compose(BASE, ["slash"], dirs)

    def test_a_wildcard_overlaps_the_names_under_it(self):
        self.assertTrue(dg.host_patterns_overlap("*.githubusercontent.com", "objects.githubusercontent.com"))
        self.assertFalse(dg.host_patterns_overlap("*.githubusercontent.com", "a.b.githubusercontent.com"))
        self.assertTrue(dg.host_patterns_overlap("", "anything.example"))

    def test_planted_packs_that_open_too_much_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            cases = {
                "addr": (self.rule(host="", allowedIps=["1.0.0.0/8"]), "never addresses"),
                "tld": (self.rule(host="*.io"), "three labels"),
                "port": (self.rule(port=22), "443 and 80"),
                "audit": (self.rule(enforcement="NETWORK_ENFORCEMENT_MODE_AUDIT"), "enforcement"),
            }
            for name, (pack, says) in cases.items():
                with self.assertRaisesRegex(dg.Refused, says, msg=name):
                    dg.compose(BASE, [name], self.plant(d, name, pack))
            bad = self.rule(); bad["rules"]["x"]["binaries"] = ["/tmp/**/cargo"]
            with self.assertRaisesRegex(dg.Refused, "a pattern only under"):
                dg.compose(BASE, ["tmp"], self.plant(d, "tmp", bad))
            silent = self.rule(); silent["rules"]["x"]["why"] = " "
            with self.assertRaisesRegex(dg.Refused, "why"):
                dg.compose(BASE, ["silent"], self.plant(d, "silent", silent))
            with self.assertRaisesRegex(dg.Refused, "no pack 'nope'"):
                dg.compose(BASE, ["nope"], DIRS)

    def test_a_pack_of_this_install_is_found_beside_the_shipped_ones(self):
        with tempfile.TemporaryDirectory() as state:
            os.mkdir(os.path.join(state, "packs.local"))
            with open(os.path.join(state, "packs.local", "govern.json"), "w") as f:
                json.dump({"pack": "govern", "about": "a project pack", **self.rule(host="npm.pkg.github.com")}, f)
            dirs = dg.pack_dirs(state)
            self.assertIn("govern", dg.available_packs(dirs))
            policy = dg.compose(BASE, ["github", "govern"], dirs)
            self.assertIn("govern_x", policy["networkPolicies"])


class TheWindow(unittest.TestCase):
    def test_the_window_widens_the_grant_to_exactly_the_packs_hosts(self):
        policy = dg.compose(BASE, ["github", "rust"], DIRS)
        w = dg.window(policy, "sha256:" + "a" * 64, "sha256:" + "b" * 64, "r@sha256:" + "a" * 64, "f", "why", "t")
        self.assertEqual(w["cidrs"], [])
        self.assertIn("index.crates.io", w["hosts"]); self.assertIn("*.githubusercontent.com", w["hosts"])
        self.assertEqual(w["ports"], [443])
        lim = dg.limits(["sha256:" + "d" * 64, *w["images"]], ["sha256:" + "e" * 64, *w["policy_digests"]], {},
                        dg.profile_hosts(), BASE, cidrs=w["cidrs"], ports=w["ports"], hosts=w["hosts"])
        self.assertEqual(schema_check.schema_errors(lim, SCHEMA), [], "the contract accepts the widened grant")
        for h in w["hosts"]:
            self.assertIn(h, lim["allowed_hosts"])
        args = dg.extras_args(w)
        self.assertEqual(args.count("--allowed-host"), len(w["hosts"]))


if __name__ == "__main__":
    unittest.main()


class PrivateRegistryKeyProfile(unittest.TestCase):
    """A private registry's host comes with its key, in one key profile (desk/profiles), never also in a pack."""
    PROFILES = os.path.join(ROOT, "desk", "profiles")

    def test_the_github_packages_key_reaches_one_host_read_only_from_node(self):
        self.assertEqual(dg.profile_hosts(self.PROFILES), ["npm.pkg.github.com"])
        with open(os.path.join(self.PROFILES, "github-packages.yaml")) as f:
            text = f.read()
        self.assertIn("access: read-only", text)
        self.assertIn("enforcement: enforce", text)
        self.assertIn("env_vars: [NODE_AUTH_TOKEN]", text)
        self.assertIn("binaries: [/usr/**/bin/node]", text)
        self.assertNotIn("allowed_ips", text)

    def test_no_shipped_pack_opens_a_host_a_key_profile_opens(self):
        keyed = set(dg.profile_hosts(self.PROFILES))
        for name in SHIPPED:
            for rule_name, rule in dg.load_pack(name, DIRS)["rules"].items():
                for ep in rule["endpoints"]:
                    self.assertNotIn(ep["host"], keyed, f"pack {name} rule {rule_name} opens a host its key profile owns")
