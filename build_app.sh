#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
APP="$ROOT/RedWhisper.app"
RUNTIME="$APP/Contents/Resources/runtime"
LAUNCHER="$APP/Contents/MacOS/RedWhisperLauncher"

if [[ ! -x "$ROOT/.venv/bin/python3" ]]; then
    echo "RedWhisper is not installed. Run ./install.sh first."
    exit 1
fi

if ! command -v xcrun >/dev/null 2>&1 || ! xcrun --find clang >/dev/null 2>&1; then
    echo "Apple Command Line Tools are required. Run: xcode-select --install"
    exit 1
fi

mkdir -p "$RUNTIME" "$(dirname "$LAUNCHER")"

# APFS clone copies keep the bundled environment isolated without initially
# consuming another full copy of its 1.3 GB of package data.
/bin/cp -cR "$ROOT/.venv" "$RUNTIME/.venv.new"
rm -rf "$RUNTIME/.venv"
mv "$RUNTIME/.venv.new" "$RUNTIME/.venv"

for file in \
    audio_devices.py \
    audio_processing.py \
    chatgpt_subscription.py \
    hotkey_config.py \
    launch_gui.py \
    mlx_whisper_core.py \
    native_settings.py \
    settings_store.py \
    voxtape.py
do
    /bin/cp "$ROOT/$file" "$RUNTIME/$file"
done

/bin/cp "$ROOT/assets/RedWhisper-icon.png" \
    "$APP/Contents/Resources/RedWhisper-icon.png"
/bin/cp "$ROOT/assets/RedWhisper-menu.png" \
    "$APP/Contents/Resources/RedWhisper-menu.png"
/bin/cp "$ROOT/assets/RedWhisper.icns" \
    "$APP/Contents/Resources/RedWhisper.icns"

# macOS ties the Accessibility grant to the launcher's ad-hoc signature, and a
# relink can change it (a Command Line Tools update is enough), so only
# recompile when the launcher source or compile flags change.
LAUNCHER_FLAGS=(
    -fobjc-arc
    -framework Cocoa
    -framework AVFoundation
    -framework ApplicationServices
)
LAUNCHER_STAMP="$APP/Contents/Resources/RedWhisperLauncher.stamp"
launcher_hash="$(
    { printf '%s\n' "${LAUNCHER_FLAGS[@]}"; cat "$ROOT/redwhisper_launcher.m"; } |
        shasum -a 256
)"
if [[ ! -f "$LAUNCHER" ]] ||
    [[ "$(cat "$LAUNCHER_STAMP" 2>/dev/null)" != "$launcher_hash" ]]; then
    xcrun clang "${LAUNCHER_FLAGS[@]}" \
        "$ROOT/redwhisper_launcher.m" -o "$LAUNCHER"
    echo "$launcher_hash" >"$LAUNCHER_STAMP"
    echo "Rebuilt the launcher. If the shortcut stops responding, re-grant"
    echo "Accessibility to RedWhisper (see Troubleshooting in README.md)."
fi
chmod +x "$LAUNCHER"
plutil -lint "$APP/Contents/Info.plist" >/dev/null
echo "RedWhisper.app is ready."
