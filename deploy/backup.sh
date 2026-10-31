#!/usr/bin/env bash
# This setup's backup and restore (docs/OPERATIONS.md, "Backup and restore"). Run as root from deploy/.
#
#   backup.sh backup  [<dir>]     one dated folder under <dir> (default $DESKS_STATE/backups), mode 0700
#   backup.sh restore <folder>    this setup back from one such folder, onto this machine
#
# What is in a backup: Keycloak's database (pg_dump), the gateway's database volume, the Enclave's home
# (vault, desk files, attempt journal) and $DESKS_STATE (certificates, secrets, gateway.toml, desks.env).
# Desk workspaces are NOT backed up: they are working state.
# A backup is a SECRET: the Enclave's home holds its vault AND, with custody "file", the vault's unlock key.
set -euo pipefail
cd "$(dirname "$0")"
export DESKS_STATE=${DESKS_STATE:-/var/lib/ekka-agent-desks}
ALPINE=alpine:3.24.2@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6
# ⛔ up.env FIRST, AS setup.sh DOES: it holds the Enclave version, the binds and telemetry that
# `up.sh` chose. Without it, every restart below came back on compose.yml's DEFAULTS, so a backup on
# 2026-10-14 restarted a 0.1.108 Enclave on 0.1.105, below the release that cannot be rolled back.
P() {
  local envs=()
  [[ -f $DESKS_STATE/up.env ]] && envs+=(--env-file "$DESKS_STATE/up.env")
  docker compose -f compose.yml ${envs[@]+"${envs[@]}"} --env-file "$DESKS_STATE/desks.env" "$@"
}
die() { echo "backup: $*" >&2; exit 1; }
# The gateway never starts able to hand desk credentials out, from here either.
preflight() { P config --format json | python3 ../tools/gateway_preflight.py; }
say() { echo "backup: $*"; }
[[ $(id -u) == 0 ]] || die "run as root: the backup holds root-only secrets"
[[ -f $DESKS_STATE/desks.env ]] || die "no $DESKS_STATE/desks.env: this machine has no desks setup"
vol() { docker run --rm -v "ekka-desks_$1:/v" -v "$2:/b" "$ALPINE" sh -eu -c "$3"; }

case "${1:-}" in
  backup)
    root=${2:-$DESKS_STATE/backups}; d=$root/$(date -u +%Y%m%dT%H%M%SZ)
    install -d -m 0700 "$root" "$d"
    say "Keycloak's database"
    P exec -T keycloak-db pg_dump -U keycloak -d keycloak --format=custom > "$d/keycloak.pgdump"
    was=$(P ps --services --status running 2>/dev/null | grep -xE 'ekka-enclave|gateway' || true)
    say "stopping the gateway, then the Enclave (reverse start order), to copy their state consistently"
    P stop gateway ekka-enclave >/dev/null 2>&1 || true
    vol gateway-state "$d" "tar -C /v -czf /b/gateway-state.tgz ."
    vol ekka-home "$d" "tar -C /v -czf /b/ekka-home.tgz ."
    if [[ -n $was ]]; then   # only what was running before: an unenrolled Enclave is not started here
      say "starting again: $(echo "$was" | tr '\n' ' ')"
      if [[ $was == *gateway* ]] && ! preflight; then
        say "the gateway preflight refused (above): starting only the Enclave; the backup itself is complete"
        was=$(echo "$was" | grep -vx gateway || true)
      fi
      # shellcheck disable=SC2086
      [[ -z $was ]] || P up -d $was >/dev/null 2>&1 || say "a service did not start; see: setup.sh compose ps"
    fi
    tar -C "$DESKS_STATE" --exclude=./backups -czf "$d/desks-state.tgz" .
    ( cd "$d" && sha256sum ./*.tgz ./*.pgdump > SHA256SUMS )
    chmod 0600 "$d"/*
    say "done: $d"; ls -l "$d" | tail -n +2
    ;;
  restore)
    d=${2:-}; [[ -d $d && -f $d/SHA256SUMS ]] || die "usage: restore <a folder this script wrote>"
    ( cd "$d" && sha256sum -c --quiet SHA256SUMS ) || die "the backup does not match its SHA256SUMS; nothing restored"
    say "stopping this setup (data volumes are replaced, the images stay)"
    P down >/dev/null 2>&1 || true
    tar -C "$DESKS_STATE" -xzf "$d/desks-state.tgz"
    for v in gateway-state ekka-home; do
      docker volume rm -f "ekka-desks_$v" >/dev/null; docker volume create "ekka-desks_$v" >/dev/null
      vol "$v" "$d" "tar -C /v -xzpf /b/$v.tgz"
    done
    docker volume rm -f ekka-desks_keycloak-db >/dev/null
    P up -d keycloak-db >/dev/null
    for _ in $(seq 1 30); do P exec -T keycloak-db pg_isready -U keycloak -d keycloak >/dev/null 2>&1 && break; sleep 2; done
    P exec -T keycloak-db pg_restore -U keycloak -d keycloak --clean --if-exists --no-owner < "$d/keycloak.pgdump"
    P up -d keycloak edge >/dev/null
    if vol ekka-home "$d" "ls /v/*/enclave.env" >/dev/null 2>&1; then
      preflight || die "the gateway preflight refused the restored setup (above); the data is restored, nothing else started"
      say "the restored home holds an enrolled Enclave: starting the Enclave, then the gateway"
      P up -d >/dev/null
    else
      say "the restored home has no enrolled Enclave: the identity stack is up; enroll, then setup.sh compose up -d"
    fi
    say "restored from $d; check: setup.sh compose ps; deploy/keycloak/apply_realm.py apply (says nothing to do)"
    ;;
  *) sed -n '2,11p' "$0"; exit 2 ;;
esac
