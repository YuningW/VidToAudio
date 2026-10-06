"""Listen to the selected part and say, in plain words, what's wrong with it.

Thresholds were calibrated on real material: the Whale Store MY ONLY CHAMPION
fancam (live, echoey, dark) against a studio master from the GMM Songs folder.
They only decide which takes get a star. Every take is always offered.
"""
import numpy as np
import soundfile as sf

# Share of the energy the crowd model calls audience (dB). Calibrated on the
# Manila Kissin' Out of Dream and 260125 Hook fancams (see git log).
SCREAMS_LOUD = -8.0
SCREAMS_SOME = -14.0

BANDS = {
    "low": (20, 120), "lowmid": (120, 500), "mid": (500, 1500),
    "presence": (1500, 3000), "upper": (3000, 5000), "harsh": (5000, 8000),
    "air": (8000, 12000), "top": (12000, 16000),
}


def _db(x):
    return float(10 * np.log10(x + 1e-20))


def spectrum(mono, sr, n=8192):
    hop = n // 2
    win = np.hanning(n).astype(np.float32)
    count = max(1, (len(mono) - n) // hop)
    step = max(1, count // 400)          # at most ~400 frames: fast on long songs
    acc = np.zeros(n // 2 + 1)
    used = 0
    for i in range(0, count * hop, hop * step):
        seg = mono[i:i + n]
        if len(seg) < n:
            break
        acc += np.abs(np.fft.rfft(seg * win)) ** 2
        used += 1
    return np.fft.rfftfreq(n, 1 / sr), acc / max(used, 1)


def measure_source(path):
    """Measurements of the original audio (before separation)."""
    x, sr = sf.read(path, dtype="float32", always_2d=True)
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    L, R = x[:, 0].astype(np.float64), x[:, 1].astype(np.float64)
    corr = float(np.sum(L * R) / (np.sqrt(np.sum(L * L) * np.sum(R * R)) + 1e-20))
    mono = (x[:, 0] + x[:, 1]) / 2
    fold = _db(np.mean(mono.astype(np.float64) ** 2)) - _db(np.mean((L * L + R * R) / 2))
    fr, psd = spectrum(mono, sr)
    bands = {k: _db(psd[(fr >= a) & (fr < b)].sum()) for k, (a, b) in BANDS.items()}
    top = max(bands.values())
    bands = {k: round(v - top, 1) for k, v in bands.items()}
    dens = 10 * np.log10(psd + 1e-20)
    ref = np.median(dens[(fr > 1000) & (fr < 4000)])
    above = np.where(dens > ref - 35)[0]
    cutoff = float(fr[above.max()]) if len(above) else 0.0
    return {
        "correlation": round(corr, 3),
        "mono_fold_db": round(fold, 2),
        "bands": bands,
        "harsh_bump": round(bands["harsh"] - bands["upper"], 1),
        "air_vs_mid": round(bands["air"] - (bands["mid"] + bands["presence"]) / 2, 1),
        "cutoff_hz": round(cutoff),
    }


def preview_window(job_dir, length, win=30.0):
    """Start of the win-second stretch with the most singing in it."""
    if length <= win + 1:
        return 0.0
    dry, sr = sf.read(f"{job_dir}/stems/vocals_dry.flac", dtype="float32", always_2d=True)
    rev, _ = sf.read(f"{job_dir}/stems/vocals_rev.flac", dtype="float32", always_2d=True)
    v = (dry + rev).mean(axis=1)
    sec = int(sr)
    n = len(v) // sec
    e = np.square(v[: n * sec].reshape(n, sec)).mean(axis=1)
    w = int(win)
    sums = np.convolve(e, np.ones(w), "valid")
    # gentle preference for the middle of the song over intros and outros
    pos = np.arange(len(sums))
    bias = 1 - 0.15 * np.abs(pos - len(sums) / 2) / max(len(sums) / 2, 1)
    return float(np.argmax(sums * bias))


def diagnose(src, loud, stems, bitrate=None):
    """Plain-language findings and the takes worth hearing first."""
    notes, stars = [], []
    crowd_share = stems["other"] - stems["original"]          # dB of energy
    echo_gap = stems["vocals_dry"] - stems["vocals_rev"]       # dB
    voice_share = stems["vocals"] - stems["original"]
    low_quality = (bitrate and bitrate < 96) or src["cutoff_hz"] < 15000

    if low_quality:
        notes.append({"tone": "warn", "title": "Low-quality upload",
                      "text": f"Sound stops around {src['cutoff_hz'] / 1000:.0f} kHz"
                              + (f" ({bitrate:.0f} kbps)" if bitrate else "")
                              + ". Heavy cleaning will sound watery. Stay gentle."})
    if loud["true_peak"] > -0.1:
        notes.append({"tone": "warn", "title": "Peaks clip",
                      "text": f"Peaks hit {round(loud['true_peak'], 1) + 0.0:+.1f} dB. Every take fixes this."})

    echo_gap_pre = stems["vocals_dry"] - stems["vocals_rev"]
    if stems.get("crowd_model") and "crowd" in stems:
        scream_share = stems["crowd"] - stems["original"]
        pct = f"{100 * 10 ** (scream_share / 10):.0f}%"
        if scream_share > SCREAMS_LOUD:
            notes.append({"tone": "info", "title": "Loud screaming & cheering",
                          "text": f"The crowd model hears {pct} of the sound as audience."})
            stars.append("screamsecho" if echo_gap_pre < 9 and not low_quality else "screams")
        elif scream_share > SCREAMS_SOME:
            notes.append({"tone": "info", "title": "Some screaming & cheering",
                          "text": f"The crowd model hears {pct} of the sound as audience. Compare J with A."})
    live = crowd_share > -9
    share = f"{100 * 10 ** (crowd_share / 10):.0f}% of the sound is outside voice, drums and bass"
    if live:
        notes.append({"tone": "info", "title": "Lots of crowd & room", "text": share + "."})
        stars.append("crowd80" if low_quality else "crowd65")
    elif crowd_share > -13:
        notes.append({"tone": "info", "title": "Some crowd & room",
                      "text": share + ". A light cut may help; compare B with A."})
    else:
        notes.append({"tone": "ok", "title": "Little crowd noise",
                      "text": "Voice, drums and bass carry most of the sound."})

    echoey = echo_gap < 9
    if echoey:
        notes.append({"tone": "info", "title": "Echo on the voice",
                      "text": f"The echo is only {echo_gap:.0f} dB under the voice. Below 9 dB you hear the hall."})
        stars.append("echo55crowd65" if live and not low_quality else "echo55")
    else:
        notes.append({"tone": "ok", "title": "Voice is fairly dry",
                      "text": f"Echo sits {echo_gap:.0f} dB under the voice."})

    if src["harsh_bump"] > 0.5:
        notes.append({"tone": "info", "title": "Stinging highs",
                      "text": "5–8 kHz is louder than the band below it: crowd hiss and cymbal wash."})
        stars.append("smooth")
    elif src["air_vs_mid"] < -15 and not low_quality:
        notes.append({"tone": "info", "title": "Dull, muffled top",
                      "text": f"Above 8 kHz sits {abs(src['air_vs_mid']):.0f} dB under the mids."})
        stars.append("open")

    if voice_share < -12:
        notes.append({"tone": "info", "title": "Voice is buried",
                      "text": "The singing is a small part of the mix."})
        stars.append("voice")

    if src["mono_fold_db"] < -2.5:
        notes.append({"tone": "warn", "title": "Fake-wide stereo",
                      "text": "The uploader widened the stereo. It will thin out on a phone speaker."})

    if not live and not echoey and not stars:
        stars.insert(0, "original")
        notes.append({"tone": "ok", "title": "Already clean",
                      "text": "This sounds like a mastered track. The original may be the best take."})

    seen, ordered = set(), []
    for s in stars:
        if s not in seen:
            seen.add(s)
            ordered.append(s)
    return {
        "notes": notes,
        "suggested": ordered[:3],
        "avoid": ["strong"] if low_quality else [],
        "numbers": {
            "lufs": loud["lufs"], "true_peak": loud["true_peak"], "lra": loud["lra"],
            "crowd_share_db": round(crowd_share, 1), "echo_gap_db": round(echo_gap, 1),
            "voice_share_db": round(voice_share, 1),
            "scream_share_db": round(stems["crowd"] - stems["original"], 1) if "crowd" in stems else None,
            **src,
        },
    }
