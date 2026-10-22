"""Decode any audio file to float32 stereo 44.1 kHz and encode MP3, via ffmpeg."""
from __future__ import annotations

import re
import subprocess
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np

from .errors import Cancelled, UserError
from .paths import SUBPROCESS_FLAGS, ffmpeg_exe

SAMPLE_RATE = 44100
CHANNELS = 2
SUPPORTED_EXTENSIONS = (".mp3", ".wav", ".m4a", ".flac", ".ogg")
MP3_BITRATE = "320k"


def _run_ffmpeg(args: list[str], **kwargs) -> subprocess.Popen:
    return subprocess.Popen(
        [ffmpeg_exe(), "-hide_banner", "-nostdin", "-v", "error", *args],
        creationflags=SUBPROCESS_FLAGS,
        **kwargs,
    )


def decode(path: Path) -> np.ndarray:
    """Return ``(2, samples)`` float32 at 44.1 kHz. Mono is upmixed by ffmpeg."""
    with tempfile.TemporaryFile() as err:
        proc = _run_ffmpeg(
            ["-i", str(path), "-map", "0:a:0", "-vn",
             "-f", "f32le", "-acodec", "pcm_f32le", "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE), "-"],
            stdout=subprocess.PIPE, stderr=err,
        )
        raw, _ = proc.communicate()
        err.seek(0)
        detail = err.read().decode("utf-8", "replace").strip()
    if proc.returncode != 0 or not raw:
        raise UserError(
            f"Couldn't read \"{path.name}\".\n"
            "The file may be damaged or not actually an audio file."
        ) from (RuntimeError(detail) if detail else None)
    audio = np.frombuffer(raw, dtype=np.float32)
    audio = audio[: len(audio) - len(audio) % CHANNELS]
    return np.ascontiguousarray(audio.reshape(-1, CHANNELS).T)


def read_tags(path: Path) -> dict:
    """Artist/title tags of an audio file (lower-case keys), or ``{}``."""
    try:
        proc = _run_ffmpeg(["-i", str(path), "-f", "ffmetadata", "-"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        out, _ = proc.communicate(timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    tags = {}
    for line in out.decode("utf-8", "replace").splitlines():
        if "=" in line and not line.startswith((";", "[")):
            key, _, value = line.partition("=")
            # ffmetadata escapes = ; # \ and newlines with a backslash.
            tags.setdefault(key.strip().lower(), re.sub(r"\\(.)", r"\1", value).strip())
    return tags


def encode_mp3(audio: np.ndarray, dest: Path, *, title: str = "",
               on_progress: Callable[[float], None] | None = None,
               cancel: threading.Event | None = None) -> None:
    """Write ``(2, samples)`` float32 audio to ``dest`` as a 320 kbps MP3.

    Writes to a temporary name first so a cancelled or failed export never
    leaves a half-written file under the final name.
    """
    tmp = dest.with_name(dest.name + ".part")
    interleaved = np.ascontiguousarray(audio.T, dtype=np.float32)
    meta = ["-metadata", f"title={title}"] if title else []
    with tempfile.TemporaryFile() as err:
        proc = _run_ffmpeg(
            ["-y", "-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "-i", "-",
             "-c:a", "libmp3lame", "-b:a", MP3_BITRATE, *meta, "-f", "mp3", str(tmp)],
            stdin=subprocess.PIPE, stderr=err,
        )
        frames = interleaved.shape[0]
        step = SAMPLE_RATE * 5
        try:
            for start in range(0, frames, step):
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                proc.stdin.write(interleaved[start:start + step].tobytes())
                if on_progress:
                    on_progress(min(1.0, (start + step) / frames))
            proc.stdin.close()
            proc.wait()
        except BrokenPipeError:
            proc.wait()
        except BaseException:
            proc.kill()
            proc.wait()
            tmp.unlink(missing_ok=True)
            raise
        err.seek(0)
        detail = err.read().decode("utf-8", "replace").strip()
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        if "No space left" in detail:
            raise UserError(f"The disk is full. Couldn't save \"{dest.name}\".")
        raise UserError(
            f"Couldn't save \"{dest.name}\" in {dest.parent}.\n"
            "Check that the folder exists and you can write to it."
        ) from RuntimeError(detail)
    try:
        tmp.replace(dest)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise UserError(f"Couldn't save \"{dest.name}\": {exc.strerror}") from exc
