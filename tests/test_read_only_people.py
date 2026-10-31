"""People's desk sign-ins are read-only .

A person changes a desk through EKKA (`ekka desk <op>` runs a plan an organization admin may run), never with
their own sign-in at the gateway. So the `ekka-cli` client grants read scopes only, and a live install is migrated:
`apply_realm.py scopes ekka-cli` sets its scopes to what realm.json declares and signs out its sessions, so a
token issued with the old scopes ends. Tested against an in-memory Keycloak admin API.
"""
import copy
import io
import json
import os
import re
import sys
import unittest
from contextlib import redirect_stdout

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "deploy", "keycloak"))
import apply_realm  # noqa: E402

with open(apply_realm.SPEC) as f:
    SPEC = json.load(f)
REALM = apply_realm.realm_path(SPEC)
READ_ONLY = ["config:read", "provider:read", "sandbox:read", "workspace:read"]


class FakeKeycloak:
    """Just the admin API calls `scopes` makes, over a realm that runs today's install."""

    def __init__(self, cli_scopes=("openshell:all",), realm=True, client=True, sessions=2, offline=0):
        self.realm = realm
        self.scopes = [{"id": f"s-{n}", "name": n, "attributes": {"include.in.token.scope": "true"}}
                       for n in ("openshell:all", "sandbox:read", "sandbox:write", "provider:read", "offline_access")]
        # As a live install holds it: the redirect URI and the empty web origins realm.json declares.
        self.cli = ({"id": "c-cli", "clientId": "ekka-cli", "redirectUris": ["http://127.0.0.1/*"], "webOrigins": []}
                    if client else None)
        self.defaults = {n for n in cli_scopes}
        self.sessions = [{"id": f"u{i}"} for i in range(sessions)]
        self.offline = [{"id": f"o{i}"} for i in range(offline)]   # Keycloak keeps these apart
        self.writes = []
        self.fail_at = None   # the write number (1-based) that fails, as a lost connection would

    def _scope(self, sid):
        return next(s for s in self.scopes if s["id"] == sid)

    def get(self, path):
        if path == REALM:
            return {"realm": "ekka-desks"} if self.realm else None
        if path == REALM + "/client-scopes":
            return list(self.scopes)
        if path.startswith(REALM + "/clients?clientId="):
            # A copy, as an HTTP answer is: a change to it reaches Keycloak only through a write.
            return [copy.deepcopy(self.cli)] if self.cli and path.endswith("=ekka-cli") else []
        if path == REALM + "/clients/c-cli/default-client-scopes":
            return [{"name": s["name"], "id": s["id"]} for s in self.scopes if s["name"] in self.defaults]
        if path.startswith(REALM + "/clients/c-cli/user-sessions"):
            return list(self.sessions)
        if path.startswith(REALM + "/clients/c-cli/offline-sessions"):
            return list(self.offline)
        raise AssertionError("unexpected GET " + path)

    def write(self, method, path, body):
        self.writes.append((method, path))
        if self.fail_at is not None and len(self.writes) == self.fail_at:
            raise ConnectionError(f"planted: write {self.fail_at} ({method} {path}) did not arrive")
        m = re.fullmatch(re.escape(REALM) + r"/clients/c-cli/default-client-scopes/(.+)", path)
        if m and method == "PUT":
            self.defaults.add(self._scope(m.group(1))["name"])
        elif m and method == "DELETE":
            self.defaults.discard(self._scope(m.group(1))["name"])
        elif path == REALM + "/client-scopes" and method == "POST":
            self.scopes.append({"id": "s-" + body["name"], "name": body["name"], "attributes": body["attributes"]})
        elif path == REALM + "/clients/c-cli" and method == "PUT":
            # As Keycloak 26.7.4 does (measured): a field the update leaves out keeps its value, named attributes
            # merge, and an attribute set to "" is removed. EXCEPT webOrigins: left out while empty, they are filled
            # in from the redirect URIs (RepresentationToModel.updateClient, defaultWebOrigins; measured on a box).
            attrs = {**self.cli.get("attributes", {}), **body.get("attributes", {})}
            attrs = {k: v for k, v in attrs.items() if v != ""}
            origins = body.get("webOrigins")
            if origins is None:
                origins = self.cli.get("webOrigins") or sorted({u.split("/*")[0].rstrip("/") for u in
                                                                 self.cli.get("redirectUris", []) if u.startswith("http")})
            self.cli = {**self.cli, **body, "attributes": attrs, "webOrigins": origins}
        elif path.startswith(REALM + "/sessions/") and method == "DELETE":
            # As Keycloak does: a plain DELETE ends an online session; only isOffline=true ends an offline one.
            sid, _, query = path[len(REALM + "/sessions/"):].partition("?")
            if query == "isOffline=true":
                self.offline = [s for s in self.offline if s["id"] != sid]
            else:
                self.sessions = [s for s in self.sessions if s["id"] != sid]
        else:
            raise AssertionError(f"unexpected {method} {path}")


class TheRealm(unittest.TestCase):
    def cli(self):
        return next(c for c in SPEC["clients"] if c["clientId"] == "ekka-cli")

    def test_gives_people_read_scopes_only(self):
        self.assertEqual(sorted(self.cli()["defaultScopes"]), READ_ONLY)
        self.assertFalse([s for s in self.cli()["defaultScopes"] if s.endswith(":write") or s == "openshell:all"])

    def test_declares_every_scope_it_grants_and_refuses_anonymous_registration(self):
        declared = {s["name"] for s in SPEC["clientScopes"]}
        for c in SPEC["clients"]:
            self.assertLessEqual(set(c["defaultScopes"]), declared, c["clientId"])
        self.assertLessEqual(declared, apply_realm.OPENSHELL_SCOPES)
        self.assertIs(SPEC["realm"]["registrationAllowed"], False)


class ScopesMigratesALiveInstall(unittest.TestCase):
    def test_a_wide_sign_in_becomes_read_only_and_its_sessions_end(self):
        kc = FakeKeycloak()
        done = apply_realm.set_scopes(kc, SPEC, "ekka-cli", self.declared())
        self.assertEqual(sorted(kc.defaults), READ_ONLY)
        self.assertEqual(done, {"added": READ_ONLY, "removed": ["openshell:all"], "sessions_ended": 2,
                                "resumed": False, "before": ["openshell:all"], "after": READ_ONLY})
        self.assertEqual(kc.sessions, [], "a token with the old scopes cannot be refreshed")

    def test_an_offline_sign_in_ends_too(self):
        # ekka desk login asks for offline_access by default, so a person's normal sign-in is an offline session.
        kc = FakeKeycloak(sessions=1, offline=1)
        done = apply_realm.set_scopes(kc, SPEC, "ekka-cli", self.declared())
        self.assertEqual((kc.sessions, kc.offline), ([], []), "both kinds end: no old-scope token can be refreshed")
        self.assertEqual(done["sessions_ended"], 2)
        self.assertIn(("DELETE", REALM + "/sessions/o0?isOffline=true"), kc.writes)

    def test_planted_an_offline_session_deleted_as_if_online_survives(self):
        # The plain DELETE Keycloak accepts but which leaves an offline session alive: the fake models it, so a
        # migration that forgot isOffline=true is caught here, not on a box.
        kc = FakeKeycloak(sessions=0, offline=1)
        kc.write("DELETE", REALM + "/sessions/o0", None)
        self.assertEqual(kc.offline, [{"id": "o0"}])

    def test_creates_a_scope_the_live_realm_has_not_met_in_the_token_claim(self):
        kc = FakeKeycloak()
        apply_realm.set_scopes(kc, SPEC, "ekka-cli", self.declared())
        made = {s["name"]: s for s in kc.scopes}
        for name in ("workspace:read", "config:read"):
            self.assertEqual(made[name]["attributes"]["include.in.token.scope"], "true", name)

    def test_running_it_again_changes_nothing_and_signs_nobody_out(self):
        kc = FakeKeycloak()
        apply_realm.set_scopes(kc, SPEC, "ekka-cli", self.declared())
        kc.sessions = [{"id": "fresh"}]
        kc.writes.clear()
        done = apply_realm.set_scopes(kc, SPEC, "ekka-cli", self.declared())
        self.assertEqual(done, {"added": [], "removed": [], "sessions_ended": 0, "resumed": False,
                                "before": READ_ONLY, "after": READ_ONLY})
        self.assertEqual(kc.writes, [])

    def test_a_run_that_stops_halfway_through_the_sign_out_is_finished_by_the_next(self):
        # Codex (ADK-E2): the scopes change first, so a run that dies while ending sessions left the next run
        # seeing nothing to change, and the old-scope sessions, offline ones included, lived on.
        kc = FakeKeycloak(sessions=2, offline=2)
        writes_before_sign_out = 2 + 1 + len(READ_ONLY) + 1   # two new scopes, the marker, four added, one removed
        kc.fail_at = writes_before_sign_out + 3               # two sessions ended, the third write never arrives
        with self.assertRaises(ConnectionError):
            apply_realm.set_scopes(kc, SPEC, "ekka-cli", self.declared())
        self.assertEqual(sorted(kc.defaults), READ_ONLY, "the scopes did change")
        self.assertEqual(len(kc.sessions) + len(kc.offline), 2, "and two old-scope sessions are still alive")
        kc.fail_at = None
        done = apply_realm.set_scopes(kc, SPEC, "ekka-cli", self.declared())
        self.assertEqual((kc.sessions, kc.offline), ([], []), "the next run ends the rest, offline ones too")
        self.assertEqual((done["added"], done["removed"], done["sessions_ended"], done["resumed"]), ([], [], 2, True))
        self.assertNotIn(apply_realm.SIGN_OUT_OWED, kc.cli["attributes"], "nothing is owed any more")

    def test_a_run_that_stops_after_one_scope_write_is_finished_by_the_next(self):
        kc = FakeKeycloak(sessions=1, offline=1)
        kc.fail_at = 3   # the marker and one new scope are written, then the connection drops
        with self.assertRaises(ConnectionError):
            apply_realm.set_scopes(kc, SPEC, "ekka-cli", self.declared())
        kc.fail_at = None
        apply_realm.set_scopes(kc, SPEC, "ekka-cli", self.declared())
        self.assertEqual(sorted(kc.defaults), READ_ONLY)
        self.assertEqual((kc.sessions, kc.offline), ([], []))

    def test_a_finished_sign_out_owes_nothing_so_later_runs_sign_nobody_out(self):
        # up.sh runs this on every run: only a sign-out still owed may end the sessions people opened since.
        kc = FakeKeycloak(sessions=1, offline=1)
        apply_realm.set_scopes(kc, SPEC, "ekka-cli", self.declared())
        kc.sessions, kc.offline = [{"id": "fresh"}], [{"id": "fresh-offline"}]
        done = apply_realm.set_scopes(kc, SPEC, "ekka-cli", self.declared())
        self.assertEqual((done["sessions_ended"], done["resumed"]), (0, False))
        self.assertEqual(len(kc.sessions) + len(kc.offline), 2)

    def test_sessions_that_will_not_end_fail_the_run_and_stay_owed(self):
        kc = FakeKeycloak(sessions=1)
        real_write = kc.write

        def sessions_survive(method, path, body):
            if path.startswith(REALM + "/sessions/"):
                kc.writes.append((method, path))
                return None   # accepted, and nothing ended
            return real_write(method, path, body)
        kc.write = sessions_survive
        with self.assertRaises(SystemExit):
            apply_realm.set_scopes(kc, SPEC, "ekka-cli", self.declared())
        self.assertEqual(kc.cli["attributes"][apply_realm.SIGN_OUT_OWED], "pending")

    def test_the_marker_write_names_only_the_marker(self):
        # A stale copy of the client, sent back after its scopes changed, must never be what Keycloak receives.
        kc = FakeKeycloak()
        sent = []
        real_write = kc.write
        kc.write = lambda m, p, b: (sent.append(b) if p == REALM + "/clients/c-cli" else None, real_write(m, p, b))
        apply_realm.set_scopes(kc, SPEC, "ekka-cli", self.declared())
        self.assertEqual([sorted(b) for b in sent], [["attributes", "clientId", "webOrigins"]] * 2)
        self.assertEqual([b["webOrigins"] for b in sent], [self.cli()["webOrigins"]] * 2, "the declared ones, from realm.json")

    def test_a_scope_change_leaves_the_web_origins_realm_json_declares(self):
        # Measured on a test box 2026-10-24, twice (a planted scope, then up.sh's own step_people_scopes putting it
        # back): after each change `apply` refused "clients.ekka-cli.webOrigins: live ['http://127.0.0.1'], want []".
        kc = FakeKeycloak()
        apply_realm.set_scopes(kc, SPEC, "ekka-cli", self.declared())
        self.assertEqual(kc.cli["webOrigins"], self.cli()["webOrigins"])
        self.assertEqual(kc.cli["redirectUris"], self.cli()["redirectUris"], "the redirect URIs are kept as they are")

    def test_a_new_install_has_nothing_to_migrate(self):
        for kc in (FakeKeycloak(realm=False), FakeKeycloak(client=False)):
            self.assertIsNone(apply_realm.set_scopes(kc, SPEC, "ekka-cli", self.declared()))
            self.assertEqual(kc.writes, [])

    def test_a_scope_openshell_does_not_have_is_refused_before_any_change(self):
        # offline_access is a real Keycloak scope the live realm has: it would hand out long-lived tokens.
        for bad in ("everything:all", "offline_access"):
            kc = FakeKeycloak()
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                apply_realm.set_scopes(kc, SPEC, "ekka-cli", ["sandbox:read", bad])
            self.assertEqual(kc.writes, [], bad)

    def test_with_no_list_it_uses_what_realm_json_declares(self):
        kc = FakeKeycloak()
        real = apply_realm.Api
        apply_realm.Api = lambda: kc
        try:
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(apply_realm.main(["scopes", "ekka-cli"]), 0)
        finally:
            apply_realm.Api = real
        self.assertEqual(sorted(kc.defaults), READ_ONLY)
        self.assertIn("ekka-cli default scopes before openshell:all, after config:read provider:read sandbox:read "
                      "workspace:read; 2 sessions signed out", out.getvalue())

    def test_one_line_says_before_and_after_and_a_second_run_says_unchanged(self):
        # The release-day runbook's done-signal on the box.
        kc = FakeKeycloak()
        real = apply_realm.Api
        apply_realm.Api = lambda: kc
        try:
            lines = []
            for _ in range(2):
                out = io.StringIO()
                with redirect_stdout(out):
                    apply_realm.main(["scopes", "ekka-cli"])
                lines.append(out.getvalue().strip())
        finally:
            apply_realm.Api = real
        self.assertEqual(len(lines[0].splitlines()), 1)
        self.assertIn("before openshell:all, after", lines[0])
        self.assertEqual(lines[1], "keycloak: ekka-cli default scopes before config:read provider:read sandbox:read "
                                   "workspace:read, after config:read provider:read sandbox:read workspace:read (unchanged)")

    def declared(self):
        return self.cli()["defaultScopes"]

    def cli(self):
        return next(c for c in SPEC["clients"] if c["clientId"] == "ekka-cli")


class Setup(unittest.TestCase):
    def test_migrates_before_it_applies(self):
        with open(os.path.join(ROOT, "deploy", "setup.sh"), encoding="utf-8") as f:
            text = f.read()
        migrate = text.index("apply_realm.py scopes ekka-cli")
        self.assertLess(migrate, text.index("apply_realm.py apply"),
                        "apply refuses the old scopes, so the live install must migrate first")



def shell_function(path, name):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return re.search(rf"^{name}\(\) \{{.*?^\}}\n", f.read(), re.S | re.M).group(0)


def chain(up_text=None):
    if up_text is None:
        with open(os.path.join(ROOT, "up.sh"), encoding="utf-8") as f:
            up_text = f.read()
    return next(l for l in up_text.splitlines() if l.startswith("step_preflight; "))


def run_upgrade(chain_line):
    """up.sh's own chain on a box that is ALREADY installed: `init` is done, so step_init skips. Every other step
    is a stub except step_people_scopes, which is up.sh's own. `bash` records what setup.sh is asked."""
    import subprocess
    steps = [s.strip() for s in chain_line.split(";") if s.strip()]
    stubs = "\n".join(f"{s}() {{ :; }}" for s in steps if s not in ("step_init", "step_people_scopes"))
    script = "\n".join([
        "set -euo pipefail", "say() { :; }; ok() { :; }; die() { echo \"die: $*\" >&2; exit 1; }",
        "is_done() { return 0; }; done_() { :; }",
        "bash() { echo \"setup.sh-call: $*\"; }",
        stubs, shell_function("up.sh", "step_init"), shell_function("up.sh", "step_people_scopes"), chain_line])
    return subprocess.run(["/bin/bash", "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=30)


class TheUpgradeOfAnInstalledBoxMigrates(unittest.TestCase):
    """`init` runs once, so the migration inside it never runs on an upgrade. up.sh runs it on every run."""

    def test_an_installed_box_upgrading_runs_the_migration_and_never_init(self):
        r = run_upgrade(chain())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("setup.sh-call: setup.sh people-scopes", r.stdout)
        self.assertNotIn("setup.sh init", r.stdout)

    def test_it_runs_once_the_services_are_started(self):
        steps = [s.strip() for s in chain().split(";")]
        self.assertEqual(steps.index("step_people_scopes"), steps.index("step_start") + 1)

    def test_planted_the_step_left_out_of_the_chain_migrates_nothing(self):
        r = run_upgrade(chain().replace(" step_people_scopes;", ""))
        self.assertNotIn("people-scopes", r.stdout)

    def test_planted_the_migration_only_in_init_never_runs_on_an_installed_box(self):
        # The shape this fixes: init skipped, so its migration is skipped.
        r = run_upgrade(chain().replace(" step_people_scopes;", ""))
        self.assertNotIn("setup.sh init", r.stdout, "step_init is skipped on an installed box")

    def test_the_setup_stage_migrates_once_keycloak_answers(self):
        import subprocess
        for keycloak_up, want_rc, want in ((True, 0, "python3-call: keycloak/apply_realm.py scopes ekka-cli"),
                                           (False, 1, "die: Keycloak did not answer")):
            script = "\n".join([
                "set -euo pipefail", f"DESKS_STATE=$(mktemp -d); touch $DESKS_STATE/desks.env; T=/x",
                "die() { echo \"die: $*\" >&2; exit 1; }; kc_env() { :; }; sleep() { :; }",
                f"curl() {{ {'return 0' if keycloak_up else 'return 7'}; }}",
                "python3() { echo \"python3-call: $*\"; }",
                shell_function("deploy/setup.sh", "stage_people_scopes"), "stage_people_scopes"])
            r = subprocess.run(["/bin/bash", "-c", script], cwd=os.path.join(ROOT, "deploy"), capture_output=True,
                               text=True, timeout=30)
            with self.subTest(keycloak_up=keycloak_up):
                self.assertEqual(r.returncode, want_rc, r.stderr)
                self.assertIn(want, r.stdout + r.stderr)
                if not keycloak_up:
                    self.assertNotIn("python3-call", r.stdout, "never a migration against a sign-in service that is down")


if __name__ == "__main__":
    unittest.main()
