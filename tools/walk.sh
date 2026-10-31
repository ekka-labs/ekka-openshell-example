#!/usr/bin/env bash
# The walk: every command these docs show, run against this installation, with a real result checked for each.
#
#   sudo ./up.sh walk
#
# Run it on the installed server. The operator signs in as root (`sudo -i`, then `ekka desk login`);
# the host's separate `ekka desk login` is used only by the personal read checks.
# It makes its own desk (walk-desk), its own example plan and a throwaway profile, uses them, and removes them.
# It runs one small real job: the agent clones a public repository onto walk-desk.
#
# A check passes only when its command succeeds AND shows the expected result. Each check names the documented
# command it covers (`covers`). Commands that need a person at a keyboard are declared with `not_walked` and a
# reason. tests/test_walk_covers_docs.py fails if the docs show a command this file does not declare.
#
# Settings (environment): WALK_REPO     a public repository to clone (default: GitHub's Hello-World)
#                         DESKS_AGENT   the agent that owns desk plans (default: desks)
#                         PERSON_EKKA   the person's ekka command (default: /usr/local/bin/ekka, the binary itself: on this
#                                       server /usr/local/sbin/ekka is the Enclave's root-only wrapper and comes first on
#                                       PATH, while a person's own computer has only the binary)
#                         WALK_SOAK     run setup and ask this many times each, all must complete (default 0: skipped)
#                         WALK_PERSON_HOME  an EKKA home named .ekka-walk-person where walk-person signed in before
#                                       the walk (the leg "who may use the desks" prints how); deleted after it
set -uo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=../deploy/lib.sh
. deploy/lib.sh   # enclave_ekka, enclave_home_file: the native Enclave, as its user
ROOT=$PWD
export DESKS_STATE=${DESKS_STATE:-/var/lib/ekka-agent-desks}
WALK_REPO=${WALK_REPO:-https://github.com/octocat/Hello-World}
DESKS_AGENT=${DESKS_AGENT:-desks}
DESK=walk-desk
WHO=${SUDO_USER:-}
PASS=0; FAIL=0; SKIP=0; FAILED=(); SKIPPED=(); STARTED=$(date -u +%s)

[[ $(id -u) == 0 && -n $WHO && $WHO != root ]] || { echo "walk: run it with sudo, as the person signed in to the desks"; exit 2; }
[[ -f $DESKS_STATE/up.env ]] || { echo "walk: this machine has no installation ($DESKS_STATE/up.env)"; exit 2; }
EKKA_ENV=$(sed -n 's/^EKKA_ENV=//p' "$DESKS_STATE/up.env"); EKKA_ENV=${EKKA_ENV:-prod}

g=$'\033[32m'; r=$'\033[31m'; y=$'\033[33m'; d=$'\033[2m'; n=$'\033[0m'
covers() { :; }   # a marker the coverage test reads
not_walked() { SKIP=$((SKIP+1)); SKIPPED+=("$1: $2"); printf "  ${y}SKIP${n}  %-30s %s\n" "$1" "$2"; }
skip_leg() { SKIP=$((SKIP+1)); SKIPPED+=("$1: $2"); printf "  ${y}SKIP${n}  %-30s %s\n" "$1" "$2"; }
# What runner 0.1.111 removed or moved to this machine (a person's sign-in only reads). A check that needs to read
# a desk's files is skipped by name with this reason, never passed some other way: nothing happens on a desk except
# through EKKA, so the walk never reads a desk with the gateway administrator's CLI instead.
NO_DESK_READ="reading a desk's files has no EKKA path in runner 0.1.111 (ekka desk shell and download are refused)"
quiet() { sed -E '/^[[:space:]]*(STAGING|a rehearsal of production)/d'; }
P() { sudo -u "$WHO" -H env EKKA_ENV="$EKKA_ENV" bash -lc "$(printf '%q ' "${PERSON_EKKA:-/usr/local/bin/ekka}" "$@") 2>&1" | quiet; }
Pin() { local input=$1; shift; printf '%s' "$input" | sudo -u "$WHO" -H env EKKA_ENV="$EKKA_ENV" bash -lc "$(printf '%q ' "${PERSON_EKKA:-/usr/local/bin/ekka}" "$@") 2>&1" | quiet; }
E() { ./up.sh ekka "$@" 2>&1 | quiet; }
pin_state() {  # what the Enclave last reported for walk-desk: pinned | replaced | waiting
  local line; line=$(E desk list | grep -E "desks/$DESK " | head -1)
  if grep -q -E 'REPLACED \(|DESK_SANDBOX_REPLACED|DESK_POLICY_MISMATCH' <<<"$line"; then echo replaced
  elif grep -q ' pinned$' <<<"$line" && ! grep -q 'not pinned' <<<"$line" && ! grep -q 'last check' <<<"$line"; then echo pinned
  else echo waiting; fi; }
until_state() {  # until_state <state>: the Enclave checks its desks about once a minute
  for _ in $(seq 1 45); do [[ $(pin_state) == "$1" ]] && return 0; sleep 4; done; return 1; }
repin() { printf '%s\n' "$DESK" | enclave_ekka desk repin "$DESK" 2>&1 | quiet; }
OUT=""; NO_PERSON=""
t() {  # t <label> <regex> <command...>: passes when the command succeeds and its output matches
  local label=$1 want=$2; shift 2
  if [[ -n ${NO_PERSON:-} && ( $1 == P || $1 == Pin ) ]]; then skip_leg "$label" "$NO_PERSON"; return; fi
  OUT=$("$@"); local rc=$?
  if (( rc == 0 )) && grep -q -E -- "$want" <<<"$OUT"; then PASS=$((PASS+1)); printf "  ${g}PASS${n}  %s\n" "$label"
  else FAIL=$((FAIL+1)); FAILED+=("$label"); printf "  ${r}FAIL${n}  %s  ${d}(exit %s)${n}\n" "$label" "$rc"
       printf "${d}%s${n}\n" "$(grep -v -E '^\s*$' <<<"$OUT" | tail -4 | sed 's/^/        /')"; fi
}
up_again() {  # the install run again, in the mode it was made in: a bare ./up.sh only prints its usage
  local name; name=$(sed -n 's/^DESKS_NAME=//p' "$DESKS_STATE/up.env")
  ./up.sh server "$name"
}
no() {  # no <label> <regex> <command...>: passes when the command is REFUSED (exits non-zero) and the refusal
  # matches. A command 0.1.111 refuses on purpose must stay refused, and say what to do instead.
  local label=$1 want=$2; shift 2
  if [[ -n ${NO_PERSON:-} && ( $1 == P || $1 == Pin ) ]]; then skip_leg "$label" "$NO_PERSON"; return; fi
  OUT=$("$@"); local rc=$?
  if (( rc != 0 )) && grep -q -E -- "$want" <<<"$OUT"; then PASS=$((PASS+1)); printf "  ${g}PASS${n}  %s\n" "$label"
       # The refusal's own words, the first line that matched, so a box run shows what each leg pinned.
       printf "${d}        %s${n}\n" "$(grep -m1 -E -- "$want" <<<"$OUT" | cut -c1-240)"
  else FAIL=$((FAIL+1)); FAILED+=("$label"); printf "  ${r}FAIL${n}  %s  ${d}(exit %s, a refusal was expected)${n}\n" "$label" "$rc"
       printf "${d}%s${n}\n" "$(grep -v -E '^\s*$' <<<"$OUT" | tail -4 | sed 's/^/        /')"; fi
}
section() { printf '\n\033[1m%s\033[0m\n' "$*"; }
TMP=$(mktemp -d); chmod 0755 "$TMP"
WP_LIVE=""
on_exit() {  # however the walk ends, even aborted: no walk-person sign-in left alive
  if [[ -n $WP_LIVE ]]; then /usr/local/sbin/ekka desk revoke walk-person >/dev/null 2>&1; rm -rf "$WP_LIVE"; fi
  rm -rf "$TMP"
}
trap on_exit EXIT

section "the installation"
covers "./up.sh server"; covers "./up.sh status"
not_walked "./up.sh desks" "Settles a stuck desk change: it restarts the gateway, and the walk has no stuck change to settle; tests/test_desks_settle.py covers it."
t "the install has run to the end" 'done: started' ./up.sh status
covers "./up.sh profiles"
t "provider profiles reload into the gateway" 'profile (claude-code|github) (imported|updated)' ./up.sh profiles
covers "./up.sh openshell"
t "NVIDIA's CLI answers as the gateway's administrator" 'NAME|claude-code|github' ./up.sh openshell provider list
t "the Enclave's ekka answers through up.sh" 'organization' ./up.sh ekka whoami
covers "./up.sh walk"

# A person's own sign-in on this server is set up after the install (docs/SERVER.md, Keep it running). Without it every check
# run as the person can only fail on the missing setup, so each one SKIPs with one hint instead. Only these
# two exact messages from ekka skip; any other error from the same read still FAILs.
person_missing() {  # person_missing <output of a person's desk read>: the hint, or nothing when the person is set up
  if grep -q -F 'no desk backend on this machine' <<<"$1"; then
    echo "no person sign-in on this server; set it up once: docs/OPERATIONS.md, Administer desks from the server itself"
  elif grep -q -F 'a sign-in is needed' <<<"$1"; then
    echo "$WHO is not signed in to the desks on this server; as $WHO run: ekka desk login"
  fi
}
NO_PERSON=$(person_missing "$(P desk profile list)")

section "sign in"
covers "ekka desk backend show"
t "the person's ekka points at this server" 'workspace' P desk backend show
covers "ekka desk backend set"; covers "./install.sh"
t "the people bundle sets the desk defaults and names the Enclave" 'enclave_subject' grep -E 'enclave_subject|desk-policy' "$DESKS_STATE/people/install.sh"
# The browser's half: the command a person runs once so the sign-in page opens with no warning. It is
# printed, never run, so the walk checks what is printed: this server's CA by fingerprint, trust for websites and
# this name only, nothing broader. A Mac running it and opening the page is the box run's step, not the walk's.
trust_printed() {  # trust_printed <server name> [people folder]: the printed commands, or why not
  local people=${2:-$DESKS_STATE/people} out fp
  out=$(bash "$people/trust-ca.sh" --all) || { echo "trust-ca.sh did not run"; return 1; }
  fp=$(openssl x509 -in "$DESKS_STATE/tls/ca.crt" -noout -fingerprint -sha256 | cut -d= -f2)
  grep -qF "fingerprint is $fp." <<<"$out" || { echo "the fingerprint printed is not tls/ca.crt's"; return 1; }
  grep -qF -- "security add-trusted-cert -r trustRoot -p ssl -s $1 -k " <<<"$out" \
    || { echo "the macOS command is not limited to websites for $1"; return 1; }
  grep -qF -- " -A -t C,, -n " <<<"$out" || { echo "the Linux command is not limited to websites"; return 1; }
  ! grep -qE 'sudo|System\.keychain|update-ca-certificates|add-trusted-cert -d' <<<"$out" \
    || { echo "a broader trust command was printed"; return 1; }
  echo "$out"
}
SERVER_NAME=$(sed -n 's/^DESKS_NAME=//p' "$DESKS_STATE/up.env")
EDGE_AT=$(sed -n 's/^DESKS_EDGE_BIND=//p' "$DESKS_STATE/up.env"); [[ -z $EDGE_AT || $EDGE_AT == 0.0.0.0 ]] && EDGE_AT=127.0.0.1
t "the bundle prints the browser's trust command: this CA, websites and $SERVER_NAME only, no sudo" \
  'add-trusted-cert' trust_printed "$SERVER_NAME"
# What the browser meets: the sign-in page's certificate, checked against the bundle's CA alone and for the name.
t "the bundle's CA alone verifies the sign-in page for $SERVER_NAME, the name checked" \
  "\"issuer\" *: *\"https://$SERVER_NAME/realms/ekka-desks\"" \
  curl -sS --fail --cacert "$DESKS_STATE/people/desks-ca.pem" --resolve "$SERVER_NAME:443:$EDGE_AT" \
  "https://$SERVER_NAME/realms/ekka-desks/.well-known/openid-configuration"
not_walked "ekka desk login" "opens a browser for a person; every check below needs a live sign-in"
not_walked "ekka desk logout" "would end the sign-in the rest of the walk needs"
not_walked "ekka login" "a person's EKKA sign-in by email; the Enclave's session above proves one"
not_walked "ekka org create" "creates an organization; done once by a person before installing"
not_walked "ekka run attach" "joins a live conversation from a terminal; ekka-ai/ekka-workspace scripts/desk-chat-walk.sh drives a reattach"

no "a host change is refused: no Enclave on this computer" 'Nothing is enrolled here, so there is nothing to start' P desk create walk-host-refused --provider claude-code

section "keys"
covers "ekka desk profile list"
t "profiles: claude-code and github" 'claude-code' P desk profile list
covers "ekka desk profile show"
t "claude-code reaches only api.anthropic.com" 'api\.anthropic\.com' P desk profile show claude-code
# ── which claude-code profile is in force ──
# The shipped profile takes an Anthropic API key (owner's ruling, 2026-10-11). An install may keep its own in
# providers.local, which up.sh profiles never replaces; the walk reports that one and never judges it.
own_claude=$DESKS_STATE/providers.local/claude-code.yaml
if [[ -e $own_claude ]]; then
  own_cred=$(python3 -c 'import re,sys; m=re.search(r"^credentials:\s*\n\s*-\s*name:\s*(\S+)", open(sys.argv[1]).read(), re.M); print(m.group(1) if m else "")' "$own_claude" 2>/dev/null)
  t "claude-code is this install's own profile ($own_claude), credential ${own_cred:-unreadable}" "credential[[:space:]]+${own_cred:-UNREADABLE}, sent as" P desk profile show claude-code
else
  t "claude-code is the shipped profile: an Anthropic API key (api_key, sent as header)" 'credential[[:space:]]+api_key, sent as header' P desk profile show claude-code
fi
# ── end of the claude-code profile check ──
# The throwaway kind of connection, added the way the server's administrator adds one (providers.local, then
# up.sh profiles): a person's own `ekka desk profile add` is refused since 0.1.111. Removed again at the end.
install -d -m 0755 "$DESKS_STATE/providers.local"
cat > "$DESKS_STATE/providers.local/walk-dummy.yaml" <<'YAML'
id: walk-dummy
display_name: Walk check (not a real service)
description: Used by tools/walk.sh only
category: source_control
credentials:
  - name: token
    description: A dummy value
    env_vars: [WALK_DUMMY_TOKEN]
    required: true
    auth_style: bearer
    header_name: authorization
endpoints:
  - host: example.com
    port: 443
    protocol: rest
    access: read-only
    enforcement: enforce
binaries: [/usr/bin/curl]
YAML
chmod 0644 "$DESKS_STATE/providers.local/walk-dummy.yaml"
t "the server's administrator adds a kind of connection" 'profile walk-dummy (imported|updated)' ./up.sh profiles
covers "ekka desk profile add"
no "a person's profile add is refused, and names the step on this machine" 'sudo \./up\.sh connect' P desk profile add "$ROOT/providers/github.json"
covers "ekka desk connect"
t "a person's connect lists the kinds of connection" 'walk-dummy' P desk connect
no "a person's connect naming one is refused, and names the step on this machine" 'sudo \./up\.sh connect walk-dummy' Pin 'walk-not-a-secret' desk connect walk-dummy
covers "ekka desk connect"
t "the server's administrator connects its key, from a pipe, as ekka desk connect" 'walk-dummy is connected' bash -c "printf %s walk-not-a-secret | /usr/local/sbin/ekka desk connect walk-dummy 2>&1"
covers "ekka desk connections"
t "connections list it" 'walk-dummy' P desk connections
if grep -q walk-not-a-secret <<<"$OUT"; then FAIL=$((FAIL+1)); FAILED+=("a key's value is never shown"); printf "  ${r}FAIL${n}  a key's value is never shown\n"
else PASS=$((PASS+1)); printf "  ${g}PASS${n}  a key's value is never shown\n"; fi

section "desks"
E desk delete "$DESK" --yes >/dev/null 2>&1 || true
covers "ekka desk create"
t "create walk-desk with the model connection (public GitHub needs no key)" "$DESK" E desk create "$DESK" --provider claude-code
for _ in $(seq 1 30); do E desk list | grep -q "desks/$DESK" && break; sleep 4; done
covers "ekka desk list"
t "it registers itself on the Enclave" "desks/$DESK" ./up.sh ekka desk list
covers "ekka desk show"
t "show it on the Enclave" "$DESK" ./up.sh ekka desk show "$DESK"
# Governed deletion retires the old pin; an approved create registers a new desk, not an unexpected replacement.
t "the first desk is pinned" '^pinned$' bash -c "$(declare -f E quiet pin_state until_state); DESK=$DESK; until_state pinned && pin_state"
E desk show "$DESK" --json > "$TMP/desk-before-delete.json"
old_id=$(python3 tools/desk_admin_checks.py pinned-id "$TMP/desk-before-delete.json" "$DESK")
t "read the old pinned backend identity" '^[A-Za-z0-9_-]+$' printf '%s\n' "$old_id"
t "governed delete retires the first desk" '.' E desk delete "$DESK" --yes
t "the old desk record is retained as retired evidence" '^retired$' bash -c \
  'test -f "/var/lib/ekka/.ekka/desks/.state/retired/$1.$2.json" && echo retired' _ "$DESK" "$old_id"
t "a second governed create registers a fresh desk under the same name" "$DESK" E desk create "$DESK" --provider claude-code
t "the new registration becomes pinned" '^pinned$' bash -c "$(declare -f E quiet pin_state until_state); DESK=$DESK; until_state pinned && pin_state"
E desk show "$DESK" --json > "$TMP/desk-after-create.json"
t "the new pin names a different backend identity" '^ok$' \
  python3 tools/desk_admin_checks.py new-pin "$TMP/desk-before-delete.json" "$DESK" "$TMP/desk-after-create.json"
covers "ekka desk repin"
no "repin is refused when no unexpected replacement needs approval" 'DESK_REPIN_NOTHING_TO_APPROVE' repin
skip_leg "unexpected replacement stays refused until repin" "requires a disposable tamper fixture outside this setup's governed create/delete path; runner v0.1.112 crates/ekka-openshell-executor/tests/pinning.rs:310-337 and crates/ekka-openshell-executor/tests/desk.rs:397-411 prove refusal before work"
covers "ekka desk logs"
t "read its log" '.' P desk logs "$DESK"
covers "ekka desk stop"
t "stop it" '.' E desk stop "$DESK"
covers "ekka desk start"
t "start it again" '.' E desk start "$DESK"

section "files on a desk"
# 0.1.111 copies nothing into or out of a desk, and runs nothing in one, outside EKKA.
covers "ekka desk upload"
no "upload is refused" 'Copying files into or out of a desk is not available' P desk upload "$DESK" "$ROOT/README.md"
# Since 0.1.119 `desk shell` is not a command at all: the CLI refuses it while parsing,
# before it reaches anything. These are the parser's words, measured on runner 78b7f6e7 (exit 64). A refusal on this
# machine says nothing about the gateway: the two knocks on its doors are walk-person's legs below.
covers "ekka desk shell"
no "shell is not a command any more" "unrecognized subcommand 'shell'" P desk shell "$DESK" -- true
covers "ekka desk download"
no "download is refused" 'Copying files into or out of a desk is not available' P desk download "$DESK" README.md "$TMP"

section "who may use the desks"
covers "ekka desk access list"
members=$(P desk access list); t "list the workspace's members" 'user' P desk access list
covers "ekka desk invite"
t "invite a person (or refuse a repeat), as ekka desk invite" 'may use desks|invited already' bash -c "/usr/local/sbin/ekka desk invite walk-person walk-person@example.com 2>&1; true"
covers "ekka desk access remove"
no "a person's access remove is refused, and names up.sh revoke" 'sudo \./up\.sh revoke' P desk access remove walk-person
covers "ekka desk access add"
no "a person's access add is refused, and names up.sh invite" 'sudo \./up\.sh invite' P desk access add walk-person
# ADK-E2, measured with a real sign-in: an invited person reads, and is refused a write for the scope they lack.
# Only a person can make that sign-in, so it is made before the walk, in an EKKA home of its own, named
# .ekka-walk-person (WALK_PERSON_HOME). Its token is used here only, never printed or an argument, and the home and
# the one private file NVIDIA's CLI reads it from are deleted at the end of this leg.
covers "ekka desk revoke"
WP=${WALK_PERSON_HOME:-}
if [[ -n $WP && $(basename "$WP") == .ekka-walk-person && -d $WP && ! -L $WP && -s $WP/openshell/person/ekka-desks.json ]]; then
  WP_LIVE=$WP   # from here, an aborted walk revokes walk-person and deletes this home (on_exit)
  desks_name=$(sed -n 's/^DESKS_NAME=//p' "$DESKS_STATE/up.env")
  PP() { sudo -u "$WHO" -H env EKKA_ENV="$EKKA_ENV" EKKA_HOME="$WP" bash -lc "$(printf '%q ' "${PERSON_EKKA:-/usr/local/bin/ekka}" "$@") 2>&1" | quiet; }
  osh=$TMP/walk-person-openshell
  wp_os() {  # NVIDIA's CLI as walk-person: their token, from a 0600 file only it reads
    ( export HOME=$osh XDG_CONFIG_HOME=$osh/.config SSL_CERT_FILE=$DESKS_STATE/tls/ca.crt
      unset OPENSHELL_GATEWAY OPENSHELL_GATEWAY_ENDPOINT OPENSHELL_GATEWAY_INSECURE OPENSHELL_OIDC_CLIENT_SECRET
      timeout 60 "$DESKS_STATE/bin/openshell" -g walk-person --workspace desks "$@" 2>&1 ) </dev/null; }
  # Read first: the person's ekka refreshes an expired token and saves it, so the token copied next is current.
  t "walk-person reads the desks with their own sign-in" "$DESK" PP desk list
  install -d -m 0700 "$osh"
  # NVIDIA's `gateway add --oidc-issuer` signs in AT ONCE, in a browser, and on failure removes the registration it
  # just made (OpenShell v0.1.4 gateway.rs:932-1031, rollback_gateway_registration), still exiting 0: the walk then
  # met "Unknown gateway 'walk-person'" and its write probe never reached authorization (walk B on commit 1301afe).
  # So the registration is written here, in v0.1.4's own shape (metadata.rs GatewayMetadata, the OIDC branch of
  # gateway add: auth_mode oidc, is_remote true, gateway_port 0), beside the token the person's sign-in made.
  python3 - "$WP/openshell/person/ekka-desks.json" "$osh/.config/openshell/gateways/walk-person" "$desks_name" <<'PY'
import json, os, sys
s = json.load(open(sys.argv[1]))
folder, name = sys.argv[2], sys.argv[3]
os.makedirs(folder, mode=0o700, exist_ok=True)
def private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
private(os.path.join(folder, "metadata.json"), {
    "name": "walk-person", "gateway_endpoint": f"https://{name}:8080", "is_remote": True, "gateway_port": 0,
    "auth_mode": "oidc", "oidc_issuer": f"https://{name}/realms/ekka-desks", "oidc_client_id": "ekka-cli",
    "oidc_audience": "ekka-desks"})
private(os.path.join(folder, "oidc_token.json"), {k: s[k] for k in ("access_token", "issuer", "client_id")})
PY
  # The probe below proves nothing unless NVIDIA's CLI resolves walk-person's gateway: checked first, by name.
  t "walk-person's gateway is registered for NVIDIA's CLI" '(^|[[:space:]])walk-person([[:space:]]|$)' \
    bash -c "HOME='$osh' XDG_CONFIG_HOME='$osh/.config' '$DESKS_STATE/bin/openshell' gateway list 2>&1"
  # ONE write that only the missing SCOPE can refuse: StartSandbox needs workspace role user (walk-person has it)
  # and sandbox:write (a person's sign-in lacks it), OpenShell v0.1.4 openshell.proto. walk-desk is running, so a
  # start that wrongly worked would change nothing; the CLI sends it straight to the gateway (run.rs sandbox_start).
  # `policy set` was used before and proved nothing: the CLI rejected this setup's desk/policy.json locally ("unknown
  # field 'filesystem' in authored policy"), and its UpdateConfig needs role admin, refused before any scope.
  # The pattern is the refusal's words as measured on the box (walk 3, ekka-agent-desks#167): the gateway answers
  # PermissionDenied with message "scope 'sandbox:write' required".
  no "walk-person's own sign-in is refused a desk write, for the write scope it lacks" "scope 'sandbox:write' required" \
    wp_os sandbox start "$DESK"
  # The two doors into a running desk that do not go through EKKA, knocked on with walk-person's own token
  #. In OpenShell v0.1.4's openshell.proto each needs workspace role user and sandbox:write,
  # as StartSandbox does, and a person's sign-in lacks the scope. The command is `true`, so a refusal that fails to
  # come changes nothing; stdin is closed and wp_os times out, so it cannot hang the walk either. --no-tty keeps exec
  # on ExecSandbox; ExecSandboxInteractive has the same scope, but NVIDIA's CLI takes it only from a real terminal.
  # The two doors do not have the same locks (measured on the box, walk 2, with sandbox:write planted):
  #   ExecSandbox has ONE lock, the scope. It is a streaming call, so EKKA's adapter has no hook on it
  #   (ekka-openshell-adapter manifest.rs). This leg is the one that detects scope drift, so its pattern
  #   takes the scope's words only.
  #   CreateSshSession has TWO: the scope, and EKKA's adapter, which refuses it for everyone in this
  #   release (decide.rs: "is not authorized in this release"). This leg passes when either holds.
  # Rerun on every OpenShell upgrade (docs/OPERATIONS.md): a new version may move a method's scope.
  # Both patterns are the refusals' words as measured on the box (walk 3, ekka-agent-desks#167).
  no "walk-person cannot run a command in a desk directly (ExecSandbox)" "scope 'sandbox:write' required" \
    wp_os sandbox exec --no-tty "$DESK" -- true
  no "walk-person cannot open an SSH session into a desk (CreateSshSession)" \
    "scope 'sandbox:write' required|is not authorized in this release: it opens another way into or out of a governed sandbox" \
    wp_os sandbox connect "$DESK"
  t "the server's administrator revokes walk-person, as ekka desk revoke" 'may no longer use desks' /usr/local/sbin/ekka desk revoke walk-person
  t "revoking again changes nothing" 'may no longer use desks' /usr/local/sbin/ekka desk revoke walk-person
  # The refusal's words as measured on the box (walk 3, ekka-agent-desks#167).
  no "walk-person's next read is refused" "not a member of workspace 'desks'" PP desk list
  rm -rf "$osh" "$WP"; WP_LIVE=""
else
  skip_leg "walk-person reads, is refused a write, and revoke ends it" "needs walk-person signed in before the walk: \
sudo ekka desk invite walk-person walk-person@example.com; EKKA_HOME=~/.ekka-walk-person bash $DESKS_STATE/people/install.sh; \
EKKA_HOME=~/.ekka-walk-person ekka desk login (as walk-person); then sudo WALK_PERSON_HOME=~/.ekka-walk-person ./up.sh walk"
fi

section "plans, grants and records"
agent_id=$(E agent list --json | A=$DESKS_AGENT python3 -c 'import json,sys,os; a=[x["agent_id"] for x in json.load(sys.stdin)["agents"] if x["agent_code"]==os.environ["A"]]; print(a[0] if a else "")' 2>/dev/null)
# walk-desk gets its own setup and ask plans, from the same templates as code-desk (tools/desk_plans.py).
python3 tools/desk_plans.py "$DESK" "$TMP" "$agent_id" >/dev/null
install -d -m 0700 -o ekka -g ekka /var/lib/ekka/.ekka/plans; enclave_home_file plans/walk-plan.json 0600 < "$TMP/plan-setup-$DESK.json"
covers "ekka plan validate"
t "validate walk-desk's setup plan" '.' E plan validate /var/lib/ekka/.ekka/plans/walk-plan.json
covers "ekka desk registry"
t "the registries with a kept credential read back through ekka desk registry show" 'no registry credential kept|  as ' /usr/local/sbin/ekka desk registry show
not_walked "ekka desk image" "pulls a project image from a private registry with a kept credential; a walk box has none (ekka-ai/.github#786 proves it on the desks server)"
covers "ekka desk grant"
t "the desks grant's limits read back through ekka desk grant show" 'no discovery window|DISCOVERY WINDOW' /usr/local/sbin/ekka desk grant show
covers "ekka desk plans"; covers "ekka plan create"
t "walk-desk gets its own setup and ask plans, as ekka desk plans" 'example\.desk\.ask\.walkDesk\.v1' /usr/local/sbin/ekka desk plans "$DESK"
# Every member may RUN the desks agent's plans; the install opened it (step_agent_open, #161). Read from Govern
# (`agent show --json`, access.use), then closed and opened again, so both documented commands are seen working.
# Stopped between the two, the agent is left `listed`: members are refused, which is the closed side.
use_mode() { E agent show "$DESKS_AGENT" --json | python3 -c 'import json,sys; t=sys.stdin.read(); i=t.find("{"); d=json.JSONDecoder().raw_decode(t[i:])[0] if i>=0 else {}; print((d.get("access") or {}).get("use",""))' 2>/dev/null; }
covers "ekka agent allow"
t "every member may run the $DESKS_AGENT agent's plans" 'every member' E agent allow "$DESKS_AGENT" --everyone --as use
t "...and Govern reads it as open" '^open$' use_mode
covers "ekka agent deny"
t "closing it makes it listed again" 'only the people on its list' E agent deny "$DESKS_AGENT" --everyone
t "...and Govern reads it as listed" '^listed$' use_mode
t "opening it again" 'every member' E agent allow "$DESKS_AGENT" --everyone --as use
t "...and Govern reads it as open again" '^open$' use_mode
covers "ekka run list"
t "recent runs include their full identities for recovery" '"id"[[:space:]]*:[[:space:]]*"[0-9a-f-]{36}"' E run list --limit 10 --json
covers "ekka plan list"
t "they are in the plan list" 'example\.desk\.setup\.walkDesk\.v1' E plan list
latest() { E plan list | grep -o -E "ekka\.example\.desk\.$1\.walkDesk\.v1@[0-9.]+" | sort -V | tail -1; }
plan=$(latest setup); ask=$(latest ask)
covers "ekka plan run"
t "run it: the agent clones $WALK_REPO onto walk-desk" 'Plan completed' E plan run "$plan" --input "repos=[\"$WALK_REPO\"]"
# The id printed at dispatch must be the run's own; later reads use it.
run_of() {  # run_of <label> <plan run output>: RUN_OF is that run's id, only when dispatch and completion agree
  t "$1: the id printed at dispatch is the id it completed under" "^[0-9a-f-]{36}$" \
    bash -c 'python3 tools/desk_admin_checks.py same-run <<<"$1"' _ "$2"
  RUN_OF=$(grep -x -E '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' <<<"$OUT" || true); }
run_of "setup" "$OUT"; run_id=$RUN_OF
skip_leg "the repository is on the desk, at a commit" "$NO_DESK_READ"
repo=$(basename "$WALK_REPO" .git)

section "ask your code"
# The dirty-checkout checks planted a tracked edit and an untracked file with `ekka desk shell`, then read them back.
skip_leg "a tracked edit, an untracked file and the branch survive asks and setup" "$NO_DESK_READ"
ASKED=""
ask_run() {  # ask_run <question>: ask on walk-desk; ASKED is the run id
  t "ask: $1" 'Plan completed' E plan run "$ask" --input "question=$1"
  run_of "ask" "$OUT"; ASKED=$RUN_OF; }
ask_run "What does the README of $repo say? Name the file and line."; first=$ASKED
skip_leg "the answer record reports no discarded files" "$NO_DESK_READ"
ask_run "Which file in $repo would a newcomer open first, and why? Name the file and line."; second=$ASKED
t "rerunning setup completes on the separate work copy" 'Plan completed' E plan run "$plan" --input "repos=[\"$WALK_REPO\"]"
# Since ekka 0.1.112 the Enclave keeps each collected answer in its own run store, and `run show` prints it on
# the Enclave's machine: read there, never copied off the desk.
answer_of() { E run show "$1"; }
t "the first answer is read on the Enclave's machine, citing the README" 'README' answer_of "$first"
t "it says it is this machine's copy, not checked against the signed receipt" \
  "not checked against the run.s signed receipt" answer_of "$first"
t "the second answer comes back under its own run" "not checked against the run.s signed receipt" answer_of "$second"
two_answers() {  # two_answers <run> <run>: BOTH runs show a collected answer, and the answers differ. The lines
  # naming a run are dropped first: `run show` prints each run's own id, so two empty screens always differ.
  local a b label="not checked against the run's signed receipt"
  a=$(answer_of "$1" | grep -v -F -e "$1" -e "${1:0:8}"); b=$(answer_of "$2" | grep -v -F -e "$2" -e "${2:0:8}")
  [[ $a == *"$label"* && $b == *"$label"* && $a != "$b" ]] && echo two
}
t "two asks keep two answers" '^two$' two_answers "$first" "$second"
# The run ends well only when the Enclave collected an answer file (exists(output.answer)); one without fails.
python3 - "$TMP/plan-ask-$DESK.json" "$TMP/walk-noanswer.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
d["plan"]["code"] = "example.desk.walkNoAnswer.v1"; d["plan"]["name"] = "Walk: an ask whose answer file never appears"
for o in d["definition"]["operations"][0]["steps"][0]["inputs"]["outputs"]:
    if o["kind"] == "answer": o["path"] = ".ekka/walk-never-written.md"
json.dump(d, open(sys.argv[2], "w"))
PY
enclave_home_file plans/walk-noanswer.json 0600 < "$TMP/walk-noanswer.json"
E plan create /var/lib/ekka/.ekka/plans/walk-noanswer.json >/dev/null 2>&1 || true
noanswer=$(E plan list | grep -o -E 'ekka\.example\.desk\.walkNoAnswer\.v1@[0-9.]+' | sort -V | tail -1)
# A pass needs the plan to exist and the RUN to end failed (its run id beside the word), so a run that
# never started cannot pass this check.
t "a run with no answer file fails" '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}.*([Ff]ailed|FAILED)|([Ff]ailed|FAILED).*[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' \
  bash -c "[[ -n '$noanswer' ]] || { echo 'no walkNoAnswer plan was created'; exit 3; }; $(declare -f E quiet); E plan run '$noanswer' --input 'question=What is in the README?'; true"
# A second ask on the same desk that writes no answer of its own must FAIL, never seal the first answer as its own
# (#71: a tester's 2nd and 3rd asks on setup v0.1.2 completed with the 1st answer, byte for byte). The stale plan
# collects the FIRST ask's own answer file, which this ask never writes: exactly a file left on the desk by an
# earlier run. regression coverage has the Enclave refuse an output this attempt did not write; until that
# runner ships, this leg is red on purpose.
stale_refused() {  # stale_refused <first run> <plan run output>: "refused" only when that run FAILED under its own id
  # and its screen shows no line of the first run's answer
  local id lines
  [[ $2 != *"Plan completed"* ]] || { echo "the run completed: it sealed an answer it never wrote"; return 0; }
  id=$(grep -o -E 'Run [0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12} dispatched' <<<"$2" | head -1 | cut -d' ' -f2)
  [[ -n $id && $id != "$1" ]] || { echo "no dispatched run id of its own"; return 0; }
  grep -q -E "[Ff]ailed|FAILED" <<<"$2" || { echo "run $id did not end failed"; return 0; }
  lines=$(answer_of "$1" | grep -v -F -e "$1" -e "${1:0:8}" -e "signed receipt" | sed -E 's/^[[:space:]]+//' | awk 'length >= 20')
  [[ -n $lines ]] || { echo "the first answer has no line to look for"; return 0; }
  if answer_of "$id" | grep -q -F -f <(printf '%s\n' "$lines"); then echo "run $id shows the first answer"; return 0; fi
  echo refused
}
python3 - "$TMP/plan-ask-$DESK.json" "$TMP/walk-stale.json" "$first" <<'PY'
import json, re, sys
first = sys.argv[3]
assert re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", first), "no first ask to go stale"
d = json.load(open(sys.argv[1]))
d["plan"]["code"] = "example.desk.walkStaleAnswer.v1"; d["plan"]["name"] = "Walk: an ask that collects an earlier run's answer"
for o in d["definition"]["operations"][0]["steps"][0]["inputs"]["outputs"]:
    if o["kind"] == "answer": o["path"] = f".ekka/answers/{first}.md"
json.dump(d, open(sys.argv[2], "w"))
PY
enclave_home_file plans/walk-stale.json 0600 < "$TMP/walk-stale.json"
E plan create /var/lib/ekka/.ekka/plans/walk-stale.json >/dev/null 2>&1 || true
stale=$(E plan list | grep -o -E 'ekka\.example\.desk\.walkStaleAnswer\.v1@[0-9.]+' | sort -V | tail -1)
t "a second ask that writes no answer fails, and never shows the first answer" '^refused$' \
  bash -c "[[ -n '$stale' ]] || { echo 'no walkStaleAnswer plan was created'; exit 3; }; $(declare -f E quiet answer_of stale_refused); \
    stale_refused '$first' \"\$(E plan run '$stale' --input 'question=Which language is $repo written in? Name the file and line.')\""
# An unattended desk has nobody to approve a command, so a prompt that needs one fails at random (#140: a tester's
# setup failed on a held clone, and passed on retry). WALK_SOAK=N runs setup and ask N times each on walk-desk, and
# every run must complete. A held command itself is not visible from here ($NO_DESK_READ), so this counts the runs
# that completed. It costs 2N real model runs, so it is off by default; the release walk runs WALK_SOAK=10.
soak=${WALK_SOAK:-0}
if [[ $soak =~ ^[1-9][0-9]?$ ]]; then
  # Each ask needs git history, so the agent's helpers run read-only git (the shapes a tester saw held on 0.1.114).
  # desk-ask@6 fails a run whose command is refused and names it, so a completed run had no held command. Every
  # run id is printed for the release log.
  completed=0
  for i in $(seq 1 "$soak"); do
    out=$(E plan run "$plan" --input "repos=[\"$WALK_REPO\"]")
    printf '        soak %s setup: %s\n' "$i" "$(grep -o -E 'Plan (completed|failed) \(run [0-9a-f-]{36}\)' <<<"$out" | head -1)"
    grep -q 'Plan completed' <<<"$out" || continue
    out=$(E plan run "$ask" --input "question=Which commit last changed the README in $repo, and what did it change? Name the commit, the file and the line.")
    printf '        soak %s ask:   %s\n' "$i" "$(grep -o -E 'Plan (completed|failed) \(run [0-9a-f-]{36}\)' <<<"$out" | head -1)"
    grep -q 'Plan completed' <<<"$out" && completed=$((completed+1))
  done
  t "setup and ask both complete $soak times in a row, with nobody to approve a command" "^$soak of $soak$" echo "$completed of $soak"
else
  skip_leg "setup and ask complete 10 times in a row, unattended" "costs 20 real model runs; run the walk with WALK_SOAK=10 "
fi
# Which models ran was read from Claude Code's session records on the desk.
skip_leg "the lead ran on Opus and handed work to a haiku or sonnet helper" "$NO_DESK_READ"

section "records"
covers "ekka run list"
t "the run is listed" "${run_id:0:8}" E run list
covers "ekka run show"
t "its decisions are recorded" '.' E run show "$run_id"
covers "ekka receipts list"
t "runs with a signed record are listed" '.' E receipts list
covers "ekka receipts show"
t "the run's record reads in plain English" '.' E receipts show "$run_id"
covers "ekka receipts verify"
t "the signed record verifies" '.' E receipts verify
covers "ekka plan schedule"
t "schedule it every hour" '.' E plan schedule "$plan" --every 1h
covers "ekka plan schedules"
sid=$(E plan schedules | grep -F "${plan%@*}" | grep -o -E '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1)
t "the schedule is listed" "${sid:0:8}" E plan schedules
covers "ekka plan unschedule"
t "unschedule it" 'Removed|removed' E plan unschedule "$sid"
covers "ekka gate grant list"
t "the desks grant is listed" 'job\.run .*/desks/\*' E gate grant list
gate=$(grep -o -E 'enclave[0-9a-f]{8}Job' <<<"$OUT" | head -1)
tmp_grant=$(E gate grant add --agent "$DESKS_AGENT" --type job --instance "$gate" --resource desks/walk-temporary --capability job.run --no-fingerprint --ttl 600)
covers "ekka gate grant revoke"
t "a temporary grant is revoked" 'Revoked|revoked' E gate grant revoke "$(grep -o -E 'revoke [0-9a-f-]{36}' <<<"$tmp_grant" | cut -d' ' -f2)"
covers "ekka decide list"
t "decisions waiting for a person are listed" '.' E decide list
covers "ekka decide answer"; covers "ekka decide show"
not_walked "ekka decide answer" "needs a plan that stops for a decision; the example plans do not"

section "what a desk may reach"
covers "ekka desk policy show"
t "read the policy in plain words" 'policy version' P desk policy show "$DESK"
covers "ekka desk policy check"
t "check a policy file" '.' P desk policy check "$ROOT/desk/policy.json"
# Since 0.1.111 every policy change is governed, and the adapter holds it to the desks grant: a host outside the
# grant's ceiling (the shipped profiles' hosts) is refused before anything is sent to the desk.
covers "ekka desk allow"
no "an allow outside the desks grant is refused by the adapter, naming CONSTRAINT_ALLOWED_HOSTS" 'CONSTRAINT_ALLOWED_HOSTS' \
  E desk allow "$DESK" --program /usr/bin/curl --host outside-ceiling.invalid --yes
covers "ekka desk deny"
no "a deny of a host the desk never allowed is refused before a change" 'NO_SUCH_RULE' \
  E desk deny "$DESK" --program /usr/bin/curl --host outside-ceiling.invalid --yes
covers "ekka desk policy set"
t "setting the policy the desk already has sends nothing" 'already has this wall; nothing was sent' \
  E desk policy set "$DESK" /var/lib/ekka/.ekka/desk-policy.json --yes
skip_leg "a governed narrowing that changes the desk" "the shipped grant gives a desk no rule it can lose (hosts come only from providers, and no new program is allowed): narrowing is walked with the approved test fixture, EKKA regression coverage"

section "on the installed machine"
covers "ekka desk add"
desk_file=$(E desk list | grep -o -E '/var/lib/ekka/[^ ]*/desks' | head -1)/$DESK.json
t "add refuses a desk that is already registered" 'already|exists|registered' bash -c "$(declare -f enclave_ekka); ENCLAVE_USER=ekka ENCLAVE_HOME=/var/lib/ekka/.ekka ENCLAVE_BIN=/usr/local/bin/ekka enclave_ekka desk add '$desk_file' 2>&1; true"
covers "ekka org edition"
t "the organization is let in and its edition is shown" 'edition' E org edition
covers "ekka enclave list"
t "this machine's Enclave is listed" 'online' E enclave list
not_walked "ekka enclave revoke" "permanently removes this machine from your organization"
# No remote door: the edge carries only the sign-in paths. Asked from this machine through the edge by
# its public name, trusting only this setup's CA.
edge_get() {  # edge_get <method> <path>: "status=<code> bytes=<n> body=<the body, one line>"
  local name; name=$(sed -n 's/^DESKS_NAME=//p' "$DESKS_STATE/up.env")
  local f; f=$(mktemp); local code
  code=$(curl -s -o "$f" -w '%{http_code}' -X "$1" --max-time 15 --cacert "$DESKS_STATE/tls/ca.crt" \
    --resolve "$name:443:127.0.0.1" "https://$name$2")
  printf 'status=%s bytes=%s body=%s\n' "$code" "$(wc -c < "$f" | tr -d ' ')" "$(tr '\n' ' ' < "$f" | head -c 200)"
  rm -f "$f"
}
run_id=$(python3 -c 'import uuid; print(uuid.uuid4())')
t "a run answer path is the edge's 404: this server serves no downloads to laptops" '^status=404 ' edge_get GET "/ekka/runs/$run_id/answer"
t "a run receipt path is the edge's 404" '^status=404 ' edge_get GET "/ekka/runs/$run_id/receipt"
t "a run session path is the edge's 404: no remote conversation" '^status=404 ' edge_get GET "/ekka/runs/$run_id/session"

not_walked "ekka enclave install-service" "run by up.sh step_start on this machine; regenerating the unit by hand is maintenance, not a walk leg"

section "desk administration through EKKA"
# ADK-G3. Only where the backend adapter runs (the desk-admin profile); elsewhere one
# SKIP line and nothing changes. Each leg's judgement is tools/desk_admin_checks.py, tested with a planted red case.
# The leg names the adapter contract's fixed words (codes, outcomes), which `ekka desk` prints.
ADAPTER=ekka-desks-ekka-backend-1
desk_admin_on() { docker inspect "$ADAPTER" >/dev/null 2>&1; }
judged() {  # judged <label> <check> <arg> <command...>: the command's exit code AND output, judged together
  local label=$1 check=$2 arg=$3; shift 3; local out rc; out=$("$@" 2>&1); rc=$?
  t "$label" '^ok$' judge_exit "$check" "$rc" "$arg" "$out"
}
judge_exit() {  # judge_exit <check> <exit code> <arg> <output>: an outcome needs exit 0, a refusal a non-zero exit.
  # A failed run prints `ended failed (ready)` when the adapter answered ready (regression review), so the
  # words alone can pass a failure; and a refusal that exited 0 refused nothing.
  case $1 in
    outcome) [[ $2 == 0 ]] || { echo "the command exited $2"; return; } ;;
    refused) [[ $2 != 0 ]] || { echo "the command exited 0: nothing was refused"; return; } ;;
  esac
  python3 tools/desk_admin_checks.py "$1" "$3" <<<"$4"
}
kc_admin() {  # this machine's Keycloak admin API, as up.sh's kc does; reads only, unless told otherwise
  (cd deploy && KC_URL=https://127.0.0.1:8443 KC_CA=$DESKS_STATE/tls/ca.crt KC_ADMIN_USER=desks-bootstrap \
     KC_ADMIN_PASSWORD_FILE=$DESKS_STATE/secrets/keycloak-admin-password python3 "$@")
}
if desk_admin_on; then
  docker inspect "$ADAPTER" > "$TMP/adapter.json"
  t "the backend adapter runs healthy, as the Enclave's user, read-only, with no port and no Docker socket" '^ok$' \
    python3 tools/desk_admin_checks.py adapter "$TMP/adapter.json"
  t "the Enclave holds the desks connection" 'desks' E api list
  enclave_ekka gate grant list --json > "$TMP/grants.json" 2>/dev/null
  t "one api.write grant on apis/desks, with its limits" '^ok$' python3 tools/desk_admin_checks.py grant "$TMP/grants.json"
  cat > "$TMP/people-scopes.py" <<'PY'
import json, sys
sys.path.insert(0, "keycloak")
import apply_realm as a
spec = json.load(open(a.SPEC)); api = a.Api(); c = a.find_client(api, spec, "ekka-cli")
print(",".join(s["name"] for s in api.get(a.realm_path(spec, f"/clients/{c['id']}/default-client-scopes")) or []))
PY
  scopes=$(kc_admin "$TMP/people-scopes.py" 2>/dev/null)
  t "a person's own sign-in reads desks and cannot change one" '^ok$' python3 tools/desk_admin_checks.py scopes "$scopes"
  judged "a create with an image the grant does not list is refused, naming image_digests" \
    refused CONSTRAINT_IMAGE_DIGESTS E desk create walk-refused --provider claude-code --image "ghcr.io/ekka-ai/not-granted@sha256:$(printf '0%.0s' $(seq 64))"
  judged "a create with a provider the grant does not list is refused, naming providers" \
    refused CONSTRAINT_PROVIDERS E desk create walk-refused --provider walk-not-listed
  covers "ekka desk disconnect"
  # disconnect is workspace-wide. Never use a refusal test on another desk's connection.
  if E desk connections --json > "$TMP/provider-before.json" && \
     provider_scope=$(python3 tools/desk_admin_checks.py provider-scope "$TMP/provider-before.json" claude-code "$DESK"); then
    if [[ $provider_scope == isolated ]]; then
      judged "detaching a provider the walk desk requires is refused" \
        refused DESK_PROVIDER_REQUIRED E desk disconnect claude-code --yes
    else
      skip_leg "required-provider detach refusal" "claude-code is not exclusive to walk-desk; disconnect would visit other desks"
    fi
    if E desk connections --json > "$TMP/provider-after.json"; then
      t "all original model-provider attachments remain unchanged" '^ok$' \
        python3 tools/desk_admin_checks.py provider-preserved "$TMP/provider-before.json" claude-code "$TMP/provider-after.json"
    else
      FAIL=$((FAIL+1)); FAILED+=("provider attachments could not be read after the refusal leg")
    fi
  else
    FAIL=$((FAIL+1)); FAILED+=("provider attachments could not be read; no disconnect attempted")
  fi
  docker cp "$ADAPTER:/var/lib/ekka-backend" "$TMP/adapter-state" >/dev/null 2>&1
  docker logs "$ADAPTER" > "$TMP/adapter.log" 2>&1
  # The planted value is the adapter's own credential, piped to the check and never an argument or a file.
  t "the adapter keeps no credential: not in its store, not in its log, no bearer header" '^ok$' bash -c \
    "$(declare -f kc_admin); kc_admin keycloak/apply_realm.py admin-client on | python3 tools/desk_admin_checks.py clean '$TMP/adapter-state' '$TMP/adapter.log'"
  # Plan steps 3, 5 and 6 (desk lifecycle checks, regression coverage): a desk an admin makes, changes and
  # loses the grant for. Each change is a run with its own sealed receipt. (Who ran it: not on a 0.1.111 receipt.)
  PROBE=walk-probe
  desks_dir=$(E desk list | grep -o -E '/var/lib/ekka/[^ ]*/desks' | head -1)
  lock_holder_of() { cat "$desks_dir/.state/$1.lock" 2>/dev/null; }
  released() {  # no job and no change holds the desk: its state folder is there and its lock is not (a ready or
    # refused release removes the lock; an unknown one keeps it, marked). A lock that cannot be read is never released.
    [[ -n $desks_dir ]] && test -d "$desks_dir/.state" && ls "$desks_dir/.state" >/dev/null 2>&1 \
      && test ! -e "$desks_dir/.state/$1.lock" && echo released
  }
  desk_containers() {  # the desk's containers as settle sees them, by OpenShell's labels: [[id, state, sandbox id]]
    python3 -c 'import json, sys; sys.path.insert(0, "tools"); import desks_settle as d; print(json.dumps(d.containers(sys.argv[1])))' "$1"
  }
  RUN=""
  ready_and_ok() {  # ready_and_ok <exit code> <answer>: the command succeeded AND answered ready
    [[ $1 == 0 ]] || { echo "the command exited $1"; return; }
    python3 tools/desk_admin_checks.py outcome ready <<<"$2"
  }
  admin_run() {  # admin_run <label> <ekka desk args...>: succeeds, answers ready, and its receipt is its own, sealed
    local label=$1; shift; local out rc
    out=$(E desk "$@"); rc=$?
    RUN=$(grep -o -E 'Receipt: run [0-9a-f-]{36}' <<<"$out" | grep -o -E '[0-9a-f-]{36}$' | head -1)
    t "$label: ready" '^ok$' ready_and_ok "$rc" "$out"
    t "$label: its receipt is that run's, sealed" '^ok$' bash -c \
      "$(declare -f E quiet); E receipts show '$RUN' | python3 tools/desk_admin_checks.py sealed '$RUN'"
  }
  skip_leg "step 3 and 5: each receipt names the admin" "runner 0.1.111 prints who ran a change nowhere on a receipt"
  admin_run "step 3: an admin creates a desk" create "$PROBE" --provider claude-code
  t "step 3: the Enclave runs work on it" 'pinned' bash -c "$(declare -f E quiet pin_state until_state); DESK=$PROBE; until_state pinned; pin_state"
  t "step 3: the gateway shows its sandbox" "$PROBE" ./up.sh openshell sandbox get "$PROBE"
  admin_run "step 5: stop" stop "$PROBE"
  admin_run "step 5: start" start "$PROBE"
  skip_leg "step 5: a narrowing policy change" "the shipped grant gives a desk no rule it can lose (hosts come only from providers, and no new program is allowed): narrowing is walked with the approved test fixture, EKKA regression coverage"
  # A governed read since the release this setup needs (DESK_ADMIN_MIN_EKKA): a plan run that answers the desk's outcome.
  covers "ekka desk status"
  judged "status reads the desk through EKKA: ready, no change running" outcome ready E desk status "$PROBE"
  # Step 6: with no grant, a desk change is refused at dispatch. The grant comes back as an install restores it:
  # up.sh run again grants from the same limits file E4 wrote, kept byte for byte.
  enclave_ekka gate grant list --json > "$TMP/grants-before.json" 2>/dev/null
  gid=$(python3 tools/desk_admin_checks.py grant-id "$TMP/grants-before.json" | head -1)
  cp "$DESKS_STATE/backend/desks-grant.json" "$TMP/desks-grant.before.json" 2>/dev/null
  if [[ $gid =~ ^[A-Za-z0-9_-]+$ && -s $TMP/desks-grant.before.json ]]; then
    t "step 6: the desks grant is revoked" '.' E gate grant revoke "$gid"
    denied_out=$(E desk stop "$PROBE"); denied_rc=$?
    denied_run=$(grep -o -E 'Receipt: run [0-9a-f-]{36}' <<<"$denied_out" | grep -o -E '[0-9a-f-]{36}$' | head -1)
    if [[ -n $denied_run ]] && E run show "$denied_run" --json > "$TMP/grant-denied.json" \
       && E receipts show "$denied_run" --json > "$TMP/grant-denied-receipts.json"; then
      t "step 6: this run is RESOURCE_GRANT_DENIED before any step" '^ok$' \
        python3 tools/desk_admin_checks.py grant-denied "$TMP/grant-denied.json" "$TMP/grant-denied-receipts.json" "$denied_run" "$denied_rc"
    else
      FAIL=$((FAIL+1)); FAILED+=("step 6: the refused command's same-run diagnostics could not be read")
      printf "  ${r}FAIL${n}  step 6: no same-run grant-refusal diagnostics\n"
    fi
    t "step 6: up.sh run again restores the grant" 'desks grant|administer desks' up_again
    enclave_ekka gate grant list --json > "$TMP/grants-after.json" 2>/dev/null
    t "step 6: one desks grant again, with its limits" '^ok$' python3 tools/desk_admin_checks.py grant "$TMP/grants-after.json"
    t "step 6: from the same limits file" '^same$' bash -c "cmp -s '$TMP/desks-grant.before.json' '$DESKS_STATE/backend/desks-grant.json' && echo same"
  else FAIL=$((FAIL+1)); FAILED+=("step 6: no single desks grant to revoke, or no limits file to restore it from")
    printf "  ${r}FAIL${n}  step 6: no single desks grant to revoke, or no limits file to restore it from\n"; fi
  # Step 17c, this setup's (ADK-D6 test (f)): a stop still running in Docker when the gateway is killed. Opt-in, on a
  # disposable box only: it kills the gateway, and needs a desk whose main process ignores SIGTERM and a gateway
  # stop_timeout_secs of at least 60. tests/test_desks_settle.py StopStillRunning proves the procedure itself.
  slow_timeout=$(python3 -c 'import sys; sys.path.insert(0, "tools"); import desks_settle as d; print(d.stop_timeout(open(sys.argv[1]).read()))' "$DESKS_STATE/gateway.toml" 2>/dev/null || echo 0)
  if [[ ${WALK_SLOW_STOP:-} == 1 && -n ${WALK_SLOW_STOP_DESK:-} ]] && (( slow_timeout >= 60 )); then
    SLOW=$WALK_SLOW_STOP_DESK
    E desk stop "$SLOW" > "$TMP/slow-stop.out" 2>&1 &
    stop_pid=$!
    op=""
    for _ in $(seq 1 30); do h=$(lock_holder_of "$SLOW"); [[ $h == admin:* ]] && { op=${h#admin:}; op=${op%% *}; break; }; sleep 1; done
    docker kill ekka-desks-gateway-1 >/dev/null 2>&1   # the gateway dies mid-stop; Docker goes on stopping the container
    wait "$stop_pid" 2>/dev/null || true
    t "step 17c: while the stop runs, the change still holds the desk" "admin:$op" lock_holder_of "$SLOW"
    t "step 17c: settle waits for the container to exit, then writes the marker" "wrote settle/$op.json" ./up.sh desks settle "$SLOW" --operation "$op"
    t "step 17c: every container of the desk had exited" '^ok$' bash -c \
      "$(declare -f desk_containers); desk_containers '$SLOW' | python3 tools/desk_admin_checks.py exited"
    # The end of it, not any answer (Codex): status settles the stop as done, and the change lets go of the desk.
    judged "step 17c: status then settles the stop as ready" outcome ready E desk status "$SLOW"
    t "step 17c: the change no longer holds the desk" '^released$' released "$SLOW"
  else
    skip_leg "step 17c: a stop still running when the gateway is killed" "opt-in on a disposable box: WALK_SLOW_STOP=1, WALK_SLOW_STOP_DESK=<a desk ignoring SIGTERM>, gateway stop_timeout_secs >= 60 (now ${slow_timeout:-unknown})"
  fi
  skip_leg "the kill switch with EKKA unreachable" "requires a network fault inside the Enclave container; changing the host's /etc/hosts does not isolate it"
  skip_leg "a member is refused at dispatch" "needs a second person; scripts/cleanup-walk.sh walks it against Govern"
  skip_leg "a copied plan without requires_role" "needs a member; scripts/cleanup-walk.sh walks it against Govern"
  # This Enclave handles tasks serially (runner112 run_loop.rs:210-215). A stop requested during a job queues.
  # Observe the job lock first, then the change claim; prove both complete in order, not a fictitious DESK_BUSY.
  lock_holder() { lock_holder_of "$DESK"; }
  E plan run "$ask" --input "question=Which files are in this repository?" > "$TMP/held-job.out" 2>&1 &
  job_pid=$!
  held=""
  for _ in $(seq 1 90); do h=$(lock_holder); [[ -n $h && $h != admin:* ]] && { held=$h; break; }; sleep 1; done
  if [[ -n $held ]]; then
  ./up.sh ekka desk stop "$DESK" --json > "$TMP/queued-stop.out" 2> "$TMP/queued-stop.progress" &
    stop_pid=$!; saw_admin=""
    watch_deadline=$((SECONDS + 900)); watch_expired=0
    while :; do
      h=$(lock_holder)
      [[ $h == admin:* ]] && saw_admin=$h
      if ! kill -0 "$job_pid" 2>/dev/null && ! kill -0 "$stop_pid" 2>/dev/null; then break; fi
      if (( SECONDS >= watch_deadline )); then
        watch_expired=1
        FAIL=$((FAIL+1)); FAILED+=("the queued tasks exceeded the 15-minute observation window")
        printf "  ${r}FAIL${n}  queued tasks exceeded the observation window\n"
        break
      fi
      sleep 0.2
    done
    wait "$job_pid"; job_rc=$?
    wait "$stop_pid"; stop_rc=$?
    t "the observed job completes successfully" 'Plan completed' bash -c '[[ $1 == 0 ]] && cat "$2"' _ "$job_rc" "$TMP/held-job.out"
    t "the queued stop completes ready" '^ok$' ready_and_ok "$stop_rc" "$(cat "$TMP/queued-stop.out")"
    run_of "the observed job" "$(cat "$TMP/held-job.out")"; job_run=$RUN_OF
    stop_run=$(python3 - "$TMP/queued-stop.out" <<'PYCODE'
import json,re,sys
try:
    identity=json.load(open(sys.argv[1])).get("run")
    if not isinstance(identity,str) or not re.fullmatch(r"[0-9a-f-]{36}",identity): raise ValueError()
    print(identity)
except (ValueError,OSError):
    print("-")
PYCODE
    )
    t "an admin change claim follows the observed job holder" '^admin:[0-9a-f-]{36}$' printf '%s\n' "$saw_admin"
    if E run list --limit 20 --json > "$TMP/queued-runs.json"; then
      t "a change requested during a job completes after that job" '^ok$' \
        python3 tools/desk_admin_checks.py completed-in-order "$TMP/queued-runs.json" "$job_run" "$stop_run"
    else
      FAIL=$((FAIL+1)); FAILED+=("the queued tasks' outcomes could not be read")
    fi
    t "neither task leaves the desk held" '^released$' released "$DESK"
  else
    wait "$job_pid" 2>/dev/null || true
    FAIL=$((FAIL+1)); FAILED+=("the ask job never held walk-desk, so queue ordering was not walked")
    printf "  ${r}FAIL${n}  the ask job never held walk-desk, so queue ordering was not walked\n"
  fi
  skip_leg "a concurrent job/admin DESK_BUSY refusal" "the default Enclave executes one task at a time; the same-lock refusal is tested in runner v0.1.112 crates/ekka-openshell-executor/src/desk_claims_tests.rs:45-55"
  skip_leg "replays, lost replies, crashes, races" "the adapter's own CI against its fake gateway (ADK-D6, D7), never a hook in this image"
else
  skip_leg "desk administration through EKKA" "the backend adapter is not installed here (the desk-admin profile is off)"
  not_walked "ekka desk status" "a governed read through the backend adapter, which is not installed here"
fi

section "clean up"
covers "ekka desk delete"
t "delete walk-desk" '.' E desk delete "$DESK" --yes
if desk_admin_on; then
  judged "another delete is refused because the desk is already gone" refused DESK_NOT_FOUND E desk delete "$DESK" --yes
  admin_run "the walk's probe desk is deleted" delete walk-probe --yes
fi
t "disconnect the unused throwaway key leaves no desk changed" "No EKKA desk uses 'walk-dummy'; nothing was detached" E desk disconnect walk-dummy --yes
t "the server's administrator removes the throwaway key" '.' ./up.sh openshell provider delete walk-dummy
rm -f "$DESKS_STATE/providers.local/walk-dummy.yaml"
if desk_admin_on; then
  t "the desks grant no longer names the throwaway key" 'clean' bash -c \
    "$(declare -f up_again); up_again >/dev/null 2>&1; ! grep -q walk-dummy '$DESKS_STATE/backend/desks-grant.json' && echo clean"
fi

took=$(( $(date -u +%s) - STARTED ))
printf '\n\033[1mwalk:\033[0m %d passed, %d failed, %d not walked, %ds\n' "$PASS" "$FAIL" "$SKIP" "$took"
for f in "${FAILED[@]:-}"; do [[ -n $f ]] && printf "  ${r}FAIL${n}  %s\n" "$f"; done
for s in "${SKIPPED[@]:-}"; do [[ -n $s ]] && printf "  ${y}SKIP${n}  %s\n" "$s"; done
. "$DESKS_STATE/up.env"
python3 - "$DESKS_STATE/walks.jsonl" "$PASS" "$FAIL" "$SKIP" "$took" "${EKKA_VERSION:-}" "$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || cat "$ROOT/.source-commit" 2>/dev/null || echo unknown)" "$(P --version | head -1)" <<'PY'
import json, sys, datetime
path, p, f, s, took, ver, src, person = sys.argv[1:]
row = {"at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "walk": "desks",
       "passed": int(p), "failed": int(f), "not_walked": int(s), "duration_s": int(took),
       "enclave_version": ver, "person_ekka": person, "repository": src, "verdict": "pass" if int(f) == 0 else "fail"}
open(path, "a").write(json.dumps(row) + "\n"); print("recorded in", path)
PY
[[ $FAIL == 0 ]]
