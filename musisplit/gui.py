"""PySide6 single-window UI. All heavy work runs on QThreads.

Flow: pick a file or paste a YouTube link → Split (Downloading → Separating →
Exporting into the local history) → listen to the stacked vocal and
instrumental tracks → Save MP3s to a folder. Every split stays in History
until the user deletes it.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, QSettings, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor, QDesktopServices, QFont, QIcon, QImage, QPainter, QPainterPath, QPalette, QPen, QPixmap,
    QPolygonF,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from . import APP_NAME, __version__, library
from .audio import SUPPORTED_EXTENSIONS
from .errors import Cancelled, UserError
from .library import Entry
from .model import MODEL_SIZE, ensure_model, is_model_ready
from .paths import IS_FROZEN, IS_WINDOWS, SUBPROCESS_FLAGS, _bundle_dir, app_data_dir, default_output_dir
from .pipeline import JobResult, Stage, export, save_to_library, separate_source
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
GREEN = "#A9D4B6"
GREEN_HOVER = "#98CAA7"
GREEN_SOFT = "#E6F2E8"
GREEN_MID = "#7FB893"
GREEN_DARK = "#2F5B45"
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
#dropzone[filled="true"] { background: @GREEN_SOFT; border: 2px solid @GREEN; }
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
QPushButton#primary:disabled { background: #D9E9DD; color: #8FAF9B; border-color: #D9E9DD; }
QPushButton#danger { color: @RED; }
QPushButton#danger:hover { background: @RED_SOFT; border-color: #EBCBBF; }
QPushButton#small { padding: 6px 12px; font-size: 9.5pt; }
QPushButton#play { background: @GREEN; border: none; border-radius: 23px; min-width: 46px; max-width: 46px; min-height: 46px; max-height: 46px; padding: 0; }
QPushButton#play:hover { background: @GREEN_HOVER; }
QPushButton#play:disabled { background: #D9E9DD; }
QPushButton#laneMute { padding: 4px 10px; font-size: 9pt; border-radius: 7px; background: @GREEN_SOFT; color: @GREEN_DARK; border: 1px solid #CFE5D5; }
QPushButton#laneMute:checked { background: @SAND; color: @MUTED; border-color: @BORDER; }
QProgressBar { background: @SAND; border: none; border-radius: 6px; height: 12px; text-align: center; }
QProgressBar::chunk { background: @GREEN_MID; border-radius: 6px; }
#stage { color: #ABA390; font-weight: 600; padding: 4px 10px; border-radius: 11px; background: @SAND; }
#stage[state="active"] { color: @GREEN_DARK; background: @GREEN; }
#stage[state="done"] { color: @GREEN_DARK; background: @GREEN_SOFT; }
#stage[state="skipped"] { color: #C9C2B1; background: #F6F2E8; text-decoration: line-through; }
#arrow { color: #BDB59F; }
#status { font-size: 10.5pt; }
#sectionTitle { font-size: 12pt; font-weight: 700; }
#pageTitle { font-size: 16pt; font-weight: 700; color: @GREEN_DARK; }
#time { color: @MUTED; font-size: 10.5pt; }
#error { background: @RED_SOFT; color: @RED; border: 1px solid #EBCBBF; border-radius: 10px; padding: 10px 12px; }
#success { background: @GREEN_SOFT; color: @GREEN_DARK; border: 1px solid #CFE5D5; border-radius: 10px; padding: 10px 12px; }
#historyRow { background: @CARD; border: 1px solid @BORDER; border-radius: 12px; }
#historyRow[current="true"] { border: 2px solid @GREEN; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #DDD4BF; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""
for _name, _value in sorted(dict(globals()).items(), key=lambda kv: -len(kv[0])):
    if _name.isupper() and isinstance(_value, str) and _value.startswith("#"):
        STYLE = STYLE.replace(f"@{_name}", _value)

# Spectrogram colour ramps (low → high energy), one shade of green per stem.
RAMPS = {
    "vocals": [CARD, "#DCEFE1", "#9FCFAE", "#5C9A75", "#24493A"],
    "instrumental": [CARD, "#E6EFD6", "#BDD59C", "#7E9B57", "#43592C"],
}


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


def _color_table(stops: list[str]) -> list[int]:
    cols = [QColor(c) for c in stops]
    table = []
    for i in range(256):
        x = i / 255 * (len(cols) - 1)
        a, b = cols[int(x)], cols[min(int(x) + 1, len(cols) - 1)]
        t = x - int(x)
        table.append(QColor(
            round(a.red() + (b.red() - a.red()) * t),
            round(a.green() + (b.green() - a.green()) * t),
            round(a.blue() + (b.blue() - a.blue()) * t),
        ).rgb())
    return table


_TABLES = {stem: _color_table(stops) for stem, stops in RAMPS.items()}


def spectrogram_image(data: np.ndarray | None, stem: str) -> QImage | None:
    if data is None or data.ndim != 2:
        return None
    data = np.ascontiguousarray(data, dtype=np.uint8)
    rows, cols = data.shape
    img = QImage(data.tobytes(), cols, rows, cols, QImage.Format_Indexed8)
    img.setColorTable(_TABLES[stem])
    return img.copy()


def _transport_icon(playing: bool) -> QIcon:
    size = 22
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(GREEN_DARK))
    if playing:
        p.drawRoundedRect(QRectF(5, 4, 4.5, 14), 1.5, 1.5)
        p.drawRoundedRect(QRectF(12.5, 4, 4.5, 14), 1.5, 1.5)
    else:
        p.drawPolygon(QPolygonF([QPointF(7, 4), QPointF(18, 11), QPointF(7, 18)]))
    p.end()
    return QIcon(pm)


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

    def __init__(self, fn: Callable, label: str) -> None:
        super().__init__()
        self.fn = fn
        self.label = label
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
            log.exception("%s failed", self.label)
            self.failed.emit(UNEXPECTED.format(log=log_path()))


def split_into_library(source: str) -> Callable:
    def run(progress, cancel) -> Entry:
        sep = separate_source(source, progress, cancel)
        return save_to_library(sep, source, progress, cancel)
    return run


# --------------------------------------------------------------------------- playback


class StemPlayer(QObject):
    """Plays a history entry with each stem mutable.

    Three players run in lockstep (the full mix, vocals, instrumental) and
    only one is ever audible: the mix when both stems are on, otherwise the
    one stem that is on. Playing two separate players at once could drift
    apart by a few milliseconds; this way what you hear is always one file.
    """

    positionChanged = Signal(int)
    durationChanged = Signal(int)
    playingChanged = Signal(bool)
    error = Signal(str)

    KEYS = ("mix", "vocals", "instrumental")

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.players: dict[str, QMediaPlayer] = {}
        self.outputs: dict[str, QAudioOutput] = {}
        for key in self.KEYS:
            out = QAudioOutput(self)
            player = QMediaPlayer(self)
            player.setAudioOutput(out)
            player.errorOccurred.connect(self._on_error)
            self.players[key] = player
            self.outputs[key] = out
        self.muted = {"vocals": False, "instrumental": False}
        self.current = "mix"
        self._reported_error = False
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(lambda: self.positionChanged.emit(self.position()))
        lead = self.players["mix"]
        lead.durationChanged.connect(lambda ms: self.durationChanged.emit(int(ms)))
        lead.playbackStateChanged.connect(self._on_state)
        self._apply_audible(sync=False)

    def audible(self) -> str | None:
        v, i = self.muted["vocals"], self.muted["instrumental"]
        if not v and not i:
            return "mix"
        if not v:
            return "vocals"
        if not i:
            return "instrumental"
        return None

    def _apply_audible(self, sync: bool = True) -> None:
        key = self.audible()
        if sync and key and key != self.current:
            ref = self.players[self.current].position()
            target = self.players[key]
            if abs(target.position() - ref) > 40:
                target.setPosition(ref)
        for k, out in self.outputs.items():
            out.setMuted(k != key)
        if key:
            self.current = key

    def set_muted(self, stem: str, muted: bool) -> None:
        self.muted[stem] = muted
        self._apply_audible()

    def load(self, entry: Entry) -> None:
        self._reported_error = False
        for key in self.KEYS:
            path = entry.mix if key == "mix" else entry.stem_path(key)
            self.players[key].setSource(QUrl.fromLocalFile(str(path)))
        self.muted = {"vocals": False, "instrumental": False}
        self.current = "mix"
        self._apply_audible(sync=False)

    def unload(self) -> None:
        """Stop and release the files so they can be deleted (Windows locks open files)."""
        for p in self.players.values():
            p.stop()
            p.setSource(QUrl())

    def position(self) -> int:
        return self.players[self.current].position()

    def duration(self) -> int:
        return self.players["mix"].duration()

    def is_playing(self) -> bool:
        return self.players["mix"].playbackState() == QMediaPlayer.PlayingState

    def toggle(self) -> None:
        if self.is_playing():
            self.pause()
            return
        if self.duration() and self.position() >= self.duration() - 100:
            self.seek(0)
        for p in self.players.values():
            p.play()

    def pause(self) -> None:
        for p in self.players.values():
            p.pause()

    def seek(self, ms: int) -> None:
        for p in self.players.values():
            p.setPosition(int(ms))
        self.positionChanged.emit(int(ms))

    def _on_state(self, state) -> None:
        playing = state == QMediaPlayer.PlayingState
        self.timer.start() if playing else self.timer.stop()
        self.positionChanged.emit(self.position())
        self.playingChanged.emit(playing)

    def _on_error(self, _err, message: str) -> None:
        log.warning("player error: %s", message)
        if not self._reported_error:
            self._reported_error = True
            self.error.emit(
                "This song can't be played on this computer (is a speaker or headset connected?).\n"
                "You can still save the files and listen to them in your music player."
            )


class TrackView(QWidget):
    """The two stems stacked as lanes, each with its spectrogram, and one playhead across both."""

    seekRequested = Signal(float)  # fraction of the song
    scrubbed = Signal(float)
    muteToggled = Signal(str, bool)

    LANES = (("vocals", "Vocals"), ("instrumental", "Instrumental"))
    HEADER = 124
    TOP = 8
    LANE_H = 84
    GAP = 12

    def __init__(self) -> None:
        super().__init__()
        self.images: dict[str, QImage | None] = {stem: None for stem, _ in self.LANES}
        self.muted = {stem: False for stem, _ in self.LANES}
        self.frac = 0.0
        self._dragging = False
        self.mute_buttons: dict[str, QPushButton] = {}
        for stem, _ in self.LANES:
            b = QPushButton("Mute", self)
            b.setObjectName("laneMute")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedWidth(b.fontMetrics().horizontalAdvance("Muted") + 32)
            b.toggled.connect(lambda on, s=stem: self._on_mute(s, on))
            self.mute_buttons[stem] = b
        height = self.TOP + len(self.LANES) * self.LANE_H + (len(self.LANES) - 1) * self.GAP + 4
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_spectrograms(self, images: dict[str, QImage | None]) -> None:
        self.images = images
        for b in self.mute_buttons.values():
            b.setChecked(False)
        self.frac = 0.0
        self.update()

    def set_fraction(self, frac: float) -> None:
        if not self._dragging:
            self.frac = min(1.0, max(0.0, frac))
            self.update()

    def lane_rect(self, i: int) -> QRectF:
        top = self.TOP + i * (self.LANE_H + self.GAP)
        return QRectF(self.HEADER, top, max(1, self.width() - self.HEADER - 1), self.LANE_H)

    def _on_mute(self, stem: str, muted: bool) -> None:
        self.muted[stem] = muted
        self.mute_buttons[stem].setText("Muted" if muted else "Mute")
        self.muteToggled.emit(stem, muted)
        self.update()

    def resizeEvent(self, e) -> None:
        for i, (stem, _) in enumerate(self.LANES):
            r = self.lane_rect(i)
            self.mute_buttons[stem].move(0, int(r.top() + 40))
        super().resizeEvent(e)

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        title_font = QFont(self.font())
        title_font.setPointSizeF(self.font().pointSizeF() + 1)
        title_font.setBold(True)

        for i, (stem, label) in enumerate(self.LANES):
            r = self.lane_rect(i)
            p.setFont(title_font)
            p.setPen(QColor(MUTED if self.muted[stem] else TEXT))
            p.drawText(QRectF(0, r.top() + 8, self.HEADER - 8, 24), Qt.AlignLeft | Qt.AlignVCenter, label)

            path = QPainterPath()
            path.addRoundedRect(r, 10, 10)
            p.save()
            p.setClipPath(path)
            p.fillRect(r, QColor(CARD))
            img = self.images.get(stem)
            if img is not None:
                p.setOpacity(0.28 if self.muted[stem] else 1.0)
                p.drawImage(r, img)
                p.setOpacity(1.0)
            p.restore()
            p.setPen(QPen(QColor(BORDER), 1))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)

        # Playhead across both lanes.
        first, last = self.lane_rect(0), self.lane_rect(len(self.LANES) - 1)
        x = first.left() + self.frac * first.width()
        p.setPen(QPen(QColor(GREEN_DARK), 2))
        p.drawLine(QPointF(x, first.top() - 4), QPointF(x, last.bottom() + 2))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(GREEN_DARK))
        p.drawEllipse(QPointF(x, first.top() - 3), 5, 5)
        p.end()

    def _frac_at(self, x: float) -> float:
        r = self.lane_rect(0)
        return min(1.0, max(0.0, (x - r.left()) / r.width()))

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton and e.position().x() >= self.HEADER:
            self._dragging = True
            self.frac = self._frac_at(e.position().x())
            self.scrubbed.emit(self.frac)
            self.update()

    def mouseMoveEvent(self, e) -> None:
        if self._dragging:
            self.frac = self._frac_at(e.position().x())
            self.scrubbed.emit(self.frac)
            self.update()
        else:
            self.setCursor(Qt.PointingHandCursor if e.position().x() >= self.HEADER else Qt.ArrowCursor)

    def mouseReleaseEvent(self, e) -> None:
        if self._dragging:
            self._dragging = False
            self.seekRequested.emit(self.frac)


# --------------------------------------------------------------------------- small widgets


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

    def set_states(self, current: Stage | None, skip_download: bool = False, all_done: bool = False) -> None:
        order = list(Stage)
        for stage, lab in self.labels.items():
            i = order.index(stage)
            if stage is Stage.DOWNLOADING and skip_download and (current or all_done):
                state = "skipped"
            elif all_done:
                state = "done"
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
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
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
        self.saved: JobResult | None = None
        self._stage: Stage | None = None
        self._skip_download = False
        self._sep_t0 = 0.0
        self._sep_f0 = 0.0
        self._duration = 0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        # --- input card
        card, c = _card()
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
        lay.addWidget(card)

        # --- progress card
        self.progress_card, p = _card()
        self.stages = StageIndicator()
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 1000)
        self.status = QLabel("Choose a song to get started.")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        self.cancel_btn = _button("Cancel", "", self.cancel_job)
        statusrow = QHBoxLayout()
        statusrow.addWidget(self.status, 1)
        statusrow.addWidget(self.cancel_btn)
        self.split_error = _message("error")
        p.addWidget(self.stages)
        p.addWidget(self.bar)
        p.addLayout(statusrow)
        p.addWidget(self.split_error)
        lay.addWidget(self.progress_card)

        # --- player card
        self.player_card, q = _card()
        q.setSpacing(14)
        self.song_title = QLabel()
        self.song_title.setObjectName("sectionTitle")
        self.song_title.setWordWrap(True)
        self.song_info = QLabel()
        self.song_info.setObjectName("muted")
        head = QVBoxLayout()
        head.setSpacing(2)
        head.addWidget(self.song_title)
        head.addWidget(self.song_info)
        q.addLayout(head)

        self.tracks = TrackView()
        q.addWidget(self.tracks)

        transport = QHBoxLayout()
        transport.setSpacing(12)
        self.play_btn = _button("", "play", self.toggle_play)
        self.play_btn.setIconSize(QSize(22, 22))
        self.play_btn.setToolTip("Play / pause")
        self.time = QLabel("0:00 / 0:00")
        self.time.setObjectName("time")
        hint = QLabel("Click on the tracks to jump. Mute one to hear the other alone.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        transport.addWidget(self.play_btn)
        transport.addWidget(self.time)
        transport.addSpacing(8)
        transport.addWidget(hint, 1)
        q.addLayout(transport)

        q.addWidget(_divider())
        outrow = QHBoxLayout()
        outrow.setSpacing(8)
        outlab = QLabel("Save to:")
        outlab.setObjectName("muted")
        self.out_label = QLabel()
        self.out_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.change_btn = _button("Change…", "", self.choose_output)
        outrow.addWidget(outlab)
        outrow.addWidget(self.out_label, 1)
        outrow.addWidget(self.change_btn)
        q.addLayout(outrow)

        saverow = QHBoxLayout()
        self.save_btn = _button("Save MP3s", "primary", self.start_save)
        self.open_btn = _button("Open folder", "", self.open_output)
        self.delete_btn = _button("Delete from history", "danger", self.delete_current)
        saverow.addWidget(self.save_btn)
        saverow.addWidget(self.open_btn)
        saverow.addStretch()
        saverow.addWidget(self.delete_btn)
        q.addLayout(saverow)
        self.player_msg = _message("success")
        self.player_err = _message("error")
        q.addWidget(self.player_msg)
        q.addWidget(self.player_err)
        lay.addWidget(self.player_card)
        lay.addStretch()

        self.player = StemPlayer(self)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.playingChanged.connect(lambda on: self.play_btn.setIcon(_transport_icon(on)))
        self.player.error.connect(self._show_player_error)
        self.tracks.muteToggled.connect(self.player.set_muted)
        self.tracks.seekRequested.connect(lambda f: self.player.seek(int(f * self._duration)))
        self.tracks.scrubbed.connect(
            lambda f: self.time.setText(f"{_fmt_clock(f * self._duration)} / {_fmt_clock(self._duration)}")
        )
        self.play_btn.setIcon(_transport_icon(False))

        self.set_output_dir(Path(self.settings.value("output_dir", str(default_output_dir()))))
        self._refresh()

    # ---- inputs

    def set_file(self, path: Path) -> None:
        if self.worker:
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
        self._refresh()

    def browse_file(self) -> None:
        start = self.settings.value("last_input_dir", str(Path.home() / "Music"))
        exts = " ".join(f"*{e}" for e in SUPPORTED_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(self, "Choose a song", start, f"Audio files ({exts})")
        if path:
            self.settings.setValue("last_input_dir", str(Path(path).parent))
            self.set_file(Path(path))

    def _on_url_dropped(self, text: str) -> None:
        if not self.worker:
            self.url.setText(text)

    def _on_url_changed(self, text: str) -> None:
        if text.strip() and self.file:
            self.file = None
            self.drop.show_file(None)
        self.split_error.hide()
        self._refresh()

    def set_output_dir(self, path: Path) -> None:
        self.out_dir = path
        self.out_label.setText(str(path))
        self.out_label.setToolTip(str(path))

    def choose_output(self) -> None:
        start = self.out_dir if self.out_dir.exists() else Path.home()
        path = QFileDialog.getExistingDirectory(self, "Choose where to save the files", str(start))
        if path:
            self.set_output_dir(Path(path))
            self.settings.setValue("output_dir", path)
            self.saved = None
            self.player_msg.hide()
            self._refresh()

    def _source(self) -> str | None:
        if self.file:
            return str(self.file)
        text = self.url.text().strip()
        return text or None

    # ---- state

    def _refresh(self) -> None:
        busy = self.worker is not None
        splitting = busy and self.worker.label == "split"
        self.go.setEnabled(not busy and self._source() is not None)
        self.go.setText("Splitting…" if splitting else "Split song")
        self.drop.setEnabled(not busy)
        self.drop.setAcceptDrops(not busy)
        self.url.setEnabled(not busy)
        self.cancel_btn.setVisible(splitting)
        self.cancel_btn.setEnabled(splitting)
        self.progress_card.setVisible(splitting or self.split_error.isVisible() or self.entry is None)
        self.player_card.setVisible(self.entry is not None)
        self.save_btn.setEnabled(not busy and self.saved is None)
        self.save_btn.setText("Saved" if self.saved else "Save MP3s")
        self.open_btn.setVisible(self.saved is not None)
        self.delete_btn.setEnabled(not busy)
        self.change_btn.setEnabled(not busy)

    def _start_worker(self, fn: Callable, label: str, on_success: Callable) -> None:
        self.worker = JobWorker(fn, label)
        self.worker.progress.connect(self._on_progress)
        self.worker.succeeded.connect(on_success)
        self.worker.failed.connect(lambda msg: self._on_failed(label, msg))
        self.worker.cancelled.connect(self._on_cancelled)
        self.worker.finished.connect(self._on_finished)
        self._refresh()
        self.worker.start()

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
        self.stages.set_states(None, self._skip_download)
        self.bar.setRange(0, 0)
        self.status.setText("Starting…")
        self._start_worker(split_into_library(source), "split", self._on_split_done)

    def _on_split_done(self, entry: Entry) -> None:
        self.stages.set_states(None, self._skip_download, all_done=True)
        self.bar.setRange(0, 1000)
        self.bar.setValue(1000)
        self.status.setText("Done!")
        self.load_entry(entry)
        self.window_.refresh_history()
        QApplication.alert(self.window_)

    # ---- player

    def load_entry(self, entry: Entry) -> None:
        self.player.unload()
        self.entry = entry
        self.saved = None
        self.player_msg.hide()
        self.player_err.hide()
        self.song_title.setText(entry.title)
        origin = "YouTube" if entry.from_youtube else "a file"
        self.song_info.setText(
            f"{_fmt_clock(entry.duration_s * 1000)}  ·  split {entry.created:%d %b %Y, %H:%M} from {origin}"
        )
        self._duration = int(entry.duration_s * 1000)
        self.tracks.set_spectrograms({
            stem: spectrogram_image(entry.spectrogram(stem), stem) for stem, _ in TrackView.LANES
        })
        self.time.setText(f"0:00 / {_fmt_clock(self._duration)}")
        self.player.load(entry)
        self._refresh()

    def close_entry(self) -> None:
        self.player.unload()
        self.entry = None
        self.saved = None
        self.stages.set_states(None)
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.status.setText("Choose a song to get started.")
        self._refresh()

    def toggle_play(self) -> None:
        if self.entry is not None:
            self.player.toggle()

    def _on_position(self, ms: int) -> None:
        if self._duration:
            self.tracks.set_fraction(ms / self._duration)
        if not self.tracks._dragging:
            self.time.setText(f"{_fmt_clock(ms)} / {_fmt_clock(self._duration)}")

    def _on_duration(self, ms: int) -> None:
        if ms > 0:
            self._duration = ms
            self.time.setText(f"{_fmt_clock(self.player.position())} / {_fmt_clock(ms)}")

    def _show_player_error(self, msg: str) -> None:
        self.player_msg.hide()
        self.player_err.setText(msg)
        self.player_err.show()

    # ---- save / delete

    def start_save(self) -> None:
        if self.worker or self.entry is None:
            return
        self.player_msg.hide()
        self.player_err.hide()
        entry, out_dir = self.entry, self.out_dir
        self._start_worker(lambda prog, cancel: export(entry, out_dir), "save", self._on_saved)

    def _on_saved(self, result: JobResult) -> None:
        self.saved = result
        self.player_msg.setText(f"Saved:\n•  {result.vocals.name}\n•  {result.instrumental.name}")
        self.player_msg.show()

    def delete_current(self) -> None:
        if self.entry is None:
            return
        if not _confirm(self, "Delete from history?",
                        f"Delete \"{self.entry.title}\" from the history?\n"
                        "MP3s you already saved to a folder are kept.", "Delete"):
            return
        entry = self.entry
        self.close_entry()
        try:
            library.delete(entry)
        except UserError as exc:
            self._show_split_error(str(exc))
        self.window_.refresh_history()

    # ---- shared worker plumbing

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

    def _reset_progress(self) -> None:
        self.stages.set_states(None)
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.status.setText("Choose a song to get started.")

    def _show_split_error(self, msg: str) -> None:
        self.split_error.setText(msg)
        self.split_error.show()
        self._refresh()

    def _on_failed(self, label: str, msg: str) -> None:
        if label == "save":
            self._show_player_error(msg)
        else:
            self._reset_progress()
            self._show_split_error(msg)
        QApplication.alert(self.window_)

    def _on_cancelled(self) -> None:
        self._reset_progress()
        self.status.setText("Cancelled.")

    def _on_finished(self) -> None:
        self.worker = None
        self._refresh()

    def open_output(self) -> None:
        if IS_WINDOWS and self.saved and self.saved.vocals.exists():
            # Open Explorer with the vocals file selected.
            subprocess.Popen(["explorer", "/select,", str(self.saved.vocals)], creationflags=SUBPROCESS_FLAGS)
        else:
            folder = self.saved.vocals.parent if self.saved else self.out_dir
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


class HistoryRow(QFrame):
    def __init__(self, entry: Entry, current: bool, on_open: Callable, on_delete: Callable) -> None:
        super().__init__()
        self.setObjectName("historyRow")
        self.setProperty("current", current)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(14)

        thumb = QLabel()
        thumb.setFixedSize(132, 44)
        img = spectrogram_image(entry.spectrogram("vocals"), "vocals")
        if img is not None:
            pm = QPixmap.fromImage(img.scaled(132 * 2, 44 * 2, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
            pm.setDevicePixelRatio(2)
            thumb.setPixmap(pm)
        thumb.setStyleSheet(f"border: 1px solid {BORDER}; border-radius: 6px;")
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
        delete.setProperty("class", "small")
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
        self.summary.setText(
            f"{n} song{'s' if n != 1 else ''}  ·  {_fmt_size(total)} on this computer"
            if n else ""
        )
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
                        "MP3s you already saved to a folder are kept.", "Delete"):
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
                        "MP3s you already saved to a folder are kept.", "Delete all"):
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
        self.setMinimumSize(600, 560)
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry().height() - 60 if screen else 900
        self.resize(720, max(560, min(900, avail)))

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
        self.history_btn = _button("History", "", self.show_history)
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

    def show_main(self) -> None:
        self.history_btn.setEnabled(True)
        self.stack.setCurrentWidget(self.main_page)

    def show_history(self) -> None:
        self.history_page.refresh()
        self.stack.setCurrentWidget(self.history_page)

    def refresh_history(self) -> None:
        n = len(library.list_entries())
        self.history_btn.setText(f"History ({n})" if n else "History")
        if self.stack.currentWidget() is self.history_page:
            self.history_page.refresh()

    def _drops_allowed(self) -> bool:
        return self.stack.currentWidget() is self.main_page and self.main_page.worker is None

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
        page.player.unload()
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
    library.clean_incomplete()
    app = make_app(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()
