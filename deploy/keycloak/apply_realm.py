"""Apply deploy/keycloak/realm.json to this setup's Keycloak through the admin API.

    apply_realm.py apply                         create what is absent; refuse, naming each difference, if
                                                 anything present differs. Nothing is written on a refusal.
    apply_realm.py enclave-secret | ekka secret put <NAME> --stdin
                                                 the Enclave client's secret, into a pipe only
    apply_realm.py scopes <client> [<scope,...>]
                                                 set a live client's OpenShell default scopes to exactly this list
                                                 (default: what realm.json declares for it) and sign out its
                                                 sessions, so a token with the old scopes ends. Nothing to do when
                                                 the client does not exist yet, as on a new install.
    apply_realm.py admin-client on | ekka api connect desks --stdin ...
                                                 the backend adapter's desk-admin client (ADK-E3): its secret, into
                                                 a pipe only. `admin-client off` disables it.
    apply_realm.py first-admin <username> <email>
                                                 the customer's first administrator; the password is typed with
                                                 no echo and is temporary, so the first sign-in changes it
    apply_realm.py person <username> <email>     one person who uses desks (up.sh invite); a person revoked
                                                 before is enabled again, with a new one-time password
    apply_realm.py revoke <username>             that person taken off (up.sh revoke): sign-in disabled, every
                                                 session ended. Never an administrator; never a name not here

Environment: KC_URL (the admin listener, default https://127.0.0.1:8443), KC_CA (CA file for KC_URL),
KC_ADMIN_USER and KC_ADMIN_PASSWORD_FILE (the bootstrap admin, written by setup, mode 0600).
Standard library only. Prints names and differences, never a secret, except enclave-secret into a pipe.
"""
import getpass
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.join(HERE, "realm.json")
CLIENT_FIELDS = ("name", "publicClient", "standardFlowEnabled", "implicitFlowEnabled",
                 "directAccessGrantsEnabled", "serviceAccountsEnabled", "redirectUris", "webOrigins")
AUDIENCE_MAPPER = "desks-audience"
# Every OpenShell scope a client could hold (openshell-0.1.4 proto annotations, the same set as 0.1.3, plus openshell:all). A client that
# holds one realm.json does not declare is a difference `apply` refuses, and `scopes` removes.
OPENSHELL_SCOPES = {"openshell:all", "sandbox:read", "sandbox:write", "provider:read", "provider:write",
                    "config:read", "config:write", "workspace:read", "workspace:write"}


class Api:
    def __init__(self):
        self.base = os.environ.get("KC_URL", "https://127.0.0.1:8443").rstrip("/")
        ca = os.environ.get("KC_CA")
        self.ctx = ssl.create_default_context(cafile=ca) if ca else ssl.create_default_context()
        with open(os.environ["KC_ADMIN_PASSWORD_FILE"]) as f:
            password = f.read().strip()
        form = urllib.parse.urlencode({"grant_type": "password", "client_id": "admin-cli",
                                       "username": os.environ.get("KC_ADMIN_USER", "admin"),
                                       "password": password}).encode()
        tok = self._send("POST", "/realms/master/protocol/openid-connect/token", form,
                         {"Content-Type": "application/x-www-form-urlencoded"})
        self.headers = {"Authorization": "Bearer " + tok["access_token"], "Content-Type": "application/json"}

    def _send(self, method, path, data=None, headers=None):
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=30) as r:
                body = r.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise SystemExit(f"keycloak: {method} {path}: HTTP {e.code}")

    def get(self, path):
        return self._send("GET", path, headers=self.headers)

    def write(self, method, path, body):
        return self._send(method, path, json.dumps(body).encode(), self.headers)


def realm_path(spec, rest=""):
    return f"/admin/realms/{spec['realm']['realm']}{rest}"


def find_client(api, spec, client_id):
    found = api.get(realm_path(spec, "/clients?clientId=" + urllib.parse.quote(client_id))) or []
    return found[0] if found else None


def plan(api, spec):
    """(creates, differences): what is absent, and every declared value that differs from the live one."""
    creates, diffs = [], []
    realm = spec["realm"]
    live = api.get(realm_path(spec))
    if live is None:
        creates.append(("realm", realm["realm"]))
        return creates + [("everything else", "after the realm")], diffs
    for k, want in realm.items():
        if live.get(k) != want:
            diffs.append(f"realm.{k}: live {live.get(k)!r}, want {want!r}")
    for role in spec["roles"]:
        if api.get(realm_path(spec, "/roles/" + urllib.parse.quote(role["name"], safe=""))) is None:
            creates.append(("role", role["name"]))
    scopes = {s["name"]: s for s in api.get(realm_path(spec, "/client-scopes")) or []}
    for s in spec["clientScopes"]:
        if s["name"] not in scopes:
            creates.append(("client scope", s["name"]))
        elif scopes[s["name"]].get("attributes", {}).get("include.in.token.scope") != "true":
            diffs.append(f"clientScopes.{s['name']}: not emitted into the token's scope claim")
    for c in spec["clients"]:
        live_c = find_client(api, spec, c["clientId"])
        if live_c is None:
            creates.append(("client", c["clientId"]))
            continue
        for k in CLIENT_FIELDS:
            if live_c.get(k) != c[k]:
                diffs.append(f"clients.{c['clientId']}.{k}: live {live_c.get(k)!r}, want {c[k]!r}")
        for k, want in c["attributes"].items():
            got = live_c.get("attributes", {}).get(k)
            if got != want:
                diffs.append(f"clients.{c['clientId']}.attributes.{k}: live {got!r}, want {want!r}")
        mappers = {m["name"]: m for m in live_c.get("protocolMappers", [])}
        aud = mappers.get(AUDIENCE_MAPPER, {}).get("config", {}).get("included.custom.audience")
        if aud != c["audience"]:
            diffs.append(f"clients.{c['clientId']}.audience: live {aud!r}, want {c['audience']!r}")
        have = {s["name"] for s in api.get(realm_path(spec, f"/clients/{live_c['id']}/default-client-scopes")) or []}
        for s in c["defaultScopes"]:
            if s not in have:
                diffs.append(f"clients.{c['clientId']}.defaultScopes: {s!r} is missing")
        extra = have & OPENSHELL_SCOPES - set(c["defaultScopes"])
        for s in sorted(extra):
            diffs.append(f"clients.{c['clientId']}.defaultScopes: {s!r} is present and not declared")
    return creates, diffs


def create(api, spec):
    if api.get(realm_path(spec)) is None:
        api.write("POST", "/admin/realms", spec["realm"])
    for role in spec["roles"]:
        if api.get(realm_path(spec, "/roles/" + urllib.parse.quote(role["name"], safe=""))) is None:
            api.write("POST", realm_path(spec, "/roles"), role)
    scopes = {s["name"]: s for s in api.get(realm_path(spec, "/client-scopes")) or []}
    for s in spec["clientScopes"]:
        if s["name"] not in scopes:
            api.write("POST", realm_path(spec, "/client-scopes"), {
                "name": s["name"], "description": s["description"], "protocol": "openid-connect",
                "attributes": {"include.in.token.scope": "true", "display.on.consent.screen": "false"}})
    scopes = {s["name"]: s["id"] for s in api.get(realm_path(spec, "/client-scopes")) or []}
    for c in spec["clients"]:
        if find_client(api, spec, c["clientId"]) is not None:
            continue
        body = {k: c[k] for k in ("clientId",) + CLIENT_FIELDS}
        body.update({"protocol": "openid-connect", "attributes": c["attributes"], "protocolMappers": [{
            "name": AUDIENCE_MAPPER, "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
            "config": {"included.custom.audience": c["audience"], "access.token.claim": "true",
                       "id.token.claim": "false"}}]})
        api.write("POST", realm_path(spec, "/clients"), body)
        cid = find_client(api, spec, c["clientId"])["id"]
        for s in c["defaultScopes"]:
            api.write("PUT", realm_path(spec, f"/clients/{cid}/default-client-scopes/{scopes[s]}"), {})
        if c.get("serviceAccountRoles"):
            user = api.get(realm_path(spec, f"/clients/{cid}/service-account-user"))
            roles = [api.get(realm_path(spec, "/roles/" + urllib.parse.quote(r, safe="")))
                     for r in c["serviceAccountRoles"]]
            api.write("POST", realm_path(spec, f"/users/{user['id']}/role-mappings/realm"), roles)


# The backend adapter's client (ADK-E3): exactly the desk administration it performs, NOT openshell:all, and the
# openshell-admin role the gateway asks of an administrator. Not in realm.json: up.sh makes it when an install turns
# desk administration on, so `apply` never sees it on an install that did not.
ADMIN_CLIENT = "ekka-desk-admin"
# ONLY the scopes the adapter's calls need, read from OpenShell 0.1.4's own annotations (regression review):
# GetSandbox and GetSandboxPolicyStatus sandbox:read; Create, Delete, Stop, Start and DetachSandboxProvider
# sandbox:write; GetProvider, GetProviderProfile, ListProviderProfiles provider:read; UpdateConfig config:write.
# Never provider:write: a stolen adapter credential must not create, delete or rotate a provider.
ADMIN_SCOPES = ["config:write", "provider:read", "sandbox:read", "sandbox:write"]
SCOPE_DESCRIPTIONS = {
    "sandbox:write": "Create, stop, start and remove sandboxes, and detach a provider (OpenShell sandbox:write).",
    "config:write": "Change desk configuration and policy (OpenShell config:write).",
}


# The client attribute that records a sign-out still owed after a scope change (see set_scopes).
SIGN_OUT_OWED = "ekka.sign-out-owed"


def set_scopes(api, spec, client_id, want):
    """Make the live client's OpenShell default scopes exactly `want`, then end its sessions. Returns what changed."""
    unknown = sorted(set(want) - OPENSHELL_SCOPES)
    if unknown:
        raise SystemExit("keycloak: not an OpenShell scope: " + ", ".join(unknown))
    if api.get(realm_path(spec)) is None:
        return None
    c = find_client(api, spec, client_id)
    if c is None:
        return None
    scopes = {s["name"]: s["id"] for s in api.get(realm_path(spec, "/client-scopes")) or []}
    # A live install has not met a scope realm.json added since (apply would refuse the old client first), so
    # create it here exactly as apply would: emitted into the token's scope claim.
    declared = {s["name"]: s["description"] for s in spec["clientScopes"]}
    declared.update({k: v for k, v in SCOPE_DESCRIPTIONS.items() if client_id == ADMIN_CLIENT and k not in declared})
    for name in sorted(set(want) - set(scopes)):
        if name not in declared:
            raise SystemExit(f"keycloak: realm.json declares no client scope {name}")
        api.write("POST", realm_path(spec, "/client-scopes"), {
            "name": name, "description": declared[name], "protocol": "openid-connect",
            "attributes": {"include.in.token.scope": "true", "display.on.consent.screen": "false"}})
    scopes = {s["name"]: s["id"] for s in api.get(realm_path(spec, "/client-scopes")) or []}
    base = realm_path(spec, f"/clients/{c['id']}/default-client-scopes")
    have = {s["name"] for s in api.get(base) or []}
    added = sorted(set(want) - have)
    removed = sorted(have & OPENSHELL_SCOPES - set(want))
    # RESUMABLE (Codex, ADK-E2): the sign-out is owed from BEFORE the first scope write until zero sessions are
    # left, and the debt is written into the client itself. A run that stops anywhere in between (a scope written,
    # half the sessions ended) leaves the marker, and the next run, which sees no scope to change, still ends every
    # session and checks that none remain. A run that owes nothing signs nobody out: up.sh runs this every time.
    owed = (c.get("attributes") or {}).get(SIGN_OUT_OWED) == "pending"
    resumed = owed
    if (added or removed) and not owed:
        set_sign_out_owed(api, spec, c, "pending")
        owed = True
    for name in added:
        api.write("PUT", f"{base}/{scopes[name]}", {})
    for name in removed:
        api.write("DELETE", f"{base}/{scopes[name]}", None)
    ended = 0
    if owed:
        ended = end_every_session(api, spec, c, client_id)
        set_sign_out_owed(api, spec, c, "")
    before = sorted(have & OPENSHELL_SCOPES)
    return {"added": added, "removed": removed, "sessions_ended": ended, "resumed": resumed, "before": before,
            "after": sorted(want)}


def set_sign_out_owed(api, spec, c, value):
    """Write the client's sign-out debt: "pending" while sessions may still hold old scopes, "" once none do.

    That attribute is sent, and nothing read from Keycloak, so no stale copy of anything goes back, such as the
    default scopes read before they were changed. Keycloak keeps most fields an update leaves out, but NOT
    `webOrigins`: an update without it, on a client whose web origins are empty, fills them in from the redirect
    URIs (Keycloak 26.7.4, RepresentationToModel.updateClient and defaultWebOrigins). ekka-cli declares none and
    redirects to http://127.0.0.1/*, so the marker write alone gave it `http://127.0.0.1`, and `apply`, the
    documented realm check, then refused the realm (measured on a test box, 2026-10-24). So the declared web
    origins go with it, from realm.json, never from the live client."""
    declared = next((d for d in spec["clients"] if d["clientId"] == c["clientId"]), {})
    api.write("PUT", realm_path(spec, f"/clients/{c['id']}"),
              {"clientId": c["clientId"], "webOrigins": declared.get("webOrigins", []),
               "attributes": {SIGN_OUT_OWED: value}})
    c.setdefault("attributes", {})[SIGN_OUT_OWED] = value


def end_every_session(api, spec, c, client_id):
    """End every session of the client, online and offline, then list again: done only when none are left.

    A token keeps the scopes it was issued with until it expires, so every session ends. Its OFFLINE sessions too:
    `ekka desk login` asks for offline_access by default, so a person's normal sign-in is an offline session, and
    Keycloak keeps those apart and ends one only when the DELETE says isOffline=true.
    Returns how many were ended."""
    ended = 0
    for _ in range(3):
        online = api.get(realm_path(spec, f"/clients/{c['id']}/user-sessions?max=10000")) or []
        offline = api.get(realm_path(spec, f"/clients/{c['id']}/offline-sessions?max=10000")) or []
        if not online and not offline:
            return ended
        for session in online:
            api.write("DELETE", realm_path(spec, f"/sessions/{session['id']}"), None)
            ended += 1
        for session in offline:
            api.write("DELETE", realm_path(spec, f"/sessions/{session['id']}?isOffline=true"), None)
            ended += 1
    raise SystemExit(f"keycloak: {client_id} still has sessions after ending them three times; nothing is "
                     "finished, and the next run ends them again")


def find_person(api, spec, username):
    found = api.get(realm_path(spec, "/users?exact=true&username=" + urllib.parse.quote(username))) or []
    return found[0] if found else None


def set_enabled(api, spec, user, enabled):
    """The person's whole record as Keycloak holds it, sent back with only `enabled` changed. Keycloak 26 updates a
    user through its user profile, which may drop an attribute the update leaves out, such as the email."""
    record = api.get(realm_path(spec, f"/users/{user['id']}"))
    if record is None:
        raise SystemExit(f"keycloak: {user['username']} is gone; nothing was changed")
    record["enabled"] = enabled
    api.write("PUT", realm_path(spec, f"/users/{user['id']}"), record)


def end_person_sessions(api, spec, user):
    """End every session of one person, online and offline on every client, then list again: done only when none
    are left. A disabled person cannot refresh, but an access token lives until it expires, and an offline session
    is ended only by a DELETE that says isOffline=true (as end_every_session). Returns how many were ended."""
    clients = api.get(realm_path(spec, "/clients?max=1000")) or []
    ended = 0
    for _ in range(3):
        online = api.get(realm_path(spec, f"/users/{user['id']}/sessions")) or []
        offline = [s for c in clients
                   for s in api.get(realm_path(spec, f"/users/{user['id']}/offline-sessions/{c['id']}")) or []]
        if not online and not offline:
            return ended
        for session in online:
            api.write("DELETE", realm_path(spec, f"/sessions/{session['id']}"), None)
            ended += 1
        for session in offline:
            api.write("DELETE", realm_path(spec, f"/sessions/{session['id']}?isOffline=true"), None)
            ended += 1
    raise SystemExit(f"keycloak: {user['username']} still has sessions after ending them three times; their sign-in "
                     "is disabled, and the next revoke ends them again")


def main(argv):
    with open(SPEC) as f:
        spec = json.load(f)
    cmd = argv[0] if argv else ""
    if cmd == "apply":
        api = Api()
        creates, diffs = plan(api, spec)
        if diffs:
            print("keycloak: REFUSED. The live realm differs from deploy/keycloak/realm.json; nothing was changed:")
            print("\n".join("  " + d for d in diffs))
            return 2
        if not creates:
            print("keycloak: the realm matches deploy/keycloak/realm.json; nothing to do")
            return 0
        create(api, spec)
        print("keycloak: created " + ", ".join(f"{kind} {name}" for kind, name in creates))
        creates, diffs = plan(api, spec)
        if creates or diffs:
            print("keycloak: after creating, the realm still differs:\n" + "\n".join("  " + d for d in diffs))
            return 2
        return 0
    if cmd == "enclave-secret":
        if sys.stdout.isatty():
            print("keycloak: refusing to print a secret to a terminal; pipe it into `ekka secret put <NAME> --stdin`",
                  file=sys.stderr)
            return 2
        api = Api()
        c = find_client(api, spec, "ekka-enclave")
        if c is None:
            raise SystemExit("keycloak: the ekka-enclave client does not exist; run apply first")
        sys.stdout.write(api.get(realm_path(spec, f"/clients/{c['id']}/client-secret"))["value"])
        return 0
    if cmd == "setup-client" and len(argv) == 2 and argv[1] in ("on", "off"):
        # up.sh's own administrator for its one-time gateway steps: a confidential client whose service account holds
        # openshell-admin. `on` enables it and writes its secret into a pipe; `off` disables it. Not in realm.json on
        # purpose: it exists only while up.sh needs it, and stays disabled otherwise.
        api = Api()
        c = find_client(api, spec, "desks-setup")
        if c is None:
            if argv[1] == "off":
                return 0
            scopes = {s["name"]: s["id"] for s in api.get(realm_path(spec, "/client-scopes")) or []}
            api.write("POST", realm_path(spec, "/clients"), {
                "clientId": "desks-setup", "name": "up.sh: the gateway's one-time setup", "enabled": True,
                "publicClient": False, "serviceAccountsEnabled": True, "standardFlowEnabled": False,
                "implicitFlowEnabled": False, "directAccessGrantsEnabled": False, "protocol": "openid-connect",
                "protocolMappers": [{"name": AUDIENCE_MAPPER, "protocol": "openid-connect",
                                     "protocolMapper": "oidc-audience-mapper",
                                     "config": {"included.custom.audience": "ekka-desks",
                                                "access.token.claim": "true", "id.token.claim": "false"}}]})
            c = find_client(api, spec, "desks-setup")
            if "openshell:all" in scopes:
                api.write("PUT", realm_path(spec, f"/clients/{c['id']}/default-client-scopes/{scopes['openshell:all']}"), {})
            user = api.get(realm_path(spec, f"/clients/{c['id']}/service-account-user"))
            api.write("POST", realm_path(spec, f"/users/{user['id']}/role-mappings/realm"),
                      [api.get(realm_path(spec, "/roles/openshell-admin"))])
        c["enabled"] = argv[1] == "on"
        api.write("PUT", realm_path(spec, f"/clients/{c['id']}"), c)
        if argv[1] == "on":
            if sys.stdout.isatty():
                print("keycloak: refusing to print a secret to a terminal", file=sys.stderr)
                return 2
            sys.stdout.write(api.get(realm_path(spec, f"/clients/{c['id']}/client-secret"))["value"])
        else:
            print("keycloak: desks-setup disabled")
        return 0
    if cmd == "scopes" and len(argv) in (2, 3):
        client_id = argv[1]
        if len(argv) == 3:
            want = [x for x in argv[2].split(",") if x]
        else:
            declared = [c for c in spec["clients"] if c["clientId"] == client_id]
            if not declared:
                raise SystemExit(f"keycloak: realm.json declares no client {client_id}; name its scopes")
            want = declared[0]["defaultScopes"]
        done = set_scopes(Api(), spec, client_id, want)
        # ONE LINE, BEFORE AND AFTER: the upgrade's done-signal on the box (ADK-E2, the release-day runbook).
        if done is None:
            print(f"keycloak: {client_id} does not exist yet; nothing to change")
        elif not done["added"] and not done["removed"] and not done["resumed"]:
            print(f"keycloak: {client_id} default scopes before {' '.join(done['before'])}, "
                  f"after {' '.join(done['after'])} (unchanged)")
        else:
            print(f"keycloak: {client_id} default scopes before {' '.join(done['before'])}, "
                  f"after {' '.join(done['after'])}; {done['sessions_ended']} sessions signed out"
                  + (", finishing a run that stopped before its sign-out" if done["resumed"] else ""))
        return 0
    if cmd == "admin-client" and len(argv) == 2 and argv[1] in ("on", "off"):
        if argv[1] == "on" and sys.stdout.isatty():
            print("keycloak: refusing to print a secret to a terminal; pipe it into `ekka api connect desks --stdin`",
                  file=sys.stderr)
            return 2
        api = Api()
        c = find_client(api, spec, ADMIN_CLIENT)
        if c is None:
            if argv[1] == "off":
                return 0
            api.write("POST", realm_path(spec, "/clients"), {
                "clientId": ADMIN_CLIENT, "name": "The backend adapter: desk administration through EKKA",
                "enabled": True, "publicClient": False, "serviceAccountsEnabled": True,
                "standardFlowEnabled": False, "implicitFlowEnabled": False, "directAccessGrantsEnabled": False,
                "protocol": "openid-connect",
                "protocolMappers": [{"name": AUDIENCE_MAPPER, "protocol": "openid-connect",
                                     "protocolMapper": "oidc-audience-mapper",
                                     "config": {"included.custom.audience": "ekka-desks",
                                                "access.token.claim": "true", "id.token.claim": "false"}}]})
            c = find_client(api, spec, ADMIN_CLIENT)
            user = api.get(realm_path(spec, f"/clients/{c['id']}/service-account-user"))
            api.write("POST", realm_path(spec, f"/users/{user['id']}/role-mappings/realm"),
                      [api.get(realm_path(spec, "/roles/openshell-admin"))])
        if argv[1] == "on":
            set_scopes(api, spec, ADMIN_CLIENT, ADMIN_SCOPES)
            c = find_client(api, spec, ADMIN_CLIENT)   # read again: set_scopes cleared its sign-out marker
        c["enabled"] = argv[1] == "on"
        api.write("PUT", realm_path(spec, f"/clients/{c['id']}"), c)
        if argv[1] == "on":
            sys.stdout.write(api.get(realm_path(spec, f"/clients/{c['id']}/client-secret"))["value"])
        else:
            print(f"keycloak: {ADMIN_CLIENT} disabled")
        return 0
    if cmd == "first-admin" and len(argv) == 3:
        username, email = argv[1], argv[2]
        api = Api()
        role = api.get(realm_path(spec, "/roles/openshell-admin"))
        if api.get(realm_path(spec, "/roles/openshell-admin/users")):
            raise SystemExit("keycloak: an administrator already exists; add more in the admin console")
        # up.sh generates a one-time password into a 0600 file when no terminal is attached; else it is typed.
        pf = os.environ.get("KC_FIRST_ADMIN_PASSWORD_FILE")
        password = open(pf).read().strip() if pf else getpass.getpass(f"Initial password for {username} (not shown; changed at first sign-in): ")
        if len(password) < 12:
            raise SystemExit("keycloak: the password must be at least 12 characters")
        api.write("POST", realm_path(spec, "/users"), {
            "username": username, "email": email, "enabled": True, "emailVerified": True,
            "credentials": [{"type": "password", "value": password, "temporary": True}]})
        user = api.get(realm_path(spec, "/users?exact=true&username=" + urllib.parse.quote(username)))[0]
        api.write("POST", realm_path(spec, f"/users/{user['id']}/role-mappings/realm"), [role])
        print(f"keycloak: administrator {username} created; the first sign-in sets a new password")
        return 0
    if cmd == "person" and len(argv) == 3:
        # One person who uses desks: the openshell-user role, a one-time password from a 0600 file that
        # up.sh writes, changed at first sign-in. Prints the person's id, which the gateway's workspace uses.
        username, email = argv[1], argv[2]
        api = Api()
        password = open(os.environ["KC_PERSON_PASSWORD_FILE"]).read().strip()
        if len(password) < 12:
            raise SystemExit("keycloak: the password must be at least 12 characters")
        user = find_person(api, spec, username)
        if user is not None and user.get("enabled", True):
            raise SystemExit(f"keycloak: {username} already exists")
        if user is not None:
            # Revoked before (`revoke`): enabled again, under the same id, with a new one-time password.
            set_enabled(api, spec, user, True)
            api.write("PUT", realm_path(spec, f"/users/{user['id']}/reset-password"),
                      {"type": "password", "value": password, "temporary": True})
            print(user["id"])
            return 0
        api.write("POST", realm_path(spec, "/users"), {
            "username": username, "email": email, "enabled": True, "emailVerified": True,
            "credentials": [{"type": "password", "value": password, "temporary": True}]})
        user = api.get(realm_path(spec, "/users?exact=true&username=" + urllib.parse.quote(username)))[0]
        api.write("POST", realm_path(spec, f"/users/{user['id']}/role-mappings/realm"),
                  [api.get(realm_path(spec, "/roles/openshell-user"))])
        print(user["id"])
        return 0
    if cmd == "revoke" and len(argv) == 2:
        # One person taken off (up.sh revoke): their sign-in disabled first, so no new session starts, then every
        # session ended. Prints the person's id, which up.sh removes from the gateway's workspace. Running it again
        # changes nothing. An administrator is never revoked here, so the last one cannot be locked out by a typo.
        username = argv[1]
        api = Api()
        user = find_person(api, spec, username)
        if user is None:
            raise SystemExit(f"keycloak: there is no person {username}; nothing was changed")
        admins = api.get(realm_path(spec, "/roles/openshell-admin/users?max=10000")) or []
        if any(a["id"] == user["id"] for a in admins):
            raise SystemExit(f"keycloak: {username} is an administrator; nothing was changed. An administrator is "
                             "removed in the Keycloak admin console, after another one exists")
        if user.get("enabled", True):
            set_enabled(api, spec, user, False)
        end_person_sessions(api, spec, user)
        print(user["id"])
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
