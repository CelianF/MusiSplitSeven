"""First-launch download of the htdemucs ONNX model into %APPDATA%/StemSplit/models."""
from __future__ import annotations

import hashlib
import os
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from . import __version__
from .errors import Cancelled, NoInternetError, UserError, looks_like_network_error
from .paths import models_dir

MODEL_FILENAME = "htdemucs.onnx"
# Pinned to a commit of StemSplitio/htdemucs-onnx so the file (and its hash)
# can't change under us.
MODEL_REVISION = "d54ed9eb60e258ea82131c6ee14578628816456a"
MODEL_URL = f"https://huggingface.co/StemSplitio/htdemucs-onnx/resolve/{MODEL_REVISION}/{MODEL_FILENAME}"
MODEL_SHA256 = "68d0bf16428ef66e692cdff8a9ccf28f1ef3f69440d57e58605a4cc55fcc5e74"
MODEL_SIZE = 316_446_953

_CHUNK = 1 << 20

# (downloaded_bytes, total_bytes)
DownloadProgress = Callable[[int, int], None]


def model_path() -> Path:
    return models_dir() / MODEL_FILENAME


def is_model_ready() -> bool:
    p = model_path()
    return p.is_file() and p.stat().st_size == MODEL_SIZE


def _sha256(path: Path) -> hashlib._Hash:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(_CHUNK), b""):
            h.update(block)
    return h


def ensure_model(on_progress: DownloadProgress | None = None,
                 cancel: threading.Event | None = None) -> Path:
    """Return the model path, downloading it first if needed.

    Partial downloads are kept as ``.part`` and resumed on the next try.
    """
    target = model_path()
    if is_model_ready():
        return target

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise UserError(f"Couldn't create the folder {target.parent}.\n{exc.strerror}") from exc

    part = target.with_suffix(target.suffix + ".part")
    have = part.stat().st_size if part.is_file() else 0
    if have > MODEL_SIZE:
        part.unlink()
        have = 0
    hasher = _sha256(part) if have else hashlib.sha256()

    req = urllib.request.Request(MODEL_URL, headers={"User-Agent": f"MusiSplitSeven/{__version__}"})
    if have:
        req.add_header("Range", f"bytes={have}-")

    try:
        if have < MODEL_SIZE:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if have and resp.status != 206:
                    # Server ignored the Range header: start over.
                    have = 0
                    hasher = hashlib.sha256()
                with open(part, "ab" if have else "wb") as f:
                    if on_progress:
                        on_progress(have, MODEL_SIZE)
                    while True:
                        if cancel is not None and cancel.is_set():
                            raise Cancelled()
                        block = resp.read(_CHUNK)
                        if not block:
                            break
                        f.write(block)
                        hasher.update(block)
                        have += len(block)
                        if on_progress:
                            on_progress(have, MODEL_SIZE)
    except urllib.error.HTTPError as exc:
        raise UserError(
            f"The model download failed (server said {exc.code}).\nPlease try again later."
        ) from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise NoInternetError("the separation model") from exc
    except OSError as exc:
        if looks_like_network_error(exc):
            raise NoInternetError("the separation model") from exc
        raise UserError(f"Couldn't save the model to {part.parent}.\n{exc.strerror or exc}") from exc

    if have != MODEL_SIZE:
        raise UserError("The model download was interrupted. Please try again.")
    if hasher.hexdigest() != MODEL_SHA256:
        part.unlink(missing_ok=True)
        raise UserError("The downloaded model was damaged. Please try again.")
    os.replace(part, target)
    return target
