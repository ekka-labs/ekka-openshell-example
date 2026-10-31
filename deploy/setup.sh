#!/usr/bin/env bash
# This setup's setup. Run as root on the desks machine, from deploy/.
# Each stage is safe to run again: it creates what is absent and refuses, by name, what differs.
#
#   setup.sh init <public-name>              certificates, secrets, gateway.toml, supervisor, Keycloak + realm,
#                                            and the customer's first administrator (password typed, no echo)
#   setup.sh enroll                          PRINTS the enrollment steps: they need a person's EKKA sign-in
#   setup.sh people-scopes                   people's desk sign-ins read-only on a running install (up.sh, every run)
#   setup.sh enclave-config <env> <workspace>  the Enclave's OpenShell keys, issuer mode, in its enclave.env
#   setup.sh enclave-secret <env>            the Enclave client's secret, piped from Keycloak into the vault
#   setup.sh people                          the bundle each person runs once: the desks CA and backend.json
#   setup.sh supervisor                      the desks' supervisor binary from the pinned image (again after a pin bump)
#   setup.sh preflight                       refuse, by name, a gateway that could hand credentials out
#   setup.sh compose <args...>               docker compose with this setup's settings loaded (ps, logs, down, ...);
#                                            up, start, restart, create and run first pass the gateway preflight
#
# Nothing here prints a secret. Secrets move on stdin or stay in files that are mode 0600.
set -euo pipefail
cd "$(dirname "$0")"
HERE=$PWD; ROOT=$(cd .. && pwd)
HOMEVOL=ekka-desks_ekka-home                    # compose.yml: name ekka-desks, volume ekka-home
ALPINE=alpine:3.24.2@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6
export DESKS_STATE=${DESKS_STATE:-/var/lib/ekka-agent-desks}
P="docker compose -f compose.yml"; [[ -f $DESKS_STATE/up.env ]] && P="$P --env-file $DESKS_STATE/up.env"   # up.sh: binds, versions, telemetry
T=$DESKS_STATE/tls
die() { echo "setup: $*" >&2; exit 1; }
say() { echo "setup: $*"; }
pins() { python3 -c "import json,sys; p=json.load(open('$ROOT/deploy/pins.json'))['openshell']; print(p$1)"; }
kc_env() {
  export KC_URL=https://127.0.0.1:8443 KC_CA=$T/ca.crt KC_ADMIN_USER=desks-bootstrap
  export KC_ADMIN_PASSWORD_FILE=$DESKS_STATE/secrets/keycloak-admin-password
}
ekka_run() { $P run --rm -T --no-deps --entrypoint /usr/local/bin/ekka -e EKKA_ENV="$1" ekka-enclave "${@:2}"; }

# The supervisor binary every desk runs, from the pinned image, checked against the committed sha256. Run again
# after a pin bump: the gateway does not start with a supervisor that is not the pinned one (tools/gateway_preflight.py).
# The new file replaces the old in one rename, so a desk already running keeps the binary it started with.
stage_supervisor() {
  local dst=$DESKS_STATE/supervisor/openshell-supervisor want; want=$(pins "['supervisor']['binary_sha256']")
  if [[ -f $dst ]] && echo "$want  $dst" | sha256sum -c - >/dev/null 2>&1; then say "supervisor: already OpenShell $(pins "['version']")"; return; fi
  say "the supervisor binary, from the pinned image, checked against the committed sha256"
  install -d -m 0755 "$DESKS_STATE/supervisor"
  local c; c=$(docker create "$(pins "['supervisor']['image']")" x)
  docker cp "$c:$(pins "['supervisor']['binary_in_image']")" "$dst.new" >/dev/null; docker rm "$c" >/dev/null
  echo "$want  $dst.new" | sha256sum -c - >/dev/null \
    || { rm -f "$dst.new"; die "the supervisor binary does not match deploy/pins.json; nothing was changed"; }
  chmod 0755 "$dst.new"; mv -f "$dst.new" "$dst"
  say "supervisor: OpenShell $(pins "['version']"); desks started from now on use it"
}

# Before any compose call that can start the gateway: refuse, by name, what would let it hand credentials out
# or run with an unpinned supervisor (tools/gateway_preflight.py).
gateway_preflight() {
  $P --env-file "$DESKS_STATE/desks.env" config --format json | python3 "$ROOT/tools/gateway_preflight.py" \
    || die "the gateway was not started (above: each reason, and its fix)"
}

# Before anything that can start the backend adapter: its configuration and the CA must be real, regular files,
# or nothing starts (tools/backend_preflight.py). Not left to Compose's create_host_path, which each Compose version
# renders and treats in its own way. `--always` for a command that runs the adapter by name.
backend_preflight() {
  python3 "$ROOT/tools/backend_preflight.py" "$DESKS_STATE" "$@" \
    || die "the backend adapter was not started (above: each reason, and its fix)"
}

# The preflights for `setup.sh compose <args>`, when the args can start something. The SUBCOMMAND is the first
# argument that is not an option or an option's value: `--profile desk-admin up` is an `up` too. The adapter is
# checked even with its profile off when it is named (`up -d ekka-backend`, `--profile desk-admin`, or
# COMPOSE_PROFILES), because naming a service starts it whatever up.env says (agent2's review of #106).
compose_starts() {
  local a sub="" skip=0 always=""
  for a in "$@"; do
    if (( skip )); then skip=0; continue; fi
    case $a in
      --profile|-f|--file|--env-file|-p|--project-name|--project-directory|--ansi|--progress|--parallel) skip=1 ;;
      -*) ;;
      *) sub=$a; break ;;
    esac
  done
  case $sub in up|start|restart|create|run) ;; *) return 0 ;; esac
  for a in "$@"; do case $a in ekka-backend|desk-admin|--profile=desk-admin) always=--always ;; esac; done
  [[ ,${COMPOSE_PROFILES:-}, == *,desk-admin,* ]] && always=--always
  backend_preflight $always
  gateway_preflight
}

# Only this kit's old service address is migrated. Enrollment and every other key stay private in the volume.
stage_loopback_config() {
  local env=${EKKA_ENV:-prod} envdir; envdir=$env; [[ $env == prod ]] && envdir=.
  docker run --rm --network none -v "$HOMEVOL:/h" -v "$HERE/migrate-loopback.sh:/migrate:ro" "$ALPINE" \
    sh /migrate "/h/$envdir/enclave.env"
}

# A network-namespace owner restarted outside Compose can leave the gateway in its old namespace.
# This checks connectivity only; the executor still verifies TLS, OIDC and the pinned gateway identity.
stage_loopback_check() {
  $P --env-file "$DESKS_STATE/desks.env" exec -T ekka-enclave sh -c 'nc -z -w 3 127.0.0.1 8080 < /dev/null' \
    || die "gateway loopback is unavailable; repair the whole stack with sudo ./up.sh server <name> (or the original local command)"
}

# IDs alone do not detect a direct Engine restart: the owner can keep its ID
# while entering a new namespace, so require actual loopback connectivity too.
stage_loopback_current() {
  local enclave gateway mode
  enclave=$($P --env-file "$DESKS_STATE/desks.env" ps -q ekka-enclave) || return 1
  gateway=$($P --env-file "$DESKS_STATE/desks.env" ps -q gateway) || return 1
  [[ -n $enclave && -n $gateway ]] || return 1
  mode=$(docker inspect --format '{{.HostConfig.NetworkMode}}' "$gateway") || return 1
  [[ $mode == "container:$enclave" ]] || return 1
  stage_loopback_check
}

# People's sign-ins are read-only (ADK-E2), on an install that already runs too. `init` runs once, so an
# upgrade that only reran it would never migrate; up.sh runs this on every run instead (step_people_scopes).
# Idempotent: a second run changes nothing and signs nobody out. It prints one line, before and after.
stage_people_scopes() {
  [[ -f $DESKS_STATE/desks.env ]] || die "no $DESKS_STATE/desks.env: run init first"
  kc_env
  local up=""
  for _ in $(seq 1 60); do curl -sf -o /dev/null --cacert "$T/ca.crt" https://127.0.0.1:8443/realms/master && { up=1; break; }; sleep 5; done
  [[ -n $up ]] || die "Keycloak did not answer in 5 minutes; people's sign-ins were not checked"
  python3 keycloak/apply_realm.py scopes ekka-cli
}

stage_init() {
  local name=${1:-}; [[ -n $name ]] || die "usage: init <public-name>"
  [[ $(id -u) == 0 ]] || die "run init as root: it gives each key to the one user that reads it"
  [[ -S /var/run/docker.sock ]] || die "no Docker socket at /var/run/docker.sock"
  install -d -m 0755 "$DESKS_STATE"; install -d -m 0700 "$DESKS_STATE/secrets"
  echo "DESKS_NAME=$name" > "$DESKS_STATE/desks.env"
  echo "DESKS_DOCKER_GID=$(stat -c %g /var/run/docker.sock)" >> "$DESKS_STATE/desks.env"
  DESKS_DOCKER_GID=$(stat -c %g /var/run/docker.sock); export DESKS_NAME=$name DESKS_DOCKER_GID

  say "certificates (CA 5 years, servers 1 year)"
  DESKS_STATE=$DESKS_STATE bash ./certs.sh init "$name" >/dev/null
  chown -R 101:101 "$T/edge"                     # nginx-unprivileged
  chown -R 1000:1000 "$T/gateway" "$T/jwt"       # NVIDIA's gateway image

  say "secrets (generated here, never printed)"
  if [[ ! -f $DESKS_STATE/secrets/keycloak.env ]]; then
    ( umask 077
      local dbp adp; dbp=$(openssl rand -hex 24); adp=$(openssl rand -hex 24)
      printf 'POSTGRES_PASSWORD=%s\n' "$dbp" > "$DESKS_STATE/secrets/keycloak-db.env"
      printf 'KC_DB_PASSWORD=%s\nKC_BOOTSTRAP_ADMIN_USERNAME=desks-bootstrap\nKC_BOOTSTRAP_ADMIN_PASSWORD=%s\n' "$dbp" "$adp" > "$DESKS_STATE/secrets/keycloak.env"
      printf '%s' "$adp" > "$DESKS_STATE/secrets/keycloak-admin-password" )
  fi

  say "gateway.toml for $name"
  sed "s/@@PUBLIC_NAME@@/$name/" gateway/gateway.toml.template > "$DESKS_STATE/gateway.toml"; chmod 0644 "$DESKS_STATE/gateway.toml"

  stage_supervisor
  python3 "$ROOT/tools/verify_pins.py" --images >/dev/null || die "a pinned image does not hold the pinned binary"

  say "Keycloak, its database and the edge"
  $P --env-file "$DESKS_STATE/desks.env" up -d keycloak-db keycloak edge >/dev/null
  kc_env
  for _ in $(seq 1 60); do curl -sf -o /dev/null --cacert "$T/ca.crt" https://127.0.0.1:8443/realms/master && break; sleep 5; done
  # A live install migrates first: people's sign-ins become read-only (ADK-E2), and apply refuses the old scopes.
  python3 keycloak/apply_realm.py scopes ekka-cli
  python3 keycloak/apply_realm.py apply

  if [[ -t 0 ]]; then
    say "the first administrator (skip with Ctrl-C if one exists)"
    read -r -p "  username: " u; read -r -p "  email: " e
    python3 keycloak/apply_realm.py first-admin "$u" "$e"
  fi
  say "init done. Next: setup.sh enroll (with the person who owns the EKKA org)"
}

stage_enroll() {
  cat <<EOF
Enrollment needs a person's EKKA sign-in; run these with them:
  1. a signed-in person, on any computer:      ekka enclave create --name "desks $(hostname)"   -> prints an id
  2. here, the Enclave's own home:              cd $HERE && EKKA_ENV=<env> $P run --rm --entrypoint /usr/local/bin/ekka ekka-enclave login --email <them>
  3. no keychain in a container:              ... ekka-enclave enclave custody file
  4. claim this machine with that id:         ... ekka-enclave enclave start <id>     (Ctrl-C once it is running)
  5. an administrator makes the Enclave a USER member of the desk workspace (not admin), e.g.
       openshell workspace create --name <workspace>
       openshell workspace member add --workspace <workspace> --subject <ekka-enclave's OIDC sub> --role user
  6. then:  setup.sh enclave-config <env> <workspace>; setup.sh enclave-secret <env>
            EKKA_ENV=<env> $P --env-file $DESKS_STATE/desks.env up -d     (the gateway starts after the Enclave is healthy)
EOF
}

stage_enclave_config() {
  local env=${1:-} ws=${2:-}; [[ -n $env && -n $ws ]] || die "usage: enclave-config <env> <workspace>"
  # shellcheck disable=SC1091
  . "$DESKS_STATE/desks.env"
  local gwkey; gwkey=$(openssl pkey -pubin -in "$T/jwt/public.pem" -outform DER | tail -c 32 | base64)
  kc_env
  local subj; subj=$(python3 - <<'PY'
import sys,json; sys.path.insert(0,"keycloak"); import apply_realm as a
api=a.Api(); spec=json.load(open(a.SPEC)); c=a.find_client(api,spec,"ekka-enclave")
print(api.get(a.realm_path(spec,f"/clients/{c['id']}/service-account-user"))["id"])
PY
)
  local f; f=$(mktemp); cat > "$f" <<EOF
EKKA_OPENSHELL_GATEWAY=ekka-desks
EKKA_OPENSHELL_GATEWAY_KEY_B64=$gwkey
EKKA_OPENSHELL_INTERCEPTOR_SOCKET=/run/ekka/openshell-interceptor.sock
EKKA_OPENSHELL_INTERCEPTOR_AUDIENCE=urn:ekka:openshell:interceptor
EKKA_OPENSHELL_INTERCEPTOR_SOCKET_MODE=0660
EKKA_OPENSHELL_INTERCEPTOR_GROUP=openshell
EKKA_OPENSHELL_EXECUTOR_URL=https://127.0.0.1:8080
EKKA_OPENSHELL_EXECUTOR_TLS_DOMAIN=$DESKS_NAME
EKKA_OPENSHELL_EXECUTOR_CA_PEM_PATH=/tls/ca.crt
EKKA_OPENSHELL_EXECUTOR_WORKSPACE=$ws
EKKA_OPENSHELL_EXECUTOR_SUBJECT=user:oidc:$subj
EKKA_OPENSHELL_EXECUTOR_ISSUER=https://$DESKS_NAME/realms/ekka-desks
EKKA_OPENSHELL_EXECUTOR_CLIENT_ID=ekka-enclave
EKKA_OPENSHELL_EXECUTOR_CLIENT_SECRET_NAME=OPENSHELL_EXECUTOR_CLIENT_SECRET
EKKA_DESKS_AUTO=true
EOF
  # merged into the Enclave's ONE config file, keeping every key the enrollment wrote. Prod keeps its files at the
  # top of the Enclave's home; every other environment in a folder named after it.
  local envdir=$env; [[ $env == prod ]] && envdir=.
  docker run --rm -i -v "$HOMEVOL:/h" -v "$f:/new:ro" "$ALPINE" sh -eu -c "
    F=/h/$envdir/enclave.env; test -f \$F || { echo 'no enclave.env for $env: enroll first'; exit 1; }
    cp -p \$F \$F.before-desks; grep -v -E '^EKKA_OPENSHELL_' \$F.before-desks > \$F; cat /new >> \$F; chown 2000:1000 \$F; chmod 0600 \$F"
  rm -f "$f"
  say "enclave.env ($env): the OpenShell keys written, issuer mode; the old file kept as enclave.env.before-desks"
}

stage_enclave_subject() {
  kc_env
  python3 - <<'PY'
import sys,json; sys.path.insert(0,"keycloak"); import apply_realm as a
api=a.Api(); spec=json.load(open(a.SPEC)); c=a.find_client(api,spec,"ekka-enclave")
print(api.get(a.realm_path(spec,f"/clients/{c['id']}/service-account-user"))["id"])  # workspace membership names the bare subject
PY
}

stage_enclave_secret() {
  local env=${1:-}; [[ -n $env ]] || die "usage: enclave-secret <env>"
  kc_env
  python3 keycloak/apply_realm.py enclave-secret | ekka_run "$env" secret put OPENSHELL_EXECUTOR_CLIENT_SECRET --stdin
  say "the Enclave client's secret is in the vault as OPENSHELL_EXECUTOR_CLIENT_SECRET (never printed)"
}

desk_backend_json() {
  # Public connection defaults only. The same shape is used by a person's install and the server CLI.
  python3 - "$1" "$2" "$3" "$4" "$5" "$6" "${7:-desks}" <<'PY'
import json, sys
home, url, name, subject, image, tls_domain, workspace = sys.argv[1:]
b = {"gateway": "ekka-desks", "url": url, "ca_pem_path": home + "/desks-ca.pem",
     "workspace": workspace, "enclave_subject": subject,
     "desk": {"image": image, "policy_file": home + "/desk-policy.json"},
     "oidc": {"issuer": "https://" + name + "/realms/ekka-desks",
              "client_id": "ekka-cli", "audience": "ekka-desks"}}
if tls_domain:
    b["tls_domain"] = tls_domain
print(json.dumps(b))
PY
}

stage_people() {
  # shellcheck disable=SC1091
  . "$DESKS_STATE/desks.env"; [[ -f $DESKS_STATE/up.env ]] && . "$DESKS_STATE/up.env"   # DESKS_ADDRESS, server mode
  local out=$DESKS_STATE/people; install -d -m 0755 "$out"; cp "$T/ca.crt" "$out/desks-ca.pem"
  # The Enclave's own member of the desk workspace: ekka shows it and never removes it, and refuses access
  # changes until the backend names it.
  local enclave_subject; enclave_subject=$(stage_enclave_subject) || die "could not read the Enclave's subject from Keycloak"
  cp ../desk/policy.json "$out/desk-policy.json"   # what a desk may reach; people do not have this repository
  # The desk image by its content ID, the value the desks grant pins (up.sh step_desk_grant): `ekka desk create`
  # then names exactly what the grant allows. The runner refuses a tag. A rebuilt image has a new ID, and up.sh
  # writes this bundle again on every run, so each person runs the new install.sh after a rebuild.
  local desk_image; desk_image=$(docker image inspect --format '{{.Id}}' ekka-desk:local 2>/dev/null) || true
  [[ $desk_image =~ ^sha256:[0-9a-f]{64}$ ]] \
    || die "the desk image ekka-desk:local is not built, so the bundle cannot name it; run ./up.sh first"
  local person_config
  person_config=$(desk_backend_json '$H' "https://$DESKS_NAME:8080" "$DESKS_NAME" "$enclave_subject" "$desk_image" "" '${1:-desks}')
  # This file contains no sign-in or credential. Never copy a person's cached token into the Enclave.
  install -d -m 0700 "$DESKS_STATE/enclave-cli"
  desk_backend_json /var/lib/ekka/.ekka https://127.0.0.1:8080 "$DESKS_NAME" "$enclave_subject" "$desk_image" "$DESKS_NAME" \
    > "$DESKS_STATE/enclave-cli/backend.json"
  chmod 0600 "$DESKS_STATE/enclave-cli/backend.json"
  cat > "$out/install.sh" <<EOF
#!/usr/bin/env bash
# Run once on each person's computer, beside desks-ca.pem. It saves the desks CA under the person's EKKA home and
# points ekka's desk backend at this setup. The sign-in trusts that CA beside the public ones (needs ekka 0.1.104
# or later). The browser needs the same CA trusted too: the last lines print the one command for that, and run nothing.
set -euo pipefail
# The server has no public DNS name (it is reached over your VPN): map its name to its address once.
ADDR=${DESKS_ADDRESS:-}
if [ -n "\$ADDR" ] && ! grep -qE "^[^#]*[[:space:]]$DESKS_NAME([[:space:]]|\\\$)" /etc/hosts; then
  echo "adding '\$ADDR $DESKS_NAME' to /etc/hosts (asks for your password)"
  echo "\$ADDR $DESKS_NAME" | sudo tee -a /etc/hosts >/dev/null
fi
H=\${EKKA_HOME:-\$HOME/.ekka}; install -d -m 0700 "\$H"; install -m 0644 "\$(dirname "\$0")/desks-ca.pem" "\$H/desks-ca.pem"
install -m 0644 "\$(dirname "\$0")/desk-policy.json" "\$H/desk-policy.json"   # every new desk's default policy
B=\$(mktemp); trap 'rm -f "\$B"' EXIT
cat > "\$B" <<JSON
$person_config
JSON
ekka desk backend set "\$B"
bash "\$(dirname "\$0")/trust-ca.sh" "\$H/desks-ca.pem" || echo "the browser's trust command could not be printed; see docs/OPERATIONS.md, Certificates"
EOF
  chmod 0755 "$out/install.sh"
  # The browser half: the command that trusts the sign-in page, printed and never run. It is written once,
  # here, and install.sh, `up.sh` and the walk all print it from this file.
  { printf '#!/usr/bin/env bash\nNAME=%q\n' "$DESKS_NAME"; cat <<'TRUST'
# Prints, and never runs, the one command that makes this computer's browser trust the desks sign-in page
# (the desk installation checks). `trust-ca.sh` prints it for this computer, with the CA beside this file;
# `trust-ca.sh <ca-file>` names that file instead; `trust-ca.sh --all` prints it for each system, to pass on.
#
# THE TRUST IS AS NARROW AS THE SYSTEM ALLOWS: websites only, this server's name only, this person only, no sudo.
# The kit's CA is constrained to the server's names (certs.sh), but that constraint names DNS names and IP
# addresses alone. A code-signing or an email certificate carries neither, so a CA trusted for EVERY purpose, the
# way `security add-trusted-cert -d ... System.keychain` trusts it, would let anyone holding the server's CA key
# sign software this computer then trusts. On Linux, browsers do not read the system store at all, so
# `update-ca-certificates` needs root for the whole machine and still leaves the warning.
set -euo pipefail
if [ "${1:-}" = --all ]; then
  ca=$(dirname "$0")/desks-ca.pem; show=desks-ca.pem
  keychain='~/Library/Keychains/login.keychain-db'; nss='sql:$HOME/.pki/nssdb'
else
  ca=${1:-$(dirname "$0")/desks-ca.pem}; show=$(printf %q "$ca")
  keychain=$(printf %q "$HOME/Library/Keychains/login.keychain-db"); nss=$(printf %q "sql:$HOME/.pki/nssdb")
fi
[ -f "$ca" ] || { echo "no desks CA at $ca" >&2; exit 1; }
fp=$(openssl x509 -in "$ca" -noout -fingerprint -sha256 | cut -d= -f2)
mac="security add-trusted-cert -r trustRoot -p ssl -s $NAME -k $keychain $show"
linux="certutil -d $nss -A -t C,, -n $(printf %q "EKKA desks CA ($NAME)") -i $show"
echo "The desks CA's SHA-256 fingerprint is $fp. Check it with your administrator's."
echo "Then, once, so your browser trusts the sign-in page at https://$NAME (websites only, this name only):"
case "${1:-}:$(uname -s)" in
  --all:*) echo "  macOS:  $mac"
           echo "  Linux, Chrome and Chromium:  $linux   (certutil is in libnss3-tools; Firefox keeps its own store)" ;;
  *:Darwin) echo "  $mac   (macOS asks for your login password)" ;;
  *:Linux) echo "  $linux   (certutil is in libnss3-tools; Firefox keeps its own store)" ;;
  *) echo "  see docs/OPERATIONS.md, Certificates" ;;
esac
TRUST
  } > "$out/trust-ca.sh"
  chmod 0755 "$out/trust-ca.sh"
  say "people bundle: $out (desks-ca.pem, install.sh, trust-ca.sh). Each person runs install.sh once, then ekka desk login"
}



case "${1:-}" in
  init) stage_init "${@:2}" ;;
  enroll) stage_enroll ;;
  enclave-config) stage_enclave_config "${@:2}" ;;
  enclave-secret) stage_enclave_secret "${@:2}" ;;
  enclave-subject) stage_enclave_subject ;;
  people) stage_people ;;
  supervisor) stage_supervisor ;;
  loopback-config) stage_loopback_config ;;
  loopback-check) stage_loopback_check ;;
  loopback-current) stage_loopback_current ;;
  preflight) gateway_preflight ;;
  backend-preflight) backend_preflight "${@:2}" ;;
  people-scopes) stage_people_scopes ;;
  compose) [[ -f $DESKS_STATE/desks.env ]] || die "no $DESKS_STATE/desks.env: run init first"
           # anything that can start the gateway or the adapter; the adapter's own check first, before any docker call
           compose_starts "${@:2}"
           exec $P --env-file "$DESKS_STATE/desks.env" "${@:2}" ;;   # compose.yml requires DESKS_NAME
  *) sed -n '2,16p' "$0"; exit 2 ;;
esac
