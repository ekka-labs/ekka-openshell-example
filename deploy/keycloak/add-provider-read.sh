# Give the Enclave's sign-in client the `provider:read` scope on a setup that already runs.
#
#   sudo ./deploy/setup.sh compose exec -T keycloak bash -s < deploy/keycloak/add-provider-read.sh
#
# EKKA's providers pin reads each desk's providers (GetProvider, GetProviderProfile), which needs
# provider:read; OpenShell answers credential names with it, never values. A new setup gets the scope from
# realm.json. This adds it to a running one, exactly as realm.json declares it: a client scope included in the
# token's scope claim (apply_realm.py checks that attribute), and a Default scope of the ekka-enclave client
# only, never of the realm. It runs inside the Keycloak container with that container's own admin login; the
# password stays in the environment, never on a command line. Running it again changes nothing.
# Measured on a staging install, 2026-10-13: then `apply_realm.py apply` says the realm matches realm.json.
set -euo pipefail
# The CLI's config file holds an admin access and refresh token: owner-only, and removed however this ends.
umask 077
trap 'rm -f /tmp/kcadm.config' EXIT
K=/opt/keycloak/bin/kcadm.sh; C="--config /tmp/kcadm.config"
A='attributes={"include.in.token.scope":"true","display.on.consent.screen":"false"}'
# The admin password stays in the environment, never on a command line.
KC_CLI_PASSWORD="$KC_BOOTSTRAP_ADMIN_PASSWORD" $K config credentials --server http://localhost:8080 \
  --realm master --user "$KC_BOOTSTRAP_ADMIN_USERNAME" $C >/dev/null
SID=$($K get client-scopes -r ekka-desks $C --fields id,name --format csv --noquotes | grep ',provider:read$' | cut -d, -f1 || true)
if [ -z "$SID" ]; then
  SID=$($K create client-scopes -r ekka-desks $C -i -s name=provider:read -s protocol=openid-connect -s "$A")
  echo "created the client scope provider:read"
fi
$K update "client-scopes/$SID" -r ekka-desks $C -s "$A"
echo "provider:read is in the token's scope claim"
CID=$($K get clients -r ekka-desks $C -q clientId=ekka-enclave --fields id --format csv --noquotes)
[ -n "$CID" ] || { echo "REFUSED: realm ekka-desks has no client ekka-enclave; nothing was changed on a client" >&2; exit 1; }
$K update "clients/$CID/default-client-scopes/$SID" -r ekka-desks $C
echo "provider:read is a default scope of the ekka-enclave client (not of the realm)"
SA=$($K get "clients/$CID/service-account-user" -r ekka-desks $C --fields id --format csv --noquotes)
$K get "clients/$CID/evaluate-scopes/generate-example-access-token" -r ekka-desks $C -q "userId=$SA" -q scope=openid \
  | grep -o '"scope" : "[^"]*"'
