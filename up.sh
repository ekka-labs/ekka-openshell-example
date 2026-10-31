#!/usr/bin/env bash
# EKKA Agent Desks, on NVIDIA OpenShell: one command from a clean Linux machine to a working desk.
#
#   sudo ./up.sh server <name>         one machine others reach by <name> (DNS points <name> here)
#   sudo ./up.sh status                what is done, and what is next
#   sudo ./up.sh profiles              put providers/ into the gateway again (after an update of this repository);
#                                      a profile in $DESKS_STATE/providers.local/ wins and is never replaced
#   sudo ./up.sh invite <user> <email> one more person who uses desks: a sign-in and a place in the desk workspace
#   sudo ./up.sh revoke <user>         that person taken off: sign-in disabled, signed out, out of the desk workspace
#   sudo ./up.sh desk-plans <desk>     the setup and ask plans for one more desk (code-desk has them from the install)
#   sudo -i, then ekka <command>       this machine's Enclave, as its own user (the installed /usr/local/sbin/ekka);
#                                      `sudo ./up.sh ekka <command>` is the same call for scripts
#   <key> | sudo ./up.sh connect <provider>  set a provider's key on this machine, read from stdin only
#   sudo ./up.sh openshell <command>   NVIDIA's openshell CLI as the gateway's administrator, e.g. sudo ./up.sh openshell provider list
#   sudo ./up.sh walk                  run every documented command against this installation, and check each
#   sudo ./up.sh desks settle <desk> --operation <request id>  settle ONE unfinished desk change after the desk
#                                      stopped changing; see docs/OPERATIONS.md
#   sudo ./up.sh desks unhold <desk> --operation <request id>  remove the hold a settle that stopped left, after
#                                      checking the desk
#
# Run it again at any time: every step checks first and skips what is done. It stops once, for your EKKA
# sign-in, prints the exact command, and carries on from there the next time you run it.
#
# Settings (environment): EKKA_ENV       the EKKA service this desk machine joins (empty: the ekka CLI default)
#                         EKKA_VERSION   the Enclave release (default below)
#                         AGENT          the provider profile of your desk's agent: claude-code (default) or codex;
#                                        see providers/README.md
#                         OPENSHELL_TELEMETRY  off (default) or on: NVIDIA OpenShell's usage reporting
#                         ADMIN_EMAIL    the desk gateway's first administrator (asked for when needed)
#                         EKKA_ORG       the EKKA organization this server's Enclave joins (asked for when needed)
#                         DESKS_AGENT    the agent that owns desk plans (default: desks)
#                         DESKS_ADDRESS  server mode: the address people reach this machine at (default: its own;
#                                        set it on a cloud machine behind NAT, such as its Elastic IP on AWS)
set -euo pipefail
cd "$(dirname "$0")"; ROOT=$PWD
# shellcheck source=deploy/lib.sh
. deploy/lib.sh                      # enclave_ekka, ENCLAVE_HOME: the one way scripts reach the native Enclave
SNAPSHOT=/usr/local/lib/ekka-desks   # root-owned copy of deploy/ and tools/ the supervisor unit runs from
export DESKS_STATE=${DESKS_STATE:-/var/lib/ekka-agent-desks}
# A folder-mode rerun must keep this installation's environment and release, not reset them.
EKKA_VERSION=${EKKA_VERSION:-$(python3 tools/workspace_policy.py retained "$DESKS_STATE" EKKA_VERSION 0.1.124)}
EKKA_ENV=${EKKA_ENV:-$(python3 tools/workspace_policy.py retained "$DESKS_STATE" EKKA_ENV prod)}
export EKKA_ENV
AGENT=${AGENT:-claude-code}
OPENSHELL_TELEMETRY=${OPENSHELL_TELEMETRY:-off}
OPENSHELL_TAG=v0.1.4
# EKKA desk administration requires the compatible Enclave release below.
# Upgrade the integration and Enclave together. The release must support governed desk
# changes, per-run output collection, verified requester identities, and conversation leases.
# OpenShell addresses commands by desk name. Identity checks narrow the replacement race
# before dispatch but do not eliminate it. See docs/SECURITY.md for the trust boundaries.
DESK_ADMIN_MIN_EKKA=0.1.124
# Docker's own plugins, pinned (the sha256 is the one Docker publishes beside each release).
COMPOSE_VER=v5.6.0;  COMPOSE_SHA=40343e21ca777173e69cff5dbafeb37c6f81f3b0d57d9e597f036e95eb63e76a
BUILDX_VER=v0.38.0;  BUILDX_SHA=4fe4cc38adf48169132749b6ca22a990928db0118e3407584ee553723115d287
# NVIDIA's openshell CLI, for the gateway administrator's steps (sha256 from NVIDIA's checksums file).
OPENSHELL_CLI_SHA=2b65178e62e1ece1d90d482555e9f1f98ff0296e058409b33418104b6333df07
PLUGINS=/usr/local/lib/docker/cli-plugins
DONE=$DESKS_STATE/up
DESKS_AGENT=${DESKS_AGENT:-desks}   # the agent that owns desk plans; never the organization's core agent
OS=$DESKS_STATE/bin/openshell   # NVIDIA's CLI, pinned; never whatever is on the PATH

say()  { printf '\033[1m==\033[0m %s\n' "$*"; }
ok()   { printf '   ok: %s\n' "$*"; }
die()  { printf 'up: %s\n' "$*" >&2; exit 1; }
done_() { install -d -m 0700 "$DONE"; date -u +%FT%TZ > "$DONE/$1"; }
is_done() { [[ -f $DONE/$1 ]]; }
stop_for_person() {  # prints what a person must do, then stops; the next run continues
  printf '\n\033[1m-- a person is needed --\033[0m\n%s\n\nThen run this again: sudo ./up.sh %s\n' "$1" "$MODE_ARGS"; exit 0; }

fetch_pinned() {  # url dest sha256 mode
  local tmp; tmp=$(mktemp)
  curl -fsSL "$1" -o "$tmp" || { rm -f "$tmp"; die "could not download $1"; }
  echo "$3  $tmp" | sha256sum -c - >/dev/null 2>&1 || { rm -f "$tmp"; die "$1 does not match its pinned sha256; nothing installed"; }
  install -m "$4" "$tmp" "$2"; rm -f "$tmp"
}

landlock_abi() {
  python3 -c 'import ctypes; print(ctypes.CDLL(None, use_errno=True).syscall(444, None, 0, 1))' 2>/dev/null || echo -1
}

step_preflight() {
  say "this machine"
  [[ $(uname -s) == Linux ]] || die "desks run on Linux (NVIDIA OpenShell's sandbox needs Linux Landlock)"
  [[ $(id -u) == 0 ]] || die "run with sudo: setup gives each key to the one user that reads it"
  [[ $(uname -m) == x86_64 ]] || die "tested on x86_64 only so far"
  for t in curl python3 openssl sha256sum runuser systemctl; do command -v $t >/dev/null || die "$t is missing"; done
  [[ -d /run/systemd/system ]] || die "systemd is not running this machine; the Enclave runs as a systemd service"
  command -v cosign >/dev/null || die "cosign is missing: the EKKA installer checks the release signature with it. Install it first:
  sudo curl -fsSLo /usr/local/bin/cosign https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-amd64 && sudo chmod +x /usr/local/bin/cosign"
  local abi; abi=$(landlock_abi)
  (( abi >= 3 )) || die "the kernel's Landlock ABI is $abi; NVIDIA OpenShell needs 3 or newer (Linux 6.2+). On Amazon Linux 2023, install kernel6.18 and reboot"
  ok "Linux $(uname -r), Landlock ABI $abi"
  docker info >/dev/null 2>&1 || die "Docker is not running"
  install -d -m 0755 "$PLUGINS"
  docker compose version >/dev/null 2>&1 || { fetch_pinned "https://github.com/docker/compose/releases/download/$COMPOSE_VER/docker-compose-linux-x86_64" "$PLUGINS/docker-compose" "$COMPOSE_SHA" 0755; ok "installed Docker Compose $COMPOSE_VER"; }
  docker buildx version >/dev/null 2>&1 || { fetch_pinned "https://github.com/docker/buildx/releases/download/$BUILDX_VER/buildx-$BUILDX_VER.linux-amd64" "$PLUGINS/docker-buildx" "$BUILDX_SHA" 0755; ok "installed Docker buildx $BUILDX_VER"; }
  ok "$(docker --version | cut -d, -f1), $(docker compose version --short 2>/dev/null | sed 's/^/compose /'), buildx $(docker buildx version | awk '{print $2}')"
  [[ -f providers/$AGENT.json ]] || die "no provider profile for AGENT=$AGENT; see providers/README.md"
  python3 -m unittest tests.test_providers >/dev/null 2>&1 || die "providers/ does not match providers/PIN.json; nothing was changed"
  ok "agent profile: $AGENT (NVIDIA OpenShell $OPENSHELL_TAG, pinned)"
  release_gate
}

release_gate() {  # the Enclave release must run desk changes through EKKA; this kit works with nothing older
  [[ -n $DESK_ADMIN_MIN_EKKA ]] \
    || die "this kit needs the EKKA release that runs desk changes through EKKA, and up.sh names none yet"
  local rc=0; python3 tools/release_gate.py "$EKKA_VERSION" "$DESK_ADMIN_MIN_EKKA" || rc=$?
  (( rc == 2 )) && die "the Enclave release '$EKKA_VERSION' is not a release number"
  (( rc == 1 )) && die "this kit needs EKKA $DESK_ADMIN_MIN_EKKA or newer, and this machine runs $EKKA_VERSION. The kit and the Enclave upgrade together: run again with EKKA_VERSION=$DESK_ADMIN_MIN_EKKA"
  ok "EKKA $EKKA_VERSION runs desk changes through EKKA (needs $DESK_ADMIN_MIN_EKKA or newer)"
}

step_host_accounts() {  # the Enclave's user and the shared group, made by systemd from deploy/host; refused by name when unsafe
  say "the Enclave's account"
  local uid name
  if uid=$(id -u ekka 2>/dev/null); then
    [[ $uid == 2000 ]] || die "a user 'ekka' exists with uid $uid, not 2000; this setup's Enclave runs as uid 2000. Remove or rename it first"
  fi
  if name=$(getent passwd 2000 | cut -d: -f1) && [[ -n $name && $name != ekka ]]; then
    die "uid 2000 belongs to '$name', not 'ekka'; this setup's Enclave runs as uid 2000. Move that account first"
  fi
  local g; for g in docker sudo; do
    id -nG ekka 2>/dev/null | tr ' ' '\n' | grep -qx "$g" && die "the 'ekka' user is in group '$g'; the Enclave never holds Docker or sudo. Remove it from '$g' first"
  done
  local ogid; if ogid=$(getent group openshell | cut -d: -f3) && [[ -n $ogid && $ogid == 1000 ]]; then
    die "group 'openshell' has gid 1000, the first person's gid on Ubuntu; it must be its own group. Remove it and run again"
  fi
  # Ubuntu ships neither folder until something needs it (measured on a fresh 24.04 server).
  install -d -m 0755 /etc/sysusers.d /etc/tmpfiles.d
  install -m 0644 -o root -g root deploy/host/sysusers.d/ekka-desks.conf /etc/sysusers.d/ekka-desks.conf
  install -m 0644 -o root -g root deploy/host/tmpfiles.d/ekka-desks.conf /etc/tmpfiles.d/ekka-desks.conf
  systemd-sysusers ekka-desks.conf >/dev/null || die "systemd-sysusers could not make the ekka user and the openshell group"
  systemd-tmpfiles --create ekka-desks.conf >/dev/null || die "systemd-tmpfiles could not make the Enclave's folders"
  [[ $(id -u ekka) == 2000 ]] || die "the ekka user is not uid 2000 after systemd-sysusers"
  getent group openshell >/dev/null || die "the openshell group is missing after systemd-sysusers"
  [[ $(stat -c '%U:%G %a' /var/lib/ekka/.ekka) == "ekka:ekka 700" ]] || die "/var/lib/ekka/.ekka is not ekka:ekka 0700"
  [[ $(stat -c '%U:%G %a' /run/ekka) == "ekka:openshell 750" ]] || die "/run/ekka is not ekka:openshell 0750"
  id -nG ubuntu 2>/dev/null | tr ' ' '\n' | grep -qx openshell && die "the 'ubuntu' user is in group 'openshell': it could reach the Enclave's interceptor socket. Remove it from the group"
  ok "user ekka (uid 2000), group openshell (gid $(getent group openshell | cut -d: -f3)), folders /var/lib/ekka/.ekka, /run/ekka, /run/ekka-backend"
}

openshell_gid() { getent group openshell | cut -d: -f3; }

host_env_files() {  # what the native Enclave and the installed `ekka` wrapper read: root-owned, no secret in either
  install -d -m 0755 /etc/ekka
  printf 'EKKA_ENV=%s\n' "${EKKA_ENV:-prod}" > /etc/ekka/desks.env.new && chmod 0644 /etc/ekka/desks.env.new && mv -f /etc/ekka/desks.env.new /etc/ekka/desks.env
  # The Enclave reaches the issuer and the gateway by this machine's public name, through the edge on this machine.
  grep -q " $NAME\$" /etc/hosts 2>/dev/null || getent hosts "$NAME" >/dev/null || echo "127.0.0.1 $NAME" >> /etc/hosts
}

step_settings() {
  install -d -m 0755 "$DESKS_STATE"
  local bind=0.0.0.0
  # The address people's computers use. This machine's own address is right on a server with a public address on its
  # network card (OVH), and wrong on a cloud machine behind NAT (AWS, GCP, Azure), whose card holds a private address.
  local addr=${DESKS_ADDRESS:-$(ip -4 route get 1.1.1.1 | awk '{for(i=1;i<NF;i++) if($i=="src") print $(i+1)}')}
  if [[ $MODE == server && -z ${DESKS_ADDRESS:-} && $addr =~ ^(10\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.) ]]; then
    printf '   note: people will reach this server at %s, a private address. Behind a cloud NAT, run again with\n' "$addr"
    printf '         DESKS_ADDRESS=<the address your VPN reaches it at>, for example its Elastic IP on AWS.\n'
  fi
  # The backend adapter's image, pinned by digest. Read before up.env is written: a missing pin stops here.
  local backend_image
  # Derived from the pin's one registry value and its digest (tools/backend_image.py); refused while unpinned.
  backend_image=$(python3 tools/backend_image.py image 2>/dev/null) \
    || die "deploy/pins.json names no ekka-backend image; this kit installs the backend adapter"
  # Read by deploy/setup.sh and by every compose call through `setup.sh compose`.
  { echo "DESKS_NAME=$NAME"; echo "DESKS_EDGE_BIND=$bind"; echo "DESKS_GATEWAY_BIND=$bind"
    echo "DESKS_DOCKER_GID=$(stat -c %g /var/run/docker.sock)"
    echo "DESKS_OPENSHELL_GID=$(openshell_gid)"   # compose group_add: the gateway reaches the interceptor socket
    # The long-lived services ekka-desks.service runs in the foreground; the adapter joins once desk administration is on.
    # Quoted: setup.sh sources up.env, and an unquoted list would run `keycloak` as a command (measured on the walk box).
    if [[ -f $DESKS_STATE/backend/config.json ]]; then echo 'DESKS_STACK_SERVICES="keycloak-db keycloak edge gateway ekka-backend"'
    else echo 'DESKS_STACK_SERVICES="keycloak-db keycloak edge gateway"'; fi
    echo "EKKA_VERSION=$EKKA_VERSION"; echo "EKKA_ENV=${EKKA_ENV:-prod}"
    # The backend adapter (ADK-E1) is on only once its configuration exists. step_desk_admin writes it after this
    # machine is enrolled; turned on before that, the first `up` would start the adapter on a missing file
    # (Compose honors COMPOSE_PROFILES from --env-file: measured, Compose 5.5.1; regression review).
    [[ -f $DESKS_STATE/backend/config.json ]] && echo "COMPOSE_PROFILES=desk-admin"
    echo "EKKA_BACKEND_IMAGE=$backend_image"
    # the address people's computers map the name to (no public DNS needed)
    echo "DESKS_ADDRESS=$addr"
    [[ $OPENSHELL_TELEMETRY == on ]] && echo "OPENSHELL_TELEMETRY_ENABLED=true" || echo "OPENSHELL_TELEMETRY_ENABLED=false"
  } > "$DESKS_STATE/up.env"
  host_env_files
  ok "$MODE: $NAME, listening on $bind; NVIDIA OpenShell telemetry $OPENSHELL_TELEMETRY"
  [[ -f $DESKS_STATE/backend/config.json ]] \
    || echo "   the backend adapter starts once its configuration is written, after this machine is enrolled"
  ok 'desk files use separate storage; no host folders'
}

step_workspace_policy() {
  local result
  install -d -m 0700 "$DONE"
  result=$(python3 tools/workspace_policy.py configure "$DESKS_STATE/gateway.toml" false "$DESKS_STATE/workspace-policy.pending") \
    || die 'the gateway folder settings were not applied; see the error above'
}

step_workspace_restart() {
  if [[ -f $DESKS_STATE/workspace-policy.pending ]]; then
    (cd deploy && bash setup.sh compose restart gateway) \
      || die 'the gateway could not reload its folder settings; see: sudo ./up.sh status'
    ok 'gateway restarted with the selected folder settings'
    rm -- "$DESKS_STATE/workspace-policy.pending"
  fi
}

step_backend_image() {  # the backend adapter's image: pulled by digest, and its signature checked, before anything runs it
  python3 tools/backend_image.py pinned || return 0     # no release pinned yet: the desk-admin profile stays off
  say "backend adapter image"
  local image identity issuer cosign
  image=$(python3 tools/backend_image.py image) && identity=$(python3 tools/backend_image.py identity) \
    && issuer=$(python3 tools/backend_image.py issuer) && cosign=$(python3 tools/backend_image.py cosign-image) \
    || die "deploy/pins.json does not name the backend adapter's image"
  # A public package (owner, 2026-10-17: ekka-labs): no login, and cosign is given none of this machine's.
  docker pull -q "$image" >/dev/null || die "could not pull $image; does this machine reach ghcr.io?"
  docker run --rm --network host \
    "$cosign" verify --certificate-identity-regexp "$identity" --certificate-oidc-issuer "$issuer" "$image" >/dev/null 2>&1 \
    || die "the backend adapter's image is not signed by EKKA's release workflow; it will not run"
  ok "backend adapter $image, signed by EKKA's release workflow"
}

step_images() {
  say "images"
  echo "   NVIDIA OpenShell $OPENSHELL_TAG: pulled from ghcr.io/nvidia by digest (Apache-2.0; notices:"
  echo "   https://github.com/NVIDIA/OpenShell/blob/$OPENSHELL_TAG/THIRD-PARTY-NOTICES). EKKA does not host them."
  # EKKA's image repository is a submodule pinned by this example's commit.
  git -c safe.directory="$ROOT" -c safe.directory="$ROOT/external/desk-base" \
    submodule update --init --checkout -- external/desk-base \
    || die "could not initialize the EKKA desk image source; check GitHub access and retry"
  local image_dir="$ROOT/external/desk-base/desk/image" src have
  [[ -f $image_dir/Dockerfile ]] || die "the EKKA desk image source is missing"
  src=$( { find "$image_dir" -type f | LC_ALL=C sort | xargs cat; } | sha256sum | cut -c1-16)
  have=$(docker image inspect -f '{{index .Config.Labels "ekka.desk.source"}}' ekka-desk:local 2>/dev/null || true)
  if [[ $have != "$src" ]]; then
    docker build -q --label "ekka.desk.source=$src" -t ekka-desk:local "$image_dir" >/dev/null \
      || die "the desk image did not build: docker build -t ekka-desk:local external/desk-base/desk/image"
    ok "built the desk image on this machine (ekka-desk:local, $src); a desk made before keeps its image until it is made again"
  else ok "desk image ekka-desk:local ($src)"; fi
}

step_enclave_install() {  # the signed EKKA release on this machine, through EKKA's own installer, cosign REQUIRED
  say "the EKKA release"
  local have="" want=$EKKA_VERSION
  [[ -x /usr/local/bin/ekka ]] && have=$(/usr/local/bin/ekka --version 2>/dev/null | head -1 | awk '{print $2}' || true)   # `ekka 0.1.126` then commit and build lines
  if [[ $have == "$want" ]]; then ok "ekka $want at /usr/local/bin/ekka"
  else
    local t; t=$(mktemp -d)
    curl -fsSL https://get.ekka.ai/install.sh -o "$t/install.sh" || { rm -rf "$t"; die "could not download EKKA's installer"; }
    curl -fsSL https://get.ekka.ai/install.sh.sha256 -o "$t/install.sh.sha256" || { rm -rf "$t"; die "could not download the installer's checksum"; }
    (cd "$t" && sed 's#  .*#  install.sh#' install.sh.sha256 | sha256sum -c - >/dev/null 2>&1) || { rm -rf "$t"; die "EKKA's installer does not match its published checksum; nothing installed"; }
    [[ -n $have ]] && cp -p /usr/local/bin/ekka "/usr/local/bin/ekka.before-$have"
    # A controlled PATH so nothing shadows /usr/local/bin (the wrapper lives in /usr/local/sbin, not on this PATH); the
    # signature check is never skipped: a refusal here is the installer's own sentence.
    # EKKA_ALLOW_DOWNGRADE=1 on the up.sh command is the ONE way to roll back: the installer refuses an older release otherwise.
    local -a downgrade=(); [[ ${EKKA_ALLOW_DOWNGRADE:-} == 1 ]] && downgrade=(--allow-downgrade)
    env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/root EKKA_VERSION="$want" EKKA_INSTALL_DIR=/usr/local/bin sh "$t/install.sh" "${downgrade[@]}" >"$DESKS_STATE/ekka-install.log" 2>&1 \
      || { rm -rf "$t"; die "EKKA $want did not install; see $DESKS_STATE/ekka-install.log (an older release needs EKKA_ALLOW_DOWNGRADE=1 on this command)"; }
    rm -rf "$t"
    [[ $(/usr/local/bin/ekka --version 2>/dev/null | head -1 | awk '{print $2}') == "$want" ]] || die "/usr/local/bin/ekka is not $want after the install"
    ok "ekka $want installed (signature checked by EKKA's installer)${have:+; the previous binary is /usr/local/bin/ekka.before-$have}"
  fi
  # `ekka` for administrators: after `sudo -i`, the same command as everywhere, run as the Enclave's user.
  install -m 0755 -o root -g root deploy/host/sbin/ekka /usr/local/sbin/ekka
  ok "sudo -i, then ekka <command>: this machine's Enclave (/usr/local/sbin/ekka)"
}

step_init() {
  is_done init && { ok "certificates, secrets, sign-in service: done"; return; }
  say "certificates, secrets and the sign-in service"
  (cd deploy && bash setup.sh init "$NAME" </dev/null)
  done_ init
}

step_first_admin() {
  is_done first-admin && { ok "the first administrator exists"; return; }
  [[ -n ${ADMIN_EMAIL:-} ]] || stop_for_person "Name the desk gateway's first administrator: run again with ADMIN_EMAIL=you@example.com
  (sudo ADMIN_EMAIL=you@example.com ./up.sh $MODE_ARGS). up.sh makes a one-time password that you change at first sign-in."
  local pf=$DESKS_STATE/secrets/first-admin-password
  ( umask 077; openssl rand -base64 24 | tr -d '\n' > "$pf" )
  (cd deploy && KC_URL=https://127.0.0.1:8443 KC_CA=$DESKS_STATE/tls/ca.crt KC_ADMIN_USER=desks-bootstrap \
     KC_ADMIN_PASSWORD_FILE=$DESKS_STATE/secrets/keycloak-admin-password KC_FIRST_ADMIN_PASSWORD_FILE=$pf \
     python3 keycloak/apply_realm.py first-admin desks-admin "$ADMIN_EMAIL")
  done_ first-admin
  ok "first administrator desks-admin ($ADMIN_EMAIL); one-time password: sudo cat $pf (changed at first sign-in)"
}

env_dir() {  # where the Enclave keeps an environment's files in its home: prod at the top, the others in a folder
  local e=${EKKA_ENV:-prod}; [[ $e == prod ]] && echo . || echo "$e"; }

enrolled() { [[ -f $ENCLAVE_HOME/$(env_dir)/enclave.env ]]; }   # what `enclave start <id>` writes once the machine is claimed

ekka_in() { enclave_ekka "$@"; }   # the Enclave's own user and home (deploy/lib.sh); kept as the name the steps use

step_enroll() {
  local env=${EKKA_ENV:-prod}
  if enrolled 2>/dev/null; then ok "the Enclave is enrolled"; return; fi
  say "enrolling this machine's Enclave"
  # whoami exits non-zero until this machine is enrolled, even when signed in: read what it says instead.
  local who; who=$(ekka_in whoami 2>&1 || true)
  if ! grep -q "Signed in as" <<<"$who"; then
    stop_for_person "Sign in to EKKA as the person who owns your organization. It emails you a link; click it and this finishes:
  sudo -i
  ekka login --email <you>"
  fi
  if grep -q "not in an organization" <<<"$who"; then
    stop_for_person "You are signed in but in no EKKA organization yet. Create one (or ask your org admin for an invite):
  sudo -i
  ekka org create <name>"
  fi
  # An account can belong to several organizations, and "current" is whichever was used last: never create the
  # Enclave there by default. Name it, switch to it, and check the switch took before anything is created.
  local orgs; orgs=$(ekka_in org list 2>&1 || true)
  [[ -n ${EKKA_ORG:-} ]] || stop_for_person "Name the EKKA organization this server's Enclave joins, then run again with EKKA_ORG=<slug>.
Your organizations:
$(grep -E '^[[:space:]]*[*]?[[:space:]]*[a-z0-9-]+[[:space:]]' <<<"$orgs" | sed 's/^/  /')"
  ekka_in org switch "$EKKA_ORG" >/dev/null 2>&1 || die "could not switch to organization $EKKA_ORG; is the signed-in person a member?"
  ekka_in org list 2>&1 | grep -qE "^[[:space:]]*[*][[:space:]]*$EKKA_ORG[[:space:]]" \
    || die "the current organization is not $EKKA_ORG after switching; nothing was created"
  ok "organization: $EKKA_ORG"
  # The output is kept BEFORE the id is looked for, so a refusal reaches the person: with `set -e`, a pipeline
  # whose grep finds nothing stopped this script with exit 1 and no message at all (an organization with no
  # Enclave slot left, measured 2026-10-31). EKKA's own sentence is what the person needs.
  local created id
  created=$(ekka_in enclave create --name "desks $(hostname -s)" 2>&1) || true
  id=$(grep -o -E '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' <<<"$created" | head -1 || true)
  [[ -n $id ]] || die "EKKA did not create an Enclave: $(grep -v -E '^\s*$|Container |Network |Volume ' <<<"$created" | head -4 | tr '\n' ' ')"
  ok "Enclave $id created"
  # The first start claims this machine, keeps the vault key in a file (a server has no keychain), then keeps
  # running. It runs as a TRANSIENT systemd unit, as the Enclave's user, so it has a managed lifecycle: stopped by
  # name with a deadline once claimed, never a backgrounded shell signalled through a wrapper (Codex review of #4,
  # reproduced hang). Its output is in the journal. The permanent unit runs it for good from then on (step_start).
  systemctl stop ekka-enroll.service >/dev/null 2>&1 || true; systemctl reset-failed ekka-enroll.service >/dev/null 2>&1 || true
  systemd-run --unit=ekka-enroll --uid=ekka --gid=ekka -p TimeoutStopSec=30 -p KillMode=mixed \
    --setenv=PATH=/usr/local/bin:/usr/bin:/bin --setenv=HOME=/var/lib/ekka --setenv=EKKA_HOME="$ENCLAVE_HOME" --setenv=EKKA_ENV="$env" \
    /usr/local/bin/ekka enclave start "$id" --custody file >/dev/null 2>&1 \
    || die "the claim could not be started as a transient unit; see: journalctl -u ekka-enroll"
  local i; for i in $(seq 1 60); do
    enrolled 2>/dev/null && break
    systemctl is-active --quiet ekka-enroll.service || break   # it exited: the journal says why
    sleep 2; done
  journalctl -u ekka-enroll.service --no-pager -o cat >"$DESKS_STATE/enclave-claim.log" 2>/dev/null || true
  systemctl stop ekka-enroll.service >/dev/null 2>&1 || true   # SIGINT then SIGKILL after TimeoutStopSec; the lock is released either way
  for i in $(seq 1 40); do systemctl is-active --quiet ekka-enroll.service || break; sleep 1; done
  systemctl is-active --quiet ekka-enroll.service && die "the claim process did not stop within 40 s; nothing else was started. See: systemctl status ekka-enroll"
  systemctl reset-failed ekka-enroll.service >/dev/null 2>&1 || true
  enrolled || die "the Enclave did not finish enrolling; see $DESKS_STATE/enclave-claim.log"
  ok "this machine is claimed by Enclave $id (vault key kept in a file)"
  # A new Enclave (a first install, or after `ekka enclave reset`) has none of what three marks say the old one had:
  # its executor URL in enclave.env (started), its gateway membership (admin), its job gate's grant (grant). Measured
  # on the integration test server 2026-10-24: after a reset the kept `started` mark ran the migration on a URL-less enclave.env.
  rm -f "$DONE/started" "$DONE/admin" "$DONE/grant"
}

kc() {  # the realm script against this machine's Keycloak admin listener
  (cd deploy && KC_URL=https://127.0.0.1:8443 KC_CA=$DESKS_STATE/tls/ca.crt KC_ADMIN_USER=desks-bootstrap \
     KC_ADMIN_PASSWORD_FILE=$DESKS_STATE/secrets/keycloak-admin-password python3 keycloak/apply_realm.py "$@")
}

# The backend adapter's credential, from Keycloak into the Enclave's vault through a pipe only (ADK-E3): never a
# file, an argument or a terminal. The connection is the `desks` catalog row: the adapter's socket, this machine only.
connect_desk_admin() {
  kc admin-client on | ekka_in api connect desks --input socket=/run/ekka-backend/backend.sock --reach loopback --stdin >/dev/null \
    || die "the backend adapter's credential did not reach the Enclave's vault; nothing was stored on disk"
  ok "the backend adapter's credential is in the Enclave's vault (connection desks, this machine only)"
}

install_cli() {  # always NVIDIA's pinned CLI at our own path: a different openshell already on the PATH is never used
  "$OS" --version 2>/dev/null | grep -q " ${OPENSHELL_TAG#v}\$" && return
  local t; t=$(mktemp -d)
  fetch_pinned "https://github.com/NVIDIA/OpenShell/releases/download/$OPENSHELL_TAG/openshell-x86_64-unknown-linux-musl.tar.gz" "$t/os.tgz" "$OPENSHELL_CLI_SHA" 0644
  tar -xzf "$t/os.tgz" -C "$t"; install -D -m 0755 "$(find "$t" -type f -name openshell | head -1)" "$OS"; rm -rf "$t"
  ok "installed NVIDIA's openshell CLI $OPENSHELL_TAG (pinned sha256) at $OS"
}

as_gateway_admin() {  # run "$@" as the gateway's administrator, then switch that identity off again
  # A confidential client with only the openshell-admin role, enabled for these few calls and disabled after.
  # NVIDIA's CLI uses client credentials when OPENSHELL_OIDC_CLIENT_SECRET is set (commands/gateway.rs, login).
  install_cli
  # `compose up -d` returns before NVIDIA's gateway listens; a call before then fails with "tls handshake eof".
  local i; for i in $(seq 1 60); do
    openssl s_client -connect 127.0.0.1:8080 -servername "$NAME" </dev/null >/dev/null 2>&1 && break; sleep 2; done
  (( i < 60 )) || die "NVIDIA's gateway is not answering on 8080; see: sudo ./deploy/setup.sh compose logs gateway"
  grep -q " $NAME\$" /etc/hosts 2>/dev/null || getent hosts "$NAME" >/dev/null || echo "127.0.0.1 $NAME" >> /etc/hosts
  local setup_client_secret rc=0; setup_client_secret=$(kc setup-client on)
  # NVIDIA's gateway flags read these too; one kept by `sudo -E` would send the calls, and this sign-in, elsewhere.
  ( unset OPENSHELL_GATEWAY OPENSHELL_GATEWAY_ENDPOINT OPENSHELL_GATEWAY_INSECURE
    export OPENSHELL_OIDC_CLIENT_SECRET=$setup_client_secret SSL_CERT_FILE=$DESKS_STATE/tls/ca.crt HOME=$DESKS_STATE/openshell-admin
    install -d -m 0700 "$HOME"
    "$OS" gateway add "https://$NAME:8080" --name ekka-agent-desks --oidc-issuer "https://$NAME/realms/ekka-desks" \
      --oidc-client-id desks-setup --oidc-audience ekka-desks >/dev/null 2>&1 || true
    "$OS" gateway login ekka-agent-desks >/dev/null
    "$@"
  ) || rc=$?
  unset setup_client_secret; kc setup-client off >/dev/null
  rm -rf "$DESKS_STATE/openshell-admin"
  return $rc
}

# An install's own profile wins over the shipped one of the same name, and `up.sh profiles` never replaces it:
# $DESKS_STATE/providers.local/<name>.yaml. It decides how an agent signs in and which hosts its key may
# reach, so it must be a real file in a real folder, owned by whoever runs up.sh (root, under sudo), writable
# by no group and no other user. The public repository carries no such profile.
profile_source() {  # profile_source <name>: the profile file sync_profiles uses for <name>
  local own=$DESKS_STATE/providers.local/$1.yaml
  if [[ -e $own || -L $own ]]; then
    python3 - "$own" <<'PY' || die "$own is not safe to use; see the line above"
import os, stat, sys
own = sys.argv[1]
for path, want in ((os.path.dirname(own), stat.S_ISDIR), (own, stat.S_ISREG)):
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode) or not want(st.st_mode):
        sys.exit(f"   REFUSED: {path} must be a real {'folder' if want is stat.S_ISDIR else 'file'}, not a link")
    if st.st_uid not in (0, os.geteuid()):
        sys.exit(f"   REFUSED: {path} belongs to uid {st.st_uid}, not root. Fix: chown root {path}")
    if st.st_mode & 0o022:
        sys.exit(f"   REFUSED: {path} is writable by group or others ({st.st_mode & 0o777:o}). Fix: chmod go-w {path}")
PY
    echo "$own"
  else
    echo "providers/$1.yaml"
  fi
}

profile_names() {  # every profile this install puts in the gateway: the shipped ones and its own
  local file
  { for file in providers/*.yaml "$DESKS_STATE"/providers.local/*.yaml; do
      [[ -f $file ]] || continue
      basename "$file" .yaml
    done; } | sort -u
}

connect_cmd() {  # connect_cmd <provider>: a provider's one key, read from stdin only, set on this machine (D7)
  # The key is never an argument, never printed and never logged. It is read from a pipe (a terminal is refused, so
  # it never sits on a screen) into a variable no other process sees, and handed to NVIDIA's CLI alone, in the
  # environment variable the provider's profile names. An argument is never echoed back: it may be a key.
  local p key secret
  (( $# == 1 )) || die "usage: <key source> | sudo ./up.sh connect <provider>   (the key comes on stdin, never as an argument)"
  p=$1
  [[ -t 0 ]] && die "the key comes from a pipe, never typed at a terminal: <key source> | sudo ./up.sh connect <provider>"
  profile_names | grep -qxF -- "$p" || die "that is not a provider this install knows; they are: $(profile_names | tr '\n' ' ')"
  key=$(python3 tools/provider_connect.py key "$(profile_source "$p")" </dev/null) || exit 1
  IFS= read -r -d '' secret || true
  secret=${secret%$'\n'}
  [[ -n $secret && $secret != *[[:space:]]* && ${#secret} -le 8192 ]] \
    || { secret=; die "stdin must carry exactly one key: no spaces, no second line; nothing was changed"; }
  OPENSHELL_WORKSPACE=desks as_gateway_admin connect_provider "$p" "$key" \
    || { secret=; die "the key for $p was not set; nothing else was changed"; }
  secret=
  ok "$p is connected: its key is in the gateway"
  # The desks grant names each provider this gateway holds, by its digest (desk_provider_args). Refreshed now, add
  # first and revoke after, or a desk naming $p is refused CONSTRAINT_PROVIDERS until the next `up.sh server`.
  # A refusal there must not read as "connect failed": the key IS set by then (regression review).
  ( step_desk_grant ) || die "the key for $p IS set; the desks grant was not refreshed, so a desk naming $p is refused until it is: sudo ./up.sh server $NAME"
}

connect_provider() {  # inside as_gateway_admin: create the provider with its key, or replace the key it has
  local p=$1 key=$2 log=$DESKS_STATE/openshell-admin.log
  local -a cmd
  if "$OS" -g ekka-agent-desks provider get "$p" >/dev/null 2>&1; then
    cmd=(provider update "$p" --credential "$key")
  else
    cmd=(provider create --name "$p" --type "$p" --global-profile --credential "$key")
  fi
  python3 tools/openshell_allow.py "$log" "${cmd[@]}" </dev/null || return 1   # the same allow-list and log as E8
  ( export "$key=$secret"; "$OS" -g ekka-agent-desks "${cmd[@]}" </dev/null >/dev/null )
}

sync_profiles() {  # the gateway's profiles = providers/, or this install's own; imported once and updated after
  local p v t src from
  # Every profile's file is checked BEFORE any is imported: an unsafe local profile stops the whole sync,
  # so the gateway is never left half updated.
  for p in $(profile_names); do profile_source "$p" >/dev/null || exit 1; done
  for p in $(profile_names); do
    src=$(profile_source "$p") || exit 1
    if [[ $src == providers/* ]]; then from="providers/"; else from="$src (this install's own)"; fi
    if ! v=$("$OS" -g ekka-agent-desks provider profile export --global "$p" 2>/dev/null | sed -n 's/^resource_version: *//p'); then v=; fi
    if [[ -z $v ]]; then
      "$OS" -g ekka-agent-desks provider profile import --global --file "$src" >/dev/null && ok "profile $p imported from $from"
    else
      t=$(mktemp --suffix .yaml); sed "0,/^id: .*/s//&\nresource_version: $v/" "$src" > "$t"
      "$OS" -g ekka-agent-desks provider profile update --global --file "$t" "$p" >/dev/null && ok "profile $p updated from $from"
      rm -f "$t"
    fi
  done
}

admin_setup() {
  "$OS" -g ekka-agent-desks workspace create --name desks 2>&1 | grep -v -i "already exists" || true
  "$OS" -g ekka-agent-desks workspace member add --workspace desks --subject "$1" --role user 2>&1 | grep -v -i "already" || true
  sync_profiles
}

# The backend adapter's one configuration file (ADK-E3, @ekka-ai/contracts backend/adapter-config.v1). Only the
# Enclave's organization, node and Govern URL lines leave its container; enclave.env holds a secret too, and that
# line never does. The file holds no credential, and compose mounts it read-only into the adapter (ADK-E1).
write_adapter_config() {
  enclave_home_read "$(env_dir)/enclave.env" | grep -E "^EKKA_(TENANT_ID|NODE_ID|GOVERN_URL)=" \
    | python3 tools/adapter_config.py write "$DESKS_STATE/backend/config.json" "$NAME" \
    || die "the backend adapter's configuration was not written; see the line above"
}

step_desk_admin() {  # desk administration through EKKA: only once up.env runs the backend adapter (ADK-E6)
  grep -q '^EKKA_BACKEND_IMAGE=.' "$DESKS_STATE/up.env" 2>/dev/null || return 0
  say "desk administration through EKKA"
  connect_desk_admin
  write_adapter_config
  # Only now, with its configuration in place, the adapter's profile goes on and the adapter starts.
  grep -q '^COMPOSE_PROFILES=desk-admin$' "$DESKS_STATE/up.env" || echo "COMPOSE_PROFILES=desk-admin" >> "$DESKS_STATE/up.env"
  sed -i 's/^DESKS_STACK_SERVICES=.*/DESKS_STACK_SERVICES="keycloak-db keycloak edge gateway ekka-backend"/' "$DESKS_STATE/up.env"
  (cd deploy && bash setup.sh backend-preflight)   # its files are real files, whatever this Compose does
  # The adapter is one more long-lived service of the supervised stack: the unit reads the list from up.env.
  systemctl restart ekka-desks || die "the desks stack did not restart with the adapter; see: journalctl -u ekka-desks"
  local i; for i in $(seq 1 60); do
    [[ $(docker inspect -f '{{.State.Health.Status}}' ekka-desks-ekka-backend-1 2>/dev/null) == healthy ]] && break; sleep 2; done
  (( i < 60 )) || die "the backend adapter did not become healthy; see: sudo ./deploy/setup.sh compose logs ekka-backend"
  ok "the backend adapter is running, with its configuration"
}

step_admin() {
  is_done admin && { ok "desk workspace, Enclave membership and profiles: done"; return; }
  say "the gateway's one-time setup (workspace, the Enclave's membership, provider profiles)"
  local sub; sub=$(cd deploy && bash setup.sh enclave-subject)
  as_gateway_admin admin_setup "$sub" || die "the gateway's setup did not finish; desks-setup is disabled again. Re-run to retry"
  done_ admin
  ok "workspace desks, the Enclave as a member, the profiles in providers/; desks-setup disabled again"
}

step_people_scopes() {  # people's sign-ins read-only (ADK-E2): every run, so an installed box migrates on upgrade
  say "people's desk sign-ins: read-only"
  (cd deploy && bash setup.sh people-scopes) || die "people's sign-ins could not be checked; re-run to retry"
}

wait_gateway() {  # NVIDIA's gateway answers TLS on this machine's 8080, where the native Enclave reaches it
  local i; for i in $(seq 1 60); do
    openssl s_client -connect 127.0.0.1:8080 -servername "$NAME" </dev/null >/dev/null 2>&1 && return 0; sleep 2; done
  die "NVIDIA's gateway is not answering on 127.0.0.1:8080; see: journalctl -u ekka-desks, sudo ./deploy/setup.sh compose logs gateway"
}

wait_enclave() {  # the Enclave's control socket answers (ekka enclave status), as its own user
  local i; for i in $(seq 1 30); do ekka_in enclave status >/dev/null 2>&1 && return 0; sleep 2; done
  die "the Enclave did not come up; see: journalctl -u ekka"
}

render() {  # render <template> <dest> <mode> KEY=VALUE...: @@KEY@@ replaced; root-owned
  local tpl=$1 dest=$2 mode=$3; shift 3
  local text; text=$(cat "$tpl")
  local kv; for kv in "$@"; do text=${text//"@@${kv%%=*}@@"/${kv#*=}}; done
  printf '%s\n' "$text" > "$dest.new" && chown root:root "$dest.new" && chmod "$mode" "$dest.new" && mv -f "$dest.new" "$dest"
}

snapshot_stack() {  # a ROOT-OWNED copy of deploy/ and tools/ the supervisor unit runs from; never a person's checkout
  rm -rf "$SNAPSHOT.new"; install -d -m 0755 "$SNAPSHOT.new"
  cp -a deploy tools "$SNAPSHOT.new/"
  chown -R root:root "$SNAPSHOT.new"; chmod -R u+rwX,go+rX,go-w "$SNAPSHOT.new"
  rm -rf "$SNAPSHOT.old"; [[ -d $SNAPSHOT ]] && mv "$SNAPSHOT" "$SNAPSHOT.old"; mv "$SNAPSHOT.new" "$SNAPSHOT"; rm -rf "$SNAPSHOT.old"
  install -D -m 0755 -o root -g root deploy/host/libexec/wait-interceptor /usr/local/libexec/ekka-desks/wait-interceptor
}

enclave_units() {  # the Enclave's unit from EKKA's own generator, this setup's drop-in, and the stack's unit
  # `ekka enclave install-service` writes /etc/systemd/system/ekka.service from the ENROLLED home and its owner, and
  # enables it. Run with the Enclave's home named, as root (it writes the unit), after enrollment.
  # A clean environment (no EKKA_HOME_OVERRIDE, no proxy); `--no-start`: this step enables and starts the units itself. The
  # generator writes the ENVIRONMENT'S state folder as EKKA_HOME (prod: the home; staging: home/staging); the drop-in below
  # pins EKKA_HOME back to the base home, or a non-prod daemon would look in home/staging/staging (Codex review of #4).
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/root EKKA_HOME=$ENCLAVE_HOME EKKA_ENV=${EKKA_ENV:-prod} /usr/local/bin/ekka enclave install-service --no-start >"$DESKS_STATE/install-service.log" 2>&1 \
    || die "ekka enclave install-service refused; see $DESKS_STATE/install-service.log"
  install -d -m 0755 /etc/systemd/system/ekka.service.d
  render deploy/host/ekka.service.d/desks.conf.template /etc/systemd/system/ekka.service.d/desks.conf 0644 "EKKA_ENV=${EKKA_ENV:-prod}" "ENCLAVE_HOME=$ENCLAVE_HOME"
  render deploy/host/ekka-desks.service.template /etc/systemd/system/ekka-desks.service 0644 "SNAPSHOT=$SNAPSHOT" "DESKS_STATE=$DESKS_STATE"
  systemctl daemon-reload
  systemctl enable ekka ekka-desks >/dev/null 2>&1 || die "the units could not be enabled"
}

# nginx reads its config only when it starts, and `git pull` replaces deploy/edge/nginx.conf with a new file the
# running edge never sees, so an upgrade that changes a route would keep serving the old one. The edge is
# restarted when its config differs from the one it was last started with, and only then: a brief pause in
# sign-ins, in the upgrade's window.
edge_conf_sha() { sha256sum <deploy/edge/nginx.conf | cut -c1-64; }
edge_current() {
  local want; want=$(edge_conf_sha)
  [[ $want == "$(cat "$DESKS_STATE/edge-conf.sha" 2>/dev/null)" ]] && return 0
  (cd deploy && bash setup.sh compose restart edge >/dev/null) \
    || die "the edge did not restart on its new config; see: sudo ./deploy/setup.sh compose logs edge"
  echo "$want" >"$DESKS_STATE/edge-conf.sha"
  ok "the edge restarted on its new config"
}

step_start() {
  (cd deploy && DESKS_STATE=$DESKS_STATE bash ./certs.sh bundle >/dev/null) || die "the gateway's CA bundle could not be built"   # system roots + desks CA, and /etc/ekka/desks-ca.crt
  # A pin bump applies whole or not at all: the supervisor first, then nothing starts the gateway able to hand
  # desk credentials out.
  (cd deploy && bash setup.sh supervisor >/dev/null) || die "the supervisor binary could not be put in place"
  (cd deploy && bash setup.sh preflight) || die "the gateway preflight refused (above); nothing was started"
  (cd deploy && bash setup.sh backend-preflight)   # with desk-admin on, the stack starts the adapter too
  local stack_sha; stack_sha=$( { cat deploy/compose.yml deploy/pins.json deploy/edge/nginx.conf deploy/host/ekka-desks.service.template "$DESKS_STATE/up.env"; } | sha256sum | cut -c1-16)
  if is_done started; then
    snapshot_stack; enclave_units
    rm -f "$DESKS_STATE/stack.hold"; systemctl reset-failed ekka-desks >/dev/null 2>&1 || true   # a first install that died while held
    # The Enclave: the installed release runs, or it is restarted onto it (an upgrade is the binary first, then this).
    systemctl start ekka >/dev/null 2>&1 || die "the Enclave did not start; see: journalctl -u ekka"
    wait_enclave
    local running; running=$(ekka_in enclave status --json 2>/dev/null | python3 -c 'import json,sys; t=sys.stdin.read(); i=t.find("{"); print(json.JSONDecoder().raw_decode(t[i:])[0].get("runner_version","") if i>=0 else "")' 2>/dev/null || true)
    if [[ -n $running && $running != "$EKKA_VERSION" ]]; then
      systemctl restart ekka || die "the Enclave did not restart onto $EKKA_VERSION; see: journalctl -u ekka"
      wait_enclave; ok "Enclave restarted onto $EKKA_VERSION (was $running)"
    fi
    # The stack: a changed compose, pin, edge config or settings restarts it (a pause for desks); otherwise only started.
    if [[ $stack_sha != "$(cat "$DESKS_STATE/stack.sha" 2>/dev/null)" ]]; then
      systemctl restart ekka-desks || die "the desks stack did not restart; see: journalctl -u ekka-desks"
      echo "$stack_sha" >"$DESKS_STATE/stack.sha"; ok "the desks stack restarted on its new configuration"
    else
      systemctl start ekka-desks >/dev/null 2>&1 || die "the desks stack did not start; see: journalctl -u ekka-desks"
    fi
    wait_gateway
    edge_current
    ok "Enclave $EKKA_VERSION (systemd: ekka) and the desks stack (systemd: ekka-desks) are running"; return; fi
  say "starting the Enclave and NVIDIA's gateway"
  local env=${EKKA_ENV:-prod}
  # The Enclave's unit Wants the stack, and the stack's gateway cannot start before the Enclave holds its client secret
  # (measured: the stack looped on a gateway that could not sign in, and knocked Keycloak over while the config and the
  # secret were being fetched). So a HOLD file keeps the stack unit from starting for the whole first start (its first
  # ExecStartPre tests for it), and the hold is removed once the secret is in the vault. The sign-in service is brought up
  # by itself where a step needs it (setup.sh wait_keycloak).
  touch "$DESKS_STATE/stack.hold"
  systemctl stop ekka-desks >/dev/null 2>&1 || true; systemctl reset-failed ekka-desks >/dev/null 2>&1 || true
  (cd deploy && bash setup.sh enclave-config "$env" desks)
  snapshot_stack; enclave_units
  # `ekka secret put` talks to a running Enclave, so: start it, store the secret, restart it so it reads the secret.
  systemctl restart ekka || die "the Enclave did not start; see: journalctl -u ekka"
  wait_enclave
  (cd deploy && bash setup.sh enclave-secret "$env")
  systemctl restart ekka || die "the Enclave did not restart; see: journalctl -u ekka"
  wait_enclave
  rm -f "$DESKS_STATE/stack.hold"; systemctl reset-failed ekka-desks >/dev/null 2>&1 || true
  systemctl restart ekka-desks || die "the desks stack did not start; see: journalctl -u ekka-desks"
  wait_gateway
  echo "$stack_sha" >"$DESKS_STATE/stack.sha"
  edge_conf_sha >"$DESKS_STATE/edge-conf.sha"   # the edge was just started on this config
  done_ started
  ok "Enclave $EKKA_VERSION (systemd: ekka) and the desks stack (systemd: ekka-desks) are running"
}

# The setup and ask plans for one desk, owned by the desks agent: create_desk_plans <desk> <agent id>.
# A plan names its desk as a literal resource, so each desk has its own copy, made from ekka/plans/ by
# tools/desk_plans.py (example.desk.ask.<deskName>.v1). Created every time: EKKA never refuses an identical plan,
# it makes a patch version ("nothing it promises changed", measured on staging), and a changed template becomes
# the newer version a bare plan code runs. ANY refusal stops here with EKKA's reason: treating one as "kept"
# would leave this machine running the old plan after an upgrade it never got.
# Shared by default: every member of the organization may run them (the desk installation checks), so a second
# member asks a desk without the administrator. `private` keeps them the administrator's alone.
create_desk_plans() {
  local desk=$1 agent=$2 sharing=${3:-shared} dir=$DESKS_STATE/plans made
  install -d -m 0755 "$dir"
  made=$(python3 tools/desk_plans.py "$desk" "$dir" "$agent") || die "no plans for desk '$desk'; see the line above"
  install_plans "$made" "$sharing"
}

# The seven desk administration plans, owned by the desks agent: create_desk_admin_plans <api gate> <agent id>.
# `ekka desk create`, `stop`, `status` and the rest each run one (ekka.desk.<op>.v1), and creating them is what
# declares api.write on the desks agent: before they exist, EKKA refuses the desks grant ("This agent does not
# declare 'api.write'"). The desk is an input, so one set serves every desk. Created every time, as above.
create_desk_admin_plans() {
  local gate=$1 agent=$2 dir=$DESKS_STATE/plans made
  install -d -m 0755 "$dir"
  made=$(python3 tools/desk_plans.py --admin "$gate" "$dir" "$agent") || die "no desk administration plans; see the line above"
  install_plans "$made" private   # never offered: only the administrator changes desks
}

# Copies each plan tools/desk_plans.py wrote into the Enclave and creates it there:
# install_plans "<path> <code>"... shared|private. A shared plan is offered to the organization and accepted (share_plan).
install_plans() {
  local sharing=${2:-private}
  local path code out identity rung
  # Every command in this loop gets /dev/null as input: otherwise the first one swallows the rest of the list.
  while read -r path code; do
    enclave_home_file "plans/$(basename "$path")" 0600 < "$path"   # a file the Enclave's user may read
    out=$(ekka_in plan create "$ENCLAVE_HOME/plans/$(basename "$path")" 2>&1 </dev/null) \
      || die "the plan $code was not created: $(grep -v -E '^\s*$|STAGING|rehearsal' <<<"$out" | head -3 | tr '\n' ' ')
   see it again: sudo -i; ekka plan create $ENCLAVE_HOME/plans/$(basename "$path")"
    ok "plan $(grep -o -E "${code//./\\.}@[0-9.]+" <<<"$out" | head -1 || echo "$code"), owned by $DESKS_AGENT"
    if [[ $sharing == shared ]]; then
      # `ekka plan create` prints "✓ Plan created: <identity> · <rung>", the rung being EKKA's own answer.
      identity=$(grep -o -E "Plan created: ([a-z0-9-]+\\.)?${code//./\\.}@[0-9.]+" <<<"$out" | head -1 | sed 's/^Plan created: //' || true)
      rung=$(grep -o -E "Plan created: ([a-z0-9-]+\\.)?${code//./\\.}@[0-9.]+ · [a-z]+" <<<"$out" | head -1 | sed 's/.* · //' || true)
      share_plan "$identity" "$rung"
    fi
  done <<<"$1"
}

# Offer one plan version to the organization and accept it, so every member may run it: share_plan <identity> <rung>.
# ⛔ ONLY A VERSION EKKA JUST REPORTED `unlisted`. `ekka plan publish` on a version already at `org` offers it to
# EKKA's PUBLIC catalog, and up.sh runs again on every upgrade. So nothing else is ever offered, and the approval
# runs only when EKKA answered that the offer is for the organization.
share_plan() {
  local identity=$1 rung=$2 out
  if [[ -z $identity || $rung != unlisted ]]; then
    ok "plan ${identity:-?} is ${rung:-of unknown standing}: not offered (an offer of an org plan would go to EKKA's public catalog)"
    return 0
  fi
  out=$(ekka_in plan publish "$identity" 2>&1 </dev/null) \
    || die "the plan $identity was not offered to the organization: $(grep -v -E '^\s*$|STAGING|rehearsal' <<<"$out" | head -2 | tr '\n' ' ')"
  grep -q "requested for org" <<<"$out" \
    || die "the plan $identity was offered somewhere other than this organization, so it was not approved: $(grep -v -E '^\s*$' <<<"$out" | head -2 | tr '\n' ' ')"
  out=$(ekka_in plan approve "$identity" 2>&1 </dev/null) \
    || die "the plan $identity was offered but not approved: $(grep -v -E '^\s*$|STAGING|rehearsal' <<<"$out" | head -2 | tr '\n' ' ')"
  grep -q "is now org" <<<"$out" || die "the plan $identity was approved to something other than this organization: $(head -1 <<<"$out")"
  ok "plan $identity: every member of this organization may run it"
}

desks_agent_id() {
  ekka_in agent list --json 2>/dev/null \
    | DESKS_AGENT=$DESKS_AGENT python3 -c '
import json, os, sys
t = sys.stdin.read(); i = t.find("{")
# The first JSON document only: the ekka CLI prints a tier banner around it.
d = json.JSONDecoder().raw_decode(t[i:])[0] if i >= 0 else {"agents": []}
a = [x["agent_id"] for x in d["agents"] if x["agent_code"] == os.environ["DESKS_AGENT"]]
print(a[0] if a else "")' 2>/dev/null
}

step_grant() {  # a desks-only agent owns the desk plans and may work on every desk in this workspace
  is_done grant && { ok "the $DESKS_AGENT agent may work on this server's desks"; return; }
  say "a desks agent for your organization ($DESKS_AGENT)"
  # Its own agent, not the organization's core one: widening a published core agent is a new major version
  # that everyone who installed it must approve again, and desk work should not ride on other plans' agent.
  local ids id gate p
  ekka_in agent create "$DESKS_AGENT" --name "Desks" >/dev/null 2>&1 || true   # an agent that exists is kept
  ids=$( { ekka_in enclave status --json; echo; ekka_in agent list --json; echo; ekka_in gate list --json; } 2>/dev/null | DESKS_AGENT=$DESKS_AGENT python3 -c '
import json, os, sys
docs, buf = [], ""
for line in sys.stdin.read().splitlines():
    if not buf and not line.lstrip().startswith(("{", "[")): continue   # banners and blank lines
    buf += line + "\n"
    try: docs.append(json.loads(buf)); buf = ""
    except ValueError: pass
status, agents, gates = (d for d in docs[:3])
node = status["node_id"]
agent = [a["agent_id"] for a in agents["agents"] if a["agent_code"] == os.environ["DESKS_AGENT"]]
job = [g["instance_id"] for g in gates if g.get("connector_type") == "job" and g.get("node_id") == node]
print(agent[0] if agent else "", job[0] if job else "")') || true
  read -r id gate <<<"$ids"
  [[ -n $id && -n $gate ]] || die "could not find agent $DESKS_AGENT or this Enclave's job gate; see: sudo -i; ekka agent list"
  # The example plans for code-desk, owned by that agent (plan.agent): creating them declares job.run on it,
  # and only on it. Another desk gets its own: sudo ./up.sh desk-plans <desk>.
  create_desk_plans code-desk "$id"
  if ekka_in gate grant list 2>/dev/null | grep -q "job.run $gate/desks/\*  agent $id "; then
    ok "$DESKS_AGENT already may work on desks/* (kept)"
  else
    ekka_in gate grant add --agent "$DESKS_AGENT" --type job --instance "$gate" --resource 'desks/*' --capability job.run --no-fingerprint \
      | grep -E "Granted|take it back" || die "the grant was refused; is your organization's edition allowed to run jobs (job.run)?"
  fi
  done_ grant
}

step_agent_open() {  # every member may RUN the desks agent's shared plans; writing plans under it stays listed
  is_done agent_open && { ok "every member may run the plans of $DESKS_AGENT"; return; }
  # Since Govern migration 233 a new agent is `listed`: only its creator, owners and admins may run its plans, so
  # a member's run of a shared desk plan (create_desk_plans offers them to the organization) is refused. Use only:
  # a member still cannot RUN an unshared plan (the admin plans are never offered, and each requires the admin
  # role), and nobody writes a plan under this agent unless an owner appoints them (`--as author`).
  # The floor (DESK_ADMIN_MIN_EKKA, 0.1.124) has `ekka agent allow`; the read-back below
  # stops the install on any EKKA where it did not take, whatever the reason.
  local out mode
  out=$(ekka_in agent allow "$DESKS_AGENT" --everyone --as use 2>&1 </dev/null) \
    || die "EKKA refused to let every member run the plans of $DESKS_AGENT: $(grep -v -E '^\s*$|STAGING|rehearsal' <<<"$out" | head -2 | tr '\n' ' ')"
  # Confirmed from Govern's own record, not from the sentence `allow` prints: `agent show --json` names the mode
  # as access.use, and a mode Govern could not read is "unknown (refused)", never open.
  mode=$(ekka_in agent show "$DESKS_AGENT" --json 2>/dev/null </dev/null | python3 -c '
import json, sys
t = sys.stdin.read(); i = t.find("{")
# The first JSON document only: the ekka CLI prints a tier banner around it.
d = json.JSONDecoder().raw_decode(t[i:])[0] if i >= 0 else {}
print((d.get("access") or {}).get("use", ""))' 2>/dev/null) || true
  [[ $mode == open ]] || die "EKKA accepted the change, but $DESKS_AGENT reads as '${mode:-nothing}', not open: sudo ./up.sh ekka agent show $DESKS_AGENT"
  ok "every member may run the plans of $DESKS_AGENT; writing plans under it still needs an owner (--as author)"
  done_ agent_open
}

# The provider digests the desks grant holds: `--provider <name>=<digest>` for each provider this gateway holds,
# computed by the adapter from the gateway's own records (ADK-E4b, ekka-backend `digest provider`). None yet: a
# grant with no provider lets a desk attach none, which is the closed side.
desk_provider_args() {  # --provider <name>=<digest>, one per provider this gateway holds (ADK-E4b)
  # The adapter reads each provider from the gateway itself, `digest provider --from-gateway`, with the same calls
  # and the same function it enforces with, so the grant and the check share one source. It runs as the adapter's
  # own service (its image, mounts, user and network) and signs in as the desk-admin client, whose secret goes from
  # Keycloak to its stdin and nowhere else. A kit provider is named after its profile (`up.sh connect`), so each
  # profile name is asked: PROVIDER_NOT_FOUND means it is not connected here and is left out (a create naming it is
  # then refused CONSTRAINT_PROVIDERS, closed). Any other refusal stops the grant.
  local p out err why
  err=$(mktemp)
  (cd deploy && bash setup.sh backend-preflight --always) || return 1   # the adapter runs by name below
  for p in $(profile_names); do
    if out=$( { kc admin-client on | (cd deploy && docker compose -f compose.yml --env-file "$DESKS_STATE/up.env" \
          run --rm -T --no-deps ekka-backend digest provider --from-gateway "$p" --config /etc/ekka-backend/config.json); } 2>"$err"); then
      [[ $out =~ ^sha256:[0-9a-f]{64}$ ]] || { rm -f "$err"; echo "provider $p's digest is not sha256:<64 hex>" >&2; return 1; }
      printf -- '--provider %s=%s\n' "$p" "$out"
    elif grep -q '^PROVIDER_NOT_FOUND' "$err"; then
      continue
    else
      why=$(grep -m1 -E '^[A-Z][A-Z_]+' "$err" | cut -c1-200); rm -f "$err"
      echo "the adapter could not read provider $p (${why:-no reason given})" >&2; return 1
    fi
  done
  rm -f "$err"
}

step_desk_grant() {  # the desks grant with its limits (ADK-E4): only once up.env runs the backend adapter
  grep -q '^EKKA_BACKEND_IMAGE=.' "$DESKS_STATE/up.env" 2>/dev/null || return 0
  say "the desks grant, with its limits"
  local image desk_id pdig ids id gate limits=$DESKS_STATE/backend/desks-grant.json decision g
  image=$(sed -n 's/^EKKA_BACKEND_IMAGE=//p' "$DESKS_STATE/up.env")
  # The content ID of the image built from the pinned EKKA image repository.
  desk_id=$(docker image inspect --format '{{.Id}}' ekka-desk:local 2>/dev/null) || die "the desk image is not built; run again"
  # The reviewed baseline's digest, by the adapter's own code from its pinned image, with no network.
  pdig=$(docker run --rm -i --network none "$image" digest policy - < desk/policy.json) \
    || die "the backend adapter could not digest desk/policy.json; no grant was changed"
  ids=$( { ekka_in enclave status --json; echo; ekka_in agent list --json; echo; ekka_in gate list --json; } 2>/dev/null | DESKS_AGENT=$DESKS_AGENT python3 -c '
import json, os, sys
docs, buf = [], ""
for line in sys.stdin.read().splitlines():
    if not buf and not line.lstrip().startswith(("{", "[")): continue
    buf += line + "\n"
    try: docs.append(json.loads(buf)); buf = ""
    except ValueError: pass
status, agents, gates = (d for d in docs[:3])
node = status["node_id"]
agent = [a["agent_id"] for a in agents["agents"] if a["agent_code"] == os.environ["DESKS_AGENT"]]
api = [g["instance_id"] for g in gates if g.get("connector_type") == "api" and g.get("node_id") == node]
print(agent[0] if agent else "", api[0] if api else "")') || true
  read -r id gate <<<"$ids"
  [[ -n $id && -n $gate ]] || die "could not find agent $DESKS_AGENT or this Enclave's API gate; no grant was changed"
  # The plans first, every run: EKKA refuses the grant to an agent no plan declares api.write on, and a box whose
  # grant is already in place still gets an upgrade's plans (the "kept" return below would otherwise skip them).
  create_desk_admin_plans "$gate" "$id"
  install -d -m 0755 "$(dirname "$limits")"
  # Collected FIRST, so a refusal stops here: inside $(...) on the limits line a failure would be lost.
  local providers; providers=$(desk_provider_args) || die "the providers' digests could not be read; no grant was changed"
  # shellcheck disable=SC2086  # one --provider word and one name=digest word per provider
  python3 tools/desk_grant.py limits --image-id "$desk_id" --policy-digest "$pdig" $providers > "$limits.new" \
    || die "the desks grant's limits could not be written; no grant was changed"
  mv "$limits.new" "$limits"
  decision=$(ekka_in gate grant list --json | python3 tools/desk_grant.py decide "$id" "$gate" "$limits") \
    || die "could not read this organization's grants; no grant was changed"
  if [[ $decision == keep* ]]; then ok "the desks grant already holds these limits (kept)"; return; fi
  # ADD FIRST, revoke after: desks are never left without a grant. The limits reach the CLI in the Enclave's
  # container on stdin, as a file only inside it, removed after.
  local grant_out
  # Drain the command before checking its words: grep -q can close stdout early,
  # making a successful CLI write fail with SIGPIPE under pipefail.
  enclave_home_file desks-grant.json 0600 < "$limits"   # the limits, as a file the Enclave's user may read
  grant_out=$(ekka_in gate grant add --agent "$DESKS_AGENT" --type api --instance "$gate" --resource apis/desks \
      --capability api.write --ttl 31536000 --constraint "$ENCLAVE_HOME/desks-grant.json") \
    || { rm -f "$ENCLAVE_HOME/desks-grant.json"; die "the desks grant outcome could not be confirmed; the previous grant, if any, is kept"; }
  rm -f "$ENCLAVE_HOME/desks-grant.json"
  grep -q "Granted" <<<"$grant_out" \
    || die "the desks grant outcome could not be confirmed; the previous grant, if any, is kept"
  for g in ${decision#replace}; do
    [[ $decision == replace* ]] || break
    ekka_in gate grant revoke "$g" >/dev/null || die "the new desks grant is in place, but $g was not revoked"
  done
  ok "the $DESKS_AGENT agent may administer desks within the limits in $limits"
}

configure_enclave_cli() {
  # Public defaults only: never import a person's sign-in, token or client secret.
  enclave_home_file desks-ca.pem 0644 < "$DESKS_STATE/people/desks-ca.pem" \
    && enclave_home_file desk-policy.json 0644 < "$DESKS_STATE/people/desk-policy.json" \
    && enclave_home_file backend.json 0600 < "$DESKS_STATE/enclave-cli/backend.json" \
    && ekka_in desk backend set "$ENCLAVE_HOME/backend.json"
}

server_ekka() {
  local a
  for a in "$@"; do
    case $a in
      --token|--token=*|--access-token|--access-token=*|--refresh-token|--refresh-token=*|--id-token|--id-token=*|--password|--password=*|--client-secret|--client-secret=*|--api-key|--api-key=*|--secret|--secret=*)
        die "do not pass a credential as an argument; use the command's sign-in or hidden-input flow" ;;
    esac
  done
  enclave_ekka "$@"
}

step_people() {
  (cd deploy && bash setup.sh people >/dev/null)
  configure_enclave_cli || die "the Enclave's public desk defaults could not be set; run the install again"
  local keys="       <your $AGENT key> | sudo ./up.sh connect $AGENT
       Optional, for private repositories: <a read-only GitHub Contents token> | sudo ./up.sh connect github" desk="ekka desk create code-desk --provider $AGENT   # add --provider github for private repositories"
  # The browser's half of step 6, from the one file that writes it: printed, never run.
  local trust; trust=$(bash "$DESKS_STATE/people/trust-ca.sh" --all 2>&1 | sed 's/^/     /') || trust="     see docs/OPERATIONS.md, Certificates"
  printf '\n\033[1m-- installed --\033[0m\n%s\n' "The server is ready. Next (docs/SERVER.md, steps 5 to 8):
  5. Add the people who run your agents:   sudo ./up.sh invite <username> <email>
  6. Each of them, on their own computer: copy $DESKS_STATE/people/, run ./install.sh desks, then ekka desk login.
     install.sh ends with the one command that makes their browser trust the sign-in page:
$trust
  7. Connect the company's keys, once, on this server (each from a pipe, never an argument):
$keys
  8. On this server, as root (sudo -i): ekka desk login (read-only gateway sign-in); then: $desk
The Enclave registers each desk by itself, and your desks agent may work on it.
For any desk but code-desk, give it its own plans once:   sudo ./up.sh desk-plans <desk>
Then: docs/SET-UP-YOUR-DESK.md and docs/ASK-YOUR-CODE.md."
}

case "${1:-}" in
  server) MODE=server; NAME=${2:-}; [[ $# == 2 && $NAME =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]] || die "usage: ./up.sh server <dns-name>"; MODE_ARGS="server $NAME" ;;
  status)
    ls -1 "$DONE" 2>/dev/null | sed 's/^/done: /' || echo "nothing done yet"
    if is_done started; then
      echo "ekka (the Enclave): $(systemctl is-active ekka 2>/dev/null)   ekka-desks (the stack): $(systemctl is-active ekka-desks 2>/dev/null)"
      [[ -S /run/ekka/openshell-interceptor.sock ]] && echo "interceptor socket: present" || { echo "interceptor socket: MISSING"; exit 1; }
      docker ps --format '  {{.Names}}: {{.Status}}' --filter label=com.docker.compose.project=ekka-desks
      [[ $(systemctl is-active ekka) == active && $(systemctl is-active ekka-desks) == active ]] || exit 1
    fi
    exit 0 ;;
  invite)  # one more person who uses desks: sudo ./up.sh invite <username> <email>
    [[ $(id -u) == 0 ]] || die "run with sudo"
    NAME=$(sed -n 's/^DESKS_NAME=//p' "$DESKS_STATE/up.env" 2>/dev/null); [[ -n $NAME ]] || die "not installed yet"
    who=${2:-}; mail=${3:-}
    [[ $who =~ ^[a-z0-9][a-z0-9._-]{1,40}$ && $mail == *@*.* ]] || die "usage: sudo ./up.sh invite <username> <email>"
    pf=$DESKS_STATE/secrets/people/$who-password; install -d -m 0700 "$(dirname "$pf")"
    [[ -e $pf ]] && die "$who was invited already; their one-time password: sudo cat $pf"
    ( umask 077; openssl rand -base64 24 | tr -d '\n' > "$pf" )
    sub=$( (cd deploy && KC_URL=https://127.0.0.1:8443 KC_CA=$DESKS_STATE/tls/ca.crt KC_ADMIN_USER=desks-bootstrap \
      KC_ADMIN_PASSWORD_FILE=$DESKS_STATE/secrets/keycloak-admin-password KC_PERSON_PASSWORD_FILE=$pf \
      python3 keycloak/apply_realm.py person "$who" "$mail") | tail -1) || { rm -f "$pf"; die "the sign-in account was not created"; }
    add_member() { "$OS" -g ekka-agent-desks workspace member add --workspace desks --subject "$1" --role user; }
    as_gateway_admin add_member "$sub" >/dev/null || die "$who can sign in but is not in the desk workspace yet; run invite again after fixing the gateway"
    ok "$who ($mail) may use desks"
    echo "   Give them: the people bundle ($DESKS_STATE/people/) and their one-time password, changed at first sign-in:"
    echo "     sudo cat $pf"
    echo "   Read them this fingerprint, so they can check the CA in the bundle before their browser trusts it:"
    echo "     $(openssl x509 -in "$DESKS_STATE/people/desks-ca.pem" -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2)"
    exit 0 ;;
  revoke)  # that person taken off again: sudo ./up.sh revoke <username>. Running it twice changes nothing.
    [[ $(id -u) == 0 ]] || die "run with sudo"
    NAME=$(sed -n 's/^DESKS_NAME=//p' "$DESKS_STATE/up.env" 2>/dev/null); [[ -n $NAME ]] || die "not installed yet"
    who=${2:-}
    [[ $# == 2 && $who =~ ^[a-z0-9][a-z0-9._-]{1,40}$ ]] || die "usage: sudo ./up.sh revoke <username>"
    # Keycloak first: the sign-in is disabled and every session ended, so the person cannot start a new one while
    # the gateway is changed. It refuses a name that is not here and an administrator, changing nothing.
    sub=$( (cd deploy && KC_URL=https://127.0.0.1:8443 KC_CA=$DESKS_STATE/tls/ca.crt KC_ADMIN_USER=desks-bootstrap \
      KC_ADMIN_PASSWORD_FILE=$DESKS_STATE/secrets/keycloak-admin-password python3 keycloak/apply_realm.py revoke "$who") \
      | tail -1) || die "$who was not revoked; see the line above"
    [[ $sub =~ ^[0-9a-f-]{36}$ ]] || die "Keycloak did not name $who's id; nothing more was changed"
    remove_member() {  # removed, or already not a member. A failed remove counts as done only when a list that
      # ANSWERED does not name the person: a list that fails is never read as "not a member".
      local members
      "$OS" -g ekka-agent-desks workspace member remove --workspace desks --subject "$1" >/dev/null 2>&1 && return 0
      members=$("$OS" -g ekka-agent-desks workspace member list --workspace desks --page-size 10000 -o json) || return 1
      ! grep -qF -- "$1" <<<"$members"
    }
    as_gateway_admin remove_member "$sub" \
      || die "$who can no longer sign in, but may still be in the desk workspace; run revoke again after fixing the gateway"
    rm -f "$DESKS_STATE/secrets/people/$who-password"
    ok "$who may no longer use desks: their sign-in is disabled, every session ended, and they are out of the desk workspace"
    exit 0 ;;
  desks)  # one unfinished desk change, settled on this machine: sudo ./up.sh desks settle <desk> --operation <request id>
    [[ $(id -u) == 0 ]] || die "run with sudo"
    case ${2:-} in
      settle) exec python3 tools/desks_settle.py "${@:3}" ;;
      unhold) exec python3 tools/desks_settle.py --unhold "${@:3}" ;;  # after a settle that stopped, and a look at the desk
      *) die "usage: sudo ./up.sh desks settle <desk> --operation <request id> [--backend-id <uuid>] | desks unhold <desk> --operation <request id>" ;;
    esac ;;
  walk)  # every documented command, run against this installation: sudo ./up.sh walk
    exec bash tools/walk.sh ;;
  ekka)  # the ekka CLI of this server's Enclave, signed in as the person who installed it: ./up.sh ekka plan list
    [[ $(id -u) == 0 ]] || die "run with sudo"
    shift; server_ekka "$@" ;;
  desk-plans)  # the setup and ask plans for one more desk: sudo ./up.sh desk-plans <desk> [--private]
    [[ $(id -u) == 0 ]] || die "run with sudo"
    [[ -f $DESKS_STATE/up.env ]] || die "not installed yet"
    desk=${2:-}; [[ -n $desk && $desk != --* ]] || die "usage: sudo ./up.sh desk-plans <desk> [--private]"
    case "${3:-}" in "") sharing=shared ;; --private) sharing=private ;; *) die "usage: sudo ./up.sh desk-plans <desk> [--private]" ;; esac
    id=$(desks_agent_id); [[ -n $id ]] || die "no agent $DESKS_AGENT yet; run the install first"
    create_desk_plans "$desk" "$id" "$sharing"; exit 0 ;;
  profiles)  # after an upgrade of providers/: put this repository's profiles into the gateway
    [[ $(id -u) == 0 ]] || die "run with sudo"
    NAME=$(sed -n 's/^DESKS_NAME=//p' "$DESKS_STATE/up.env" 2>/dev/null); [[ -n $NAME ]] || die "not installed yet"
    python3 -m unittest tests.test_providers >/dev/null 2>&1 || die "providers/ does not match providers/PIN.json; nothing was changed"
    say "provider profiles"; as_gateway_admin sync_profiles || die "the profiles were not updated"; exit 0 ;;
  connect)  # a provider's key, set on this machine: gh auth token | sudo ./up.sh connect github
    [[ $(id -u) == 0 ]] || die "run with sudo"
    NAME=$(sed -n 's/^DESKS_NAME=//p' "$DESKS_STATE/up.env" 2>/dev/null); [[ -n $NAME ]] || die "not installed yet"
    connect_cmd "${@:2}"; exit $? ;;
  desk-grant)  # the desks grant again, for the installed base image: sudo ./up.sh desk-grant
    # Recomputes the installed image and policy limits and installs the admin plans; this is not a preview.
    [[ $(id -u) == 0 ]] || die "run with sudo"; [[ -f $DESKS_STATE/up.env ]] || die "not installed yet"
    old=$(python3 -c 'import json,sys; print(" ".join(json.load(open(sys.argv[1])).get("image_digests", [])))' \
      "$DESKS_STATE/backend/desks-grant.json" 2>/dev/null || true)
    ( step_desk_grant ) || die "the desks grant was not changed"
    echo "   image_digests before: ${old:-none}"
    echo "   image_digests after:  $(python3 -c 'import json,sys; print(" ".join(json.load(open(sys.argv[1]))["image_digests"]))' "$DESKS_STATE/backend/desks-grant.json")"
    exit 0 ;;
  openshell)  # NVIDIA's own CLI as the gateway's administrator, for what NVIDIA documents: sudo ./up.sh openshell provider list
    # Secret material reaches it through the environment only, never an argument:
    #   sudo --preserve-env=SECRET ./up.sh openshell provider refresh configure ... --secret-material-env key=SECRET
    [[ $(id -u) == 0 ]] || die "run with sudo"
    NAME=$(sed -n 's/^DESKS_NAME=//p' "$DESKS_STATE/up.env" 2>/dev/null); [[ -n $NAME ]] || die "not installed yet"
    shift; python3 tools/openshell_allow.py "$DESKS_STATE/openshell-admin.log" "$@" || exit 1   # an allow-list, logged (ADK-E8)
    OPENSHELL_WORKSPACE=${OPENSHELL_WORKSPACE:-desks} as_gateway_admin "$OS" -g ekka-agent-desks "$@"; exit $? ;;   # the desks' workspace unless told otherwise
  *) sed -n '2,/^set -euo pipefail/p' "$0" | sed '$d'; exit 2 ;;
esac
step_preflight; step_host_accounts; step_settings; step_images; step_backend_image; step_enclave_install; step_init; step_workspace_policy; step_first_admin; step_enroll; step_start; step_people_scopes; step_workspace_restart; step_admin; step_desk_admin; step_grant; step_agent_open; step_desk_grant; step_people
