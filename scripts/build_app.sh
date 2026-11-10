#!/bin/bash
# Build the Friday.app bundle
#
# Rebuilds the bundle from scratch:
# 1. Check system requirements (macOS 26+, swiftc)
# 2. Compile Swift UI into bundle executable
# 3. Copy Python core into Resources/core/friday
# 4. Set up Python virtual environment
# 5. Render Info.plist
# 6. Code-sign the bundle with stable trusted certificate
# 7. Verify signature

set -euo pipefail

cd "$(dirname "$0")/.."

BUNDLE_PATH="build/Friday.app"
VENV_PATH="$HOME/Library/Application Support/friday/venv"

step() {
    echo "[*] $@"
}

fail() {
    echo "ERROR: $@" >&2
    exit 1
}

step "Checking system requirements..."
MACOS_VERSION=$(sw_vers -productVersion | cut -d. -f1)
if [ "$MACOS_VERSION" -lt 26 ]; then
    fail "Requires macOS 26 or later, found $MACOS_VERSION"
fi

if ! command -v swiftc >/dev/null 2>&1; then
    fail "swiftc not found in PATH"
fi

step "Cleaning previous build..."
rm -rf "$BUNDLE_PATH"

BUNDLE_MACOS="$BUNDLE_PATH/Contents/MacOS"
mkdir -p "$BUNDLE_MACOS"

step "Compiling Swift UI..."
swiftc -O \
    ui/Settings.swift \
    ui/NotchWindow.swift \
    ui/Microphone.swift \
    ui/StatusMenu.swift \
    ui/CoreProcess.swift \
    ui/main.swift \
    -o "$BUNDLE_MACOS/friday" || fail "Swift compilation failed"

step "Copying Python core into bundle..."
BUNDLE_RESOURCES="$BUNDLE_PATH/Contents/Resources"
CORE_PATH="$BUNDLE_RESOURCES/core/friday"
mkdir -p "$CORE_PATH"

rsync -av --exclude='__pycache__' --exclude='*.pyc' src/friday/ "$CORE_PATH/"

# The skill store resolves skills/ as a sibling of core/ (store.py DEFAULT_SKILLS_DIR).
step "Copying skills into bundle..."
rsync -av --exclude='__pycache__' --exclude='*.pyc' skills/ "$BUNDLE_RESOURCES/skills/"

step "Setting up Python virtual environment..."
mkdir -p "$(dirname "$VENV_PATH")"

if [ ! -f "$VENV_PATH/bin/python" ]; then
    if command -v uv >/dev/null 2>&1; then
        uv venv "$VENV_PATH" --python 3.12
        uv pip install --python "$VENV_PATH/bin/python" -e .
    else
        python3 -m venv "$VENV_PATH"
        "$VENV_PATH/bin/pip" install -e .
    fi
fi

CORE_PYTHON="$VENV_PATH/bin/python3"
step "Venv ready at $VENV_PATH"

step "Precompiling Python core bytecode..."
"$CORE_PYTHON" -m compileall --invalidation-mode unchecked-hash "$CORE_PATH/" || fail "Python compilation failed"

step "Rendering Info.plist..."
VERSION="0.1.0"
if [ -f "pyproject.toml" ]; then
    VERSION=$(grep -m1 'version' pyproject.toml | sed 's/.*version.*=.*"\([^"]*\)".*/\1/' || echo "0.1.0")
fi

mkdir -p "$BUNDLE_PATH/Contents"
sed \
    -e "s|{VERSION}|$VERSION|g" \
    -e "s|{CORE_PYTHON}|$CORE_PYTHON|g" \
    -e "s|{CORE_DIR}|core|g" \
    packaging/Info.plist > "$BUNDLE_PATH/Contents/Info.plist"

# FRIDAY_SIGN_IDENTITY signs with an existing certificate instead. macOS ties
# Microphone/Accessibility grants to the signing certificate, so reusing the
# one a previous build was signed with keeps those grants.
IDENTITY="${FRIDAY_SIGN_IDENTITY:-friday-dev}"
if [ -z "${FRIDAY_SIGN_IDENTITY:-}" ]; then
    step "Setting up code signing identity..."
    ./scripts/create_signing_identity.sh || true
fi
if ! security find-identity -v -p codesigning 2>/dev/null | grep -q "\"$IDENTITY\""; then
    [ -n "${FRIDAY_SIGN_IDENTITY:-}" ] && fail "signing identity '$IDENTITY' not found"
    IDENTITY="-"
fi

step "Signing bundle with identity: $IDENTITY..."
codesign --force --sign "$IDENTITY" --identifier "com.friday.local" "$BUNDLE_PATH" || fail "Code signing failed"

step "Verifying signature and plist..."
codesign --verify --strict "$BUNDLE_PATH" || fail "Signature verification failed"
plutil -lint "$BUNDLE_PATH/Contents/Info.plist" >/dev/null || fail "Info.plist invalid"

echo ""
echo "SUCCESS: Built $BUNDLE_PATH"
echo "  Bundle ID: com.friday.local"
echo "  Version: $VERSION"
echo "  Signed with: $IDENTITY"
