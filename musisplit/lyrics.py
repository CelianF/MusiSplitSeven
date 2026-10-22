"""Lyrics lookup on LRCLIB (https://lrclib.net), a free and open lyrics database.

No API key. Only the song's artist/title (and duration) are sent. Synced
lyrics (LRC) come back as ``[(time_ms, line), ...]`` so the player can
highlight the current line.
"""
from __future__ import annotations

import difflib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field

from . import __version__
from .errors import NoInternetError, UserError, looks_like_network_error

API = "https://lrclib.net/api"
USER_AGENT = f"MusiSplitSeven/{__version__} (https://github.com/CelianF/MusiSplitSeven)"

FOUND = "found"
INSTRUMENTAL = "instrumental"
NOT_FOUND = "not_found"


@dataclass
class Lyrics:
    status: str
    artist: str = ""
    track: str = ""
    plain: str = ""
    synced: list[tuple[int, str]] = field(default_factory=list)
    query: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=1)

    @classmethod
    def from_json(cls, text: str) -> "Lyrics":
        d = json.loads(text)
        d["synced"] = [(int(t), str(line)) for t, line in d.get("synced", [])]
        return cls(**d)


# --------------------------------------------------------------------------- parsing

_TIME_TAG = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")


def parse_lrc(text: str) -> list[tuple[int, str]]:
    """``[01:02.50] words`` lines → ``[(62500, "words")]``, sorted. A line may carry several tags."""
    out = []
    for raw in text.splitlines():
        tags = _TIME_TAG.findall(raw)
        if not tags:
            continue
        line = _TIME_TAG.sub("", raw).strip()
        for m, s in tags:
            out.append((int((int(m) * 60 + float(s)) * 1000), line))
    out.sort(key=lambda x: x[0])
    return out


# Words that mark the bracketed / trailing junk of YouTube titles.
_JUNK = (
    r"official|video|music video|lyric|lyrics|audio|visuali[sz]er|clip|hd|hq|4k|remaster(ed)?|"
    r"live|explicit|clean|mv|m/v|vevo|full|version|color coded|paroles|vid[ée]o officielle|"
    r"clip officiel|en direct|feat\.?|ft\.?|prod\.?"
)
_BRACKETS = re.compile(r"\s*[(\[【「][^)\]】」]*[)\]】」]")
_DASHES = re.compile(r"\s+[-–—|~]\s+")


def clean_title(title: str) -> str:
    """'Daft Punk - Get Lucky (Official Video) [HD]' → 'Daft Punk - Get Lucky'."""
    def drop(m: re.Match) -> str:
        return "" if re.search(_JUNK, m.group(0), re.I) else m.group(0)

    t = _BRACKETS.sub(drop, title)
    t = re.sub(r"\s+(feat\.?|ft\.?|featuring)\s.*$", "", t, flags=re.I)  # trailing "ft. X"
    t = re.sub(r"\s*\|\s*$", "", t)
    t = t.replace("\"", "").replace("“", "").replace("”", "")
    return re.sub(r"\s+", " ", t).strip(" -–—")


def clean_channel(channel: str) -> str:
    """'Daft Punk - Topic' / 'DaftPunkVEVO' → 'Daft Punk'."""
    c = re.sub(r"\s*-\s*Topic$", "", channel or "")
    c = re.sub(r"VEVO$", "", c)
    c = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", c) if " " not in c else c
    return c.strip()


def guess_artist_track(title: str, artist: str | None = None, track: str | None = None,
                       channel: str | None = None) -> tuple[str, str]:
    """Best guess of (artist, track) from tags or a YouTube title."""
    if artist and track:
        return artist.strip(), clean_title(track)
    cleaned = clean_title(title)
    parts = _DASHES.split(cleaned)
    if len(parts) >= 2 and parts[0].strip() and parts[1].strip():
        return parts[0].strip(), parts[1].strip()
    return (artist or clean_channel(channel or "")).strip(), cleaned


# --------------------------------------------------------------------------- LRCLIB


def _get(path: str, params: dict) -> object | None:
    """GET ``API/path``; ``None`` on 404. Network failures raise NoInternetError."""
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise UserError(f"The lyrics service had a problem (error {exc.code}). Try again later.") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise NoInternetError("the lyrics") from exc
    except OSError as exc:
        if looks_like_network_error(exc):
            raise NoInternetError("the lyrics") from exc
        raise
    except ValueError as exc:
        raise UserError("The lyrics service sent something unexpected. Try again later.") from exc


def _usable(rec: dict) -> bool:
    """LRCLIB is crowd-sourced; skip empty or junk entries (e.g. a one-word "probe")."""
    if rec.get("instrumental"):
        return True
    text = rec.get("plainLyrics") or rec.get("syncedLyrics") or ""
    return len(text.strip()) >= 80


def _to_lyrics(rec: dict, query: str) -> Lyrics:
    if rec.get("instrumental") and not rec.get("plainLyrics"):
        return Lyrics(INSTRUMENTAL, rec.get("artistName", ""), rec.get("trackName", ""), query=query)
    synced = parse_lrc(rec.get("syncedLyrics") or "")
    plain = rec.get("plainLyrics") or "\n".join(line for _, line in synced)
    return Lyrics(FOUND, rec.get("artistName", ""), rec.get("trackName", ""), plain.strip(), synced, query)


def _score(rec: dict, want: str, duration_s: float | None) -> float:
    have = f"{rec.get('artistName', '')} {rec.get('trackName', '')}".lower()
    score = difflib.SequenceMatcher(None, want.lower(), have).ratio() * 3
    if duration_s and rec.get("duration"):
        diff = abs(float(rec["duration"]) - duration_s)
        score += 2 if diff <= 3 else (1 if diff <= 8 else -min(diff, 120) / 20)
    if rec.get("syncedLyrics"):
        score += 0.5
    if not _usable(rec):
        score -= 10
    return score


def search(query: str, duration_s: float | None = None) -> Lyrics:
    """Free-text search ("artist title"), best match by name and duration."""
    query = query.strip()
    if not query:
        return Lyrics(NOT_FOUND)
    results = _get("search", {"q": query}) or []
    if not isinstance(results, list) or not results:
        return Lyrics(NOT_FOUND, query=query)
    best = max(results, key=lambda r: _score(r, query, duration_s))
    if _score(best, query, duration_s) < 1.2:
        return Lyrics(NOT_FOUND, query=query)
    return _to_lyrics(best, query)


def find(title: str, duration_s: float | None = None, *, artist: str | None = None,
         track: str | None = None, channel: str | None = None) -> Lyrics:
    """Look up lyrics for a song, trying an exact match first, then a search."""
    a, t = guess_artist_track(title, artist, track, channel)
    query = f"{a} {t}".strip()
    if a and t and duration_s:
        rec = _get("get", {"artist_name": a, "track_name": t, "duration": round(duration_s)})
        if isinstance(rec, dict) and _usable(rec):
            return _to_lyrics(rec, query)
    result = search(query, duration_s)
    if result.status == NOT_FOUND and a and t:
        # Some titles are "Title - Artist".
        swapped = search(f"{t} {a}", duration_s)
        if swapped.status != NOT_FOUND:
            return swapped
    if result.status == NOT_FOUND:
        result.query = query
    return result
