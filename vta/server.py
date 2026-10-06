"""Local web app: python -m vta  ->  http://127.0.0.1:8770"""
import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import cover, jobs, mix, paths

PORT = int(os.environ.get("VTA_PORT", 8770))
HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}

app = FastAPI(title="VidToAudio", docs_url=None, redoc_url=None)


@app.middleware("http")
async def local_only(request: Request, call_next):
    # Only this Mac's browser may drive the app: other websites can't reach it
    # through DNS tricks (Host check) or cross-site form posts (custom header).
    if request.headers.get("host") not in HOSTS and not os.environ.get("VTA_TEST"):
        return JSONResponse({"error": "Open the app at http://127.0.0.1:%d" % PORT}, 403)
    if request.method not in ("GET", "HEAD") and request.headers.get("x-vta") != "1":
        return JSONResponse({"error": "Requests must come from the VidToAudio page."}, 403)
    return await call_next(request)


@app.exception_handler(jobs.JobError)
@app.exception_handler(ValueError)
async def friendly(_request, exc):
    return JSONResponse({"error": str(exc)}, 400)


# ---------------------------------------------------------------- config

@app.get("/api/config")
def get_config():
    return {**mix.config(), "settings": paths.load_settings(), "models": jobs.separate_models()}


class SettingsIn(BaseModel):
    out_dir: str | None = None
    format: str | None = None
    cover_size: int | None = None
    level: str | None = None
    model: str | None = None


@app.post("/api/settings")
def set_settings(s: SettingsIn):
    return paths.save_settings({k: v for k, v in s.model_dump().items() if v is not None})


# ---------------------------------------------------------------- jobs

class FetchIn(BaseModel):
    url: str


@app.post("/api/jobs")
def new_job(body: FetchIn):
    return jobs.create(body.url)


class LocalIn(BaseModel):
    name: str


@app.post("/api/jobs/local")
def local_job(body: LocalIn):
    return jobs.import_inbox(body.name)


@app.get("/api/inbox")
def inbox():
    return {"folder": str(paths.INBOX).replace(str(Path.home()), "~"), "files": jobs.inbox_files()}


@app.post("/api/inbox/open")
def open_inbox():
    paths.INBOX.mkdir(parents=True, exist_ok=True)
    subprocess.run(["open", str(paths.INBOX)], check=False)
    return {"ok": True}


@app.get("/api/jobs")
def all_jobs():
    return jobs.listing()


@app.get("/api/jobs/{jid}")
def one_job(jid: str):
    return {**jobs.load(jid), "disk_mb": jobs.disk_mb(jid)}


@app.delete("/api/jobs/{jid}")
def remove_job(jid: str):
    jobs.delete(jid)
    return {"ok": True}


@app.get("/api/jobs/{jid}/peaks")
def peaks(jid: str):
    return FileResponse(jobs.job_dir(jid) / "peaks.json")


@app.get("/api/jobs/{jid}/listen")
def listen(jid: str):
    return FileResponse(jobs.job_dir(jid) / "listen.m4a", media_type="audio/mp4")


class PrepareIn(BaseModel):
    start: float
    end: float
    model: str = "htdemucs"
    crowd: bool = True


@app.post("/api/jobs/{jid}/prepare")
def prepare(jid: str, body: PrepareIn):
    jobs.prepare(jid, body.start, body.end, body.model, body.crowd)
    paths.save_settings({"model": body.model, "crowd": body.crowd})
    return jobs.load(jid)


@app.get("/api/jobs/{jid}/stem/{stem}")
def stem(jid: str, stem: str, start: float, length: float):
    data = jobs.clip_wav(jid, stem, start, length)
    return Response(data, media_type="audio/wav", headers={"Cache-Control": "no-store"})


class ClipIn(BaseModel):
    params: dict
    level: str | float = "natural"
    start: float
    length: float
    take: str = "take"


@app.post("/api/jobs/{jid}/clip")
def save_clip(jid: str, body: ClipIn):
    path, name = jobs.test_clip(jid, body.params, body.level, body.start, body.length, body.take)
    return FileResponse(path, media_type="audio/mp4", filename=name)


class ExportIn(BaseModel):
    params: dict
    level: str | float = "natural"
    meta: dict
    cover: dict | None = None
    format: str = "mp3"
    out_dir: str
    fade_in: float = 0.0
    fade_out: float = 0.0
    overwrite: bool = False
    take: str | None = None


@app.post("/api/jobs/{jid}/export")
def export(jid: str, body: ExportIn):
    try:
        jobs.export(jid, body.params, body.level, body.meta, body.cover, body.format,
                    body.out_dir, max(0, body.fade_in), max(0, body.fade_out), body.overwrite, body.take)
    except FileExistsError as e:
        return JSONResponse({"error": "exists", "path": str(e)}, 409)
    return jobs.load(jid)


@app.get("/api/file")
def play_file(path: str):
    """Play a finished export. Only files this app wrote can be served."""
    known = {e["path"] for j in jobs.listing() for e in (jobs.load(j["id"]).get("exports") or [])}
    if path not in known or not Path(path).exists():
        raise HTTPException(404, "Not one of your exports.")
    mt = "audio/mpeg" if path.endswith(".mp3") else "audio/mp4"
    return FileResponse(path, media_type=mt)


# ---------------------------------------------------------------- covers

@app.post("/api/covers")
async def upload_cover(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > 40 * 1024 * 1024:
        raise ValueError("That picture is over 40 MB.")
    return cover.store(data, file.filename or "cover")


@app.get("/api/covers/{cid}")
def cover_source(cid: str):
    return FileResponse(cover.source_path(cid), media_type="image/jpeg")


@app.get("/api/covers/{cid}/square")
def cover_square(cid: str, x: float | None = None, y: float | None = None, size: float | None = None,
                 mode: str = "crop", out: int = 640):
    crop = {"x": x, "y": y, "size": size} if size else None
    return Response(cover.make_square(cid, crop, mode, out), media_type="image/jpeg")


# ---------------------------------------------------------------- Finder

class PathIn(BaseModel):
    path: str


@app.post("/api/reveal")
def reveal(body: PathIn):
    p = Path(body.path).expanduser()
    if not p.exists():
        raise ValueError("That file isn't there any more.")
    subprocess.run(["open", "-R", str(p)], check=False)
    return {"ok": True}


@app.post("/api/choose-folder")
def choose_folder():
    script = 'POSIX path of (choose folder with prompt "Where should finished songs go?")'
    p = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if p.returncode != 0:
        return {"path": None}
    return {"path": p.stdout.strip().rstrip("/")}


if os.environ.get("VTA_TEST"):
    @app.post("/tests-result")
    async def test_result(request: Request):
        (paths.WORK / "test-result.json").write_bytes(await request.body())
        return {"ok": True}

    app.mount("/tests", StaticFiles(directory=paths.ROOT / "tests" / "web"), name="tests")
app.mount("/", StaticFiles(directory=paths.WEB, html=True), name="web")


def _already_running(url):
    import urllib.request
    try:
        with urllib.request.urlopen(url + "api/config", timeout=1) as r:
            return r.status == 200
    except OSError:
        return False


def main():
    import uvicorn
    paths.ensure_dirs()
    url = f"http://127.0.0.1:{PORT}/"
    if _already_running(url):
        # opened twice (e.g. double-clicked again): just bring up the page
        print(f"VidToAudio is already running at {url}")
        if "--no-browser" not in sys.argv:
            webbrowser.open(url)
        return
    if "--no-browser" not in sys.argv:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"VidToAudio is running at {url}  (Ctrl+C to stop)")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
