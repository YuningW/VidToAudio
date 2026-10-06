"""Does the browser preview sound like the export?

Renders the same takes with web/engine.js (headless Chrome, OfflineAudioContext)
and with vta/mix.py (ffmpeg), then compares sample by sample and by loudness.

  VTA_WORK=<work dir with a prepared song> .venv/bin/python tests/parity.py
"""
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from vta import jobs, media, mix, paths  # noqa: E402

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 8779
CASES = {p["id"]: p["set"] for p in mix.PRESETS}
CASES["everything"] = {"crowd": 70, "echo": 40, "width": 60, "vocal": 2, "clarity": 3,
                       "harsh": -6, "air": 4, "mud": -3, "low": -4, "drums": 120, "bass": 80}


def main():
    ready = [j for j in jobs.listing() if j["state"] in ("ready", "exported")]
    if not ready:
        sys.exit("No prepared song in " + str(paths.WORK))
    jid = ready[0]["id"]
    d = jobs.job_dir(jid)
    start, length = jobs.load(jid)["preview"]["start"], 10.0
    env = {**os.environ, "VTA_PORT": str(PORT), "VTA_TEST": "1"}
    server = subprocess.Popen([sys.executable, "-m", "vta", "--no-browser"], cwd=ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/config")
                break
            except OSError:
                time.sleep(0.2)
        q = urllib.parse.urlencode({"job": jid, "start": start, "len": length, "cases": json.dumps(CASES)})
        url = f"http://127.0.0.1:{PORT}/tests/parity.html?{q}"
        result = paths.WORK / "test-result.json"
        result.unlink(missing_ok=True)
        with tempfile.TemporaryDirectory() as prof:
            chrome = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", f"--user-data-dir={prof}",
                                       "--no-first-run", url],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                for _ in range(600):
                    if result.exists():
                        break
                    time.sleep(0.2)
            finally:
                chrome.terminate()
                chrome.wait()
    finally:
        server.terminate()
    if not result.exists():
        sys.exit("The browser never reported back.")
    got = json.loads(result.read_text())
    result.unlink()
    if "error" in got:
        sys.exit("Browser error: " + got["error"])

    worst = -999.0
    print(f"{'take':16} {'diff vs signal':>15} {'LUFS browser':>13} {'LUFS ffmpeg':>12}")
    with tempfile.TemporaryDirectory() as tmp:
        for name, p in CASES.items():
            ref_path = Path(tmp) / f"{name}.wav"
            mix.render_pre(d, mix.full_params(p), ref_path, start, length)
            ref, _ = sf.read(ref_path, dtype="float32", always_2d=True)
            L = np.frombuffer(base64.b64decode(got[name]["L"]), dtype=np.float32)
            R = np.frombuffer(base64.b64decode(got[name]["R"]), dtype=np.float32)
            web = np.stack([L, R], axis=1)
            n = min(len(ref), len(web))
            diff = web[:n] - ref[:n]
            rel = 10 * np.log10(np.mean(diff ** 2) / np.mean(ref[:n] ** 2) + 1e-20)
            worst = max(worst, rel)
            lufs_ff = media.loudness(ref_path)["lufs"]
            print(f"{name:16} {rel:12.1f} dB {got[name]['lufs']:13.2f} {lufs_ff:12.2f}")
            assert abs(got[name]["lufs"] - lufs_ff) < 0.3, f"{name}: loudness differs"
    assert worst < -40, f"browser and export differ by {worst:.1f} dB"
    print(f"OK: worst difference {worst:.1f} dB below the signal")


if __name__ == "__main__":
    main()
