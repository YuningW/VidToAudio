#!/bin/bash
# Double-click in Finder to start VidToAudio; it opens in your browser.
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || ./setup.sh
unset ELECTRON_RUN_AS_NODE
# Video sites change often; keep the downloader current (a few seconds, skipped when offline).
echo "Checking for a newer yt-dlp…"
.venv/bin/pip install -q --disable-pip-version-check --upgrade --timeout 5 yt-dlp >/dev/null 2>&1 || true
exec .venv/bin/python -m vta "$@"
