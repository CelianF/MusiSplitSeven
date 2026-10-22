"""Core tests (no UI). Run with:  python -m unittest -v

Tests that need the 316 MB model are skipped unless it is present in
MUSISPLIT_MODEL_DIR (or the default %APPDATA%/StemSplit/models).
YouTube tests need MUSISPLIT_NET_TESTS=1.
"""
from __future__ import annotations

import functools
import hashlib
import http.server
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from musisplit import audio, library, model, pipeline, waveform, youtube
from musisplit.errors import Cancelled, NoInternetError, UserError
from musisplit.paths import ffmpeg_exe
from musisplit.separator import PEAK_TARGET, normalize_pair

HAVE_MODEL = model.is_model_ready()
NET_TESTS = os.environ.get("MUSISPLIT_NET_TESTS") == "1"


def make_song(path: Path, seconds: float = 12.0) -> None:
    """A stereo test track: a voice-like formant tone, a kick and a bass line."""
    expr = (
        "0.3*sin(2*PI*(220+30*sin(2*PI*5*t))*t)*(0.5+0.5*sin(2*PI*0.5*t))"
        "+0.5*sin(2*PI*(50+120*exp(-40*mod(t,0.5)))*mod(t,0.5))*exp(-12*mod(t,0.5))"
        "+0.25*sin(2*PI*55*t)"
    )
    subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi",
         "-i", f"aevalsrc='{expr}|{expr}':s=44100:d={seconds}", str(path)],
        check=True,
    )


def duration_s(path: Path) -> float:
    return audio.decode(path).shape[1] / audio.SAMPLE_RATE


class TitleAndUrlTests(unittest.TestCase):
    def test_safe_title(self):
        self.assertEqual(pipeline.safe_title('AC/DC: "Back" <In> Black?'), "AC_DC_ _Back_ _In_ Black_")
        self.assertEqual(pipeline.safe_title("  trailing dots... "), "trailing dots")
        self.assertEqual(pipeline.safe_title("CON"), "_CON")
        self.assertEqual(pipeline.safe_title(""), "song")
        self.assertEqual(pipeline.safe_title("Beyoncé — Halo 🎵"), "Beyoncé — Halo 🎵")
        self.assertLessEqual(len(pipeline.safe_title("x" * 500)), 120)

    def test_youtube_urls(self):
        good = [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtube.com/watch?v=dQw4w9WgXcQ&list=PL123&t=42",
            "youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ?si=abc",
            "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://www.youtube.com/shorts/abcdefghijk",
            "  https://www.youtube.com/watch?v=dQw4w9WgXcQ  ",
        ]
        bad = [
            "https://www.youtube.com/",
            "https://www.youtube.com/playlist?list=PL123",
            "https://www.youtube.com/watch?list=PL123",
            "https://vimeo.com/12345",
            "https://notyoutube.com/watch?v=dQw4w9WgXcQ",
            "https://youtube.com.evil.example/watch?v=x",
            "C:\\Music\\song.mp3",
            "hello",
        ]
        for u in good:
            self.assertTrue(youtube.is_youtube_url(u), u)
        for u in bad:
            self.assertFalse(youtube.is_youtube_url(u), u)

    def test_output_paths_are_numbered(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            v, i = pipeline.output_paths(out, "Song")
            self.assertEqual((v.name, i.name), ("Song - vocals.mp3", "Song - instrumental.mp3"))
            v.touch()
            v2, i2 = pipeline.output_paths(out, "Song")
            self.assertEqual((v2.name, i2.name), ("Song (2) - vocals.mp3", "Song (2) - instrumental.mp3"))


class NormalizeTests(unittest.TestCase):
    def test_clipping_pair_scaled_with_shared_gain(self):
        v = np.full((2, 10), 0.5, np.float32)
        i = np.full((2, 10), 1.6, np.float32)
        nv, ni = normalize_pair(v, i)
        self.assertAlmostEqual(float(np.abs(ni).max()), PEAK_TARGET, places=5)
        self.assertAlmostEqual(float(nv[0, 0] / ni[0, 0]), 0.5 / 1.6, places=5)

    def test_quiet_pair_untouched(self):
        v = np.full((2, 10), 0.2, np.float32)
        i = np.full((2, 10), 0.4, np.float32)
        nv, ni = normalize_pair(v, i)
        np.testing.assert_array_equal(nv, v)
        np.testing.assert_array_equal(ni, i)


class AudioIoTests(unittest.TestCase):
    def test_decode_formats_and_mono(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "src.wav"
            make_song(src, 3)
            for ext, extra in [(".mp3", []), (".m4a", ["-c:a", "aac"]), (".flac", []),
                               (".ogg", []), (".wav", ["-ac", "1", "-ar", "22050"])]:
                p = Path(d) / f"t{ext}"
                subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-i", str(src), *extra, str(p)], check=True)
                a = audio.decode(p)
                self.assertEqual(a.shape[0], 2, ext)
                self.assertAlmostEqual(a.shape[1] / audio.SAMPLE_RATE, 3.0, delta=0.1, msg=ext)

    def test_decode_garbage_is_user_error(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "fake.mp3"
            p.write_text("this is not audio")
            with self.assertRaises(UserError):
                audio.decode(p)

    def test_mp3_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            t = np.arange(audio.SAMPLE_RATE * 2) / audio.SAMPLE_RATE
            x = np.stack([0.5 * np.sin(2 * np.pi * 440 * t)] * 2).astype(np.float32)
            dest = Path(d) / "x.mp3"
            audio.encode_mp3(x, dest, title="Test")
            self.assertTrue(dest.is_file())
            self.assertFalse(dest.with_name("x.mp3.part").exists())
            self.assertAlmostEqual(duration_s(dest), 2.0, delta=0.1)

    def test_mp3_cancel_leaves_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            x = np.zeros((2, audio.SAMPLE_RATE * 30), np.float32)
            dest = Path(d) / "x.mp3"
            ev = threading.Event()
            ev.set()
            with self.assertRaises(Cancelled):
                audio.encode_mp3(x, dest, cancel=ev)
            self.assertEqual(list(Path(d).iterdir()), [])


class InputValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_job(self, src):
        return pipeline.run_job(src, self.dir / "out", lambda *a: None)

    def test_unsupported_extension(self):
        p = self.dir / "doc.pdf"
        p.write_bytes(b"%PDF")
        with self.assertRaisesRegex(UserError, "isn't a supported audio file"):
            self.run_job(p)

    def test_missing_file(self):
        with self.assertRaisesRegex(UserError, "Couldn't find the file"):
            self.run_job(self.dir / "nope.mp3")

    def test_non_youtube_url(self):
        with self.assertRaisesRegex(UserError, "Only YouTube links"):
            self.run_job("https://vimeo.com/12345")

    def test_youtube_error_messages(self):
        from yt_dlp.utils import DownloadError
        cases = {
            "ERROR: [youtube] x: Sign in to confirm you’re not a bot.": "blocking downloads",
            "ERROR: [youtube] x: Private video. Sign in": "private",
            "ERROR: [youtube] x: Video unavailable": "unavailable",
            "ERROR: Unable to download webpage: <urlopen error [Errno 11001] getaddrinfo failed>": "internet",
        }
        for raw, expected in cases.items():
            self.assertIn(expected, str(youtube._friendly(DownloadError(raw))), raw)

    def test_bad_youtube_url(self):
        with self.assertRaisesRegex(UserError, "doesn't look like a YouTube video link"):
            youtube.download_audio("https://www.youtube.com/playlist?list=x", self.dir)


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_head(self):
        # Minimal Range support so resume can be tested.
        rng = self.headers.get("Range")
        if not rng:
            return super().send_head()
        path = Path(self.translate_path(self.path))
        data = path.read_bytes()
        start = int(rng.split("=")[1].rstrip("-"))
        self.send_response(206)
        self.send_header("Content-Length", str(len(data) - start))
        self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        self.end_headers()
        import io
        return io.BytesIO(data[start:])


class ModelDownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.served = root / "srv"
        self.served.mkdir()
        self.payload = os.urandom(3 * (1 << 20) + 123)
        (self.served / "m.onnx").write_bytes(self.payload)
        handler = functools.partial(_QuietHandler, directory=str(self.served))
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{self.httpd.server_address[1]}/m.onnx"
        self.models = root / "models"
        self.patches = [
            mock.patch.dict(os.environ, {"MUSISPLIT_MODEL_DIR": str(self.models)}),
            mock.patch.object(model, "MODEL_URL", url),
            mock.patch.object(model, "MODEL_SIZE", len(self.payload)),
            mock.patch.object(model, "MODEL_SHA256", hashlib.sha256(self.payload).hexdigest()),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        self.tmp.cleanup()

    def test_download_with_progress(self):
        seen = []
        path = model.ensure_model(lambda d, t: seen.append((d, t)))
        self.assertEqual(path.read_bytes(), self.payload)
        self.assertEqual(seen[-1], (len(self.payload), len(self.payload)))
        self.assertTrue(model.is_model_ready())

    def test_resume_partial(self):
        self.models.mkdir(parents=True)
        (self.models / "htdemucs.onnx.part").write_bytes(self.payload[:1_000_000])
        seen = []
        path = model.ensure_model(lambda d, t: seen.append(d))
        self.assertEqual(seen[0], 1_000_000)
        self.assertEqual(path.read_bytes(), self.payload)

    def test_corrupt_download_rejected(self):
        with mock.patch.object(model, "MODEL_SHA256", "0" * 64):
            with self.assertRaisesRegex(UserError, "damaged"):
                model.ensure_model()
        self.assertFalse(model.is_model_ready())
        self.assertFalse((self.models / "htdemucs.onnx.part").exists())

    def test_cancel_keeps_part_for_resume(self):
        ev = threading.Event()
        with self.assertRaises(Cancelled):
            model.ensure_model(lambda d, t: ev.set() if d > 0 else None, cancel=ev)
        self.assertTrue((self.models / "htdemucs.onnx.part").exists())
        self.assertEqual(model.ensure_model().read_bytes(), self.payload)

    def test_no_internet(self):
        port = self.httpd.server_address[1]
        self.httpd.shutdown()
        self.httpd.server_close()
        self.httpd = None
        with mock.patch.object(model, "MODEL_URL", f"http://127.0.0.1:{port}/m.onnx"):
            with self.assertRaises(NoInternetError):
                model.ensure_model()


class WaveformTests(unittest.TestCase):
    def tone(self, seconds=3.0, amp=0.5):
        t = np.arange(int(44100 * seconds)) / 44100
        return np.stack([amp * np.sin(2 * np.pi * 440 * t)] * 2).astype(np.float32)

    def test_shape_and_shared_scale(self):
        loud, quiet = self.tone(amp=0.8), self.tone(amp=0.2)
        a, b = waveform.compute_pair(loud, quiet)
        self.assertEqual(a.shape, (waveform.BUCKETS,))
        self.assertEqual(a.dtype, np.uint8)
        self.assertEqual(int(a.max()), 255)
        self.assertLess(int(b.max()), 200)  # shared scale: the quiet stem looks quieter

    def test_silence_and_short_audio(self):
        silent = np.zeros((2, 44100), np.float32)
        a, b = waveform.compute_pair(silent, silent)
        self.assertEqual(int(a.max()), 0)
        a, _ = waveform.compute_pair(self.tone(0.001), self.tone(0.001))
        self.assertEqual(a.shape, (waveform.BUCKETS,))

    def test_follows_loudness_over_time(self):
        x = self.tone(4.0)
        x[:, : x.shape[1] // 2] *= 0.1  # first half quiet
        a, _ = waveform.compute_pair(x, np.zeros_like(x))
        self.assertLess(a[: waveform.BUCKETS // 2 - 5].max(), a[waveform.BUCKETS // 2 + 5:].min())


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"MUSISPLIT_LIBRARY_DIR": str(Path(self.tmp.name) / "lib")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def add(self, title):
        x = np.zeros((2, 44100), np.float32)
        return library.add(x, x, title, "https://youtu.be/abc")

    def test_add_list_delete(self):
        self.assertEqual(library.list_entries(), [])
        a = self.add("First")
        b = self.add("Second — é ✓")
        entries = library.list_entries()
        self.assertEqual([e.title for e in entries], ["Second — é ✓", "First"])
        self.assertTrue(entries[0].from_youtube)
        self.assertGreater(a.size_bytes(), 0)
        library.delete(a)
        self.assertEqual([e.folder for e in library.list_entries()], [b.folder])
        library.delete_all()
        self.assertEqual(library.list_entries(), [])

    def test_broken_and_incomplete_folders_ignored(self):
        self.add("Good")
        root = library.library_dir()
        (root / "garbage").mkdir()
        (root / "badmeta").mkdir()
        (root / "badmeta" / "meta.json").write_text("{not json")
        (root / ".20260101-000000-abcdef.tmp").mkdir()
        self.assertEqual([e.title for e in library.list_entries()], ["Good"])
        library.clean_incomplete()
        self.assertFalse((root / ".20260101-000000-abcdef.tmp").exists())

    def test_waveform_rebuilt_for_old_entries(self):
        x = np.stack([np.sin(np.linspace(0, 2000, 44100))] * 2).astype(np.float32) * 0.5
        e = library.add(x, x * 0.5, "Old", "song.mp3")
        for stem in library.STEMS:
            (e.folder / f"{stem}.wave.npy").unlink()  # like an entry saved by v0.1.1
        bars = e.waveform("vocals")
        self.assertEqual(bars.shape, (waveform.BUCKETS,))
        self.assertTrue((e.folder / "instrumental.wave.npy").is_file())

    def test_missing_library_dir(self):
        self.assertEqual(library.list_entries(), [])
        library.delete_all()


@unittest.skipUnless(HAVE_MODEL, "model not downloaded")
class SeparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.song = cls.dir / "My Song.wav"
        make_song(cls.song, 12)
        cls.env = mock.patch.dict(os.environ, {"MUSISPLIT_LIBRARY_DIR": str(cls.dir / "library")})
        cls.env.start()

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.tmp.cleanup()

    def test_matches_demucs_onnx_reference_loop(self):
        import onnxruntime as ort
        from demucs_onnx.inference import SOURCES, _chunked_separate_single

        mix = audio.decode(self.song)
        sess = ort.InferenceSession(str(model.model_path()), providers=["CPUExecutionProvider"])
        ref = _chunked_separate_single(sess, SOURCES, mix, progress=False)
        vocals, inst = pipeline.get_separator().separate(mix)
        np.testing.assert_allclose(vocals, ref["vocals"], atol=2e-3)
        np.testing.assert_allclose(inst, ref["drums"] + ref["bass"] + ref["other"], atol=2e-3)
        # The two stems add back up to the input.
        self.assertGreater(np.corrcoef((vocals + inst).ravel(), mix.ravel())[0, 1], 0.99)

    def test_run_job_end_to_end(self):
        out = self.dir / "out"
        events = []
        res = pipeline.run_job(self.song, out, lambda s, f, m: events.append((s, f)))
        self.assertEqual(res.vocals.name, "My Song - vocals.mp3")
        self.assertEqual(res.instrumental.name, "My Song - instrumental.mp3")
        for p in (res.vocals, res.instrumental):
            self.assertAlmostEqual(duration_s(p), 12.0, delta=0.15)
            self.assertLessEqual(float(np.abs(audio.decode(p)).max()), 1.0)
        stages = [s for s, _ in events]
        self.assertNotIn(pipeline.Stage.DOWNLOADING, stages)
        self.assertLess(stages.index(pipeline.Stage.SEPARATING), stages.index(pipeline.Stage.EXPORTING))
        fracs = [f for s, f in events if s is pipeline.Stage.SEPARATING and f is not None]
        self.assertEqual(fracs[-1], 1.0)
        self.assertEqual(fracs, sorted(fracs))
        self.assertEqual(sorted(p.name for p in out.iterdir()),
                         ["My Song - instrumental.mp3", "My Song - vocals.mp3"])

    def test_cancel_during_separation(self):
        ev = threading.Event()

        def progress(stage, frac, msg):
            if stage is pipeline.Stage.SEPARATING and frac:
                ev.set()

        out = self.dir / "cancelled"
        with self.assertRaises(Cancelled):
            pipeline.run_job(self.song, out, progress, cancel=ev)
        # Nothing is written: no output folder, no history entry, no leftovers.
        self.assertFalse(out.exists())
        before = {e.folder for e in library.list_entries()}
        lib = library.library_dir()
        self.assertFalse(any(p.name.endswith(".tmp") for p in lib.iterdir()) if lib.exists() else False)
        self.assertEqual(before, {e.folder for e in library.list_entries()})

    def test_cancel_during_export_leaves_no_entry(self):
        ev = threading.Event()
        sep = pipeline.separate_source(self.song, lambda *a: None)
        count = len(library.list_entries())

        def progress(stage, frac, msg):
            if stage is pipeline.Stage.EXPORTING and frac:
                ev.set()

        with self.assertRaises(Cancelled):
            pipeline.save_to_library(sep, self.song, progress, cancel=ev)
        self.assertEqual(len(library.list_entries()), count)
        self.assertEqual([p for p in library.library_dir().iterdir() if p.name.startswith(".")], [])

    def test_history_then_export_later(self):
        sep = pipeline.separate_source(self.song, lambda *a: None)
        self.assertEqual(sep.title, "My Song")
        self.assertEqual(sep.vocals.shape, sep.instrumental.shape)
        stages = []
        entry = pipeline.save_to_library(sep, self.song, lambda s, f, m: stages.append(s))
        self.assertEqual(set(stages), {pipeline.Stage.EXPORTING})
        self.assertEqual(entry.title, "My Song")
        self.assertAlmostEqual(entry.duration_s, 12.0, delta=0.05)
        self.assertEqual(entry.source, str(self.song))
        for f in (entry.vocals, entry.instrumental):
            self.assertAlmostEqual(duration_s(f), 12.0, delta=0.15)
        self.assertEqual(entry.waveform("vocals").shape, (waveform.BUCKETS,))
        self.assertIn(entry.folder, [e.folder for e in library.list_entries()])

        out = self.dir / "later"
        res = pipeline.export(entry, out)
        self.assertEqual(res.vocals.read_bytes(), entry.vocals.read_bytes())
        self.assertEqual(res.instrumental.read_bytes(), entry.instrumental.read_bytes())
        # Exporting the same song again doesn't overwrite the first export.
        again = pipeline.export(entry, out)
        self.assertEqual(again.vocals.name, "My Song (2) - vocals.mp3")
        # Deleting from history keeps the exported copies.
        library.delete(entry)
        self.assertTrue(res.vocals.is_file())
        self.assertNotIn(entry.folder, [e.folder for e in library.list_entries()])
        with self.assertRaisesRegex(UserError, "missing from the history"):
            pipeline.export(entry, out)
        with self.assertRaisesRegex(UserError, "missing from the history"):
            pipeline.export_stem(entry, "vocals", out / "x.mp3")

    def test_export_single_stem(self):
        sep = pipeline.separate_source(self.song, lambda *a: None)
        entry = pipeline.save_to_library(sep, self.song, lambda *a: None)
        dest = self.dir / "single" / "Mine.mp3"
        self.assertEqual(pipeline.export_stem(entry, "instrumental", dest), dest)
        self.assertEqual(dest.read_bytes(), entry.instrumental.read_bytes())
        self.assertEqual([p.name for p in dest.parent.iterdir()], ["Mine.mp3"])


@unittest.skipUnless(NET_TESTS and HAVE_MODEL, "set MUSISPLIT_NET_TESTS=1 (and download the model)")
class YouTubeTests(unittest.TestCase):
    def test_download_and_split(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"MUSISPLIT_LIBRARY_DIR": str(Path(d) / "lib")}):
            events = []
            res = pipeline.run_job("https://www.youtube.com/watch?v=jNQXAC9IVRw", Path(d),
                                   lambda s, f, m: events.append(s))
            self.assertEqual(res.title, "Me at the zoo")
            self.assertIn(pipeline.Stage.DOWNLOADING, events)
            self.assertTrue(res.vocals.is_file() and res.instrumental.is_file())

    def test_unavailable_video(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(UserError) as cm:
                youtube.download_audio("https://www.youtube.com/watch?v=xxxxxxxxxxx", Path(d))
            self.assertNotIsInstance(cm.exception, NoInternetError)


if __name__ == "__main__":
    unittest.main()
