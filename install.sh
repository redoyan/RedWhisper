#!/bin/bash
# ─────────────────────────────────────────────────────────
#  RedWhisper — Install (macOS Apple Silicon)
# ─────────────────────────────────────────────────────────
set -e

WITH_LOCAL_LLM=false
if [[ "${1:-}" == "--with-local-llm" ]]; then
    WITH_LOCAL_LLM=true
elif [[ -n "${1:-}" ]]; then
    echo "Usage: ./install.sh [--with-local-llm]"
    exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "🎙️  Installing RedWhisper"
echo "   Local voice-to-text by default"
echo ""

# ── 1. Check: Apple Silicon ──────────────────────────────
ARCH=$(uname -m)
if [[ "$ARCH" != "arm64" ]]; then
    echo "⚠️  RedWhisper requires Apple Silicon."
    echo "   Detected architecture: $ARCH"
    echo "   MLX does not run on Intel Macs."
    exit 1
fi

# ── 2. Check: Python 3.10+ ──────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "❌ Python 3 not found."
    echo "   Install it: brew install python@3.12"
    exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [[ "$PY_MAJOR" -lt 3 ]] || [[ "$PY_MINOR" -lt 10 ]]; then
    echo "❌ Python 3.10+ required (found: $PY_VERSION)"
    echo "   Install: brew install python@3.12"
    exit 1
fi
echo "✓ Python $PY_VERSION"

# ── 3. Check: portaudio (needed by sounddevice) ─────────
if command -v brew &>/dev/null; then
    if ! brew list portaudio &>/dev/null 2>&1; then
        echo "📦 Installing portaudio (required for audio capture)..."
        brew install portaudio
    fi
    echo "✓ portaudio"
else
    echo "⚠️  Homebrew not found. Make sure portaudio is installed."
fi

# ── 4. Create virtual environment ────────────────────────
echo ""
echo "📦 Creating virtual environment..."
python3 -m venv .venv
source .venv/bin/activate

# ── 5. Install dependencies ─────────────────────────────
echo "📦 Installing dependencies (MLX, Whisper, audio)..."
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet

if [[ "$WITH_LOCAL_LLM" == true ]]; then
    echo "📦 Installing optional local LLM support..."
    pip install -r requirements-llm.txt --quiet
fi

echo "✓ Dependencies installed"

# ── 6. Pre-download model ───────────────────────────────
echo ""
echo "📦 Downloading whisper-large-v3-turbo model (~1.6 GB)..."
echo "   (one-time download, cached in ~/.cache/huggingface)"
python3 -c "
from huggingface_hub import snapshot_download
snapshot_download('mlx-community/whisper-large-v3-turbo', local_files_only=False)
print('✅ Model downloaded!')
"

# ── 7. Create launch scripts ────────────────────────────
cat > start.sh << 'LAUNCHER'
#!/bin/bash
cd "$(dirname "$0")"
source .venv/bin/activate
python3 voxtape.py "$@"
LAUNCHER
chmod +x start.sh

chmod +x "RedWhisper.app/Contents/MacOS/RedWhisper"
chmod +x build_app.sh
./build_app.sh

# ── 8. Done ──────────────────────────────────────────────
echo ""
echo "┌─────────────────────────────────────────────────┐"
echo "│  ✅  RedWhisper installed!                      │"
echo "│                                                  │"
echo "│  Launch:                                         │"
echo "│    Double-click RedWhisper.app in Finder        │"
echo "│    ./start.sh  (Terminal diagnostics only)       │"
echo "│                                                  │"
echo "│  Hotkey: Fn / Globe key to dictate               │"
echo "│                                                  │"
echo "│  Options:                                        │"
echo "│    ./start.sh --language en    (English)          │"
echo "│    ./start.sh --language auto  (auto-detect)      │"
echo "│    ./start.sh --maximum-accuracy                  │"
echo "│    Shortcut is configurable in launch settings   │"
echo "│    ./start.sh --list-devices   (audio devices)    │"
echo "└─────────────────────────────────────────────────┘"
echo ""
echo "⚠️  macOS permissions needed (first launch):"
echo "   • Microphone → allow RedWhisper"
echo "   • Accessibility → allow RedWhisper"
echo "   (System Settings → Privacy & Security)"
echo ""
