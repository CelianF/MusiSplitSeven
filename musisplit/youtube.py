"""Audio-only YouTube download with yt-dlp."""
from __future__ import annotations

import re
import threading
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yt_dlp
from yt_dlp.utils import DownloadCancelled, DownloadError

from .errors import Cancelled, NoInternetError, UserError, looks_like_network_error
from .paths import ffmpeg_exe

MAX_DURATION_S = 30 * 60

_YT_HOSTS = {"youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "youtube-nocookie.com"}


def is_youtube_url(text: str) -> bool:
    text = text.strip()
    if not re.match(r"^(https?://)?([\w-]+\.)*(youtube\.com|youtu\.be|youtube-nocookie\.com)/", text, re.I):
        return False
    url = urlparse(text if "://" in text else "https://" + text)
    host = (url.hostname or "").lower().removeprefix("www.")
    if host not in _YT_HOSTS:
        return False
    if host == "youtu.be":
        return len(url.path.strip("/")) > 0
    if url.path == "/watch":
        return bool(parse_qs(url.query).get("v"))
    return bool(re.match(r"^/(shorts|live|embed)/[\w-]+", url.path))


class _QuietLogger:
    """yt-dlp prints to stderr by default, which doesn't exist in a windowed exe."""

    def debug(self, msg: str) -> None: ...
    def info(self, msg: str) -> None: ...
    def warning(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...


def _friendly(exc: DownloadError) -> UserError:
    msg = str(exc)
    low = msg.lower()
    if looks_like_network_error(msg):
        return NoInternetError("the video")
    if "private video" in low:
        return UserError("This video is private, so it can't be downloaded.")
    if "sign in to confirm your age" in low or "age-restricted" in low:
        return UserError("This video is age-restricted, so it can't be downloaded.")
    if "not available in your country" in low or ("geo" in low and "restrict" in low):
        return UserError("This video isn't available in your country.")
    if "members-only" in low or "join this channel" in low:
        return UserError("This video is for channel members only.")
    if "unsupported url" in low or "incomplete youtube id" in low or ("invalid" in low and "id" in low):
        return UserError("That doesn't look like a valid YouTube video link.")
    if "unavailable" in low or "removed" in low or "does not exist" in low:
        return UserError("This video is unavailable. It may have been removed.")
    return UserError("Couldn't download this video from YouTube.\nPlease check the link and try again.")


def download_audio(url: str, workdir: Path,
                   on_progress: Callable[[float | None], None] | None = None,
                   cancel: threading.Event | None = None) -> tuple[Path, str]:
    """Download the best audio-only stream into ``workdir``. Returns ``(file, title)``."""
    if not is_youtube_url(url):
        raise UserError(
            "That doesn't look like a YouTube video link.\n"
            "It should look like https://www.youtube.com/watch?v=..."
        )

    def hook(d: dict) -> None:
        if cancel is not None and cancel.is_set():
            raise DownloadCancelled()
        if on_progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            on_progress(d.get("downloaded_bytes", 0) / total if total else None)

    opts = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "outtmpl": str(workdir / "download.%(ext)s"),
        "restrictfilenames": True,
        "ffmpeg_location": ffmpeg_exe(),
        "logger": _QuietLogger(),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [hook],
        "socket_timeout": 20,
        "retries": 3,
        "cachedir": False,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url.strip(), download=False, process=True)
            if info is None:
                raise UserError("Couldn't find a video at that link.")
            if info.get("_type") == "playlist":
                raise UserError("That's a playlist link. Please paste the link of a single video.")
            if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
                raise UserError("Live streams can't be split. Please wait until the stream is over.")
            duration = info.get("duration") or 0
            if duration > MAX_DURATION_S:
                raise UserError(
                    f"This video is {int(duration // 60)} minutes long. "
                    f"Videos longer than {MAX_DURATION_S // 60} minutes aren't supported."
                )
            info = ydl.process_ie_result(info, download=True)
    except DownloadCancelled as exc:
        raise Cancelled() from exc
    except DownloadError as exc:
        if isinstance(exc.exc_info[1] if exc.exc_info else None, DownloadCancelled):
            raise Cancelled() from exc
        raise _friendly(exc) from exc

    downloads = info.get("requested_downloads") or []
    path = Path(downloads[0]["filepath"]) if downloads else None
    if path is None or not path.is_file():
        found = sorted(workdir.glob("download.*"))
        if not found:
            raise UserError("The download finished but no audio file was found. Please try again.")
        path = found[0]
    return path, info.get("title") or "YouTube audio"
