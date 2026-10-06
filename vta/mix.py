"""The mix: stems + settings -> finished audio.

The same settings drive two renderers that must agree:
  * web/engine.js plays them live in the browser (Web Audio), and
  * render() below writes the real file with ffmpeg.
Both use the same stems, the same RBJ biquad EQ (Web Audio's peaking/shelf
filters are the RBJ cookbook ones, as are ffmpeg's with width_type q / slope),
the same mid/side width matrix and the same loudness target, so the take you
approve in the browser is the take you get on disk.

Stems (each 44.1 kHz stereo, all the same length, and they add up exactly to
the original):
  crowd       screams and cheering (crowd model; silent for songs separated
              before it existed)
  vocals_dry  the voice with the echo taken off
  vocals_rev  the echo that came off the voice   (= vocals - vocals_dry)
  drums, bass
  other       everything else: room, keys, guitars, leftover crowd
              (= original - crowd - vocals - drums - bass)
"""
import math
import shutil
import tempfile
from pathlib import Path

from . import media

STEMS = ["crowd", "vocals_dry", "vocals_rev", "drums", "bass", "other"]

# key, label, unit, min, max, step, default, group, help
PARAMS = [
    ("screams", "Screams & cheering", "%", 0, 120, 5, 100, "clean",
     "What the crowd model heard as audience: screams, cheers, clapping."),
    ("crowd", "Crowd & backing", "%", 0, 120, 5, 100, "clean",
     "Everything that isn't voice, drums or bass: room noise, keys, guitars, leftover crowd."),
    ("echo", "Echo on the voice", "%", 0, 100, 5, 100, "clean",
     "How much of the hall's echo stays on the voice."),
    ("width", "Stereo width", "%", 0, 150, 5, 100, "clean",
     "Crowd and echo sit at the sides. Narrower = less of them, flatter sound."),
    ("vocal", "Voice level", "dB", -6, 6, 0.5, 0, "tone",
     "Lifts or lowers the singing against the rest."),
    ("clarity", "Clarity", "dB", -4, 6, 0.5, 0, "tone",
     "2.8 kHz, where words become easy to follow."),
    ("harsh", "Harshness", "dB", -10, 4, 0.5, 0, "tone",
     "6.5 kHz, where crowd hiss and cymbal wash sting. Cut to smooth."),
    ("air", "Air", "dB", -8, 8, 0.5, 0, "tone",
     "Shelf above 10 kHz. Lift for dull phone audio, cut for hiss."),
    ("mud", "Boxiness", "dB", -8, 4, 0.5, 0, "tone",
     "300 Hz, the boxy, crowded-room sound."),
    ("low", "Low end", "dB", -8, 6, 0.5, 0, "tone",
     "Shelf below 110 Hz. Cut boom that eats headroom."),
    ("drums", "Drums", "%", 0, 150, 5, 100, "stems", "Drum stem level."),
    ("bass", "Bass", "%", 0, 150, 5, 100, "stems", "Bass stem level."),
]
DEFAULTS = {p[0]: p[6] for p in PARAMS}

# Filter shapes shared with engine.js. type: Web Audio BiquadFilterNode type.
EQ = [
    {"key": "low", "type": "lowshelf", "freq": 110},
    {"key": "mud", "type": "peaking", "freq": 300, "q": 1.0},
    {"key": "clarity", "type": "peaking", "freq": 2800, "q": 1.0},
    {"key": "harsh", "type": "peaking", "freq": 6500, "q": 1.4},
    {"key": "air", "type": "highshelf", "freq": 10000},
]

LEVELS = {
    "natural": {"label": "Natural", "lufs": -13.0,
                "help": "Keeps the live dynamics. What you chose for the Whale Store tracks."},
    "library": {"label": "Match September", "lufs": -11.5,
                "help": "Same loudness as the files we made in September."},
    "spotify": {"label": "Spotify level", "lufs": -14.0,
                "help": "Spotify's own normal level. Quieter, most headroom."},
}
LIMIT = 0.80        # limiter ceiling, linear (-1.9 dBFS); proven in September
LIMIT_LOW = 0.70    # used if the encoded file still peaks too high

# Takes offered for comparison. Numbers follow the September vocabulary:
# "other at 65%" -> crowd 65, "deverb 55" -> echo 55.
PRESETS = [
    {"id": "original", "name": "Original",
     "about": "Untouched, only brought to the same loudness. Your reference.",
     "set": {}},
    {"id": "crowd80", "name": "Light crowd cut",
     "about": "Crowd & backing down to 80%. Barely changes the sound.",
     "set": {"crowd": 80}},
    {"id": "crowd65", "name": "Crowd cut",
     "about": "Crowd & backing at 65%, your most-picked level in September.",
     "set": {"crowd": 65}},
    {"id": "echo55", "name": "Less echo",
     "about": "Takes the hall echo off the voice, keeps 55% of it.",
     "set": {"echo": 55}},
    {"id": "echo55crowd65", "name": "Less echo + crowd cut",
     "about": "Echo 55% and crowd 65%. Your pick for Whale Store and Please Be Mine.",
     "set": {"echo": 55, "crowd": 65}},
    {"id": "voice", "name": "Voice forward",
     "about": "Voice up 3 dB with a light crowd cut, for songs where singing gets buried.",
     "set": {"vocal": 3, "crowd": 80, "clarity": 1.5}},
    {"id": "smooth", "name": "Smooth the highs",
     "about": "Tames the stinging 5–8 kHz crowd hiss. Your Please Be Mine fix.",
     "set": {"harsh": -5, "air": -3}},
    {"id": "open", "name": "Open up dull audio",
     "about": "Less boom, more clarity and air for muffled phone recordings. Your Hook fix.",
     "set": {"low": -4, "clarity": 3, "air": 5}},
    {"id": "strong", "name": "Strong clean-up",
     "about": "Screams 25%, crowd 50%, echo 45%, a little narrower. The far end of the ladder.",
     "set": {"screams": 25, "crowd": 50, "echo": 45, "width": 85, "vocal": 1.5}},
    {"id": "screams", "name": "Scream cut",
     "about": "Screams & cheering down to 35%. Leaves the music alone.",
     "set": {"screams": 35}, "needs": "crowd"},
    {"id": "screamsecho", "name": "Scream cut + less echo",
     "about": "Screams 35% and echo 55%, for loud halls full of fans.",
     "set": {"screams": 35, "echo": 55}, "needs": "crowd"},
]


def full_params(p=None):
    out = dict(DEFAULTS)
    for k, v in (p or {}).items():
        if k in out:
            out[k] = float(v)
    return out


def stem_gains(p):
    v = 10 ** (p["vocal"] / 20)
    return {
        "crowd": p["screams"] / 100,
        "vocals_dry": v,
        "vocals_rev": v * p["echo"] / 100,
        "drums": p["drums"] / 100,
        "bass": p["bass"] / 100,
        "other": p["crowd"] / 100,
    }


def width_matrix(p):
    """L' = a L + b R, R' = b L + a R  ==  mid kept, side scaled by s."""
    s = p["width"] / 100
    return (1 + s) / 2, (1 - s) / 2


def _num(x):
    return f"{x:.6f}".rstrip("0").rstrip(".") or "0"


def mix_graph(p):
    """filter_complex taking inputs [0..4] in STEMS order, output [pre]."""
    g = stem_gains(p)
    parts = [f"[{i}:a]volume={_num(g[name])}[s{i}]" for i, name in enumerate(STEMS)]
    ins = "".join(f"[s{i}]" for i in range(len(STEMS)))
    chain = [f"amix=inputs={len(STEMS)}:normalize=0:duration=longest"]
    a, b = width_matrix(p)
    if abs(b) > 1e-9:
        chain.append(f"pan=stereo|c0={_num(a)}*c0+{_num(b)}*c1|c1={_num(b)}*c0+{_num(a)}*c1")
    for f in EQ:
        gain = p[f["key"]]
        if abs(gain) < 1e-6:
            continue
        if f["type"] == "peaking":
            chain.append(f"equalizer=f={f['freq']}:t=q:w={f['q']}:g={_num(gain)}")
        else:  # shelves with slope 1, matching Web Audio
            chain.append(f"{f['type']}=f={f['freq']}:t=s:w=1:g={_num(gain)}")
    parts.append(ins + ",".join(chain) + "[pre]")
    return ";".join(parts)


def stem_paths(job_dir):
    d = Path(job_dir) / "stems"
    return [d / f"{name}.flac" for name in STEMS]


def ensure_stems(job_dir):
    """Songs separated before the crowd model have no crowd stem: add a silent one."""
    import numpy as np
    import soundfile as sf
    d = Path(job_dir) / "stems"
    crowd = d / "crowd.flac"
    ref = d / "vocals_dry.flac"
    if not crowd.exists() and ref.exists():
        info = sf.info(ref)
        sf.write(crowd, np.zeros((info.frames, info.channels), "float32"), info.samplerate, subtype="PCM_24")


def render_pre(job_dir, p, out_wav, start=None, dur=None):
    """Mix + width + EQ, no level change yet, to a float WAV."""
    ensure_stems(job_dir)
    args = []
    for path in stem_paths(job_dir):
        if start is not None:
            args += ["-ss", f"{start:.3f}"]
        if dur is not None:
            args += ["-t", f"{dur:.3f}"]
        args += ["-i", str(path)]
    media.run([*args, "-filter_complex", mix_graph(p), "-map", "[pre]",
               "-ar", str(media.SR), "-c:a", "pcm_f32le", "-y", str(out_wav)])


ENCODERS = {
    "mp3": ["-c:a", "libmp3lame", "-b:a", "320k"],
    "m4a": ["-c:a", "aac_at", "-aac_at_mode", "cvbr", "-b:a", "256k"],
    "wav": ["-c:a", "pcm_s16le"],
}


def level_db(measured_lufs, target_lufs):
    if measured_lufs <= -70:   # silence: leave it alone
        return 0.0
    return target_lufs - measured_lufs


def render(job_dir, params, level, out_path, fmt="mp3", start=None, dur=None,
           fade_in=0.0, fade_out=0.0, on_step=None):
    """Write the finished audio. Returns measurements of the written file."""
    p = full_params(params)
    target = LEVELS.get(level, LEVELS["natural"])["lufs"] if isinstance(level, str) else float(level)
    step = on_step or (lambda *_: None)
    tmp = Path(tempfile.mkdtemp(prefix="vta-render-", dir=Path(job_dir)))
    try:
        step("mix", "Mixing the stems")
        pre = tmp / "pre.wav"
        render_pre(job_dir, p, pre, start, dur)
        step("measure", "Measuring loudness")
        before = media.loudness(pre)
        gain = level_db(before["lufs"], target)
        length = media.duration(pre)
        enc = ENCODERS[fmt]
        if fmt == "m4a" and "aac_at" not in _encoders():
            enc = ["-c:a", "aac", "-b:a", "256k"]
        # pass 1 at the measured gain; the limiter shaves a little loudness off
        # loud songs, so a second pass corrects the gain (and lowers the
        # ceiling if the encoder pushed true peaks above -1 dB)
        plan = [(gain, LIMIT)]
        for attempt in range(3):
            gain, limit = plan[-1]
            step("encode", "Levelling and encoding")
            af = [f"volume={gain:.2f}dB"]
            if fade_in > 0:
                af.append(f"afade=t=in:st=0:d={fade_in:.2f}")
            if fade_out > 0:
                af.append(f"afade=t=out:st={max(0.0, length - fade_out):.3f}:d={fade_out:.2f}")
            af.append(f"alimiter=limit={limit}:attack=5:release=60:level=disabled:latency=1")
            out_tmp = tmp / f"out.{fmt}"
            extra = ["-movflags", "+faststart"] if fmt == "m4a" else []
            media.run(["-i", str(pre), "-af", ",".join(af), "-ar", str(media.SR),
                       *enc, *extra, "-map_metadata", "-1", "-y", str(out_tmp)])
            after = media.loudness(out_tmp)
            off = target - after["lufs"]
            peak_ok = after["true_peak"] <= -1.0
            if (abs(off) <= 0.25 and peak_ok) or after["lufs"] <= -70 or attempt == 2:
                break
            plan.append((gain + (off if abs(off) > 0.25 else 0), limit if peak_ok else LIMIT_LOW))
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(out_tmp), out_path)
        return {"lufs": after["lufs"], "true_peak": after["true_peak"], "lra": after["lra"],
                "gain_db": round(gain, 2), "duration": media.duration(out_path),
                "target": target}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


_enc_cache = None


def _encoders():
    global _enc_cache
    if _enc_cache is None:
        import subprocess
        _enc_cache = subprocess.run([media.FFMPEG, "-hide_banner", "-encoders"],
                                    capture_output=True, text=True).stdout
    return _enc_cache


def config():
    """Everything the browser needs to build sliders, takes and the engine."""
    return {
        "params": [dict(zip(("key", "label", "unit", "min", "max", "step", "default", "group", "help"), p))
                   for p in PARAMS],
        "eq": EQ,
        "levels": LEVELS,
        "limit": LIMIT,
        "presets": PRESETS,
        "stems": STEMS,
        "sr": media.SR,
    }
