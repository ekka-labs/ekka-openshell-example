#!/usr/bin/env bash
# This setup's certificates.
#
#   deploy/certs.sh init  <public-name>   a local CA (5 years), the gateway and edge server
#                                              certificates (1 year) and the sandbox JWT keys
#   deploy/certs.sh check                 every expiry; exit 1 if any is within 30 days
#   deploy/certs.sh renew <public-name>   new server certificates from the SAME CA
#
# Types match what OpenShell 0.1.3's own generator makes: EC P-256 certificates under a
# CA:TRUE root, Ed25519 JWT keys and a 32-hex-character kid. Not NVIDIA's generate-certs itself: it
# has no lifetime option and issues certificates valid to the year 4096, so nothing would rotate.
# A customer with their own certificates skips init and places tls.crt/tls.key under gateway/ and edge/.
set -euo pipefail
umask 077
STATE=${DESKS_STATE:-/var/lib/ekka-agent-desks}
T=$STATE/tls
CA_DAYS=1826
SERVER_DAYS=365
WARN_DAYS=30

die() { echo "certs: $*" >&2; exit 1; }

server_cert() { # server_cert <dir> <public-name> <extra SANs...>
  local dir=$T/$1 name=$2; shift 2
  local san="DNS:$name"
  for s in "$@"; do
    if [[ $s =~ ^[0-9.:]+$ ]]; then san="$san,IP:$s"; else san="$san,DNS:$s"; fi
  done
  mkdir -p "$dir"
  openssl ecparam -name prime256v1 -genkey -noout -out "$dir/tls.key.new"
  openssl req -new -key "$dir/tls.key.new" -subj "/CN=$name" -out "$dir/tls.csr"
  openssl x509 -req -in "$dir/tls.csr" -CA "$T/ca.crt" -CAkey "$T/ca.key" -CAcreateserial \
    -days "$SERVER_DAYS" -sha256 -out "$dir/tls.crt.new" \
    -extfile <(printf 'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyAgreement\nextendedKeyUsage=serverAuth\nsubjectAltName=%s\n' "$san") 2>/dev/null
  rm -f "$dir/tls.csr"
  mv "$dir/tls.key.new" "$dir/tls.key"; mv "$dir/tls.crt.new" "$dir/tls.crt"
  chmod 0644 "$dir/tls.crt"
}

servers() { # the gateway: people by public name, desk supervisors over the host loopback
  server_cert gateway "$1" host.openshell.internal localhost 127.0.0.1
  server_cert edge "$1" localhost 127.0.0.1   # the sign-in admin listener is on 127.0.0.1
}

case "${1:-}" in
  init)
    name=${2:-}; [[ -n $name ]] || die "usage: init <public-name>"
    mkdir -p "$T"
    if [[ -f $T/ca.key ]]; then
      echo "certs: a CA already exists in $T; keeping it (renew makes new server certificates)"
    else
      openssl ecparam -name prime256v1 -genkey -noout -out "$T/ca.key"
      openssl req -x509 -new -key "$T/ca.key" -subj "/CN=EKKA desks CA ($name)" -days "$CA_DAYS" -sha256 \
        -addext "basicConstraints=critical,CA:TRUE,pathlen:0" -addext "keyUsage=critical,keyCertSign,cRLSign" \
        -addext "nameConstraints=critical,permitted;DNS:$name,permitted;DNS:host.openshell.internal,permitted;DNS:localhost,permitted;IP:127.0.0.1/255.255.255.255" \
        -out "$T/ca.crt"
      # Name-constrained: every person's computer trusts this CA for the sign-in page, so it may only ever
      # vouch for this server's own names. A stolen ca.key cannot impersonate any other site.
      chmod 0644 "$T/ca.crt"
    fi
    servers "$name"
    if [[ ! -f $T/jwt/signing.pem ]]; then
      mkdir -p "$T/jwt"
      openssl genpkey -algorithm ed25519 -out "$T/jwt/signing.pem"
      openssl pkey -in "$T/jwt/signing.pem" -pubout -out "$T/jwt/public.pem"
      openssl rand -hex 16 | tr -d '\n' > "$T/jwt/kid"
    fi
    bash "${BASH_SOURCE[0]}" bundle
    bash "${BASH_SOURCE[0]}" check
    ;;
  renew)
    name=${2:-}; [[ -n $name ]] || die "usage: renew <public-name>"
    [[ -f $T/ca.key ]] || die "no CA in $T; run init first"
    servers "$name"
    echo "certs: renewed; restart the gateway and edge to load them"
    bash "${BASH_SOURCE[0]}" check
    ;;
  bundle)
    # The gateway's TLS trust: the system's public roots AND the desks CA. Trusting only the desks CA
    # (SSL_CERT_FILE=/tls/ca.crt) made every public host untrusted to the gateway's own clients, so a
    # gateway-refreshed provider (AWS STS, OAuth token grants) failed with "dispatch failure".
    # Rebuilt on every start, so it follows the host's CA updates.
    sys=/etc/ssl/certs/ca-certificates.crt
    [[ -f $sys ]] || die "no system CA bundle at $sys"
    [[ -f $T/ca.crt ]] || die "missing $T/ca.crt; run init first"
    cat "$sys" "$T/ca.crt" > "$T/ca-bundle.crt.tmp" && chmod 0644 "$T/ca-bundle.crt.tmp" && mv "$T/ca-bundle.crt.tmp" "$T/ca-bundle.crt"
    echo "certs: gateway trust = system roots + the desks CA"
    ;;
  check)
    soon=0
    for c in "$T/ca.crt" "$T/gateway/tls.crt" "$T/edge/tls.crt"; do
      [[ -f $c ]] || die "missing $c"
      end=$(openssl x509 -in "$c" -noout -enddate | cut -d= -f2)
      if openssl x509 -in "$c" -noout -checkend $((WARN_DAYS * 86400)) >/dev/null; then
        echo "ok      ${c#$STATE/}  until $end"
      else
        echo "RENEW   ${c#$STATE/}  until $end (within $WARN_DAYS days)"; soon=1
      fi
    done
    exit $soon
    ;;
  *) die "usage: init <public-name> | check | renew <public-name> | bundle" ;;
esac
