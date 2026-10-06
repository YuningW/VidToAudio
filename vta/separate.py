"""Split range.wav into the five stems mix.py works with.

Run as its own process (python -m vta.separate <job_dir> [model]) so the
models' memory is handed back when it finishes. Prints one JSON object per
line: {"step": ..., "label": ...} while working, then {"done": stats}.

  1. Demucs (htdemucs, or htdemucs_ft = slower, a bit cleaner) -> vocals,
     drums, bass. "other" is taken as original - vocals - drums - bass, so the
     stems always add back up to the original exactly.
  2. UVR-DeEcho-DeReverb on the vocals -> the dry voice. The echo stem is
     vocals - dry, again so nothing is lost or invented.
In September the MelBand Roformer de-reverb models passed audio straight
through and wrote silence; the VR model below is the one that worked, and
check_stems() refuses silent or passthrough results.
"""
import json
import logging
import shutil
import sys
import tempfile
import warnings
from pathlib import Path

import numpy as np
import soundfile as sf

from . import paths

DEREVERB = "UVR-DeEcho-DeReverb.pth"
MODELS = {"htdemucs": "htdemucs.yaml", "htdemucs_ft": "htdemucs_ft.yaml"}


def say(**msg):
    print(json.dumps(msg), flush=True)


def db(x):
    return float(10 * np.log10(np.mean(np.square(x, dtype=np.float64)) + 1e-12))


def fit(x, n):
    """Trim or zero-pad x (frames, 2) to n frames."""
    if len(x) >= n:
        return x[:n]
    return np.concatenate([x, np.zeros((n - len(x), x.shape[1]), x.dtype)])


def read(path, n):
    x, _ = sf.read(path, dtype="float32", always_2d=True)
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    return fit(x[:, :2], n)


def check_stems(original, vocals, dry):
    """Raise if separation obviously failed."""
    if db(original) < -70:
        raise RuntimeError("The selected part is silent.")
    if db(vocals) < -75 and db(original) > -40:
        raise RuntimeError("Voice separation returned silence. Try again, or try the other model.")
    if db(vocals) > -60 and db(dry) < db(vocals) - 30:
        raise RuntimeError("Echo removal returned silence for the voice.")


def separate(job_dir, model="htdemucs"):
    from audio_separator.separator import Separator

    job_dir = Path(job_dir)
    src = job_dir / "range.wav"
    out = job_dir / "stems"
    out.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="sep-", dir=job_dir))
    original, sr = sf.read(src, dtype="float32", always_2d=True)
    n = len(original)
    try:
        sep = Separator(log_level=logging.ERROR, model_file_dir=str(paths.MODELS),
                        output_dir=str(tmp), output_format="WAV", use_soundfile=True,
                        normalization_threshold=1.0, sample_rate=sr)

        say(step="demucs", label="Separating voice, drums and bass")
        sep.load_model(MODELS.get(model, MODELS["htdemucs"]))
        sep.separate(str(src), custom_output_names={
            "Vocals": "vocals", "Drums": "drums", "Bass": "bass", "Other": "other_model"})
        vocals = read(tmp / "vocals.wav", n)
        drums = read(tmp / "drums.wav", n)
        bass = read(tmp / "bass.wav", n)
        other = original - vocals - drums - bass

        say(step="dereverb", label="Taking the echo off the voice")
        sep.load_model(DEREVERB)
        sep.separate(str(tmp / "vocals.wav"),
                     custom_output_names={"No Reverb": "vocals_dry", "Reverb": "vocals_rev_model"})
        dry = read(tmp / "vocals_dry.wav", n)
        check_stems(original, vocals, dry)
        rev = vocals - dry

        say(step="save", label="Saving stems")
        stems = {"vocals_dry": dry, "vocals_rev": rev, "drums": drums, "bass": bass, "other": other}
        for name, x in stems.items():
            sf.write(out / f"{name}.flac", x, sr, subtype="PCM_24")
        residual = original - sum(stems.values())
        stats = {name: round(db(x), 2) for name, x in stems.items()}
        stats.update(original=round(db(original), 2), vocals=round(db(vocals), 2),
                     residual=round(db(residual), 2), model=model, frames=n, sr=sr)
        (out / "stats.json").write_text(json.dumps(stats))
        return stats
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    warnings.filterwarnings("ignore")
    job_dir = sys.argv[1]
    model = sys.argv[2] if len(sys.argv) > 2 else "htdemucs"
    try:
        say(done=separate(job_dir, model))
    except Exception as e:  # reported to the UI, not a traceback
        say(error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
