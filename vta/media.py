"""Thin helpers around ffmpeg / ffprobe."""
import json
import re
import shutil
import subprocess

FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
SR = 44100  # every working file is 44.1 kHz stereo


class MediaError(RuntimeError):
    pass


def run(args, capture=True):
    """Run ffmpeg with args; raise MediaError with the tail of stderr on failure."""
    p = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", *args],
                       capture_output=capture, text=True)
    if p.returncode != 0:
        tail = (p.stderr or "").strip().splitlines()[-6:]
        raise MediaError("ffmpeg failed: " + " | ".join(tail))
    return p.stderr or ""


def probe(path):
    p = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json",
                        "-show_format", "-show_streams", str(path)],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise MediaError(f"ffprobe could not read {path}")
    return json.loads(p.stdout)


def duration(path):
    return float(probe(path)["format"].get("duration") or 0)


def parse_time(text):
    """'1:23.5' / '83.5' / '1:02:03' -> seconds. Empty -> None."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    t = str(text).strip().replace("：", ":")
    if not t:
        return None
    if not re.fullmatch(r"\d+(:\d{1,2}){0,2}(\.\d+)?", t):
        raise ValueError(f"'{text}' isn't a time. Use 1:23 or 83.5")
    secs = 0.0
    for part in t.split(":"):
        secs = secs * 60 + float(part)
    return secs


def fmt_time(s):
    s = max(0.0, float(s))
    m, sec = divmod(s, 60)
    return f"{int(m)}:{sec:04.1f}"


_EBU = {
    "lufs": re.compile(r"I:\s+(-?[\d.]+|-inf) LUFS"),
    "lra": re.compile(r"LRA:\s+(-?[\d.]+) LU"),
    "true_peak": re.compile(r"Peak:\s+(-?[\d.]+|-inf) dBFS"),
}


def loudness(path, start=None, dur=None):
    """Integrated loudness (LUFS), loudness range (LU) and true peak (dBTP)."""
    args = []
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    if dur is not None:
        args += ["-t", f"{dur:.3f}"]
    err = run([*args, "-nostats", "-i", str(path), "-map", "0:a:0",
               "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-"])
    summary = err[err.rfind("Summary:"):]
    out = {}
    for key, rx in _EBU.items():
        m = rx.search(summary)
        v = m.group(1) if m else "-inf"
        out[key] = -99.0 if v == "-inf" else float(v)
    return out
