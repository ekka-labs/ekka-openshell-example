#!/bin/sh
# Runs inside the kit's pinned Alpine helper with only the Enclave home mounted.
# Change the old kit address, preserving enrollment, TLS identity and every other key.
set -eu
file=$1
[ ! -L "$file" ] || { echo 'loopback migration: enclave.env must be a regular file' >&2; exit 1; }
[ -e "$file" ] || exit 0                 # initial installation, before enrollment
[ -f "$file" ] || exit 1
next="$file.loopback-new.$$"
trap 'rm -f "$next"' EXIT HUP INT TERM
cp -p "$file" "$next"
awk '
  /^EKKA_OPENSHELL_EXECUTOR_URL=/ {
    count++
    if ($0 != "EKKA_OPENSHELL_EXECUTOR_URL=https://gateway:8080" &&
        $0 != "EKKA_OPENSHELL_EXECUTOR_URL=https://127.0.0.1:8080") bad=1
    print "EKKA_OPENSHELL_EXECUTOR_URL=https://127.0.0.1:8080"
    next
  }
  { print }
  END { if (count != 1 || bad) exit 1 }
' "$file" > "$next" || {
  echo 'loopback migration: expected exactly one kit executor URL; no configuration was changed' >&2
  exit 1
}
if ! cmp -s "$file" "$next"; then
  cp -p "$file" "$file.before-loopback"
  chmod 0600 "$next"
  mv "$next" "$file"
fi
