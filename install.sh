#!/bin/bash
# Ferospark Equalizer installer (per user, no root needed for the app itself).
#   ./install.sh             install or update
#   ./install.sh --uninstall remove the app (your EasyEffects presets are kept)
set -e

APP_ID="io.github.matewinslet.FerosparkEqualizer"
DEST="$HOME/.local/share/ferospark-equalizer"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor/scalable/apps"
AUTOSTART="$HOME/.config/autostart/$APP_ID-restore.desktop"
SRC="$(cd "$(dirname "$0")" && pwd)"

if [ "$1" = "--uninstall" ]; then
    pgrep -f "python3 $DEST/ferospark-equalizer.py" | xargs -r kill 2>/dev/null || true
    rm -rf "$DEST"
    rm -f "$APPS/$APP_ID.desktop" "$ICONS/$APP_ID.svg" "$AUTOSTART"
    rm -rf "$HOME/.config/ferospark-equalizer"
    update-desktop-database "$APPS" 2>/dev/null || true
    echo "Ferospark Equalizer removed. EasyEffects and your presets were left alone."
    exit 0
fi

# ── check what the app needs ────────────────────────────────────────────────
missing=()
python3 -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); from gi.repository import Gtk, Adw' 2>/dev/null \
    || missing+=("GTK 4 + libadwaita for Python (Debian/Ubuntu: python3-gi gir1.2-adw-1)")
python3 -c 'import cairo' 2>/dev/null || missing+=("pycairo (python3-gi-cairo)")
python3 -c 'import numpy' 2>/dev/null || missing+=("numpy (python3-numpy)")
command -v easyeffects >/dev/null || missing+=("EasyEffects 7.x: see 'Step 1' in README.md (the Flatpak version will NOT work)")
command -v pw-cat >/dev/null || missing+=("pw-cat (pipewire-bin)")
command -v pactl >/dev/null || missing+=("pactl (pulseaudio-utils)")

if [ ${#missing[@]} -gt 0 ]; then
    echo "Missing:"
    printf '  - %s\n' "${missing[@]}"
    echo "Install those with your package manager, then run ./install.sh again."
    exit 1
fi

ver="$(easyeffects --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -1)"
if [ -n "$ver" ] && [ "${ver%%.*}" -ge 8 ]; then
    echo "Warning: EasyEffects $ver found. Ferospark Equalizer is built for EasyEffects 7.x;"
    echo "         version 8 changed how it stores settings, so the EQ may not respond."
fi

# ── install ─────────────────────────────────────────────────────────────────
mkdir -p "$DEST/data/presets" "$APPS" "$ICONS"
install -m 755 "$SRC/ferospark-equalizer.py" "$DEST/ferospark-equalizer.py"
install -m 644 "$SRC/data/donate_qr.png" "$DEST/data/donate_qr.png"
install -m 644 "$SRC"/data/presets/*.json "$DEST/data/presets/"
install -m 644 "$SRC/data/$APP_ID.svg" "$ICONS/$APP_ID.svg"

cat > "$APPS/$APP_ID.desktop" <<EOF
[Desktop Entry]
Name=Ferospark Equalizer
Comment=Simple Peace-style equalizer powered by EasyEffects
Exec=python3 $DEST/ferospark-equalizer.py
Icon=$APP_ID
Terminal=false
Type=Application
Categories=AudioVideo;Audio;
Keywords=eq;equalizer;peace;sound;easyeffects;
StartupWMClass=$APP_ID
EOF

update-desktop-database "$APPS" 2>/dev/null || true
gtk-update-icon-cache -q "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
echo "Ferospark Equalizer installed. Find it in your app menu."
