"""PySide6 single-window UI. All heavy work runs on QThreads.

The main page has three states:

- input:   drop a file or paste a YouTube link, then Split
- working: only the progress (Downloading → Separating → Exporting) and Cancel
- result:  one row per stem (play, waveform, Save), plus "New split"

Every split is kept in History until the user deletes it.
"""
from __future__ import annotations

import bisect
import getpass
import logging
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PySide6.QtCore import QLockFile, QObject, QPointF, QRectF, QSettings, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor, QDesktopServices, QFont, QIcon, QPainter, QPainterPath, QPalette, QPixmap, QPolygonF,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from . import APP_NAME, __version__, library
from . import lyrics as lyrics_mod
from .audio import SUPPORTED_EXTENSIONS
from .errors import Cancelled, UserError
from .library import Entry
from .model import MODEL_SIZE, ensure_model, is_model_ready
from .paths import IS_FROZEN, IS_WINDOWS, SUBPROCESS_FLAGS, _bundle_dir, app_data_dir, default_output_dir
from .pipeline import Stage, export_stem, output_paths, save_to_library, separate_source
from .youtube import is_youtube_url

log = logging.getLogger("musisplit")

UNEXPECTED = "Something unexpected went wrong.\nDetails were saved to {log}."

# Pastel green and cream, flat colours only.
CREAM = "#FAF6EC"
CARD = "#FFFDF7"
SAND = "#F2ECDD"
SAND_DARK = "#E9E1CE"
BORDER = "#E6DDC8"
TEXT = "#2C3A31"
MUTED = "#7C7666"
# Accent: pastel emerald.
GREEN = "#9FDFC4"
GREEN_HOVER = "#88D6B5"
GREEN_SOFT = "#E2F5EC"
GREEN_MID = "#4FBF94"
EMERALD = "#2E9A72"
GREEN_DARK = "#1D5C45"
RED_SOFT = "#F6E2DA"
RED = "#9C4434"

STYLE = """
QWidget { color: @TEXT; font-size: 10pt; }
QMainWindow, QScrollArea, #scrollBody, #historyList { background: @CREAM; }
QScrollArea { border: none; }
#title { font-size: 20pt; font-weight: 700; color: @GREEN_DARK; }
#subtitle { color: @MUTED; font-size: 10.5pt; }
#card { background: @CARD; border: 1px solid @BORDER; border-radius: 14px; }
#dropzone { background: @CREAM; border: 2px dashed #D8CDB2; border-radius: 12px; }
#dropzone[active="true"] { background: @GREEN_SOFT; border-color: @GREEN_MID; }
#dropzone[filled="true"] { background: @GREEN_SOFT; border: 2px solid @GREEN_MID; }
#dropTitle { font-size: 11.5pt; font-weight: 600; }
#muted { color: @MUTED; }
#orLabel { color: #A79F8B; font-weight: 600; }
#divider { background: @BORDER; max-height: 1px; min-height: 1px; border: none; }
QLineEdit { background: @CARD; border: 1px solid #DCD2BC; border-radius: 9px; padding: 9px 11px; font-size: 10.5pt; }
QLineEdit:focus { border-color: @GREEN_MID; }
QPushButton { background: @SAND; color: @TEXT; border: 1px solid @BORDER; border-radius: 9px; padding: 8px 16px; font-weight: 600; }
QPushButton:hover { background: @SAND_DARK; }
QPushButton:disabled { color: #B5AE9C; background: #F4F0E6; border-color: #EEE8DA; }
QPushButton#primary { background: @GREEN; color: @GREEN_DARK; border: 1px solid @GREEN; padding: 11px 22px; font-size: 11pt; }
QPushButton#primary:hover { background: @GREEN_HOVER; border-color: @GREEN_HOVER; }
QPushButton#primary:disabled { background: #D6EEE4; color: #86B5A2; border-color: #D6EEE4; }
QPushButton#danger { color: @RED; }
QPushButton#danger:hover { background: @RED_SOFT; border-color: #EBCBBF; }
QPushButton#small { padding: 6px 12px; font-size: 9.5pt; }
QPushButton#play { background: @GREEN; border: none; border-radius: 21px; min-width: 42px; max-width: 42px; min-height: 42px; max-height: 42px; padding: 0; }
QPushButton#play:hover { background: @GREEN_HOVER; }
#stage { color: #ABA390; font-weight: 600; padding: 4px 10px; border-radius: 11px; background: @SAND; }
#stage[state="active"] { color: @GREEN_DARK; background: @GREEN; }
#stage[state="done"] { color: @GREEN_DARK; background: @GREEN_SOFT; }
#stage[state="skipped"] { color: #C9C2B1; background: #F6F2E8; text-decoration: line-through; }
#arrow { color: #BDB59F; }
#status { font-size: 10.5pt; }
#sectionTitle { font-size: 13pt; font-weight: 700; }
#pageTitle { font-size: 16pt; font-weight: 700; color: @GREEN_DARK; }
#trackName { font-size: 11pt; font-weight: 700; }
#time { color: @MUTED; font-size: 9.5pt; }
#error { background: @RED_SOFT; color: @RED; border: 1px solid #EBCBBF; border-radius: 10px; padding: 10px 12px; }
#success { background: @GREEN_SOFT; color: @GREEN_DARK; border: 1px solid #C2E9D8; border-radius: 10px; padding: 10px 12px; }
#historyRow { background: @CARD; border: 1px solid @BORDER; border-radius: 12px; }
#historyRow[current="true"] { border: 2px solid @GREEN; }
QPushButton#tab { background: transparent; border: none; border-bottom: 3px solid transparent; border-radius: 0; padding: 6px 2px; margin-right: 18px; color: @MUTED; font-size: 10.5pt; }
QPushButton#tab:hover { color: @TEXT; }
QPushButton#tab:checked { color: @GREEN_DARK; border-bottom: 3px solid @GREEN_MID; }
QPushButton#pill { padding: 6px 14px 6px 10px; border-radius: 15px; background: @GREEN_SOFT; color: @GREEN_DARK; border: 1px solid #C2E9D8; }
QPushButton#pill:hover { background: #D3EFE2; }
#lyricLine { color: #8E887A; font-size: 12pt; padding: 3px 8px; border-radius: 7px; }
#lyricLine:hover { background: @SAND; }
#lyricLine[current="true"] { color: @GREEN_DARK; font-weight: 700; background: @GREEN_SOFT; }
#lyricsPlain { font-size: 11.5pt; padding: 4px 8px; }
#lyricsBox { background: @CARD; border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #DDD4BF; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""
for _name, _value in sorted(dict(globals()).items(), key=lambda kv: -len(kv[0])):
    if _name.isupper() and isinstance(_value, str) and _value.startswith("#"):
        STYLE = STYLE.replace(f"@{_name}", _value)

STEMS = (("vocals", "Vocals"), ("instrumental", "Instrumental"))


def _repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)


def _fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "less than a minute"
    minutes = round(seconds / 60)
    return f"about {minutes} minute{'s' if minutes != 1 else ''}"


def _fmt_clock(ms: float) -> str:
    s = max(0, int(ms)) // 1000
    return f"{s // 60}:{s % 60:02d}"


def _fmt_size(n: int) -> str:
    return f"{n / 1e6:.0f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB"


def _transport_icon(playing: bool) -> QIcon:
    size = 20
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(GREEN_DARK))
    if playing:
        p.drawRoundedRect(QRectF(4.5, 3.5, 4, 13), 1.5, 1.5)
        p.drawRoundedRect(QRectF(11.5, 3.5, 4, 13), 1.5, 1.5)
    else:
        p.drawPolygon(QPolygonF([QPointF(6.5, 3.5), QPointF(16.5, 10), QPointF(6.5, 16.5)]))
    p.end()
    return QIcon(pm)


def paint_bars(p: QPainter, rect: QRectF, levels: np.ndarray | None, played: float = 0.0,
               bar: float = 3.0, gap: float = 2.0) -> None:
    """Draw ``levels`` (uint8) as rounded bars mirrored around the middle of ``rect``.

    Bars left of ``played`` (0..1) are dark green, the rest light green.
    """
    n = max(1, int((rect.width() + gap) // (bar + gap)))
    if levels is None or len(levels) == 0:
        heights = np.full(n, 0.04)
    else:
        # Max over each group of buckets so short peaks still show.
        edges = np.linspace(0, len(levels), n + 1).astype(np.int64)
        edges[1:] = np.maximum(edges[1:], edges[:-1] + 1)
        edges = np.minimum(edges, len(levels))
        heights = np.array([levels[a:b].max() if b > a else 0 for a, b in zip(edges[:-1], edges[1:])]) / 255
    mid = rect.center().y()
    played_x = rect.left() + played * rect.width()
    dark, light = QColor(EMERALD), QColor(GREEN)
    p.setPen(Qt.NoPen)
    for i, h in enumerate(heights):
        x = rect.left() + i * (bar + gap)
        half = max(1.5, h * rect.height() / 2)
        p.setBrush(dark if x + bar / 2 <= played_x else light)
        p.drawRoundedRect(QRectF(x, mid - half, bar, 2 * half), bar / 2, bar / 2)


# --------------------------------------------------------------------------- workers


class ModelWorker(QThread):
    progress = Signal(int, int)
    succeeded = Signal()
    failed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.cancel = threading.Event()

    def run(self) -> None:
        try:
            ensure_model(self.progress.emit, self.cancel)
            self.succeeded.emit()
        except Cancelled:
            pass
        except UserError as exc:
            self.failed.emit(str(exc))
        except Exception:
            log.exception("model download failed")
            self.failed.emit(UNEXPECTED.format(log=log_path()))


class JobWorker(QThread):
    """Runs ``fn(progress, cancel)`` off the UI thread and reports back with signals."""

    progress = Signal(object, object, str)  # Stage, fraction | None, message
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, fn: Callable) -> None:
        super().__init__()
        self.fn = fn
        self.cancel = threading.Event()

    def run(self) -> None:
        try:
            self.succeeded.emit(self.fn(self.progress.emit, self.cancel))
        except Cancelled:
            self.cancelled.emit()
        except UserError as exc:
            self.failed.emit(str(exc))
        except MemoryError:
            self.failed.emit("Your computer ran out of memory.\nClose other programs and try again.")
        except Exception:
            log.exception("background job failed")
            self.failed.emit(UNEXPECTED.format(log=log_path()))


class BackgroundCall(QObject):
    """Run a short network call on a daemon thread (dropped at exit, unlike a QThread)."""

    succeeded = Signal(object)
    failed = Signal(str)

    def start(self, fn: Callable) -> None:
        def run() -> None:
            try:
                self.succeeded.emit(fn())
            except UserError as exc:
                self.failed.emit(str(exc))
            except Exception:
                log.exception("background call failed")
                self.failed.emit(UNEXPECTED.format(log=log_path()))
        threading.Thread(target=run, daemon=True).start()


def split_into_library(source: str) -> Callable:
    def run(progress, cancel) -> Entry:
        sep = separate_source(source, progress, cancel)
        return save_to_library(sep, source, progress, cancel)
    return run


# --------------------------------------------------------------------------- widgets


class PillBar(QWidget):
    """A flat, fully rounded progress bar.

    Qt's own busy animation draws a square-ended chunk, so this paints both
    modes itself and clips everything to the rounded track.
    ``setRange(0, 0)`` means busy, like QProgressBar.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(12)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._max = 1000
        self._value = 0
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._step)

    def setRange(self, lo: int, hi: int) -> None:
        self._max = hi - lo
        busy = self._max <= 0
        if busy and not self._timer.isActive():
            self._phase = 0.0
            self._timer.start()
        elif not busy:
            self._timer.stop()
        self.update()

    def setValue(self, v: int) -> None:
        self._value = v
        self.update()

    def value(self) -> int:
        return self._value

    def is_busy(self) -> bool:
        return self._max <= 0

    def _step(self) -> None:
        self._phase = (self._phase + 0.008) % 1.0
        self.update()

    def hideEvent(self, e) -> None:
        self._timer.stop()
        super().hideEvent(e)

    def showEvent(self, e) -> None:
        if self.is_busy():
            self._timer.start()
        super().showEvent(e)

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        radius = r.height() / 2
        track = QPainterPath()
        track.addRoundedRect(r, radius, radius)
        p.fillPath(track, QColor(SAND))
        p.setClipPath(track)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(GREEN_MID))
        if self.is_busy():
            # A pill sliding left to right, entering and leaving through the rounded ends.
            w = r.width() * 0.3
            x = -w + self._phase * (r.width() + w)
            p.drawRoundedRect(QRectF(x, 0, w, r.height()), radius, radius)
        elif self._value > 0:
            w = max(r.height(), r.width() * min(1.0, self._value / self._max))
            p.drawRoundedRect(QRectF(0, 0, w, r.height()), radius, radius)
        p.end()


class Waveform(QWidget):
    """Bars for one stem; click or drag to seek."""

    seekRequested = Signal(float)
    scrubbed = Signal(float)

    def __init__(self) -> None:
        super().__init__()
        self.levels: np.ndarray | None = None
        self.played = 0.0
        self.dragging = False
        self.setMinimumWidth(160)
        self.setFixedHeight(54)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)

    def set_levels(self, levels: np.ndarray | None) -> None:
        self.levels = levels
        self.played = 0.0
        self.update()

    def set_played(self, frac: float) -> None:
        if not self.dragging:
            self.played = min(1.0, max(0.0, frac))
            self.update()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        paint_bars(p, QRectF(self.rect()), self.levels, self.played)
        p.end()

    def _frac(self, x: float) -> float:
        return min(1.0, max(0.0, x / max(1, self.width())))

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self.dragging = True
            self.played = self._frac(e.position().x())
            self.scrubbed.emit(self.played)
            self.update()

    def mouseMoveEvent(self, e) -> None:
        if self.dragging:
            self.played = self._frac(e.position().x())
            self.scrubbed.emit(self.played)
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        if self.dragging:
            self.dragging = False
            self.seekRequested.emit(self.played)


class TrackRow(QWidget):
    """(▶) Name  ▮▮▮▮▮▮▮▮  (Save): one stem with its own player."""

    started = Signal(object)  # this row, so the page can pause the other one
    saveClicked = Signal(str)
    error = Signal(str)
    position = Signal(int)  # ms, while playing and after seeks
    playingChanged = Signal(bool)

    def __init__(self, stem: str, label: str) -> None:
        super().__init__()
        self.stem = stem
        self.output = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.output)
        self.player.errorOccurred.connect(self._on_error)
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.durationChanged.connect(lambda ms: self._show_time(self.player.position(), int(ms)))
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._tick)
        self._duration_hint = 0
        self._reported_error = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        self.play_btn = QPushButton()
        self.play_btn.setObjectName("play")
        self.play_btn.setCursor(Qt.PointingHandCursor)
        self.play_btn.setIconSize(QSize(20, 20))
        self.play_btn.setIcon(_transport_icon(False))
        self.play_btn.setToolTip(f"Play / pause the {label.lower()}")
        self.play_btn.clicked.connect(self.toggle)

        names = QVBoxLayout()
        names.setSpacing(0)
        self.name = QLabel(label)
        self.name.setObjectName("trackName")
        self.time = QLabel("0:00 / 0:00")
        self.time.setObjectName("time")
        names.addStretch()
        names.addWidget(self.name)
        names.addWidget(self.time)
        names.addStretch()
        namebox = QWidget()
        namebox.setLayout(names)
        namebox.setFixedWidth(112)

        self.wave = Waveform()
        self.wave.seekRequested.connect(self._seek_frac)
        self.wave.scrubbed.connect(lambda f: self._show_time(f * self.duration(), self.duration()))

        self.save_btn = QPushButton("Save")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.setToolTip(f"Save the {label.lower()} as an MP3")
        self.save_btn.clicked.connect(lambda: self.saveClicked.emit(self.stem))

        lay.addWidget(self.play_btn)
        lay.addWidget(namebox)
        lay.addWidget(self.wave, 1)
        lay.addWidget(self.save_btn)

    def load(self, path: Path, levels: np.ndarray | None, duration_ms: int) -> None:
        self._reported_error = False
        self._duration_hint = duration_ms
        self.wave.set_levels(levels)
        self.save_btn.setText("Save")
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self._show_time(0, duration_ms)

    def unload(self) -> None:
        """Stop and release the file so it can be deleted (Windows locks open files)."""
        self.player.stop()
        self.player.setSource(QUrl())

    def duration(self) -> int:
        return int(self.player.duration()) or self._duration_hint

    def is_playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlayingState

    def toggle(self) -> None:
        if self.is_playing():
            self.player.pause()
            return
        if self.duration() and self.player.position() >= self.duration() - 100:
            self.player.setPosition(0)
        self.started.emit(self)
        self.player.play()

    def pause(self) -> None:
        if self.is_playing():
            self.player.pause()

    def _seek_frac(self, frac: float) -> None:
        self.player.setPosition(int(frac * self.duration()))
        self._tick()

    def _tick(self) -> None:
        pos, dur = int(self.player.position()), self.duration()
        if dur:
            self.wave.set_played(pos / dur)
        if not self.wave.dragging:
            self._show_time(pos, dur)
        self.position.emit(pos)

    def seek(self, ms: int) -> None:
        self.player.setPosition(int(ms))
        self._tick()

    def _show_time(self, pos: float, dur: int) -> None:
        self.time.setText(f"{_fmt_clock(pos)} / {_fmt_clock(dur)}")

    def _on_state(self, state) -> None:
        playing = state == QMediaPlayer.PlayingState
        self.play_btn.setIcon(_transport_icon(playing))
        self.timer.start() if playing else self.timer.stop()
        self._tick()
        self.playingChanged.emit(playing)

    def _on_error(self, _err, message: str) -> None:
        log.warning("player error (%s): %s", self.stem, message)
        if not self._reported_error:
            self._reported_error = True
            self.error.emit(
                "This song can't be played on this computer (is a speaker or headset connected?).\n"
                "You can still save the files and listen to them in your music player."
            )


class LyricLine(QLabel):
    clicked = Signal(int)

    def __init__(self, ms: int, text: str) -> None:
        super().__init__(text or "♪")
        self.ms = ms
        self.setObjectName("lyricLine")
        self.setWordWrap(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"Jump to {_fmt_clock(ms)}")

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.ms)


class LyricsView(QWidget):
    """Lyrics for the shown song, following the playing track when they are synced."""

    playRequested = Signal(str)  # stem
    lineClicked = Signal(int)  # ms
    searchRequested = Signal(str)
    retryRequested = Signal()

    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        transport = QHBoxLayout()
        transport.setSpacing(8)
        self.play_buttons: dict[str, QPushButton] = {}
        for stem, label in (("instrumental", "Instrumental"), ("vocals", "Vocals")):
            b = _button(label, "pill", lambda _=False, s=stem: self.playRequested.emit(s))
            b.setIcon(_transport_icon(False))
            b.setIconSize(QSize(16, 16))
            self.play_buttons[stem] = b
            transport.addWidget(b)
        transport.addStretch()
        self.time = QLabel("")
        self.time.setObjectName("time")
        transport.addWidget(self.time)
        lay.addLayout(transport)

        self.status = QLabel()
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.retry = _button("Try again", "small", lambda: self.retryRequested.emit())
        statusrow = QHBoxLayout()
        statusrow.addWidget(self.status, 1)
        statusrow.addWidget(self.retry)
        lay.addLayout(statusrow)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("lyricsBox")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFixedHeight(320)
        self.body = QWidget()
        self.body.setObjectName("lyricsBox")
        self.lines_lay = QVBoxLayout(self.body)
        self.lines_lay.setContentsMargins(0, 0, 8, 0)
        self.lines_lay.setSpacing(2)
        self.scroll.setWidget(self.body)
        lay.addWidget(self.scroll)

        search = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Wrong song? Type the artist and title")
        self.query.returnPressed.connect(self._search)
        search.addWidget(self.query, 1)
        search.addWidget(_button("Search", "", self._search))
        lay.addLayout(search)
        credit = QLabel('Lyrics from <a href="https://lrclib.net" style="color:#2E9A72;">LRCLIB</a>, a free community database.')
        credit.setObjectName("muted")
        credit.setOpenExternalLinks(True)
        lay.addWidget(credit)

        self.lines: list[LyricLine] = []
        self.times: list[int] = []
        self.current = -1
        self.show_loading()

    def _search(self) -> None:
        text = self.query.text().strip()
        if text:
            self.searchRequested.emit(text)

    def _clear(self) -> None:
        while self.lines_lay.count():
            item = self.lines_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.lines, self.times, self.current = [], [], -1

    def _set_status(self, text: str, retry: bool = False) -> None:
        self.status.setText(text)
        self.status.setVisible(bool(text))
        self.retry.setVisible(retry)

    def show_loading(self) -> None:
        self._clear()
        self._set_status("Looking for the lyrics…")
        self.scroll.hide()

    def show_error(self, msg: str) -> None:
        self._clear()
        self._set_status(msg, retry=True)
        self.scroll.hide()

    def show_result(self, result: "lyrics_mod.Lyrics") -> None:
        self._clear()
        if result.query and not self.query.text():
            self.query.setText(result.query)
        if result.status == lyrics_mod.INSTRUMENTAL:
            self._set_status(f"\"{result.track}\" is an instrumental: there are no lyrics.")
            self.scroll.hide()
            return
        if result.status != lyrics_mod.FOUND:
            self._set_status("No lyrics found for this song. Try typing the artist and title below.")
            self.scroll.hide()
            return
        if result.synced:
            self._set_status(f"{result.artist} – {result.track}  ·  click a line to jump there")
            for ms, text in result.synced:
                line = LyricLine(ms, text)
                line.clicked.connect(self.lineClicked)
                self.lines_lay.addWidget(line)
                self.lines.append(line)
                self.times.append(ms)
        else:
            self._set_status(f"{result.artist} – {result.track}  ·  these lyrics aren't timed, so they won't follow the music")
            plain = QLabel(result.plain)
            plain.setObjectName("lyricsPlain")
            plain.setWordWrap(True)
            plain.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.lines_lay.addWidget(plain)
        self.lines_lay.addStretch()
        self.scroll.verticalScrollBar().setValue(0)
        self.scroll.show()

    def set_position(self, ms: int, duration: int) -> None:
        self.time.setText(f"{_fmt_clock(ms)} / {_fmt_clock(duration)}")
        if not self.times:
            return
        idx = bisect.bisect_right(self.times, ms + 150) - 1
        if idx == self.current:
            return
        for i in (self.current, idx):
            if 0 <= i < len(self.lines):
                self.lines[i].setProperty("current", i == idx)
                _repolish(self.lines[i])
        self.current = idx
        if idx >= 0 and self.isVisible():
            line = self.lines[idx]
            bar = self.scroll.verticalScrollBar()
            bar.setValue(int(line.y() - self.scroll.viewport().height() / 2 + line.height() / 2))

    def set_playing(self, stem: str, playing: bool) -> None:
        self.play_buttons[stem].setIcon(_transport_icon(playing))


class DropZone(QFrame):
    fileDropped = Signal(str)
    urlDropped = Signal(str)
    browseClicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("dropzone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(124)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(6)
        self.title = QLabel()
        self.title.setObjectName("dropTitle")
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setWordWrap(True)
        self.hint = QLabel()
        self.hint.setObjectName("muted")
        self.hint.setAlignment(Qt.AlignCenter)
        self.browse = QPushButton()
        self.browse.setCursor(Qt.PointingHandCursor)
        self.browse.clicked.connect(self.browseClicked)
        lay.addWidget(self.title)
        lay.addWidget(self.hint)
        lay.addWidget(self.browse, alignment=Qt.AlignCenter)
        self.show_file(None)

    def show_file(self, path: Path | None) -> None:
        if path:
            self.title.setText(path.name)
            self.hint.setText("Ready to split. Drop another file to replace it.")
            self.browse.setText("Choose a different file…")
        else:
            self.title.setText("Drop an audio file here")
            self.hint.setText("MP3, WAV, M4A, FLAC or OGG")
            self.browse.setText("Choose a file…")
        self.setProperty("filled", bool(path))
        _repolish(self)

    def _set_active(self, on: bool) -> None:
        self.setProperty("active", on)
        _repolish(self)

    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()
            self._set_active(True)

    def dragLeaveEvent(self, e) -> None:
        self._set_active(False)

    def dropEvent(self, e) -> None:
        self._set_active(False)
        md = e.mimeData()
        for url in md.urls():
            if url.isLocalFile():
                self.fileDropped.emit(url.toLocalFile())
            else:
                self.urlDropped.emit(url.toString())
            return
        if md.hasText():
            self.urlDropped.emit(md.text().strip())


class StageIndicator(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.labels: dict[Stage, QLabel] = {}
        for i, stage in enumerate(Stage):
            if i:
                arrow = QLabel("→")
                arrow.setObjectName("arrow")
                lay.addWidget(arrow)
            lab = QLabel(stage.value)
            lab.setObjectName("stage")
            self.labels[stage] = lab
            lay.addWidget(lab)
        lay.addStretch()

    def set_states(self, current: Stage | None, skip_download: bool = False) -> None:
        order = list(Stage)
        for stage, lab in self.labels.items():
            i = order.index(stage)
            if stage is Stage.DOWNLOADING and skip_download:
                state = "skipped"
            elif current is None:
                state = "pending"
            elif i < order.index(current):
                state = "done"
            elif stage is current:
                state = "active"
            else:
                state = "pending"
            lab.setProperty("state", state)
            _repolish(lab)


def _card() -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("card")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(20, 18, 20, 18)
    lay.setSpacing(12)
    return card, lay


def _message(name: str) -> QLabel:
    lab = QLabel()
    lab.setObjectName(name)
    lab.setWordWrap(True)
    lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    lab.hide()
    return lab


def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("divider")
    return line


def _button(text: str, name: str = "", slot: Callable | None = None) -> QPushButton:
    b = QPushButton(text)
    if name:
        b.setObjectName(name)
    b.setCursor(Qt.PointingHandCursor)
    if slot:
        b.clicked.connect(slot)
    return b


def _confirm(parent: QWidget, title: str, text: str, yes: str) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Question)
    ok = box.addButton(yes, QMessageBox.AcceptRole)
    box.addButton("Cancel", QMessageBox.RejectRole)
    box.exec()
    return box.clickedButton() is ok


# --------------------------------------------------------------------------- pages


class SetupPage(QWidget):
    """First launch: download the model."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window_ = window
        self.worker: ModelWorker | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        card, c = _card()
        head = QLabel("One-time setup")
        head.setObjectName("sectionTitle")
        body = QLabel(
            f"MusiSplitSeven needs to download its separation model "
            f"({MODEL_SIZE / 1e6:.0f} MB). This only happens once."
        )
        body.setWordWrap(True)
        body.setObjectName("muted")
        self.bar = PillBar()
        self.status = QLabel("Starting download…")
        self.status.setObjectName("status")
        self.error = _message("error")
        self.retry = _button("Try again", "primary", self.start)
        self.retry.hide()
        for w in (head, body, self.bar, self.status, self.error):
            c.addWidget(w)
        c.addWidget(self.retry, alignment=Qt.AlignLeft)
        lay.addWidget(card)
        lay.addStretch()
        self._t0 = 0.0
        self._start_bytes = 0

    def start(self) -> None:
        self.error.hide()
        self.retry.hide()
        self.bar.show()
        self.bar.setRange(0, 0)
        self.status.setText("Connecting…")
        self.status.show()
        self._t0 = 0.0
        self.worker = ModelWorker()
        self.worker.progress.connect(self._on_progress)
        self.worker.succeeded.connect(self.window_.show_main)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_progress(self, done: int, total: int) -> None:
        now = time.monotonic()
        if not self._t0:
            self._t0, self._start_bytes = now, done
        self.bar.setRange(0, 1000)
        self.bar.setValue(int(done / total * 1000))
        text = f"Downloading… {done / 1e6:.0f} of {total / 1e6:.0f} MB"
        elapsed = now - self._t0
        got = done - self._start_bytes
        if elapsed > 3 and got > 0:
            text += f"  ·  {_fmt_duration((total - done) / (got / elapsed))} left"
        self.status.setText(text)

    def _on_failed(self, msg: str) -> None:
        self.bar.hide()
        self.status.hide()
        self.error.setText(msg)
        self.error.show()
        self.retry.show()


class MainPage(QWidget):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window_ = window
        self.settings = window.settings
        self.file: Path | None = None
        self.worker: JobWorker | None = None
        self.entry: Entry | None = None
        self.last_saved: Path | None = None
        self._stage: Stage | None = None
        self._skip_download = False
        self._sep_t0 = 0.0
        self._sep_f0 = 0.0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        # --- input card
        self.input_card, c = _card()
        self.drop = DropZone()
        self.drop.fileDropped.connect(lambda p: self.set_file(Path(p)))
        self.drop.urlDropped.connect(self._on_url_dropped)
        self.drop.browseClicked.connect(self.browse_file)
        c.addWidget(self.drop)
        orrow = QHBoxLayout()
        orl = QLabel("or")
        orl.setObjectName("orLabel")
        orrow.addWidget(_divider(), 1)
        orrow.addWidget(orl)
        orrow.addWidget(_divider(), 1)
        c.addLayout(orrow)
        self.url = QLineEdit()
        self.url.setPlaceholderText("Paste a YouTube link, e.g. https://www.youtube.com/watch?v=…")
        self.url.setClearButtonEnabled(True)
        self.url.textChanged.connect(self._on_url_changed)
        self.url.returnPressed.connect(self.start_split)
        c.addWidget(self.url)
        self.go = _button("Split song", "primary", self.start_split)
        c.addWidget(self.go)
        self.split_error = _message("error")
        c.addWidget(self.split_error)
        lay.addWidget(self.input_card)

        # --- progress card (the only thing shown while working)
        self.progress_card, p = _card()
        self.working_title = QLabel()
        self.working_title.setObjectName("sectionTitle")
        self.working_title.setWordWrap(True)
        self.stages = StageIndicator()
        self.bar = PillBar()
        self.status = QLabel()
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        self.cancel_btn = _button("Cancel", "", self.cancel_job)
        statusrow = QHBoxLayout()
        statusrow.addWidget(self.status, 1)
        statusrow.addWidget(self.cancel_btn)
        p.addWidget(self.working_title)
        p.addWidget(self.stages)
        p.addWidget(self.bar)
        p.addLayout(statusrow)
        lay.addWidget(self.progress_card)

        # --- result card
        self.result_card, q = _card()
        q.setSpacing(16)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.song_title = QLabel()
        self.song_title.setObjectName("sectionTitle")
        self.song_title.setWordWrap(True)
        self.song_info = QLabel()
        self.song_info.setObjectName("muted")
        titles.addWidget(self.song_title)
        titles.addWidget(self.song_info)
        head.addLayout(titles, 1)
        q.addLayout(head)

        tabs = QHBoxLayout()
        tabs.setSpacing(0)
        self.tab_buttons: list[QPushButton] = []
        for i, name in enumerate(("Tracks", "Lyrics")):
            b = _button(name, "tab", lambda _=False, i=i: self.show_tab(i))
            b.setCheckable(True)
            self.tab_buttons.append(b)
            tabs.addWidget(b)
        tabs.addStretch()
        q.addLayout(tabs)
        q.addSpacing(-12)
        q.addWidget(_divider())

        self.tabs = QStackedWidget()
        tracks_page = QWidget()
        tl = QVBoxLayout(tracks_page)
        tl.setContentsMargins(0, 4, 0, 0)
        tl.setSpacing(16)
        self.rows: dict[str, TrackRow] = {}
        for stem, label in STEMS:
            row = TrackRow(stem, label)
            row.started.connect(self._on_row_started)
            row.saveClicked.connect(self.save_stem)
            row.error.connect(self._show_result_error)
            row.position.connect(lambda ms, r=row: self._on_row_position(r, ms))
            row.playingChanged.connect(lambda on, s=stem: self.lyrics_view.set_playing(s, on))
            self.rows[stem] = row
            tl.addWidget(row)
        tl.addStretch()
        self.tabs.addWidget(tracks_page)

        self.lyrics_view = LyricsView()
        self.lyrics_view.playRequested.connect(lambda stem: self.rows[stem].toggle())
        self.lyrics_view.lineClicked.connect(self._on_lyric_clicked)
        self.lyrics_view.searchRequested.connect(lambda q: self._fetch_lyrics(query=q))
        self.lyrics_view.retryRequested.connect(lambda: self._fetch_lyrics())
        self.tabs.addWidget(self.lyrics_view)
        q.addWidget(self.tabs)
        self.active_stem = "instrumental"
        self.show_tab(0)

        q.addWidget(_divider())
        self.result_msg = _message("success")
        self.result_err = _message("error")
        q.addWidget(self.result_msg)
        q.addWidget(self.result_err)
        foot = QHBoxLayout()
        self.open_btn = _button("Open folder", "", self.open_output)
        self.open_btn.hide()
        foot.addWidget(self.open_btn)
        foot.addStretch()
        foot.addWidget(_button("Delete from history", "danger", self.delete_current))
        q.addLayout(foot)
        lay.addWidget(self.result_card)
        lay.addStretch()

        self._show_mode("input")

    # ---- modes

    def _show_mode(self, mode: str) -> None:
        self.mode = mode
        self.input_card.setVisible(mode == "input")
        self.progress_card.setVisible(mode == "working")
        self.result_card.setVisible(mode == "result")
        self.go.setEnabled(self._source() is not None)
        self.window_.on_mode_changed(mode)

    def new_split(self) -> None:
        for row in self.rows.values():
            row.unload()
        self.entry = None
        self.file = None
        self.drop.show_file(None)
        self.url.blockSignals(True)
        self.url.clear()
        self.url.blockSignals(False)
        self.split_error.hide()
        self._show_mode("input")

    # ---- inputs

    def set_file(self, path: Path) -> None:
        if self.mode != "input":
            return
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            self._show_split_error(
                f"\"{path.name}\" isn't a supported audio file.\n"
                "Please choose an MP3, WAV, M4A, FLAC or OGG file."
            )
            return
        self.file = path
        self.url.blockSignals(True)
        self.url.clear()
        self.url.blockSignals(False)
        self.drop.show_file(path)
        self.split_error.hide()
        self.go.setEnabled(True)

    def browse_file(self) -> None:
        start = self.settings.value("last_input_dir", str(Path.home() / "Music"))
        exts = " ".join(f"*{e}" for e in SUPPORTED_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(self, "Choose a song", start, f"Audio files ({exts})")
        if path:
            self.settings.setValue("last_input_dir", str(Path(path).parent))
            self.set_file(Path(path))

    def _on_url_dropped(self, text: str) -> None:
        if self.mode == "input":
            self.url.setText(text)

    def _on_url_changed(self, text: str) -> None:
        if text.strip() and self.file:
            self.file = None
            self.drop.show_file(None)
        self.split_error.hide()
        self.go.setEnabled(self._source() is not None)

    def _source(self) -> str | None:
        if self.file:
            return str(self.file)
        text = self.url.text().strip()
        return text or None

    def _show_split_error(self, msg: str) -> None:
        self.split_error.setText(msg)
        self.split_error.show()

    # ---- split

    def start_split(self) -> None:
        source = self._source()
        if self.worker or not source:
            return
        if not self.file and not is_youtube_url(source):
            self._show_split_error(
                "That doesn't look like a YouTube video link.\n"
                "It should look like https://www.youtube.com/watch?v=…"
            )
            return
        self.split_error.hide()
        self._skip_download = self.file is not None
        self._stage = None
        self.working_title.setText(self.file.name if self.file else "YouTube video")
        self.stages.set_states(None, self._skip_download)
        self.bar.setRange(0, 0)
        self.status.setText("Starting…")
        self.cancel_btn.setEnabled(True)
        self.worker = JobWorker(split_into_library(source))
        self.worker.progress.connect(self._on_progress)
        self.worker.succeeded.connect(self._on_split_done)
        self.worker.failed.connect(self._on_split_failed)
        self.worker.cancelled.connect(self._on_cancelled)
        self.worker.finished.connect(self._on_finished)
        self._show_mode("working")
        self.worker.start()

    def cancel_job(self) -> None:
        if self.worker:
            self.worker.cancel.set()
            self.cancel_btn.setEnabled(False)
            self.status.setText("Cancelling…")

    def _on_progress(self, stage: Stage, frac: float | None, msg: str) -> None:
        if self.worker is None or self.worker.cancel.is_set():
            return
        if stage is not self._stage:
            self._stage = stage
            self.stages.set_states(stage, self._skip_download)
            self._sep_t0 = 0.0
        if frac is None:
            self.bar.setRange(0, 0)
            self.status.setText(msg)
            return
        self.bar.setRange(0, 1000)
        self.bar.setValue(int(frac * 1000))
        text = f"{msg}  {frac:.0%}"
        if stage is Stage.SEPARATING:
            now = time.monotonic()
            if not self._sep_t0:
                self._sep_t0, self._sep_f0 = now, frac
            elapsed = now - self._sep_t0
            done = frac - self._sep_f0
            if elapsed > 5 and done > 0.02 and frac < 1:
                text += f"  ·  {_fmt_duration(elapsed / done * (1 - frac))} left"
            elif frac < 1:
                text += "  ·  this can take a few minutes"
        self.status.setText(text)

    def _on_split_done(self, entry: Entry) -> None:
        self.load_entry(entry)
        self.window_.refresh_history()
        QApplication.alert(self.window_)

    def _on_split_failed(self, msg: str) -> None:
        self._show_mode("input")
        self._show_split_error(msg)
        QApplication.alert(self.window_)

    def _on_cancelled(self) -> None:
        self._show_mode("input")

    def _on_finished(self) -> None:
        self.worker = None
        self.window_.on_mode_changed(self.mode)

    # ---- result

    def load_entry(self, entry: Entry) -> None:
        for row in self.rows.values():
            row.unload()
        self.entry = entry
        self.last_saved = None
        self.result_msg.hide()
        self.result_err.hide()
        self.open_btn.hide()
        self.song_title.setText(entry.title)
        origin = "YouTube" if entry.from_youtube else "a file"
        self.song_info.setText(
            f"{_fmt_clock(entry.duration_s * 1000)}  ·  split {entry.created:%d %b %Y, %H:%M} from {origin}"
        )
        for stem, row in self.rows.items():
            row.load(entry.stem_path(stem), entry.waveform(stem), int(entry.duration_s * 1000))
        self.active_stem = "instrumental"
        self.lyrics_view.query.clear()
        self.lyrics_view.set_position(0, int(entry.duration_s * 1000))
        self._fetch_lyrics()
        self._show_mode("result")

    def close_entry(self) -> None:
        """Forget the shown song (it was deleted) and go back to the input."""
        self.new_split()

    def show_tab(self, i: int) -> None:
        self.tabs.setCurrentIndex(i)
        # A QStackedWidget is as tall as its tallest page; only count the visible one.
        for j in range(self.tabs.count()):
            vertical = QSizePolicy.Preferred if j == i else QSizePolicy.Ignored
            self.tabs.widget(j).setSizePolicy(QSizePolicy.Preferred, vertical)
        self.tabs.adjustSize()
        for j, b in enumerate(self.tab_buttons):
            b.setChecked(j == i)

    def _fetch_lyrics(self, query: str | None = None) -> None:
        entry = self.entry
        if entry is None:
            return
        if query is None:
            cached = entry.cached_lyrics()
            if cached is not None:
                self.lyrics_view.show_result(cached)
                return

        def run():
            if query:
                return lyrics_mod.search(query, entry.duration_s)
            return lyrics_mod.find(entry.title, entry.duration_s, artist=entry.artist,
                                   track=entry.track, channel=entry.channel)

        self.lyrics_view.show_loading()
        call = BackgroundCall(self)
        call.succeeded.connect(lambda result, e=entry: self._on_lyrics(e, result))
        call.failed.connect(lambda msg, e=entry: self._on_lyrics_failed(e, msg))
        call.succeeded.connect(call.deleteLater)
        call.failed.connect(call.deleteLater)
        call.start(run)

    def _is_current(self, entry: Entry) -> bool:
        return self.entry is not None and self.entry.folder == entry.folder

    def _on_lyrics(self, entry: Entry, result: "lyrics_mod.Lyrics") -> None:
        entry.save_lyrics(result)
        if self._is_current(entry):
            self.lyrics_view.show_result(result)
            self._on_row_position(self.rows[self.active_stem], self.rows[self.active_stem].player.position())

    def _on_lyrics_failed(self, entry: Entry, msg: str) -> None:
        if self._is_current(entry):
            self.lyrics_view.show_error(msg)

    def _on_row_position(self, row: TrackRow, ms: int) -> None:
        if row.stem == self.active_stem:
            self.lyrics_view.set_position(int(ms), row.duration())

    def _on_lyric_clicked(self, ms: int) -> None:
        row = self.rows[self.active_stem]
        row.seek(ms)
        if not row.is_playing():
            row.toggle()

    def _on_row_started(self, row: TrackRow) -> None:
        self.active_stem = row.stem
        for other in self.rows.values():
            if other is not row:
                other.pause()

    def _show_result_error(self, msg: str) -> None:
        self.result_msg.hide()
        self.result_err.setText(msg)
        self.result_err.show()

    def save_stem(self, stem: str) -> None:
        if self.entry is None:
            return
        folder = Path(self.settings.value("output_dir", str(default_output_dir())))
        vocals, inst = output_paths(folder, self.entry.title)
        suggested = vocals if stem == "vocals" else inst
        label = dict(STEMS)[stem].lower()
        path, _ = QFileDialog.getSaveFileName(
            self, f"Save the {label}", str(suggested), "MP3 audio (*.mp3)",
        )
        if not path:
            return
        dest = Path(path)
        if dest.suffix.lower() != ".mp3":
            dest = dest.with_name(dest.name + ".mp3")
        self.result_err.hide()
        try:
            export_stem(self.entry, stem, dest)
        except UserError as exc:
            self._show_result_error(str(exc))
            return
        self.settings.setValue("output_dir", str(dest.parent))
        self.last_saved = dest
        self.rows[stem].save_btn.setText("Saved")
        self.result_msg.setText(f"Saved {dest.name}")
        self.result_msg.show()
        self.open_btn.show()

    def delete_current(self) -> None:
        if self.entry is None:
            return
        if not _confirm(self, "Delete from history?",
                        f"Delete \"{self.entry.title}\" from the history?\n"
                        "MP3s you already saved are kept.", "Delete"):
            return
        entry = self.entry
        self.close_entry()
        try:
            library.delete(entry)
        except UserError as exc:
            self._show_split_error(str(exc))
        self.window_.refresh_history()

    def open_output(self) -> None:
        if self.last_saved is None:
            return
        if IS_WINDOWS and self.last_saved.exists():
            # Open Explorer with the saved file selected.
            subprocess.Popen(["explorer", "/select,", str(self.last_saved)], creationflags=SUBPROCESS_FLAGS)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_saved.parent)))

    def unload_all(self) -> None:
        for row in self.rows.values():
            row.unload()


class HistoryRow(QFrame):
    def __init__(self, entry: Entry, current: bool, on_open: Callable, on_delete: Callable) -> None:
        super().__init__()
        self.setObjectName("historyRow")
        self.setProperty("current", current)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(14)

        thumb = QLabel()
        w, h = 132, 40
        pm = QPixmap(w * 2, h * 2)
        pm.setDevicePixelRatio(2)
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing)
        paint_bars(painter, QRectF(0, 0, w, h), entry.waveform("instrumental"), played=1.0, bar=2, gap=1.5)
        painter.end()
        thumb.setPixmap(pm)
        thumb.setFixedSize(w, h)
        lay.addWidget(thumb)

        text = QVBoxLayout()
        text.setSpacing(2)
        title = QLabel(entry.title)
        title.setStyleSheet("font-weight: 600; font-size: 10.5pt;")
        title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        title.setToolTip(entry.title)
        origin = "YouTube" if entry.from_youtube else "file"
        info = QLabel(
            f"{entry.created:%d %b %Y, %H:%M}  ·  {_fmt_clock(entry.duration_s * 1000)}  ·  "
            f"{origin}  ·  {_fmt_size(entry.size_bytes())}"
        )
        info.setObjectName("muted")
        text.addWidget(title)
        text.addWidget(info)
        lay.addLayout(text, 1)

        lay.addWidget(_button("Open", "small", lambda: on_open(entry)))
        delete = _button("Delete", "danger", lambda: on_delete(entry))
        delete.setStyleSheet("padding: 6px 12px; font-size: 9.5pt;")
        lay.addWidget(delete)


class HistoryPage(QWidget):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window_ = window
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        top = QHBoxLayout()
        back = _button("← Back", "", window.show_main)
        title = QLabel("History")
        title.setObjectName("pageTitle")
        self.delete_all_btn = _button("Delete all", "danger", self.delete_all)
        top.addWidget(back)
        top.addSpacing(8)
        top.addWidget(title)
        top.addStretch()
        top.addWidget(self.delete_all_btn)
        lay.addLayout(top)

        self.summary = QLabel()
        self.summary.setObjectName("muted")
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)

        self.list = QWidget()
        self.list.setObjectName("historyList")
        self.rows = QVBoxLayout(self.list)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(8)
        lay.addWidget(self.list)
        self.empty = QLabel("Nothing here yet. Songs you split will show up here.")
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setMinimumHeight(120)
        lay.addWidget(self.empty)
        self.error = _message("error")
        lay.addWidget(self.error)
        lay.addStretch()
        self.entries: list[Entry] = []

    def refresh(self) -> None:
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.entries = library.list_entries()
        current = self.window_.main_page.entry
        for e in self.entries:
            is_current = current is not None and current.folder == e.folder
            self.rows.addWidget(HistoryRow(e, is_current, self.open_entry, self.delete_entry))
        total = sum(e.size_bytes() for e in self.entries)
        n = len(self.entries)
        self.summary.setText(f"{n} song{'s' if n != 1 else ''}  ·  {_fmt_size(total)} on this computer" if n else "")
        self.summary.setVisible(bool(n))
        self.empty.setVisible(not n)
        self.delete_all_btn.setEnabled(bool(n))

    def open_entry(self, entry: Entry) -> None:
        self.window_.show_main()
        self.window_.main_page.load_entry(entry)

    def _release_if_current(self, entries: list[Entry]) -> None:
        page = self.window_.main_page
        if page.entry is not None and any(page.entry.folder == e.folder for e in entries):
            page.close_entry()

    def delete_entry(self, entry: Entry) -> None:
        if not _confirm(self, "Delete from history?",
                        f"Delete \"{entry.title}\" from the history?\n"
                        "MP3s you already saved are kept.", "Delete"):
            return
        self._release_if_current([entry])
        self.error.hide()
        try:
            library.delete(entry)
        except UserError as exc:
            self.error.setText(str(exc))
            self.error.show()
        self.window_.refresh_history()

    def delete_all(self) -> None:
        if not self.entries:
            return
        total = sum(e.size_bytes() for e in self.entries)
        if not _confirm(self, "Delete everything?",
                        f"Delete all {len(self.entries)} songs from the history? This frees {_fmt_size(total)}.\n"
                        "MP3s you already saved are kept.", "Delete all"):
            return
        self._release_if_current(self.entries)
        self.error.hide()
        try:
            library.delete_all()
        except UserError as exc:
            self.error.setText(str(exc))
            self.error.show()
        self.window_.refresh_history()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings(APP_NAME, APP_NAME)
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(620, 520)
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry().height() - 60 if screen else 800
        self.resize(760, max(520, min(760, avail)))

        body = QWidget()
        body.setObjectName("scrollBody")
        outer = QVBoxLayout(body)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(16)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel(APP_NAME)
        title.setObjectName("title")
        subtitle = QLabel("Split any song into vocals and instrumental.")
        subtitle.setObjectName("subtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles, 1)
        self.new_btn = _button("New split", "primary", lambda: self.main_page.new_split())
        self.new_btn.setStyleSheet("padding: 8px 16px; font-size: 10pt;")
        self.new_btn.hide()
        self.history_btn = _button("History", "", self.show_history)
        header.addWidget(self.new_btn, alignment=Qt.AlignTop)
        header.addSpacing(8)
        header.addWidget(self.history_btn, alignment=Qt.AlignTop)
        outer.addLayout(header)

        self.stack = QStackedWidget()
        self.setup_page = SetupPage(self)
        self.main_page = MainPage(self)
        self.history_page = HistoryPage(self)
        for page in (self.setup_page, self.main_page, self.history_page):
            self.stack.addWidget(page)
        outer.addWidget(self.stack, 1)

        footer = QLabel(f"v{__version__}")
        footer.setObjectName("muted")
        footer.setAlignment(Qt.AlignRight)
        outer.addWidget(footer)

        # Scroll rather than clip on small laptop screens.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        self.setCentralWidget(scroll)
        self.setAcceptDrops(True)

        self.refresh_history()
        if is_model_ready():
            self.show_main()
        else:
            self.stack.setCurrentWidget(self.setup_page)
            self.history_btn.setEnabled(False)
            self.setup_page.start()

    def on_mode_changed(self, mode: str) -> None:
        # Nothing else can be started while a song is being split.
        if hasattr(self, "history_btn"):
            self.history_btn.setEnabled(mode != "working" and self.stack.currentWidget() is not self.setup_page)
            self.new_btn.setVisible(mode == "result" and self.stack.currentWidget() is self.main_page)

    def show_main(self) -> None:
        self.stack.setCurrentWidget(self.main_page)
        self.on_mode_changed(self.main_page.mode)

    def show_history(self) -> None:
        if self.main_page.mode == "working":
            return
        for row in self.main_page.rows.values():
            row.pause()
        self.history_page.refresh()
        self.stack.setCurrentWidget(self.history_page)
        self.new_btn.hide()

    def refresh_history(self) -> None:
        n = len(library.list_entries())
        self.history_btn.setText(f"History ({n})" if n else "History")
        if self.stack.currentWidget() is self.history_page:
            self.history_page.refresh()

    def _drops_allowed(self) -> bool:
        return self.stack.currentWidget() is self.main_page and self.main_page.mode == "input"

    def dragEnterEvent(self, e) -> None:
        if self._drops_allowed():
            self.main_page.drop.dragEnterEvent(e)

    def dragLeaveEvent(self, e) -> None:
        self.main_page.drop.dragLeaveEvent(e)

    def dropEvent(self, e) -> None:
        if self._drops_allowed():
            self.main_page.drop.dropEvent(e)

    def closeEvent(self, e) -> None:
        page = self.main_page
        if page.worker is not None:
            if not _confirm(self, "Quit?", "A song is still being processed. Quit anyway?", "Quit"):
                e.ignore()
                return
            page.worker.cancel.set()
            page.worker.wait(10_000)
        dl = self.setup_page.worker
        if dl is not None and dl.isRunning():
            dl.cancel.set()
            dl.wait(5_000)
        page.unload_all()
        e.accept()


# --------------------------------------------------------------------------- entry point


def log_path() -> Path:
    return app_data_dir() / "log.txt"


def _setup_logging() -> None:
    try:
        log_path().parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.FileHandler(log_path(), encoding="utf-8")
    except OSError:
        handler = logging.NullHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    # A windowed exe has no console: anything printed would raise on None streams.
    if sys.stdout is None or sys.stderr is None:
        sink = open(os.devnull, "w", encoding="utf-8")
        sys.stdout = sys.stdout or sink
        sys.stderr = sys.stderr or sink

    def excepthook(*exc_info) -> None:
        log.error("uncaught exception:\n%s", "".join(traceback.format_exception(*exc_info)))

    sys.excepthook = excepthook


def _light_palette(app: QApplication) -> None:
    """Pin a light palette so Windows dark mode doesn't half-apply to the Fusion style."""
    pal = QPalette()
    for role, color in (
        (QPalette.Window, CREAM), (QPalette.WindowText, TEXT), (QPalette.Base, CARD),
        (QPalette.AlternateBase, CREAM), (QPalette.Text, TEXT), (QPalette.Button, SAND),
        (QPalette.ButtonText, TEXT), (QPalette.Highlight, GREEN), (QPalette.HighlightedText, GREEN_DARK),
        (QPalette.ToolTipBase, CARD), (QPalette.ToolTipText, TEXT), (QPalette.PlaceholderText, "#A79F8B"),
    ):
        pal.setColor(role, QColor(color))
    app.setPalette(pal)


def make_app(argv: list[str]) -> QApplication:
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    _light_palette(app)
    if IS_WINDOWS:
        app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(STYLE)
    icon = _bundle_dir() / "assets" / "icon.ico"
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))
    return app


class SingleInstance:
    """Only one window at a time.

    Launching the app again (a double double-click, or the installer's
    "launch" box plus the shortcut) brings the open window to the front
    instead of starting a second copy that would fight over the same files.
    The lock file is atomic, so even two launches a few ms apart are safe.
    """

    def __init__(self) -> None:
        user = re.sub(r"\W", "_", getpass.getuser() or "user")
        self.name = f"{APP_NAME}-{user}"
        self.lock = QLockFile(str(app_data_dir() / "app.lock"))
        self.lock.setStaleLockTime(0)  # only stale if the owning process is gone
        self.server: QLocalServer | None = None
        self.window: QMainWindow | None = None

    def acquire(self) -> bool:
        try:
            app_data_dir().mkdir(parents=True, exist_ok=True)
        except OSError:
            return True  # can't lock: better to run than to refuse
        return self.lock.tryLock(0)

    def notify_running(self, timeout_s: float = 8.0) -> bool:
        """Ask the running copy to show itself (it may still be starting up)."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            sock = QLocalSocket()
            sock.connectToServer(self.name)
            if sock.waitForConnected(500):
                sock.write(b"show")
                sock.waitForBytesWritten(1000)
                sock.disconnectFromServer()
                return True
            time.sleep(0.25)
        return False

    def serve(self) -> None:
        self.server = QLocalServer()
        QLocalServer.removeServer(self.name)
        self.server.newConnection.connect(self._on_connection)
        if not self.server.listen(self.name):
            log.warning("single-instance server: %s", self.server.errorString())

    def _on_connection(self) -> None:
        while self.server.hasPendingConnections():
            self.server.nextPendingConnection().disconnectFromServer()
        log.info("another launch asked to show the window")
        if self.window is not None:
            w = self.window
            w.setWindowState((w.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
            w.show()
            w.raise_()
            w.activateWindow()
            QApplication.alert(w)


def main() -> int:
    _setup_logging()
    log.info("%s %s starting (frozen=%s)", APP_NAME, __version__, IS_FROZEN)
    if IS_WINDOWS:
        try:
            import ctypes
            # Own taskbar group + icon instead of python.exe's.
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_NAME}.{APP_NAME}")
        except Exception:
            pass
    app = make_app(sys.argv)
    instance = SingleInstance()
    if not instance.acquire():
        log.info("already running")
        if not instance.notify_running():
            QMessageBox.information(None, APP_NAME, f"{APP_NAME} is already open.")
        return 0
    instance.serve()
    # Safe now that no other copy can be in the middle of a split.
    library.clean_incomplete()
    win = MainWindow()
    instance.window = win
    win.show()
    code = app.exec()
    instance.lock.unlock()
    return code
