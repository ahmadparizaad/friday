#!/bin/bash
# Install the built Friday.app to /Applications
#
# Steps:
# 1. Cleanly quit any running instance
# 2. Copy bundle to /Applications
# 3. Register with LaunchServices
# 4. Make it executable

set -euo pipefail

cd "$(dirname "$0")/.."

step() {
    echo "[*] $@"
}

step "Installing Friday.app..."

# 1. Quit running instances
step "Checking for running instances..."
osascript -e 'quit app "Friday"' 2>/dev/null || true
osascript -e 'quit app "friday-ui"' 2>/dev/null || true
sleep 1

# 2. Copy to /Applications
step "Copying to /Applications..."
if [ -d "build/Friday.app" ]; then
    rm -rf /Applications/Friday.app
    ditto build/Friday.app /Applications/Friday.app 2>&1 || {
        echo "ERROR: Failed to copy to /Applications" >&2
        exit 1
    }
else
    echo "ERROR: build/Friday.app not found. Run './scripts/build_app.sh' first." >&2
    exit 1
fi

# 3. Register with LaunchServices
step "Registering with LaunchServices..."
/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister -f /Applications/Friday.app 2>&1 || true

# 4. Ensure executable
chmod +x /Applications/Friday.app/Contents/MacOS/friday

# 5. Re-verify after the copy — ditto preserves signatures, trust it
# and check (M5). A broken seal here means the bundle was damaged in
# transit, not that the build was wrong.
step "Verifying installed signature..."
codesign --verify --strict /Applications/Friday.app || {
    echo "ERROR: installed bundle fails signature verification" >&2
    exit 1
}

step "Installation complete: /Applications/Friday.app"
echo ""
echo "You can now launch Friday from Spotlight, Launchpad, or the Applications folder."
