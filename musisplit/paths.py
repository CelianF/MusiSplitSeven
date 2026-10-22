"""Where things live on disk: bundled ffmpeg, model cache, default output."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_DIR_NAME = "StemSplit"
IS_WINDOWS = sys.platform == "win32"
IS_FROZEN = getattr(sys, "frozen", False)

# Hide the console window that would otherwise flash up for every ffmpeg call
# when running as a windowed exe.
SUBPROCESS_FLAGS = 0x08000000 if IS_WINDOWS else 0  # CREATE_NO_WINDOW


def _bundle_dir() -> Path:
    if IS_FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def app_data_dir() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_DIR_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_DIR_NAME


def models_dir() -> Path:
    override = os.environ.get("MUSISPLIT_MODEL_DIR")
    return Path(override) if override else app_data_dir() / "models"


def ffmpeg_exe() -> str:
    """Bundled ffmpeg first, then vendor/ffmpeg in a dev checkout, then PATH."""
    name = "ffmpeg.exe" if IS_WINDOWS else "ffmpeg"
    override = os.environ.get("MUSISPLIT_FFMPEG")
    candidates = [Path(override)] if override else []
    candidates += [_bundle_dir() / "ffmpeg" / name, _bundle_dir() / "vendor" / "ffmpeg" / name]
    for c in candidates:
        if c.is_file():
            return str(c)
    found = shutil.which("ffmpeg")
    if found:
        return found
    from .errors import UserError
    raise UserError("The app is missing ffmpeg.exe. Please re-extract the zip and try again.")


def _windows_music_dir() -> Path | None:
    """The user's real Music folder, which may be redirected (e.g. to OneDrive)."""
    import ctypes
    from ctypes import wintypes
    import uuid

    folderid_music = uuid.UUID("{4BD8D571-6D19-48D3-BE97-422220080E43}")
    guid = (ctypes.c_byte * 16).from_buffer_copy(folderid_music.bytes_le)
    out = ctypes.c_wchar_p()
    shell32 = ctypes.windll.shell32
    if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, wintypes.HANDLE(0), ctypes.byref(out)) != 0:
        return None
    try:
        return Path(out.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(out)


def default_output_dir() -> Path:
    music = None
    if IS_WINDOWS:
        try:
            music = _windows_music_dir()
        except OSError:
            music = None
    return (music or Path.home() / "Music") / APP_DIR_NAME
