#!/bin/bash
# One-time setup on a Mac: Homebrew ffmpeg + Python 3.12, then a private venv.
set -euo pipefail
cd "$(dirname "$0")"
command -v brew >/dev/null || { echo "Install Homebrew first: https://brew.sh"; exit 1; }
command -v ffmpeg >/dev/null || brew install ffmpeg
PY=$(command -v python3.12 || true)
[ -n "$PY" ] || { brew install python@3.12; PY=$(command -v python3.12); }
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
echo "Ready. Double-click VidToAudio.command (or run ./VidToAudio.command) to start."
