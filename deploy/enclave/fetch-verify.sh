#!/bin/sh
# Inside the image build only. Downloads one EKKA release for x86_64-unknown-linux-musl and refuses it unless:
#   1. the archive's sha256 equals the release manifest's;
#   2. cosign verify-blob passes with the signer pinned to EXACTLY release-binaries.yml@refs/tags/v<version>;
#   3. the binary says `ekka <version>`.
set -eu
VER=$1; TAG=v$VER; TARGET=x86_64-unknown-linux-musl; BASE=https://get.ekka.ai/runner/$TAG
SIGNER="https://github.com/ekka-ai/ekka-runner/.github/workflows/release-binaries.yml@refs/tags/$TAG"
W=/work; mkdir -p $W /out
get() { curl -fsSL "$BASE/$1" -o "$2"; }
get manifest.json $W/manifest.json
LINE=$(tr '}' '\n' < $W/manifest.json | grep "\"target\"[[:space:]]*:[[:space:]]*\"$TARGET\"" | head -1)
FILE=$(echo "$LINE" | sed -n 's/.*"filename"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
WANT=$(echo "$LINE" | sed -n 's/.*"sha256"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
MVER=$(tr ',' '\n' < $W/manifest.json | sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
[ "$MVER" = "$VER" ] || { echo "manifest says version $MVER, not $VER"; exit 1; }
[ -n "$FILE" ] && echo "$WANT" | grep -qE '^[0-9a-f]{64}$' || { echo "no $TARGET entry in the manifest"; exit 1; }
for x in "" .sig .crt; do get "$FILE$x" "$W/$FILE$x"; done
echo "$WANT  $W/$FILE" | sha256sum -c - >/dev/null || { echo "archive sha256 differs from the manifest"; exit 1; }
cosign verify-blob --certificate "$W/$FILE.crt" --signature "$W/$FILE.sig" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com --certificate-identity "$SIGNER" "$W/$FILE"
tar -xzf "$W/$FILE" -C $W
BIN=$(find $W -type f -name ekka | head -1); [ -n "$BIN" ] || { echo "no ekka binary"; exit 1; }
[ "$("$BIN" --version | head -1)" = "ekka $VER" ] || { echo "the binary is not ekka $VER"; exit 1; }
install -m 0755 "$BIN" /out/ekka
sha256sum /out/ekka | cut -d' ' -f1 > /out/ekka.sha256
echo "verified: ekka $VER, archive $WANT, binary $(cat /out/ekka.sha256), signer $SIGNER"
