#!/bin/bash
# Create or reuse a stable self-signed code signing certificate.
#
# A stable identity ensures TCC grants (Microphone, Accessibility, AppleScript)
# survive across rebuilds. Without it, ad-hoc (-s -) signing creates a new
# identity each time, forcing re-grants.

set -euo pipefail

IDENTITY_NAME="friday-dev"
KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"

if security find-identity -v -p codesigning "$KEYCHAIN" 2>/dev/null | grep -q "\"$IDENTITY_NAME\""; then
    echo "Signing identity '$IDENTITY_NAME' already exists and is trusted"
    exit 0
fi

# Fallback: check if macbrow-dev already exists
if security find-identity -v -p codesigning "$KEYCHAIN" 2>/dev/null | grep -q "\"macbrow-dev\""; then
    echo "Reusing existing trusted identity 'macbrow-dev'"
    exit 0
fi

echo "Creating self-signed code-signing certificate '$IDENTITY_NAME'..."

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

cat > "$WORKDIR/openssl.cnf" <<EOF
[req]
distinguished_name = dn
x509_extensions = v3_ext
prompt = no

[dn]
CN = $IDENTITY_NAME

[v3_ext]
keyUsage = critical, digitalSignature
extendedKeyUsage = critical, codeSigning
basicConstraints = critical, CA:false
EOF

openssl req -x509 -newkey rsa:2048 \
    -keyout "$WORKDIR/key.pem" -out "$WORKDIR/cert.pem" \
    -days 3650 -nodes -config "$WORKDIR/openssl.cnf"

PASSPHRASE="$(openssl rand -base64 24)"
openssl pkcs12 -export \
    -out "$WORKDIR/identity.p12" \
    -inkey "$WORKDIR/key.pem" -in "$WORKDIR/cert.pem" \
    -name "$IDENTITY_NAME" -passout "pass:$PASSPHRASE" -legacy

security import "$WORKDIR/identity.p12" -k "$KEYCHAIN" -P "$PASSPHRASE" -T /usr/bin/codesign -A
security add-trusted-cert -r trustRoot -p codeSign -k "$KEYCHAIN" "$WORKDIR/cert.pem"

if ! security find-identity -v -p codesigning "$KEYCHAIN" 2>/dev/null | grep -q "\"$IDENTITY_NAME\""; then
    echo "ERROR: identity '$IDENTITY_NAME' was created but is not a valid codesigning identity" >&2
    exit 1
fi

echo "Self-signed certificate '$IDENTITY_NAME' created and trusted for code signing"
