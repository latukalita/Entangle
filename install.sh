#!/usr/bin/env bash
# Entangle installer: downloads the latest prebuilt Linux release and adds it to your
# app menu and PATH. No Python, cloning or building needed.
#   Install:   curl -fsSL https://raw.githubusercontent.com/latukalita/Entangle/main/install.sh | bash
#   Uninstall: curl -fsSL https://raw.githubusercontent.com/latukalita/Entangle/main/install.sh | bash -s -- --uninstall
set -euo pipefail

REPO="latukalita/Entangle"
APP_DIR="$HOME/.local/share/entangle"
BIN="$HOME/.local/bin/entangle"
DESKTOP="$HOME/.local/share/applications/entangle.desktop"
ICON="$HOME/.local/share/icons/hicolor/256x256/apps/entangle.png"

if [ "${1:-}" = "--uninstall" ]; then
    rm -rf "$APP_DIR" "$BIN" "$DESKTOP" "$ICON"
    echo "Entangle removed. Your saved login details are in ~/.guide_runner.json (delete it too if you like)."
    exit 0
fi

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
    echo "Sorry, the prebuilt Entangle only runs on 64-bit Linux (x86_64)." >&2; exit 1
fi

# fetch URL FILE [quiet]
fetch() {
    if command -v curl >/dev/null; then
        if [ -n "${3:-}" ]; then curl -fsSL -o "$2" "$1"; else curl -fL --progress-bar -o "$2" "$1"; fi
    elif command -v wget >/dev/null; then
        if [ -n "${3:-}" ]; then wget -q -O "$2" "$1"; else wget -q --show-progress -O "$2" "$1"; fi
    else
        echo "Please install curl or wget first." >&2; exit 1
    fi
}

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
echo "Downloading Entangle (about 110 MB)..."
fetch "https://github.com/$REPO/releases/latest/download/Entangle-linux.tar.xz" "$tmp/entangle.tar.xz"
echo "Unpacking..."
tar -xJf "$tmp/entangle.tar.xz" -C "$tmp"

mkdir -p "$(dirname "$APP_DIR")" "$(dirname "$BIN")" "$(dirname "$DESKTOP")" "$(dirname "$ICON")"
rm -rf "$APP_DIR"
mv "$tmp/Entangle" "$APP_DIR"

# Small launcher rather than a symlink, so the app always finds its bundled libraries.
printf '#!/bin/sh\nexec "%s/Entangle" "$@"\n' "$APP_DIR" > "$BIN"
chmod +x "$BIN"

fetch "https://raw.githubusercontent.com/$REPO/main/icon.png" "$ICON" quiet || true
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Entangle
Comment=One-click remote Jupyter over SSH
Exec=$APP_DIR/Entangle
Icon=$ICON
Terminal=false
Categories=Development;Science;
StartupWMClass=Entangle
EOF
command -v update-desktop-database >/dev/null && update-desktop-database "$(dirname "$DESKTOP")" 2>/dev/null || true

echo
echo "Entangle is installed. Open it from your app menu, or run: entangle"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "(~/.local/bin isn't on your PATH yet, so use: $BIN)";; esac
if ! ldconfig -p 2>/dev/null | grep -q 'libxcb-cursor\.so\.0'; then
    echo "If the window doesn't open (common on X11 desktops), run: sudo apt install libxcb-cursor0"
fi
