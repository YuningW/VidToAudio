"""Where things live on disk, and the small settings file."""
import json
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
# Everything the tool makes while working stays out of git (see .gitignore).
WORK = Path(os.environ.get("VTA_WORK", ROOT / "work"))
JOBS = WORK / "jobs"
COVERS = WORK / "covers"
SETTINGS = WORK / "settings.json"
# Drop videos here to use them. Kept out of ~/Music so Spotify never lists raw videos.
INBOX = Path(os.environ.get("VTA_INBOX", Path.home() / "Movies" / "VidToAudio"))
MODELS = Path(os.environ.get("VTA_MODELS", Path.home() / "Library/Caches/VidToAudio/models"))

DEFAULT_SETTINGS = {
    "out_dir": str(Path.home() / "Music" / "VidToAudio"),
    "format": "mp3",          # mp3 | m4a
    "cover_size": 640,        # px, square
    "level": "natural",       # see mix.LEVELS
    "model": "htdemucs",      # htdemucs | htdemucs_ft
    "crowd": True,            # run the crowd model (screams & cheering stem)
    "artists": [],            # remembered for the artist field
    "albums": [],
}

_lock = threading.Lock()


def ensure_dirs():
    for d in (JOBS, COVERS, MODELS, INBOX):
        d.mkdir(parents=True, exist_ok=True)


def load_settings():
    s = dict(DEFAULT_SETTINGS)
    try:
        s.update(json.loads(SETTINGS.read_text()))
    except (OSError, ValueError):
        pass
    return s


def save_settings(changes):
    with _lock:
        s = load_settings()
        s.update({k: v for k, v in changes.items() if k in DEFAULT_SETTINGS})
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, ensure_ascii=False, indent=2))
        tmp.replace(SETTINGS)
        return s


def remember(key, value, keep=30):
    """Put value at the front of a remembered list (artists, albums)."""
    value = (value or "").strip()
    if not value:
        return
    s = load_settings()
    items = [v for v in s.get(key, []) if v != value]
    save_settings({key: [value] + items[: keep - 1]})
