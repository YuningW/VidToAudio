"""Core checks that need no network or AI models.

  .venv/bin/python -m unittest discover tests
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("VTA_WORK", tempfile.mkdtemp(prefix="vta-test-"))

from vta import analyze, cover, jobs, media, mix, paths, tags  # noqa: E402

SR = 44100


def tone(freq, secs, amp=0.2, pan=0.0, sr=SR):
    t = np.arange(int(secs * sr)) / sr
    x = amp * np.sin(2 * np.pi * freq * t).astype(np.float32)
    return np.stack([x * (1 - pan), x * (1 + pan)], axis=1)


def make_stems(d, secs=8.0):
    """Five stems with distinct content, written like separate.py does."""
    st = d / "stems"
    st.mkdir(parents=True)
    rng = np.random.default_rng(1)
    stems = {
        "vocals_dry": tone(440, secs, 0.2),
        "vocals_rev": tone(660, secs, 0.05, pan=0.6),
        "drums": tone(90, secs, 0.1),
        "bass": tone(55, secs, 0.15),
        "other": (rng.standard_normal((int(secs * SR), 2)) * 0.03).astype(np.float32),
    }
    for k, x in stems.items():
        sf.write(st / f"{k}.flac", x, SR, subtype="PCM_24")
    return stems


class Times(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(media.parse_time("1:23"), 83)
        self.assertEqual(media.parse_time("2:58.5"), 178.5)
        self.assertEqual(media.parse_time("83.5"), 83.5)
        self.assertEqual(media.parse_time("1:02:03"), 3723)
        self.assertEqual(media.parse_time("0：08"), 8)   # full-width colon from a Thai/Chinese keyboard
        self.assertIsNone(media.parse_time(""))
        with self.assertRaises(ValueError):
            media.parse_time("abc")


class SameVideo(unittest.TestCase):
    def test_links_to_the_same_video_match(self):
        from vta.fetch import video_key
        a = video_key("https://www.youtube.com/watch?v=Zagz0zDvpho&list=RDZagz0zDvpho&start_radio=1")
        self.assertEqual(a, video_key("https://youtu.be/Zagz0zDvpho?si=abc"))
        self.assertEqual(a, "youtube:Zagz0zDvpho")
        b = video_key("https://www.bilibili.com/video/BV1ypV66oEZU?spm_id_from=333.788&vd_source=x")
        self.assertEqual(b, video_key("https://www.bilibili.com/video/BV1ypV66oEZU/"))
        self.assertNotEqual(b, video_key("https://www.bilibili.com/video/BV1ypV66oEZU?p=2"))


class Names(unittest.TestCase):
    def test_safe_name_keeps_thai_and_drops_slashes(self):
        self.assertEqual(jobs.safe_name("จีบได้มั้ย / Would You Mind?"), "จีบได้มั้ย Would You Mind")
        self.assertEqual(jobs.safe_name("..."), "Untitled")
        self.assertLessEqual(len(jobs.safe_name("ก" * 200).encode()), 150)

    def test_export_needs_title_and_artist(self):
        with self.assertRaises(jobs.JobError):
            jobs.export_target({"title": "x", "artist": ""}, "mp3", "/tmp")
        p = jobs.export_target({"title": "B612", "artist": "Love"}, "m4a", "~/Music")
        self.assertEqual(p.name, "B612.m4a")
        self.assertTrue(p.is_absolute())


class Mix(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_presets_only_use_known_settings(self):
        keys = {p[0] for p in mix.PARAMS}
        for p in mix.PRESETS:
            self.assertLessEqual(set(p["set"]), keys, p["id"])
        self.assertEqual(mix.PRESETS[0]["id"], "original")
        self.assertEqual(mix.PRESETS[0]["set"], {})

    def test_neutral_mix_is_the_sum_of_stems(self):
        stems = make_stems(self.d)
        out = self.d / "pre.wav"
        mix.render_pre(self.d, mix.full_params({}), out)
        got, _ = sf.read(out, dtype="float32", always_2d=True)
        want = sum(stems.values())
        n = min(len(got), len(want))
        err = np.max(np.abs(got[:n] - want[:n]))
        self.assertLess(err, 1e-4)

    def test_crowd_and_echo_scale_their_stems(self):
        stems = make_stems(self.d)
        out = self.d / "pre.wav"
        mix.render_pre(self.d, mix.full_params({"crowd": 50, "echo": 0, "vocal": 6}), out)
        got, _ = sf.read(out, dtype="float32", always_2d=True)
        v = 10 ** (6 / 20)
        want = stems["vocals_dry"] * v + stems["drums"] + stems["bass"] + stems["other"] * 0.5
        n = min(len(got), len(want))
        self.assertLess(np.max(np.abs(got[:n] - want[:n])), 1e-4)

    def test_width_zero_is_mono(self):
        make_stems(self.d)
        out = self.d / "pre.wav"
        mix.render_pre(self.d, mix.full_params({"width": 0}), out)
        got, _ = sf.read(out, dtype="float32", always_2d=True)
        self.assertLess(np.max(np.abs(got[:, 0] - got[:, 1])), 1e-5)

    def test_eq_boosts_its_band(self):
        make_stems(self.d)
        flat, boosted = self.d / "a.wav", self.d / "b.wav"
        mix.render_pre(self.d, mix.full_params({"vocal": -60, "echo": 0, "drums": 0, "bass": 0, "crowd": 100}), flat)
        mix.render_pre(self.d, mix.full_params({"vocal": -60, "echo": 0, "drums": 0, "bass": 0, "crowd": 100, "air": 6}), boosted)
        a = analyze.measure_source(flat)["bands"]
        b = analyze.measure_source(boosted)["bands"]
        # relative to the loudest band, the top gets louder and the lows get quieter
        self.assertGreater(b["top"] - b["low"], a["top"] - a["low"] + 3)

    def test_render_hits_the_loudness_target_without_clipping(self):
        make_stems(self.d)
        out = self.d / "song.mp3"
        r = mix.render(self.d, {"crowd": 65}, "natural", out, fmt="mp3", fade_in=0.3, fade_out=1.0)
        self.assertTrue(out.exists())
        self.assertAlmostEqual(r["lufs"], -13.0, delta=0.4)
        self.assertLessEqual(r["true_peak"], -0.9)
        r = mix.render(self.d, {}, "library", self.d / "song.m4a", fmt="m4a")
        self.assertAlmostEqual(r["lufs"], -11.5, delta=0.4)


class Analysis(unittest.TestCase):
    def test_diagnosis_stars_echo_and_crowd_for_a_live_echoey_recording(self):
        src = {"cutoff_hz": 19000, "harsh_bump": -5, "air_vs_mid": -10, "mono_fold_db": -0.5}
        loud = {"lufs": -14, "true_peak": 0.7, "lra": 7}
        stems = {"other": -18, "original": -15, "vocals_dry": -22, "vocals_rev": -29, "vocals": -21}
        d = analyze.diagnose(src, loud, stems, 130)
        self.assertIn("echo55crowd65", d["suggested"])
        self.assertIn("crowd65", d["suggested"])
        self.assertTrue(any(n["title"] == "Peaks clip" for n in d["notes"]))

    def test_clean_studio_track_suggests_the_original(self):
        src = {"cutoff_hz": 19000, "harsh_bump": -3, "air_vs_mid": -6, "mono_fold_db": -0.3}
        loud = {"lufs": -9, "true_peak": -0.3, "lra": 4}
        stems = {"other": -30, "original": -12, "vocals_dry": -16, "vocals_rev": -35, "vocals": -16}
        self.assertEqual(analyze.diagnose(src, loud, stems, 256)["suggested"], ["original"])

    def test_low_bitrate_stays_gentle(self):
        src = {"cutoff_hz": 13000, "harsh_bump": -5, "air_vs_mid": -10, "mono_fold_db": -0.5}
        loud = {"lufs": -14, "true_peak": -1, "lra": 7}
        stems = {"other": -17, "original": -15, "vocals_dry": -22, "vocals_rev": -29, "vocals": -21}
        d = analyze.diagnose(src, loud, stems, 72)
        self.assertIn("crowd80", d["suggested"])
        self.assertNotIn("crowd65", d["suggested"])
        self.assertIn("strong", d["avoid"])


class CoverAndTags(unittest.TestCase):
    def setUp(self):
        paths.ensure_dirs()
        self.d = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.d)

    def _picture(self, w, h, fmt="PNG"):
        from PIL import Image
        import io
        im = Image.new("RGBA", (w, h), (200, 30, 90, 255))
        buf = io.BytesIO()
        im.save(buf, fmt)
        return buf.getvalue()

    def test_portrait_png_becomes_square_baseline_jpeg(self):
        from PIL import Image
        import io
        rec = cover.store(self._picture(1146, 1428))
        for mode in ("crop", "fit"):
            data = cover.make_square(rec["id"], {"x": 0, "y": 100, "size": 1146}, mode, 640)
            im = Image.open(io.BytesIO(data))
            self.assertEqual(im.format, "JPEG")
            self.assertEqual(im.size, (640, 640))
            self.assertEqual(im.mode, "RGB")
            self.assertFalse(im.info.get("progressive") or im.info.get("progression"))

    def test_tags_round_trip_with_thai_text(self):
        make_stems(self.d)
        meta = {"title": "รางวัลคนเก่ง (MY ONLY CHAMPION)", "artist": "มิ้ลค์เลิฟ MilkLove",
                "album": "Whale Store", "year": "2024", "track": "3"}
        art = cover.make_square(cover.store(self._picture(800, 800))["id"])
        for fmt in ("mp3", "m4a"):
            out = self.d / f"t.{fmt}"
            mix.render(self.d, {}, "natural", out, fmt=fmt)
            tags.write(out, meta, art)
            got = tags.read(out)
            self.assertEqual(got["title"], meta["title"])
            self.assertEqual(got["artist"], meta["artist"])
            self.assertEqual(got["album_artist"], meta["artist"])
            self.assertEqual(got["pictures"], 1)
            if fmt == "mp3":
                self.assertEqual(got["version"], "2.3")
            # ffprobe agrees there is exactly one attached picture
            streams = media.probe(out)["streams"]
            pics = [s for s in streams if s.get("disposition", {}).get("attached_pic")]
            self.assertEqual(len(pics), 1)


class Separation(unittest.TestCase):
    def test_check_stems_rejects_silence_and_passthrough(from_self):
        from vta.separate import check_stems
        loud = tone(440, 1)
        with from_self.assertRaises(RuntimeError):
            check_stems(loud, np.zeros_like(loud), np.zeros_like(loud))
        with from_self.assertRaises(RuntimeError):
            check_stems(loud, loud, np.zeros_like(loud))   # echo removal wrote silence
        check_stems(loud, loud * 0.8, loud * 0.6)


if __name__ == "__main__":
    unittest.main()
