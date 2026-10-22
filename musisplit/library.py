"""Local history of split songs, kept in %APPDATA%/StemSplit/library.

Each split is a folder::

    20261022-143201-a1b2c3/
        meta.json              title, source, date, duration
        vocals.mp3             the two stems
        instrumental.mp3
        vocals.wave.npy        waveform bars for the player view
        instrumental.wave.npy

A split is written into a hidden ``.<name>.tmp`` folder and renamed when
complete, so a crash or cancel never leaves a half-written entry.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from . import audio, waveform
from .errors import UserError
from .paths import app_data_dir

STEMS = ("vocals", "instrumental")


def library_dir() -> Path:
    override = os.environ.get("MUSISPLIT_LIBRARY_DIR")
    return Path(override) if override else app_data_dir() / "library"


@dataclass
class Entry:
    folder: Path
    title: str
    source: str
    created: datetime
    duration_s: float

    @property
    def vocals(self) -> Path:
        return self.folder / "vocals.mp3"

    @property
    def instrumental(self) -> Path:
        return self.folder / "instrumental.mp3"

    def stem_path(self, stem: str) -> Path:
        return self.folder / f"{stem}.mp3"

    def waveform(self, stem: str) -> np.ndarray | None:
        """Bar levels for ``stem``. Entries from v0.1.1 have none yet: compute and cache them."""
        path = self.folder / f"{stem}.wave.npy"
        try:
            return np.load(path, allow_pickle=False)
        except (OSError, ValueError):
            pass
        try:
            bars = dict(zip(STEMS, waveform.compute_pair(*(audio.decode(self.stem_path(s)) for s in STEMS))))
        except (UserError, OSError, ValueError):
            return None
        for s, data in bars.items():
            try:
                np.save(self.folder / f"{s}.wave.npy", data)
            except OSError:
                pass
        return bars[stem]

    @property
    def from_youtube(self) -> bool:
        return self.source.startswith("http")

    def size_bytes(self) -> int:
        try:
            return sum(p.stat().st_size for p in self.folder.iterdir() if p.is_file())
        except OSError:
            return 0

    @classmethod
    def load(cls, folder: Path) -> "Entry":
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        return cls(
            folder=folder,
            title=meta["title"],
            source=meta.get("source", ""),
            created=datetime.fromisoformat(meta["created"]),
            duration_s=float(meta.get("duration_s", 0)),
        )


def add(vocals: np.ndarray, instrumental: np.ndarray, title: str, source: str,
        on_progress: Callable[[float | None, str], None] | None = None,
        cancel: threading.Event | None = None) -> Entry:
    """Store a separated song (MP3s + waveform bars) and return its entry."""
    progress = on_progress or (lambda f, m: None)
    root = library_dir()
    # Full precision so newest-first order is exact even for splits in the same second.
    created = datetime.now()
    name = f"{created:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    tmp = root / f".{name}.tmp"
    try:
        tmp.mkdir(parents=True)
    except OSError as exc:
        raise UserError(f"Couldn't create the history folder {root}.\n{exc.strerror}") from exc

    try:
        bars_v, bars_i = waveform.compute_pair(vocals, instrumental)
        np.save(tmp / "vocals.wave.npy", bars_v)
        np.save(tmp / "instrumental.wave.npy", bars_i)

        jobs = (
            (vocals, "vocals.mp3", f"{title} (vocals)", "Saving vocals…"),
            (instrumental, "instrumental.mp3", f"{title} (instrumental)", "Saving instrumental…"),
        )
        for n, (data, filename, tag, msg) in enumerate(jobs):
            progress(n / len(jobs), msg)
            audio.encode_mp3(data, tmp / filename, title=tag,
                             on_progress=lambda f, n=n, msg=msg: progress((n + f) / len(jobs), msg),
                             cancel=cancel)

        meta = {
            "title": title,
            "source": source,
            "created": created.isoformat(),
            "duration_s": round(vocals.shape[1] / audio.SAMPLE_RATE, 3),
        }
        (tmp / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        final = root / name
        tmp.rename(final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    progress(1.0, "Done")
    return Entry.load(final)


def list_entries() -> list[Entry]:
    """All complete entries, newest first."""
    root = library_dir()
    entries = []
    try:
        folders = [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")]
    except OSError:
        return []
    for folder in folders:
        try:
            entries.append(Entry.load(folder))
        except (OSError, ValueError, KeyError):
            continue
    entries.sort(key=lambda e: (e.created, e.folder.name), reverse=True)
    return entries


def delete(entry: Entry) -> None:
    try:
        shutil.rmtree(entry.folder)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise UserError(f"Couldn't delete \"{entry.title}\".\n{exc.strerror}") from exc


def delete_all() -> None:
    errors = []
    for entry in list_entries():
        try:
            delete(entry)
        except UserError as exc:
            errors.append(str(exc))
    if errors:
        raise UserError(errors[0])


def clean_incomplete() -> None:
    """Remove ``.tmp`` folders left by a crash. Call at startup, before any split runs."""
    try:
        for p in library_dir().glob(".*.tmp"):
            shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass
