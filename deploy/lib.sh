# Shared by up.sh and setup.sh: the ONE way scripts call this server's Enclave (the same step the installed
# /usr/local/sbin/ekka wrapper makes). Never a container, never a compose exec: the Enclave is a systemd service
# and its files belong to the `ekka` user.
ENCLAVE_USER=ekka
ENCLAVE_HOME=/var/lib/ekka/.ekka
ENCLAVE_BIN=/usr/local/bin/ekka

enclave_ekka() {  # enclave_ekka <ekka args...>: stdin, stdout and the exit status pass through
  runuser -u "$ENCLAVE_USER" -- /usr/bin/env -i \
    PATH=/usr/local/bin:/usr/bin:/bin HOME=/var/lib/ekka EKKA_HOME="$ENCLAVE_HOME" \
    EKKA_ENV="${EKKA_ENV:-prod}" TERM="${TERM:-dumb}" LANG=C.UTF-8 \
    "$ENCLAVE_BIN" "$@"
}

# enclave_home_file <relative path> <mode> < content: a file in the Enclave's home, written whole, AS THE ENCLAVE'S USER.
# Never as root: root following a path inside a folder that user can write (a planted link named `plans`) would carry
# root's authority outside the home (Codex review of #4, reproduced). As `ekka`, a link the Enclave planted leads only to
# what `ekka` may already touch. The relative path must be a plain path: no `..`, no leading `/`.
enclave_home_file() {
  local rel=$1 mode=$2
  case $rel in */../*|../*|*/..|..|/*) echo "enclave_home_file: refused path $rel" >&2; return 1 ;; esac
  runuser -u "$ENCLAVE_USER" -- /usr/bin/env -i PATH=/usr/bin:/bin HOME=/var/lib/ekka \
    bash -c 'set -euo pipefail; umask 077; home=$1 rel=$2 mode=$3
      dir=$(dirname "$home/$rel")
      [[ -L $dir ]] && { echo "enclave_home_file: $dir is a link" >&2; exit 1; }
      mkdir -p "$dir"
      tmp=$(mktemp -p "$dir" ".write.XXXXXX"); cat > "$tmp"; chmod "$mode" "$tmp"
      [[ -L $home/$rel ]] && { rm -f "$tmp"; echo "enclave_home_file: $home/$rel is a link" >&2; exit 1; }
      mv -f "$tmp" "$home/$rel"' _ "$ENCLAVE_HOME" "$rel" "$mode"
}

# enclave_home_read <relative path>: a file in the Enclave's home, read AS THE ENCLAVE'S USER (same reason).
enclave_home_read() {
  local rel=$1
  case $rel in */../*|../*|*/..|..|/*) echo "enclave_home_read: refused path $rel" >&2; return 1 ;; esac
  runuser -u "$ENCLAVE_USER" -- /usr/bin/env -i PATH=/usr/bin:/bin HOME=/var/lib/ekka \
    bash -c 'set -euo pipefail; [[ -L $1 ]] && { echo "enclave_home_read: $1 is a link" >&2; exit 1; }; cat "$1"' _ "$ENCLAVE_HOME/$rel"
}
