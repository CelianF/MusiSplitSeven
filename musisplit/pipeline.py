"""Download → Separate → Export, with no UI dependency.

The UI calls :func:`separate_source` then :func:`save_to_library` from a
worker thread; every split is kept in the local history (see ``library``),
where the user can listen to it and later :func:`export` (copy) the MP3s to a
folder. The CLI calls :func:`run_job`, which does all three. Progress is
reported through a callback.
"""
from __future__ import annotations

import enum
import errno
import re
import shutil
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from . import audio, library, youtube
from .errors import UserError
from .model import is_model_ready, model_path

if TYPE_CHECKING:
    from .separator import Separator


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


_separator: "Separator | None" = None
_separator_lock = threading.Lock()


def get_separator() -> "Separator":
    """Load the ONNX session once and reuse it for every song."""
    from .separator import Separator  # onnxruntime is slow to import: only when splitting

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
    tags: dict = field(default_factory=dict)  # artist / track / channel hints for lyrics


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
            path, title, tags = youtube.download_audio(
                src, Path(tmp),
                on_progress=lambda f: on_progress(Stage.DOWNLOADING, f, "Downloading audio…"),
                cancel=cancel,
            )
        else:
            path = Path(src)
            check_local_file(path)
            title = path.stem
            file_tags = audio.read_tags(path)
            tags = {"artist": file_tags.get("artist", ""), "track": file_tags.get("title", ""), "channel": ""}

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
    from .separator import normalize_pair

    vocals, instrumental = normalize_pair(vocals, instrumental)
    return Separated(title=title, vocals=vocals, instrumental=instrumental, tags=tags)


def save_to_library(sep: Separated, source: str | Path, on_progress: ProgressCallback,
                    cancel: threading.Event | None = None) -> library.Entry:
    """Exporting: encode both stems into the local history."""
    return library.add(
        sep.vocals, sep.instrumental, sep.title, str(source).strip(), tags=sep.tags,
        on_progress=lambda f, msg: on_progress(Stage.EXPORTING, f, msg),
        cancel=cancel,
    )


def export(entry: library.Entry, out_dir: Path) -> JobResult:
    """Copy ``<title> - vocals.mp3`` and ``<title> - instrumental.mp3`` from the history to ``out_dir``."""
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise UserError(f"Couldn't create the folder {out_dir}.\n{exc.strerror}") from exc

    vocals_path, inst_path = output_paths(out_dir, entry.title)
    done: list[Path] = []
    try:
        for src, dst in ((entry.vocals, vocals_path), (entry.instrumental, inst_path)):
            tmp = dst.with_name(dst.name + ".part")
            try:
                shutil.copyfile(src, tmp)
                tmp.replace(dst)
            except FileNotFoundError as exc:
                if not src.exists():
                    raise UserError(
                        f"The files for \"{entry.title}\" are missing from the history.\nPlease split it again."
                    ) from exc
                raise UserError(f"Couldn't save to {out_dir}.\n{exc.strerror}") from exc
            except OSError as exc:
                tmp.unlink(missing_ok=True)
                if exc.errno == errno.ENOSPC:
                    raise UserError(f"The disk is full. Couldn't save \"{dst.name}\".") from exc
                raise UserError(
                    f"Couldn't save \"{dst.name}\" in {out_dir}.\n"
                    "Check that the folder exists and you can write to it."
                ) from exc
            done.append(dst)
    except BaseException:
        for p in done:
            p.unlink(missing_ok=True)
        raise
    return JobResult(title=entry.title, vocals=vocals_path, instrumental=inst_path)


def export_stem(entry: library.Entry, stem: str, dest: Path) -> Path:
    """Copy one stem from the history to ``dest`` (the UI's "Save as…")."""
    src = entry.stem_path(stem)
    if not src.is_file():
        raise UserError(f"The files for \"{entry.title}\" are missing from the history.\nPlease split it again.")
    tmp = dest.with_name(dest.name + ".part")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, tmp)
        tmp.replace(dest)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        if exc.errno == errno.ENOSPC:
            raise UserError(f"The disk is full. Couldn't save \"{dest.name}\".") from exc
        raise UserError(
            f"Couldn't save \"{dest.name}\" in {dest.parent}.\n"
            "Check that the folder exists and you can write to it."
        ) from exc
    return dest


def run_job(source: str | Path, out_dir: Path, on_progress: ProgressCallback,
            cancel: threading.Event | None = None) -> JobResult:
    """Split ``source`` (a local audio file or a YouTube URL), keep it in the history, and copy the MP3s to ``out_dir``."""
    sep = separate_source(source, on_progress, cancel)
    entry = save_to_library(sep, source, on_progress, cancel)
    del sep
    return export(entry, out_dir)
