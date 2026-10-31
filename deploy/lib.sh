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

enclave_home_file() {  # enclave_home_file <relative path> <mode> < content: a file the Enclave's user owns, written whole
  local rel=$1 mode=$2 tmp
  tmp=$(mktemp -p "$ENCLAVE_HOME" ".write.XXXXXX")
  cat > "$tmp"
  chown "$ENCLAVE_USER:$ENCLAVE_USER" "$tmp"; chmod "$mode" "$tmp"
  mv -f "$tmp" "$ENCLAVE_HOME/$rel"
}
