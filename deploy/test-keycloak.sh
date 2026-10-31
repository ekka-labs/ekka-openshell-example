# deploy/test-keycloak.sh: Keycloak + Postgres + edge on a scratch compose project, realm applied, every promise
# of this setup's identity setup checked, then torn down. Needs docker and certs.sh beside compose.yml.
# Run: W=$(mktemp -d); cp -r deploy/* $W/; cp deploy/certs.sh $W/; W=$W bash $W/test-keycloak.sh
set -uo pipefail
cd "$W"
export DESKS_STATE=$W/state DESKS_NAME=desks.example.test DESKS_EDGE_BIND=127.0.0.1
P="docker compose -p ekka-desks-scratch -f compose.yml"
ok() { echo "PASS  $*"; }; bad() { echo "FAIL  $*"; }
ss -ltn | grep -qE ":(443|8443) " && { echo "port 443 or 8443 busy; stopping"; exit 1; }

echo "== certificates and secrets (as setup will write them)"
bash certs.sh init desks.example.test >/dev/null || { echo "certs failed"; exit 1; }
docker run --rm -v $DESKS_STATE/tls/edge:/e alpine:3.20 chown -R 101:101 /e   # nginx-unprivileged is uid 101
mkdir -p $DESKS_STATE/secrets && chmod 700 $DESKS_STATE/secrets
DBP=$(openssl rand -hex 24); ADP=$(openssl rand -hex 24)
( umask 077
  printf 'POSTGRES_PASSWORD=%s\n' "$DBP" > $DESKS_STATE/secrets/keycloak-db.env
  printf 'KC_DB_PASSWORD=%s\nKC_BOOTSTRAP_ADMIN_USERNAME=desks-bootstrap\nKC_BOOTSTRAP_ADMIN_PASSWORD=%s\n' "$DBP" "$ADP" > $DESKS_STATE/secrets/keycloak.env
  printf '%s' "$ADP" > $DESKS_STATE/secrets/keycloak-admin-password )
unset DBP ADP

echo "== up (start order by health)"
$P config -q && ok "compose file valid" || bad "compose file"
$P up -d >/dev/null 2>&1
for i in $(seq 1 60); do curl -sf -o /dev/null --cacert $DESKS_STATE/tls/ca.crt https://127.0.0.1:8443/realms/master 2>/dev/null && break; s=$($P ps --format '{{.Service}}={{.Health}}' | tr '\n' ' '); [[ $s == *"keycloak=healthy"* ]] && $P ps --format '{{.Service}}' | grep -q edge && break; sleep 5; done
$P ps --format '{{.Service}} {{.State}} {{.Health}}'
[[ $($P ps --format '{{.Service}}={{.Health}}') == *keycloak=healthy* ]] && ok "keycloak healthy through its own /dev/tcp healthcheck" || bad "keycloak not healthy"
sleep 3

export KC_URL=https://127.0.0.1:8443 KC_CA=$DESKS_STATE/tls/ca.crt KC_ADMIN_USER=desks-bootstrap KC_ADMIN_PASSWORD_FILE=$DESKS_STATE/secrets/keycloak-admin-password
echo "== apply the realm"
python3 keycloak/apply_realm.py apply
python3 keycloak/apply_realm.py apply | grep -q "nothing to do" && ok "second apply: nothing to do" || bad "second apply"

echo "== a present value that differs is refused by name, and nothing is changed"
python3 - <<'PY'
import sys; sys.path.insert(0, "keycloak"); import apply_realm as a, json
api=a.Api(); spec=json.load(open(a.SPEC)); c=a.find_client(api,spec,"ekka-cli"); c["directAccessGrantsEnabled"]=True
api.write("PUT", a.realm_path(spec, f"/clients/{c['id']}"), c)
PY
out=$(python3 keycloak/apply_realm.py apply); rc=$?
[[ $rc == 2 && $out == *"clients.ekka-cli.directAccessGrantsEnabled: live True, want False"* ]] && ok "refused, naming clients.ekka-cli.directAccessGrantsEnabled" || { bad "tamper not refused (rc=$rc)"; echo "$out"; }
python3 - <<'PY'
import sys; sys.path.insert(0, "keycloak"); import apply_realm as a, json
api=a.Api(); spec=json.load(open(a.SPEC)); c=a.find_client(api,spec,"ekka-cli")
print("PASS  the refused run changed nothing" if c["directAccessGrantsEnabled"] else "FAIL  the refused run changed it")
c["directAccessGrantsEnabled"]=False; api.write("PUT", a.realm_path(spec, f"/clients/{c['id']}"), c)
PY

echo "== the public listener exposes the desks realm only"
pub() { curl -s -o /dev/null -w '%{http_code}' --cacert $KC_CA --resolve desks.example.test:443:127.0.0.1 "https://desks.example.test$1"; }
iss=$(curl -s --cacert $KC_CA --resolve desks.example.test:443:127.0.0.1 https://desks.example.test/realms/ekka-desks/.well-known/openid-configuration | python3 -c 'import json,sys; print(json.load(sys.stdin)["issuer"])')
[[ $iss == https://desks.example.test/realms/ekka-desks ]] && ok "discovery issuer = $iss (TLS verified against the desks CA)" || bad "issuer $iss"
for p in /realms/ekka-desks/clients-registrations/openid-connect /realms/ekka-desks/clients-registrations/default /admin/ /admin/master/console/ /realms/master/ /realms/master/.well-known/openid-configuration /metrics /health /health/ready /; do
  c=$(pub $p); [[ $c == 404 ]] && ok "public $p -> 404" || bad "public $p -> $c"; done
c=$(curl -s -o /dev/null -w '%{http_code}' --cacert $KC_CA https://127.0.0.1:8443/admin/master/console/); [[ $c == 200 ]] && ok "admin console on 127.0.0.1:8443 -> 200" || bad "admin listener -> $c"
ss -ltn | grep -E ":(443|8443) " | awk '{print "      listening " $4}'

echo "== the Enclave's client: client credentials only, its roles and scopes"
SEC_TOK=$(python3 keycloak/apply_realm.py enclave-secret | python3 -c '
import sys,urllib.request,urllib.parse,ssl,os,json
sec=sys.stdin.read(); ctx=ssl.create_default_context(cafile=os.environ["KC_CA"])
req=urllib.request.Request("https://127.0.0.1:8443/realms/ekka-desks/protocol/openid-connect/token",
  data=urllib.parse.urlencode({"grant_type":"client_credentials","client_id":"ekka-enclave","client_secret":sec}).encode())
print(json.load(urllib.request.urlopen(req,context=ctx))["access_token"])')
echo "$SEC_TOK" | python3 -c '
import sys,json,base64
p=sys.stdin.read().split(".")[1]; c=json.loads(base64.urlsafe_b64decode(p+"="*(-len(p)%4)))
aud=c["aud"] if isinstance(c["aud"],list) else [c["aud"]]
print(("PASS" if "ekka-desks" in aud else "FAIL")+"  enclave token aud includes ekka-desks:", aud)
sc=set(c.get("scope","").split()); bad=sc & {"openshell:all","provider:write","config:write"}
print(("PASS" if {"sandbox:read","sandbox:write","provider:read"}<=sc and not bad else "FAIL")+"  enclave token scopes:", sorted(sc))
r=c.get("realm_access",{}).get("roles",[])
print(("PASS" if "openshell-user" in r and "openshell-admin" not in r else "FAIL")+"  enclave roles:", sorted(r))'
unset SEC_TOK

echo "== the people client: no password grant, device code available, PKCE required"
c=$(curl -s --cacert $KC_CA --resolve desks.example.test:443:127.0.0.1 -d grant_type=password -d client_id=ekka-cli -d username=x -d password=y https://desks.example.test/realms/ekka-desks/protocol/openid-connect/token | python3 -c 'import json,sys; print(json.load(sys.stdin).get("error"))')
[[ $c == unauthorized_client ]] && ok "ekka-cli password grant refused ($c)" || bad "ekka-cli password grant -> $c"
uc=$(curl -s --cacert $KC_CA --resolve desks.example.test:443:127.0.0.1 -d client_id=ekka-cli -d code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM -d code_challenge_method=S256 https://desks.example.test/realms/ekka-desks/protocol/openid-connect/auth/device | python3 -c 'import json,sys; d=json.load(sys.stdin); print("user_code" in d, d.get("verification_uri",""))')
[[ $uc == True* ]] && ok "ekka-cli device code issued with PKCE; verification_uri ${uc#True }" || bad "device code: $uc"
c=$(curl -s -o /dev/null -w '%{redirect_url}' --cacert $KC_CA --resolve desks.example.test:443:127.0.0.1 "https://desks.example.test/realms/ekka-desks/protocol/openid-connect/auth?client_id=ekka-cli&response_type=code&redirect_uri=http://127.0.0.1:5555/cb&scope=openid")
[[ $c == *error=invalid_request*code_challenge_method* ]] && ok "authorization request without PKCE refused (redirect error=invalid_request, missing code_challenge_method)" || bad "no-PKCE request -> $c"

echo "== ekka-cli's loopback redirect on a RANDOM port (the CLI picks a free one), and a foreign redirect refused"
CH="code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM&code_challenge_method=S256"
for port in $(shuf -i 20000-60000 -n 2); do
  c=$(curl -s -o /dev/null -w '%{http_code}' --cacert $KC_CA --resolve desks.example.test:443:127.0.0.1 "https://desks.example.test/realms/ekka-desks/protocol/openid-connect/auth?client_id=ekka-cli&response_type=code&redirect_uri=http://127.0.0.1:$port/callback&scope=openid&$CH")
  [[ $c == 200 ]] && ok "loopback redirect on port $port accepted (login page, HTTP $c)" || bad "loopback redirect on port $port -> HTTP $c"
done
for r in http://evil.example/cb http://localhost:4444/cb https://127.0.0.1:4444/cb; do
  c=$(curl -s -o /dev/null -w '%{http_code}' --cacert $KC_CA --resolve desks.example.test:443:127.0.0.1 "https://desks.example.test/realms/ekka-desks/protocol/openid-connect/auth?client_id=ekka-cli&response_type=code&redirect_uri=$r&scope=openid&$CH")
  [[ $c == 400 ]] && ok "redirect $r refused (HTTP $c)" || bad "redirect $r -> HTTP $c"
done
s=$(curl -s --cacert $KC_CA --resolve desks.example.test:443:127.0.0.1 https://desks.example.test/realms/ekka-desks/.well-known/openid-configuration >/dev/null; python3 -c '
import sys,json; sys.path.insert(0,"keycloak"); import apply_realm as a
api=a.Api(); spec=json.load(open(a.SPEC)); print(api.get(a.realm_path(spec))["sslRequired"])')
[[ $s == all ]] && ok "realm sslRequired = all, and sign-in through the edge still works" || bad "sslRequired = $s"

echo "== first administrator: password typed with no echo, temporary"
printf 'a-very-temporary-pass1\n' | setsid -w python3 keycloak/apply_realm.py first-admin desks-admin admin@example.com 2>/dev/null | tail -1   # no terminal: getpass falls back to stdin, for the test only
python3 - <<'PY'
import sys; sys.path.insert(0, "keycloak"); import apply_realm as a, json
api=a.Api(); spec=json.load(open(a.SPEC))
u=api.get(a.realm_path(spec, "/users?exact=true&username=desks-admin"))[0]
roles=[r["name"] for r in api.get(a.realm_path(spec, f"/users/{u['id']}/role-mappings/realm"))]
creds=api.get(a.realm_path(spec, f"/users/{u['id']}/credentials"))
print(("PASS" if "openshell-admin" in roles else "FAIL")+"  first admin roles:", sorted(roles))
print(("PASS" if "UPDATE_PASSWORD" in u.get("requiredActions",[]) else "FAIL")+"  first sign-in must change the password:", u.get("requiredActions"))
PY
python3 keycloak/apply_realm.py first-admin second x@y 2>&1 </dev/null | tail -1

echo "== restart: data survives"
$P restart >/dev/null 2>&1; for i in $(seq 1 60); do [[ $($P ps --format '{{.Service}}={{.Health}}') == *keycloak=healthy* ]] && curl -sf -o /dev/null --cacert $KC_CA https://127.0.0.1:8443/realms/master && break; sleep 5; done
$P ps --format '{{.Service}} {{.State}} {{.Health}}'
python3 keycloak/apply_realm.py apply | grep -q "nothing to do" && ok "after restart the realm is intact" || bad "realm after restart"

echo "== teardown"
$P down -v >/dev/null 2>&1; docker run --rm -v $W:/w alpine:3.20 rm -rf /w/state; echo "containers left: $(docker ps -aq --filter label=com.docker.compose.project=ekka-desks-scratch | wc -l)"
