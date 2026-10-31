"""The backend adapter's desk-admin client, and its secret into the Enclave's vault .

`apply_realm.py admin-client on` makes the confidential client `ekka-desk-admin`: a service account with the
openshell-admin role and exactly the desk administration scopes, never openshell:all. Its secret leaves Keycloak
only into a pipe, and up.sh's connect_desk_admin pipes it into `ekka api connect desks --stdin` inside the Enclave,
so it is never a file, an argument or a terminal.
"""
import copy
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "deploy", "keycloak"))
import apply_realm  # noqa: E402

with open(apply_realm.SPEC) as f:
    SPEC = json.load(f)
REALM = apply_realm.realm_path(SPEC)
SECRET = "s3cr3t-value-never-on-disk"


class FakeKeycloak:
    """The admin API calls `admin-client` makes, over a live realm without the client."""

    def __init__(self):
        self.scopes = [{"id": f"s-{n}", "name": n} for n in
                       ("openshell:all", "sandbox:read", "sandbox:write", "provider:read", "workspace:read", "config:read")]
        self.client = None
        self.defaults = set()
        self.roles = []

    def get(self, path):
        if path == REALM:
            return {"realm": "ekka-desks"}
        if path == REALM + "/client-scopes":
            return list(self.scopes)
        if path.startswith(REALM + "/clients?clientId="):
            # A copy, as an HTTP answer is: a change to it reaches Keycloak only through a write.
            return [copy.deepcopy(self.client)] if self.client and path.endswith("=" + apply_realm.ADMIN_CLIENT) else []
        if path == REALM + "/clients/c-admin/default-client-scopes":
            return [s for s in self.scopes if s["name"] in self.defaults]
        if path == REALM + "/clients/c-admin/service-account-user":
            return {"id": "u-sa"}
        if path == REALM + "/roles/openshell-admin":
            return {"name": "openshell-admin"}
        if path.startswith(REALM + "/clients/c-admin/user-sessions"):
            return []
        if path.startswith(REALM + "/clients/c-admin/offline-sessions"):
            return []
        if path == REALM + "/clients/c-admin/client-secret":
            return {"value": SECRET}
        raise AssertionError("unexpected GET " + path)

    def write(self, method, path, body):
        m = re.fullmatch(re.escape(REALM) + r"/clients/c-admin/default-client-scopes/s-(.+)", path)
        if path == REALM + "/clients" and method == "POST":
            self.client = {"id": "c-admin", **body}
        elif path == REALM + "/clients/c-admin" and method == "PUT":
            # As Keycloak 26.7.4 does (measured): a field the update leaves out keeps its value, named attributes
            # merge, and an attribute set to "" is removed.
            attrs = {**self.client.get("attributes", {}), **body.get("attributes", {})}
            attrs = {k: v for k, v in attrs.items() if v != ""}
            self.client = {**self.client, **body, "attributes": attrs}
        elif path == REALM + "/users/u-sa/role-mappings/realm" and method == "POST":
            self.roles += [r["name"] for r in body]
        elif path == REALM + "/client-scopes" and method == "POST":
            self.scopes.append({"id": "s-" + body["name"], "name": body["name"]})
        elif m and method == "PUT":
            self.defaults.add(m.group(1))
        elif m and method == "DELETE":
            self.defaults.discard(m.group(1))
        else:
            raise AssertionError(f"unexpected {method} {path}")


class Piped(io.StringIO):
    def isatty(self):
        return False


def run(argv, kc):
    real = apply_realm.Api
    apply_realm.Api = lambda: kc
    out = Piped()
    try:
        with redirect_stdout(out):
            code = apply_realm.main(argv)
    finally:
        apply_realm.Api = real
    return code, out.getvalue()


class TheAdminClient(unittest.TestCase):
    def test_holds_exactly_the_desk_administration_it_performs(self):
        kc = FakeKeycloak()
        code, out = run(["admin-client", "on"], kc)
        self.assertEqual(code, 0)
        self.assertEqual(sorted(kc.defaults), apply_realm.ADMIN_SCOPES)
        self.assertNotIn("openshell:all", kc.defaults)
        self.assertEqual(kc.roles, ["openshell-admin"])
        c = kc.client
        self.assertEqual((c["clientId"], c["publicClient"], c["serviceAccountsEnabled"], c["enabled"]),
                         ("ekka-desk-admin", False, True, True))
        self.assertFalse(c["standardFlowEnabled"] or c["directAccessGrantsEnabled"] or c["implicitFlowEnabled"],
                         "a service account only: no person signs in as it")
        self.assertEqual(out, SECRET, "the secret, and nothing else, into the pipe")

    def test_turning_it_on_finishes_an_owed_sign_out_and_leaves_none_owed(self):
        # A run that stopped while ending the adapter's sessions left the marker; `on` ends them and clears it,
        # and its own enable must not write the marker back from the copy it read first.
        kc = FakeKeycloak()
        run(["admin-client", "on"], kc)
        kc.client["attributes"][apply_realm.SIGN_OUT_OWED] = "pending"
        run(["admin-client", "on"], kc)
        self.assertNotIn(apply_realm.SIGN_OUT_OWED, kc.client["attributes"])
        self.assertIs(kc.client["enabled"], True)

    def test_creates_the_write_scopes_a_live_realm_lacks(self):
        kc = FakeKeycloak()
        run(["admin-client", "on"], kc)
        self.assertIn("config:write", {s["name"] for s in kc.scopes})

    def test_its_scopes_are_exactly_the_four_the_adapters_calls_need(self):
        # Written here from OpenShell 0.1.4's annotations, never read from the code under test (scope review).
        kc = FakeKeycloak()
        run(["admin-client", "on"], kc)
        self.assertEqual(sorted(kc.defaults), ["config:write", "provider:read", "sandbox:read", "sandbox:write"])
        for wider in ("provider:write", "config:read", "workspace:read", "openshell:all"):
            self.assertNotIn(wider, kc.defaults, f"{wider}: more than the adapter's calls need")

    def test_refuses_to_print_its_secret_to_a_terminal(self):
        class Tty(io.StringIO):
            def isatty(self):
                return True
        kc, err = FakeKeycloak(), io.StringIO()
        real_out, real_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = Tty(), err
        try:
            self.assertEqual(apply_realm.main(["admin-client", "on"]), 2)
        finally:
            sys.stdout, sys.stderr = real_out, real_err
        self.assertIsNone(kc.client)
        self.assertNotIn(SECRET, err.getvalue())

    def test_off_disables_it_and_prints_no_secret(self):
        kc = FakeKeycloak()
        run(["admin-client", "on"], kc)
        code, out = run(["admin-client", "off"], kc)
        self.assertEqual(code, 0)
        self.assertIs(kc.client["enabled"], False)
        self.assertNotIn(SECRET, out)


def up_function(name):
    with open(os.path.join(ROOT, "up.sh"), encoding="utf-8") as f:
        text = f.read()
    m = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.S | re.M)
    assert m, f"{name} is not in up.sh"
    return m.group(0)


class ConnectDeskAdmin(unittest.TestCase):
    def run_it(self, kc_rc=0):
        state, seen = tempfile.mkdtemp(), tempfile.mkdtemp()
        os.makedirs(os.path.join(state, "deploy"))
        script = f"""
set -euo pipefail
DESKS_STATE={state}
ok() {{ echo "ok: $*"; }}
die() {{ echo "die: $*" >&2; exit 1; }}
kc() {{ printf '%s' "$KC_SECRET"; return {kc_rc}; }}
ekka_in() {{ printf '%s\\n' "$@" > {seen}/args; cat > {seen}/stdin; }}
cd {state}
{up_function("connect_desk_admin")}
connect_desk_admin
"""
        r = subprocess.run(["/bin/bash", "-c", script], capture_output=True, text=True, timeout=30,
                           stdin=subprocess.DEVNULL, env=dict(os.environ, KC_SECRET=SECRET))
        def read(name):
            path = os.path.join(seen, name)
            if not os.path.exists(path):
                return ""
            with open(path) as fh:
                return fh.read()
        args, stdin = read("args").split("\n"), read("stdin")
        return r, args, stdin, state

    def test_the_secret_reaches_the_vault_through_stdin_only(self):
        r, args, stdin, state = self.run_it()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(stdin, SECRET)
        self.assertEqual(args[:7], ["api", "connect", "desks", "--input",
                                   "socket=/run/ekka-backend/backend.sock", "--reach", "loopback"])
        self.assertIn("--stdin", args)
        self.assertNotIn(SECRET, " ".join(args), "never an argument")
        self.assertNotIn(SECRET, r.stdout + r.stderr, "never printed")
        for root, _, files in os.walk(state):
            for f in files:
                with open(os.path.join(root, f), errors="ignore") as fh:
                    self.assertNotIn(SECRET, fh.read(), f"the secret is on disk in {f}")

    def test_a_failure_anywhere_in_the_pipe_stops_the_install(self):
        r, _, _, _ = self.run_it(kc_rc=1)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("did not reach the Enclave's vault", r.stderr)


if __name__ == "__main__":
    unittest.main()
