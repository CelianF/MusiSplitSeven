"""Smoke test of a *built* app, run by CI on the packaged exe:

    MusiSplitSeven.exe --self-test report.txt [--net]

A windowed exe has no console, so results go to the report file. Exit code
is 0 when every check passed. Needs the model to be present already
(MUSISPLIT_MODEL_DIR or %APPDATA%/StemSplit/models).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    report_path = Path(args[0]) if args else Path("selftest-report.txt")
    lines: list[str] = []
    failures = 0

    def check(name: str, fn) -> None:
        nonlocal failures
        t0 = time.perf_counter()
        try:
            detail = fn()
            lines.append(f"PASS {name} ({time.perf_counter() - t0:.1f}s) {detail or ''}")
        except Exception:
            failures += 1
            lines.append(f"FAIL {name}\n{traceback.format_exc()}")
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from . import __version__, audio, model, paths, pipeline, youtube

    tmp = Path(tempfile.mkdtemp(prefix="musisplit_selftest_"))
    song = tmp / "Self Test.wav"

    lines.append(f"MusiSplitSeven {__version__} frozen={paths.IS_FROZEN} exe={sys.executable}")

    def ffmpeg_bundled():
        exe = paths.ffmpeg_exe()
        if paths.IS_FROZEN:
            assert Path(exe).resolve().is_relative_to(Path(sys._MEIPASS).resolve()), exe
        out = subprocess.run([exe, "-hide_banner", "-encoders"], capture_output=True, text=True,
                             creationflags=paths.SUBPROCESS_FLAGS, check=True).stdout
        assert "libmp3lame" in out, "ffmpeg has no MP3 encoder"
        return exe

    def make_song():
        expr = ("0.3*sin(2*PI*(220+30*sin(2*PI*5*t))*t)"
                "+0.5*sin(2*PI*(50+120*exp(-40*mod(t,0.5)))*mod(t,0.5))*exp(-12*mod(t,0.5))")
        subprocess.run([paths.ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi",
                        "-i", f"aevalsrc='{expr}|{expr}':s=44100:d=10", str(song)],
                       check=True, creationflags=paths.SUBPROCESS_FLAGS)
        a = audio.decode(song)
        assert a.shape == (2, 441000), a.shape
        return f"{a.shape}"

    def music_dir():
        return str(paths.default_output_dir())

    def youtube_module():
        assert youtube.is_youtube_url("https://youtu.be/dQw4w9WgXcQ")
        import yt_dlp
        return yt_dlp.version.__version__

    def separation():
        assert model.is_model_ready(), f"model missing in {paths.models_dir()}"
        out = tmp / "out"
        res = pipeline.run_job(song, out, lambda *a: None)
        for p in (res.vocals, res.instrumental):
            secs = audio.decode(p).shape[1] / audio.SAMPLE_RATE
            assert abs(secs - 10) < 0.2, (p, secs)
        return ", ".join(sorted(p.name for p in out.iterdir()))

    def preview_player():
        from PySide6.QtCore import QCoreApplication, QUrl
        from PySide6.QtMultimedia import QMediaPlayer

        wav = tmp / "preview.wav"
        audio.write_wav(audio.decode(song), wav)
        player = QMediaPlayer()
        player.setSource(QUrl.fromLocalFile(str(wav)))
        deadline = time.monotonic() + 15
        while player.duration() <= 0 and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.05)
        duration = player.duration()
        assert duration > 9000, f"duration={duration} error={player.errorString()}"
        player.setSource(QUrl())
        return f"duration={duration}ms"

    def main_window():
        from .gui import MainWindow
        win = MainWindow()
        win.show()
        app.processEvents()
        page = "main" if win.stack.currentWidget() is win.main_page else "setup"
        win.main_page.drop_preview()
        win.hide()
        return f"page={page} icon={not app.windowIcon().isNull()}"

    def youtube_download():
        path, title = youtube.download_audio("https://www.youtube.com/watch?v=jNQXAC9IVRw", tmp)
        return f"{title!r} {path.stat().st_size} bytes"

    from .gui import make_app
    app = make_app([])

    check("ffmpeg bundled with mp3 encoder", ffmpeg_bundled)
    check("decode test audio", make_song)
    check("music folder", music_dir)
    check("yt-dlp import", youtube_module)
    check("separate + export", separation)
    check("preview player (QtMultimedia)", preview_player)
    check("main window", main_window)
    if "--net" in argv:
        # Not counted as a failure: YouTube often blocks CI machines as bots.
        try:
            lines.append(f"PASS youtube download {youtube_download()}")
        except Exception as exc:
            lines.append(f"WARN youtube download failed: {exc} (cause: {exc.__cause__})")
    lines.append("ALL PASSED" if failures == 0 else f"{failures} FAILED")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if failures == 0 else 1
