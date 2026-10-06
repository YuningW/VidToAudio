"""Jobs: one per link. Each step runs in a background thread and reports
progress through job.json, which the page polls.

work/jobs/<id>/
  job.json        state, info, range, analysis, exports
  source.*        downloaded audio (+ thumbnail)
  listen.m4a      whole track, for choosing start and end in the browser
  peaks.json      waveform
  range.wav       the chosen part, 44.1 kHz float (kept for re-separation)
  stems/*.flac    the five stems (see mix.py)
"""
import io
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf

from . import analyze, cover, fetch, media, mix, paths, tags

_locks = {}
_sep_gate = threading.Semaphore(1)   # one separation at a time: they share the GPU


class JobError(RuntimeError):
    pass


def _lock(jid):
    return _locks.setdefault(jid, threading.RLock())


def job_dir(jid):
    if not re.fullmatch(r"[0-9a-f]{12}", jid or ""):
        raise JobError("Unknown job.")
    d = paths.JOBS / jid
    if not d.exists():
        raise JobError("That job no longer exists.")
    return d


def load(jid):
    return json.loads((job_dir(jid) / "job.json").read_text())


def save(job):
    d = paths.JOBS / job["id"]
    with _lock(job["id"]):
        tmp = d / "job.json.tmp"
        tmp.write_text(json.dumps(job, ensure_ascii=False, indent=1))
        tmp.replace(d / "job.json")


def update(jid, **changes):
    with _lock(jid):
        job = load(jid)
        job.update(changes)
        job["updated"] = time.time()
        save(job)
        return job


def listing():
    out = []
    for f in sorted(paths.JOBS.glob("*/job.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            j = json.loads(f.read_text())
        except ValueError:
            continue
        out.append({"id": j["id"], "title": (j.get("meta") or {}).get("title") or j.get("info", {}).get("title") or j.get("url"),
                    "state": j.get("state"), "updated": j.get("updated"),
                    "exports": len(j.get("exports") or [])})
    return out


def disk_mb(jid):
    return round(sum(f.stat().st_size for f in job_dir(jid).rglob("*") if f.is_file()) / 1e6)


def delete(jid):
    d = job_dir(jid)
    if load(jid).get("busy"):
        raise JobError("Wait for the current step to finish first.")
    shutil.rmtree(d)


def _run(jid, fn, *args):
    def target():
        try:
            fn(jid, *args)
        except Exception as e:  # shown on the page
            msg = str(e) or e.__class__.__name__
            update(jid, busy=False, error=msg, label="", progress=None)
    threading.Thread(target=target, daemon=True).start()


def _begin(jid, state):
    with _lock(jid):
        job = load(jid)
        if job.get("busy"):
            raise JobError("This song is still busy with the last step.")
        job.update(busy=True, state=state, error=None, progress=None, updated=time.time())
        save(job)


# ---------------------------------------------------------------- fetch

def _new_job(**fields):
    jid = uuid.uuid4().hex[:12]
    (paths.JOBS / jid).mkdir(parents=True)
    job = {"id": jid, "url": "", "created": time.time(), "updated": time.time(),
           "state": "fetching", "busy": True, "label": "Starting", "progress": None,
           "error": None, "info": {}, "exports": [], **fields}
    save(job)
    return job


def _existing(key):
    """A finished earlier job for the same video, if there is one."""
    for f in paths.JOBS.glob("*/job.json"):
        try:
            j = json.loads(f.read_text())
        except ValueError:
            continue
        if j.get("key") == key and j.get("state") != "fetching" and j.get("source"):
            return j
    return None


def create(url):
    url = fetch.clean_url(url)
    key = fetch.video_key(url)
    old = _existing(key)
    if old:
        return {**old, "reused": True}
    job = _new_job(url=url, key=key, label="Starting download")
    _run(job["id"], _fetch)
    return job


def _fetch(jid):
    d = job_dir(jid)
    job = load(jid)

    def progress(frac, label=None):
        update(jid, progress=None if frac is None else round(frac, 3),
               label=label or "Downloading audio")

    info, audio, thumb = fetch.download(job["url"], d, progress)
    _finish_source(jid, d, audio, thumb, info)


def _finish_source(jid, d, audio, thumb, info):
    update(jid, label="Getting it ready to play", progress=None, info=info)
    media.run(["-i", str(audio), "-map", "0:a:0", "-ac", "2", "-ar", "44100",
               "-c:a", "aac_at", "-b:a", "160k", "-movflags", "+faststart", "-y", str(d / "listen.m4a")])
    dur = media.duration(d / "listen.m4a")
    (d / "peaks.json").write_text(json.dumps(_peaks(audio, dur)))
    thumb_cover = None
    if thumb:
        try:
            thumb_cover = cover.from_file(thumb)
        except ValueError:
            pass
    update(jid, busy=False, state="fetched", label="", source=audio.name, duration=dur,
           range={"start": 0.0, "end": dur}, thumb_cover=thumb_cover)


# ---------------------------------------------------------------- files on this Mac

MEDIA_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".flv", ".ts", ".mts",
             ".m4a", ".mp3", ".aac", ".wav", ".flac", ".aif", ".aiff", ".ogg", ".opus", ".wma"}


def inbox_files():
    """Videos and audio in the drop folder, newest first."""
    paths.INBOX.mkdir(parents=True, exist_ok=True)
    out = []
    for f in paths.INBOX.iterdir():
        if f.is_file() and f.suffix.lower() in MEDIA_EXT and not f.name.startswith("."):
            st = f.stat()
            out.append({"name": f.name, "mb": round(st.st_size / 1e6, 1), "mtime": st.st_mtime})
    return sorted(out, key=lambda x: x["mtime"], reverse=True)


def import_inbox(name):
    """Use a file from the drop folder. Only its audio is copied."""
    p = (paths.INBOX / name).resolve()
    if p.parent != paths.INBOX.resolve() or not p.is_file():
        raise JobError("That file isn't in the VidToAudio folder any more.")
    st = p.stat()
    key = f"file:{p}:{st.st_size}:{int(st.st_mtime)}"
    old = _existing(key)
    if old:
        return {**old, "reused": True}
    job = _new_job(key=key, local={"name": p.name}, label="Reading the file")
    _run(job["id"], _import, str(p))
    return job


def _import(jid, path):
    d = job_dir(jid)
    src = Path(path)
    try:
        info = media.probe(src)
    except media.MediaError:
        raise JobError("That file can't be read as video or audio.")
    streams = info.get("streams", [])
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if not audio:
        raise JobError("That file has no sound in it.")
    a = audio[0]
    fmt = info.get("format", {})
    tags_ = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    dur = float(fmt.get("duration") or 0)
    update(jid, label="Taking the audio out")
    out = d / "source.mka"   # Matroska holds any audio codec untouched
    try:
        media.run(["-i", str(src), "-map", "0:a:0", "-vn", "-c:a", "copy", "-y", str(out)])
    except media.MediaError:
        out = d / "source.flac"
        media.run(["-i", str(src), "-map", "0:a:0", "-vn", "-c:a", "flac", "-y", str(out)])
    thumb = None
    pics = [s for s in streams if s.get("codec_type") == "video"]
    if pics:
        thumb = d / "thumb.jpg"
        attached = pics[0].get("disposition", {}).get("attached_pic")
        try:
            if attached:
                media.run(["-i", str(src), "-map", f"0:{pics[0]['index']}", "-frames:v", "1", "-y", str(thumb)])
            else:   # a frame a third of the way in usually shows the performers
                media.run(["-ss", f"{dur * 0.33:.2f}", "-i", str(src), "-map", "0:v:0", "-frames:v", "1",
                           "-vf", "scale='min(1920,iw)':-2", "-q:v", "3", "-y", str(thumb)])
        except media.MediaError:
            thumb = None
    br = a.get("bit_rate")
    meta = {
        "url": "", "webpage_url": "", "local": True,
        "title": tags_.get("title") or src.stem,
        "uploader": tags_.get("artist") or "On this Mac",
        "duration": dur, "acodec": a.get("codec_name", ""),
        "abr": round(int(br) / 1000) if br and str(br).isdigit() else None,
        "site": "file",
    }
    _finish_source(jid, d, out, thumb if thumb and thumb.exists() else None, meta)


def _peaks(audio, dur, buckets=3000):
    p = subprocess.run([media.FFMPEG, "-v", "error", "-i", str(audio), "-map", "0:a:0", "-ac", "1",
                        "-ar", "4000", "-f", "f32le", "-"], capture_output=True)
    x = np.frombuffer(p.stdout, dtype=np.float32)
    if len(x) < buckets:
        buckets = max(1, len(x))
    n = len(x) // buckets
    x = np.abs(x[: n * buckets].reshape(buckets, n))
    peak = x.max(axis=1)
    rms = np.sqrt(np.square(x).mean(axis=1))
    top = float(peak.max()) or 1.0
    return {"duration": dur, "peak": np.round(peak / top, 3).tolist(),
            "rms": np.round(rms / top, 3).tolist()}


# ---------------------------------------------------------------- prepare

def prepare(jid, start, end, model="htdemucs", crowd=True):
    job = load(jid)
    if job.get("state") == "fetching":
        raise JobError("Still downloading.")
    dur = job.get("duration") or 0
    start = max(0.0, float(start or 0))
    end = min(dur, float(end if end is not None else dur))
    if end - start < 5:
        raise JobError("Choose at least 5 seconds between start and end.")
    if model not in separate_models():
        model = "htdemucs"
    _begin(jid, "preparing")
    update(jid, range={"start": round(start, 2), "end": round(end, 2)}, model=model,
           label="Cutting your part")
    _run(jid, _prepare, start, end, model, bool(crowd))


def separate_models():
    return {"htdemucs": "Standard (about 1 minute)", "htdemucs_ft": "Best quality (about 3 minutes, a bit cleaner)"}


def _prepare(jid, start, end, model, crowd):
    d = job_dir(jid)
    job = load(jid)
    key = f"{start:.2f}-{end:.2f}-{model}" + ("-crowd" if crowd else "")
    stems_ok = all(p.exists() for p in mix.stem_paths(d)) and job.get("prepared_key") == key
    if not stems_ok:
        media.run(["-i", str(d / job["source"]), "-map", "0:a:0", "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
                   "-ac", "2", "-ar", str(media.SR), "-c:a", "pcm_f32le", "-y", str(d / "range.wav")])
        update(jid, label="Measuring the original")
        loud = media.loudness(d / "range.wav")
        src = analyze.measure_source(d / "range.wav")
        update(jid, label="Loading the separation models", source_loud=loud, source_measure=src)
        if not _sep_gate.acquire(blocking=False):
            update(jid, label="Waiting for another song to finish separating")
            _sep_gate.acquire()
        try:
            stats = _separate(jid, d, model, crowd)
        finally:
            _sep_gate.release()
        (d / "range.wav").unlink(missing_ok=True)   # the stems hold everything now
    else:
        loud, src = job["source_loud"], job["source_measure"]
        stats = json.loads((d / "stems" / "stats.json").read_text())
    length = end - start
    diag = analyze.diagnose(src, loud, stats, (job.get("info") or {}).get("abr"))
    win = analyze.preview_window(d, length)
    update(jid, busy=False, state="ready", label="", prepared_key=key, analysis=diag,
           preview={"start": round(win, 1), "length": min(30.0, length)}, stem_stats=stats)


def _separate(jid, d, model, crowd):
    proc = subprocess.Popen([sys.executable, "-m", "vta.separate", str(d), model, "crowd" if crowd else "nocrowd"],
                            cwd=str(paths.ROOT), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    result, error = None, None
    for line in proc.stdout:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if "step" in msg:
            update(jid, label=msg["label"], step=msg["step"])
        elif "done" in msg:
            result = msg["done"]
        elif "error" in msg:
            error = msg["error"]
    proc.wait()
    if error or result is None:
        raise JobError(error or "The separator stopped without finishing.")
    return result


# ---------------------------------------------------------------- listening

def clip_wav(jid, stem, start, length):
    """A stretch of one stem as 16-bit WAV bytes, for the browser to play."""
    if stem not in mix.STEMS:
        raise JobError("Unknown stem.")
    mix.ensure_stems(job_dir(jid))
    path = job_dir(jid) / "stems" / f"{stem}.flac"
    with sf.SoundFile(path) as f:
        sr = f.samplerate
        a = int(max(0, start) * sr)
        f.seek(min(a, f.frames))
        x = f.read(int(min(length, 120) * sr), dtype="float32", always_2d=True)
    buf = io.BytesIO()
    sf.write(buf, np.clip(x, -1, 1), sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def test_clip(jid, params, level, start, length, take_name):
    """Render a take exactly as the export would, for saving as a test file."""
    d = job_dir(jid)
    job = load(jid)
    out = d / "clips" / f"{uuid.uuid4().hex[:8]}.m4a"
    mix.render(d, params, level, out, fmt="m4a", start=start, dur=min(length, 120))
    title = safe_name((job.get("meta") or {}).get("title") or job["info"].get("title") or "clip")
    return out, f"{title} - {take_name}.m4a"


# ---------------------------------------------------------------- export

def safe_name(text, limit=150):
    t = re.sub(r'[/\\:*?"<>|\x00-\x1f]', " ", text or "").strip().strip(".")
    t = re.sub(r"\s+", " ", t)
    b = t.encode("utf-8")[:limit]
    return b.decode("utf-8", "ignore").strip() or "Untitled"


def export_target(meta, fmt, out_dir):
    if fmt not in ("mp3", "m4a"):
        raise JobError("Choose MP3 or M4A.")
    title = (meta.get("title") or "").strip()
    artist = (meta.get("artist") or "").strip()
    if not title or not artist:
        raise JobError("Song name and artist are needed.")
    folder = Path(out_dir).expanduser()
    if not folder.is_absolute():
        raise JobError("Choose a full folder path.")
    return folder / f"{safe_name(title)}.{fmt}"


def export(jid, params, level, meta, cover_spec, fmt, out_dir, fade_in, fade_out, overwrite, take=None):
    job = load(jid)
    if job.get("state") not in ("ready", "exported"):
        raise JobError("Make the test takes first.")
    target = export_target(meta, fmt, out_dir)
    if target.exists() and not overwrite:
        raise FileExistsError(str(target))
    _begin(jid, "exporting")
    update(jid, label="Starting", meta=meta)
    _run(jid, _export, params, level, meta, cover_spec, fmt, target, fade_in, fade_out, take)


def _export(jid, params, level, meta, cover_spec, fmt, target, fade_in, fade_out, take):
    d = job_dir(jid)
    art = None
    if cover_spec and cover_spec.get("id"):
        art = cover.make_square(cover_spec["id"], cover_spec.get("crop"), cover_spec.get("mode", "crop"),
                                cover_spec.get("size", 640))
    tmp = d / f"export.{fmt}"
    result = mix.render(d, params, level, tmp, fmt=fmt, fade_in=fade_in, fade_out=fade_out,
                        on_step=lambda _k, label: update(jid, label=label))
    update(jid, label="Writing song details and cover")
    tags.write(tmp, meta, art)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(tmp), target)
    paths.remember("artists", meta.get("artist"))
    paths.remember("albums", meta.get("album"))
    paths.save_settings({"out_dir": str(target.parent), "format": fmt,
                         "level": level if isinstance(level, str) else "natural"})
    # everything needed to reopen this song exactly as it was saved
    entry = {"path": str(target), "at": time.time(), "params": params, "level": level, "take": take,
             "cover": cover_spec, "fade_in": fade_in, "fade_out": fade_out, "prepared_key": load(jid).get("prepared_key"),
             "format": fmt, "tags": tags.read(target), **result}
    with _lock(jid):
        job = load(jid)
        job["exports"] = [entry] + [e for e in job.get("exports", []) if e.get("path") != str(target)]
        job.update(busy=False, state="exported", label="", last_export=entry, updated=time.time())
        save(job)
