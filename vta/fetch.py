"""Download the best audio (and the thumbnail) from a video link."""
import re
import time
from pathlib import Path

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0 Safari/537.36")


class FetchError(RuntimeError):
    pass


def clean_url(url):
    url = (url or "").strip()
    m = re.search(r"https?://\S+", url)
    if not m:
        raise FetchError("Paste a full link that starts with https://")
    return m.group(0)


def video_key(url):
    """Same video, however the link was copied (playlist, tracking, share params)."""
    from urllib.parse import parse_qs, urlparse
    u = urlparse(url)
    q = parse_qs(u.query)
    host = u.netloc.lower()
    if "youtube." in host or "youtu.be" in host:
        vid = q.get("v", [None])[0] or (u.path.strip("/").split("/")[-1] if "youtu.be" in host or "/shorts/" in u.path else None)
        if vid:
            return f"youtube:{vid}"
    m = re.search(r"(BV[0-9A-Za-z]{10})", url)
    if m:
        return f"bilibili:{m.group(1)}:p{q.get('p', ['1'])[0]}"
    return f"url:{host}{u.path}".rstrip("/")


def _opts(job_dir, hook, bilibili):
    headers = {"User-Agent": UA}
    if bilibili:
        headers["Referer"] = "https://www.bilibili.com/"
    return {
        # best audio-only stream; skip YouTube's dynamic-range-compressed copies
        "format": "ba[format_id!*=drc]/ba/b",
        "outtmpl": str(Path(job_dir) / "source.%(ext)s"),
        "noplaylist": True,          # YouTube "RD..." radio links: just this video
        "writethumbnail": True,
        "http_headers": headers,
        "retries": 10,
        "fragment_retries": 10,
        "sleep_interval_requests": 1 if bilibili else 0,
        "concurrent_fragment_downloads": 4,
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "overwrites": True,
    }


def download(url, job_dir, on_progress):
    """Returns (info dict, path to the audio file, path to the thumbnail or None)."""
    import yt_dlp

    url = clean_url(url)
    bilibili = "bilibili." in url or "b23.tv" in url

    def hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            on_progress(done / total if total else None)

    last = None
    for attempt, wait in enumerate((0, 8, 20, 45)):
        if wait:
            on_progress(None, f"The site pushed back ({last}). Retrying in {wait} s…")
            time.sleep(wait)
        try:
            with yt_dlp.YoutubeDL(_opts(job_dir, hook, bilibili)) as ydl:
                info = ydl.extract_info(url, download=True)
            break
        except yt_dlp.utils.DownloadError as e:
            last = _short(str(e))
            if not _retryable(str(e)) or attempt == 3:
                raise FetchError(last) from None
    if info.get("_type") == "playlist":
        entries = [e for e in info.get("entries") or [] if e]
        if not entries:
            raise FetchError("That link is a playlist with nothing playable in it.")
        info = entries[0]

    files = sorted(Path(job_dir).glob("source.*"))
    audio = [f for f in files if f.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp", ".part")]
    thumbs = [f for f in files if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]
    if not audio:
        raise FetchError("The download finished but no audio file was written.")
    return _summary(info, url), audio[0], (thumbs[0] if thumbs else None)


def _summary(info, url):
    abr = info.get("abr") or 0
    for f in info.get("requested_formats") or []:
        abr = abr or f.get("abr") or 0
    return {
        "url": url,
        "webpage_url": info.get("webpage_url") or url,
        "title": info.get("title") or "",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "duration": info.get("duration") or 0,
        "acodec": info.get("acodec") or "",
        "abr": round(abr) if abr else None,
        "site": info.get("extractor_key") or "",
        "upload_date": info.get("upload_date") or "",
    }


def _retryable(msg):
    return any(s in msg for s in ("412", "429", "403", "timed out", "Read timed out",
                                   "Connection reset", "IncompleteRead", "HTTP Error 5"))


def _short(msg):
    msg = re.sub(r"\x1b\[[0-9;]*m", "", msg).replace("ERROR: ", "")
    return msg.strip().splitlines()[0][:240]
