"""Download → Separate → Export, with no UI dependency.

The UI calls :func:`separate_source` and later :func:`export` from worker
threads (so the user can listen in between); the CLI calls :func:`run_job`,
which does both. Progress is reported through a callback.
"""
from __future__ import annotations

import enum
import re
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import audio, youtube
from .errors import UserError
from .model import is_model_ready, model_path
from .separator import Separator, normalize_pair


class Stage(enum.Enum):
    DOWNLOADING = "Downloading"
    SEPARATING = "Separating"
    EXPORTING = "Exporting"


# (stage, fraction 0..1 or None when unknown, short status message)
ProgressCallback = Callable[[Stage, float | None, str], None]


@dataclass
class JobResult:
    title: str
    vocals: Path
    instrumental: Path


_separator: Separator | None = None
_separator_lock = threading.Lock()


def get_separator() -> Separator:
    """Load the ONNX session once and reuse it for every song."""
    global _separator
    with _separator_lock:
        if _separator is None:
            if not is_model_ready():
                raise UserError("The separation model hasn't been downloaded yet.")
            try:
                _separator = Separator(model_path())
            except Exception as exc:
                raise UserError(
                    "Couldn't load the separation model. It may be damaged.\n"
                    "Delete the models folder and restart the app to download it again."
                ) from exc
        return _separator


_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_title(title: str, max_len: int = 120) -> str:
    """Make ``title`` usable as a Windows file name."""
    name = _INVALID_CHARS.sub("_", title)
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ")
    name = name[:max_len].rstrip(". ")
    if not name or name.upper() in _RESERVED:
        name = f"_{name}" if name else "song"
    return name


def output_paths(out_dir: Path, title: str) -> tuple[Path, Path]:
    """``<title> - vocals.mp3`` / ``<title> - instrumental.mp3``, numbered if they already exist."""
    base = safe_title(title)
    n = 1
    while True:
        suffix = "" if n == 1 else f" ({n})"
        vocals = out_dir / f"{base}{suffix} - vocals.mp3"
        inst = out_dir / f"{base}{suffix} - instrumental.mp3"
        if not vocals.exists() and not inst.exists():
            return vocals, inst
        n += 1


def check_local_file(path: Path) -> None:
    if not path.is_file():
        raise UserError(f"Couldn't find the file \"{path.name}\".")
    if path.suffix.lower() not in audio.SUPPORTED_EXTENSIONS:
        exts = ", ".join(e.lstrip(".") for e in audio.SUPPORTED_EXTENSIONS)
        raise UserError(f"\"{path.name}\" isn't a supported audio file.\nSupported formats: {exts}.")


@dataclass
class Separated:
    """Separated audio held in memory until the user decides to export it."""
    title: str
    vocals: np.ndarray
    instrumental: np.ndarray


def separate_source(source: str | Path, on_progress: ProgressCallback,
                    cancel: threading.Event | None = None) -> Separated:
    """Downloading + Separating: turn a local file or YouTube URL into two normalized stems."""
    src = str(source).strip()
    is_url = youtube.is_youtube_url(src)
    if not is_url and re.match(r"^\w+://", src):
        raise UserError("Only YouTube links are supported.")

    with tempfile.TemporaryDirectory(prefix="musisplit_") as tmp:
        if is_url:
            on_progress(Stage.DOWNLOADING, None, "Connecting to YouTube…")
            path, title = youtube.download_audio(
                src, Path(tmp),
                on_progress=lambda f: on_progress(Stage.DOWNLOADING, f, "Downloading audio…"),
                cancel=cancel,
            )
        else:
            path = Path(src)
            check_local_file(path)
            title = path.stem

        on_progress(Stage.SEPARATING, None, "Reading audio…")
        mix = audio.decode(path)
    if mix.shape[1] < audio.SAMPLE_RATE:
        raise UserError("This audio is shorter than one second, so there's nothing to split.")
    if mix.shape[1] > youtube.MAX_DURATION_S * audio.SAMPLE_RATE:
        raise UserError(
            f"This song is longer than {youtube.MAX_DURATION_S // 60} minutes, which isn't supported."
        )

    on_progress(Stage.SEPARATING, None, "Loading the model…")
    separator = get_separator()
    on_progress(Stage.SEPARATING, 0.0, "Separating vocals from music…")
    vocals, instrumental = separator.separate(
        mix,
        on_progress=lambda f: on_progress(Stage.SEPARATING, f, "Separating vocals from music…"),
        cancel=cancel,
    )
    del mix
    vocals, instrumental = normalize_pair(vocals, instrumental)
    return Separated(title=title, vocals=vocals, instrumental=instrumental)


def export(sep: Separated, out_dir: Path, on_progress: ProgressCallback,
           cancel: threading.Event | None = None) -> JobResult:
    """Exporting: write ``<title> - vocals.mp3`` and ``<title> - instrumental.mp3`` to ``out_dir``."""
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise UserError(f"Couldn't create the output folder {out_dir}.\n{exc.strerror}") from exc

    vocals_path, inst_path = output_paths(out_dir, sep.title)
    on_progress(Stage.EXPORTING, 0.0, "Saving vocals…")
    audio.encode_mp3(sep.vocals, vocals_path, title=f"{sep.title} (vocals)",
                     on_progress=lambda f: on_progress(Stage.EXPORTING, f / 2, "Saving vocals…"),
                     cancel=cancel)
    try:
        audio.encode_mp3(sep.instrumental, inst_path, title=f"{sep.title} (instrumental)",
                         on_progress=lambda f: on_progress(Stage.EXPORTING, 0.5 + f / 2, "Saving instrumental…"),
                         cancel=cancel)
    except BaseException:
        vocals_path.unlink(missing_ok=True)
        raise
    on_progress(Stage.EXPORTING, 1.0, "Done")
    return JobResult(title=sep.title, vocals=vocals_path, instrumental=inst_path)


def run_job(source: str | Path, out_dir: Path, on_progress: ProgressCallback,
            cancel: threading.Event | None = None) -> JobResult:
    """Split ``source`` (a local audio file or a YouTube URL) into two MP3s in ``out_dir``."""
    return export(separate_source(source, on_progress, cancel), out_dir, on_progress, cancel)
