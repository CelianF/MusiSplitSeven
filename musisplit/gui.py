"""PySide6 single-window UI. All heavy work runs on QThreads.

Flow: pick a file or paste a YouTube link → Split (Downloading → Separating)
→ listen to both stems in the built-in player → Save MP3s (Exporting).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPalette
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider,
    QStackedWidget, QStyle, QVBoxLayout, QWidget,
)

from . import APP_NAME, __version__
from .audio import SUPPORTED_EXTENSIONS, write_wav
from .errors import Cancelled, UserError
from .model import MODEL_SIZE, ensure_model, is_model_ready
from .paths import IS_FROZEN, IS_WINDOWS, SUBPROCESS_FLAGS, _bundle_dir, app_data_dir, default_output_dir
from .pipeline import JobResult, Separated, Stage, export, separate_source
from .youtube import is_youtube_url

log = logging.getLogger("musisplit")

UNEXPECTED = "Something unexpected went wrong.\nDetails were saved to {log}."
PREVIEW_PREFIX = "musisplit_preview_"

STYLE = """
QWidget { color: #1d1d2b; font-size: 10pt; }
QMainWindow, #page, QScrollArea, #scrollBody { background: #f4f5fb; }
QScrollArea { border: none; }
#title { font-size: 20pt; font-weight: 700; color: #1d1d2b; }
#subtitle { color: #6b6f85; font-size: 10.5pt; }
#card { background: white; border: 1px solid #e3e5f0; border-radius: 14px; }
#dropzone { background: #f8f8fe; border: 2px dashed #c9cbe0; border-radius: 12px; }
#dropzone[active="true"] { background: #efeaff; border-color: #7c5cff; }
#dropzone[filled="true"] { background: #f1fbf4; border: 2px solid #9ad7ad; }
#dropTitle { font-size: 11.5pt; font-weight: 600; }
#muted { color: #6b6f85; }
#orLabel { color: #9a9db3; font-weight: 600; }
#divider { background: #e3e5f0; max-height: 1px; min-height: 1px; border: none; }
QLineEdit { background: white; border: 1px solid #d5d7e6; border-radius: 9px; padding: 9px 11px; font-size: 10.5pt; }
QLineEdit:focus { border-color: #7c5cff; }
QPushButton { background: white; border: 1px solid #d5d7e6; border-radius: 9px; padding: 8px 16px; font-weight: 600; }
QPushButton:hover { background: #f1f1f8; }
QPushButton:disabled { color: #a9abbd; background: #f4f4f8; }
QPushButton#primary { background: #6c4dff; color: white; border: none; padding: 12px 22px; font-size: 11.5pt; }
QPushButton#primary:hover { background: #5b3df0; }
QPushButton#primary:disabled { background: #c6bdf6; color: #f4f2ff; }
QPushButton#segment { border-radius: 0; padding: 7px 18px; color: #4a4d63; }
QPushButton#segment[pos="left"] { border-top-left-radius: 9px; border-bottom-left-radius: 9px; }
QPushButton#segment[pos="right"] { border-top-right-radius: 9px; border-bottom-right-radius: 9px; border-left: none; }
QPushButton#segment:checked { background: #6c4dff; color: white; border-color: #6c4dff; }
QPushButton#play { border-radius: 22px; min-width: 44px; max-width: 44px; min-height: 44px; max-height: 44px; padding: 0; background: #6c4dff; border: none; }
QPushButton#play:hover { background: #5b3df0; }
QSlider::groove:horizontal { height: 6px; background: #e4e6f1; border-radius: 3px; }
QSlider::sub-page:horizontal { background: #7c5cff; border-radius: 3px; }
QSlider::handle:horizontal { background: white; border: 2px solid #6c4dff; width: 14px; height: 14px; margin: -6px 0; border-radius: 9px; }
QProgressBar { background: #eceef6; border: none; border-radius: 6px; height: 12px; text-align: center; }
QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #7c5cff, stop:1 #3e8bff); border-radius: 6px; }
#stage { color: #a9abbd; font-weight: 600; padding: 4px 10px; border-radius: 11px; background: #f1f2f7; }
#stage[state="active"] { color: white; background: #6c4dff; }
#stage[state="done"] { color: #1f8a4c; background: #e3f6ea; }
#stage[state="skipped"] { color: #c3c5d4; background: #f6f6fa; text-decoration: line-through; }
#arrow { color: #b9bbcc; }
#status { font-size: 10.5pt; }
#sectionTitle { font-size: 11.5pt; font-weight: 600; }
#time { color: #6b6f85; font-family: "Segoe UI", sans-serif; }
#error { background: #fdecec; color: #a12626; border: 1px solid #f3c4c4; border-radius: 10px; padding: 10px 12px; }
#success { background: #e9f8ef; color: #1f6b3d; border: 1px solid #bfe6cd; border-radius: 10px; padding: 10px 12px; }
"""


def _repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)


def _fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "less than a minute"
    minutes = round(seconds / 60)
    return f"about {minutes} minute{'s' if minutes != 1 else ''}"


def _fmt_clock(ms: int) -> str:
    s = max(0, ms) // 1000
    return f"{s // 60}:{s % 60:02d}"


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


class Preview:
    """A separated song plus the temporary WAV files the player reads."""

    def __init__(self, sep: Separated, folder: Path) -> None:
        self.sep = sep
        self.folder = folder
        self.vocals_wav = folder / "vocals.wav"
        self.instrumental_wav = folder / "instrumental.wav"
        self.saved: JobResult | None = None


def separate_with_preview(source: str):
    def run(progress, cancel) -> Preview:
        sep = separate_source(source, progress, cancel)
        progress(Stage.SEPARATING, None, "Preparing the preview…")
        folder = Path(tempfile.mkdtemp(prefix=PREVIEW_PREFIX))
        preview = Preview(sep, folder)
        try:
            write_wav(sep.vocals, preview.vocals_wav)
            write_wav(sep.instrumental, preview.instrumental_wav)
        except OSError as exc:
            shutil.rmtree(folder, ignore_errors=True)
            raise UserError(f"Couldn't prepare the preview: {exc.strerror or exc}") from exc
        return preview
    return run


def _clean_stale_previews() -> None:
    """Remove preview folders left behind by a crash or a locked file."""
    for p in Path(tempfile.gettempdir()).glob(PREVIEW_PREFIX + "*"):
        shutil.rmtree(p, ignore_errors=True)


# --------------------------------------------------------------------------- widgets


class DropZone(QFrame):
    fileDropped = Signal(str)
    urlDropped = Signal(str)
    browseClicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("dropzone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(130)
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
            self.title.setText(f"🎵  {path.name}")
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

    def set_states(self, current: Stage | None, skip_download: bool = False,
                   done_through: Stage | None = None) -> None:
        """Highlight ``current``; everything before it (or up to ``done_through``) is done."""
        order = list(Stage)
        for stage, lab in self.labels.items():
            i = order.index(stage)
            if stage is Stage.DOWNLOADING and skip_download and (current or done_through):
                state = "skipped"
            elif done_through is not None and i <= order.index(done_through):
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


class SeekSlider(QSlider):
    """A slider that jumps to where you click instead of stepping."""

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton and self.maximum() > 0:
            value = QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(), int(e.position().x()), self.width(),
            )
            self.setValue(value)
            self.sliderMoved.emit(value)
        super().mousePressEvent(e)


class PreviewPlayer(QWidget):
    """Play the vocals or the instrumental; switching keeps the position.

    Both stems play in sync on two players and only the selected one is
    audible, so switching is instant.
    """

    error = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.players: dict[str, QMediaPlayer] = {}
        self.outputs: dict[str, QAudioOutput] = {}
        for name in ("vocals", "instrumental"):
            out = QAudioOutput(self)
            player = QMediaPlayer(self)
            player.setAudioOutput(out)
            player.errorOccurred.connect(self._on_error)
            self.players[name] = player
            self.outputs[name] = out
        self.active = "vocals"
        self._reported_error = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        seg = QHBoxLayout()
        seg.setSpacing(0)
        self.group = QButtonGroup(self)
        self.buttons: dict[str, QPushButton] = {}
        for name, label, pos in (("vocals", "🎤  Vocals", "left"), ("instrumental", "🎸  Instrumental", "right")):
            b = QPushButton(label)
            b.setObjectName("segment")
            b.setProperty("pos", pos)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, n=name: self.select(n))
            self.group.addButton(b)
            self.buttons[name] = b
            seg.addWidget(b)
        seg.addStretch()
        self.buttons["vocals"].setChecked(True)
        lay.addLayout(seg)

        row = QHBoxLayout()
        row.setSpacing(12)
        self.play_btn = QPushButton()
        self.play_btn.setObjectName("play")
        self.play_btn.setCursor(Qt.PointingHandCursor)
        self.play_btn.setToolTip("Play / pause")
        self.play_btn.clicked.connect(self.toggle)
        self.slider = SeekSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self._on_slider_moved)
        self.slider.sliderReleased.connect(lambda: self.seek(self.slider.value()))
        self.time = QLabel("0:00 / 0:00")
        self.time.setObjectName("time")
        row.addWidget(self.play_btn)
        row.addWidget(self.slider, 1)
        row.addWidget(self.time)
        lay.addLayout(row)

        lead = self.players["vocals"]
        lead.positionChanged.connect(self._on_position)
        lead.durationChanged.connect(self._on_duration)
        lead.playbackStateChanged.connect(self._update_play_icon)
        self._update_play_icon()

    def load(self, vocals: Path, instrumental: Path) -> None:
        self._reported_error = False
        self.players["vocals"].setSource(QUrl.fromLocalFile(str(vocals)))
        self.players["instrumental"].setSource(QUrl.fromLocalFile(str(instrumental)))
        self.select("vocals")
        self.slider.setValue(0)

    def unload(self) -> None:
        """Stop and release the files so they can be deleted (Windows locks open files)."""
        for p in self.players.values():
            p.stop()
            p.setSource(QUrl())
        self.slider.setRange(0, 0)
        self.time.setText("0:00 / 0:00")

    def select(self, name: str) -> None:
        self.active = name
        self.buttons[name].setChecked(True)
        lead = self.players["vocals"]
        other = self.players["instrumental"]
        if abs(lead.position() - other.position()) > 80:
            other.setPosition(lead.position())
        for n, out in self.outputs.items():
            out.setMuted(n != name)

    def toggle(self) -> None:
        playing = self.players["vocals"].playbackState() == QMediaPlayer.PlayingState
        for p in self.players.values():
            p.pause() if playing else p.play()

    def pause(self) -> None:
        for p in self.players.values():
            p.pause()

    def seek(self, ms: int) -> None:
        for p in self.players.values():
            p.setPosition(ms)

    def _on_slider_moved(self, ms: int) -> None:
        self.time.setText(f"{_fmt_clock(ms)} / {_fmt_clock(self.slider.maximum())}")
        if not self.slider.isSliderDown():
            self.seek(ms)

    def _on_position(self, ms: int) -> None:
        if not self.slider.isSliderDown():
            self.slider.setValue(ms)
            self.time.setText(f"{_fmt_clock(ms)} / {_fmt_clock(self.slider.maximum())}")

    def _on_duration(self, ms: int) -> None:
        self.slider.setRange(0, ms)
        self.time.setText(f"{_fmt_clock(self.slider.value())} / {_fmt_clock(ms)}")

    def _update_play_icon(self, *_) -> None:
        playing = self.players["vocals"].playbackState() == QMediaPlayer.PlayingState
        icon = QStyle.SP_MediaPause if playing else QStyle.SP_MediaPlay
        self.play_btn.setIcon(self.style().standardIcon(icon))

    def _on_error(self, _err, message: str) -> None:
        log.warning("preview player error: %s", message)
        if not self._reported_error:
            self._reported_error = True
            self.error.emit(
                "The preview can't be played on this computer (is a speaker or headset connected?).\n"
                "You can still save the files and listen to them in your music player."
            )


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
        self.retry = QPushButton("Try again")
        self.retry.setObjectName("primary")
        self.retry.setCursor(Qt.PointingHandCursor)
        self.retry.clicked.connect(self.start)
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
        self.preview: Preview | None = None
        self._stage: Stage | None = None
        self._skip_download = False
        self._sep_t0 = 0.0
        self._sep_f0 = 0.0

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

        self.go = QPushButton("Split song")
        self.go.setObjectName("primary")
        self.go.setCursor(Qt.PointingHandCursor)
        self.go.clicked.connect(self.start_split)
        c.addWidget(self.go)
        lay.addWidget(card)

        # --- progress / preview / save card
        pcard, p = _card()
        self.stages = StageIndicator()
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 1000)
        self.status = QLabel("Choose a song to get started.")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.clicked.connect(self.cancel_job)
        statusrow = QHBoxLayout()
        statusrow.addWidget(self.status, 1)
        statusrow.addWidget(self.cancel_btn)
        for w in (self.stages, self.bar):
            p.addWidget(w)
        p.addLayout(statusrow)

        # Preview + save section, shown once a song has been separated.
        self.preview_box = QWidget()
        pb = QVBoxLayout(self.preview_box)
        pb.setContentsMargins(0, 6, 0, 0)
        pb.setSpacing(12)
        pb.addWidget(_divider())
        self.preview_title = QLabel()
        self.preview_title.setObjectName("sectionTitle")
        self.preview_title.setWordWrap(True)
        pb.addWidget(self.preview_title)
        self.player = PreviewPlayer()
        self.player.error.connect(self.show_error)
        pb.addWidget(self.player)

        outrow = QHBoxLayout()
        outrow.setSpacing(8)
        outlab = QLabel("Save to:")
        outlab.setObjectName("muted")
        self.out_label = QLabel()
        self.out_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.change_btn = QPushButton("Change…")
        self.change_btn.setCursor(Qt.PointingHandCursor)
        self.change_btn.clicked.connect(self.choose_output)
        outrow.addWidget(outlab)
        outrow.addWidget(self.out_label, 1)
        outrow.addWidget(self.change_btn)
        pb.addLayout(outrow)

        saverow = QHBoxLayout()
        self.save_btn = QPushButton("Save MP3s")
        self.save_btn.setObjectName("primary")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.clicked.connect(self.start_export)
        self.open_btn = QPushButton("Open output folder")
        self.open_btn.setObjectName("primary")
        self.open_btn.setCursor(Qt.PointingHandCursor)
        self.open_btn.clicked.connect(self.open_output)
        self.discard_btn = QPushButton("Discard")
        self.discard_btn.setCursor(Qt.PointingHandCursor)
        self.discard_btn.clicked.connect(self.discard_preview)
        saverow.addWidget(self.save_btn)
        saverow.addWidget(self.open_btn)
        saverow.addStretch()
        saverow.addWidget(self.discard_btn)
        pb.addLayout(saverow)
        p.addWidget(self.preview_box)

        self.error = _message("error")
        self.success = _message("success")
        p.addWidget(self.error)
        p.addWidget(self.success)
        lay.addWidget(pcard)
        lay.addStretch()

        self.set_output_dir(Path(self.settings.value("output_dir", str(default_output_dir()))))
        self.preview_box.hide()
        self._refresh()

    # ---- inputs

    def set_file(self, path: Path) -> None:
        if self.worker:
            return
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            self.show_error(
                f"\"{path.name}\" isn't a supported audio file.\n"
                "Please choose an MP3, WAV, M4A, FLAC or OGG file."
            )
            return
        self.file = path
        self.url.blockSignals(True)
        self.url.clear()
        self.url.blockSignals(False)
        self.drop.show_file(path)
        self.clear_messages()
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
        self.clear_messages()
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

    def _source(self) -> str | None:
        if self.file:
            return str(self.file)
        text = self.url.text().strip()
        return text or None

    # ---- state

    @property
    def has_unsaved_preview(self) -> bool:
        return self.preview is not None and self.preview.saved is None

    def _refresh(self) -> None:
        busy = self.worker is not None
        splitting = busy and self.worker.label == "split"
        self.go.setEnabled(not busy and self._source() is not None)
        self.go.setText("Splitting…" if splitting else "Split song")
        self.drop.setEnabled(not busy)
        self.drop.setAcceptDrops(not busy)
        self.url.setEnabled(not busy)
        self.cancel_btn.setVisible(busy)
        self.cancel_btn.setEnabled(busy)
        self.bar.setVisible(busy or self.preview is None)
        saved = self.preview is not None and self.preview.saved is not None
        self.preview_box.setVisible(self.preview is not None and not splitting)
        self.save_btn.setVisible(not saved)
        self.save_btn.setEnabled(not busy)
        self.open_btn.setVisible(saved)
        self.discard_btn.setText("Close" if saved else "Discard")
        self.discard_btn.setEnabled(not busy)
        self.change_btn.setEnabled(not busy and not saved)

    def _start_worker(self, fn: Callable, label: str, on_success: Callable) -> None:
        self.worker = JobWorker(fn, label)
        self.worker.progress.connect(self._on_progress)
        self.worker.succeeded.connect(on_success)
        self.worker.failed.connect(self._on_failed)
        self.worker.cancelled.connect(self._on_cancelled)
        self.worker.finished.connect(self._on_finished)
        self._stage = None
        self.bar.setRange(0, 0)
        self._refresh()
        self.worker.start()

    # ---- split

    def start_split(self) -> None:
        source = self._source()
        if self.worker or not source:
            return
        if not self.file and not is_youtube_url(source):
            self.show_error(
                "That doesn't look like a YouTube video link.\n"
                "It should look like https://www.youtube.com/watch?v=…"
            )
            return
        if self.has_unsaved_preview:
            answer = QMessageBox.question(
                self, "Split another song?",
                f"\"{self.preview.sep.title}\" hasn't been saved yet. Discard it and split the new song?",
            )
            if answer != QMessageBox.Yes:
                return
        self.drop_preview()
        self.clear_messages()
        self._skip_download = self.file is not None
        self.stages.set_states(None, self._skip_download)
        self.status.setText("Starting…")
        self._start_worker(separate_with_preview(source), "split", self._on_separated)

    def _on_separated(self, preview: Preview) -> None:
        self.preview = preview
        self.stages.set_states(Stage.EXPORTING, self._skip_download, done_through=Stage.SEPARATING)
        self.stages.labels[Stage.EXPORTING].setProperty("state", "pending")
        _repolish(self.stages.labels[Stage.EXPORTING])
        self.bar.setRange(0, 1000)
        self.bar.setValue(1000)
        self.status.setText("Separation done. Have a listen, then save the two MP3 files.")
        self.preview_title.setText(f"🎧  {preview.sep.title}")
        self.player.load(preview.vocals_wav, preview.instrumental_wav)
        QApplication.alert(self.window_)

    # ---- export

    def start_export(self) -> None:
        if self.worker or self.preview is None:
            return
        self.player.pause()
        self.clear_messages()
        sep, out_dir = self.preview.sep, self.out_dir
        self._start_worker(lambda prog, cancel: export(sep, out_dir, prog, cancel), "export", self._on_exported)

    def _on_exported(self, result: JobResult) -> None:
        if self.preview is not None:
            self.preview.saved = result
        self.stages.set_states(None, self._skip_download, done_through=Stage.EXPORTING)
        self.bar.setRange(0, 1000)
        self.bar.setValue(1000)
        self.status.setText("All done!")
        self.success.setText(f"Saved:\n•  {result.vocals.name}\n•  {result.instrumental.name}")
        self.success.show()
        QApplication.alert(self.window_)

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

    def _after_stop(self) -> None:
        """Back to the right resting state after a failed or cancelled job."""
        self.bar.setRange(0, 1000)
        if self.preview is not None:
            # An export failed or was cancelled: the separated song is still here.
            self.stages.set_states(Stage.EXPORTING, self._skip_download, done_through=Stage.SEPARATING)
            self.stages.labels[Stage.EXPORTING].setProperty("state", "pending")
            _repolish(self.stages.labels[Stage.EXPORTING])
            self.bar.setValue(1000)
            self.status.setText("Not saved yet. You can try again or pick another folder.")
        else:
            self.stages.set_states(None)
            self.bar.setValue(0)
            self.status.setText("Choose a song to get started.")

    def _on_failed(self, msg: str) -> None:
        self._after_stop()
        self.show_error(msg)
        QApplication.alert(self.window_)

    def _on_cancelled(self) -> None:
        self._after_stop()
        self.status.setText("Cancelled. " + self.status.text())

    def _on_finished(self) -> None:
        self.worker = None
        self._refresh()

    # ---- preview lifetime

    def drop_preview(self) -> None:
        self.player.unload()
        if self.preview is not None:
            shutil.rmtree(self.preview.folder, ignore_errors=True)
        self.preview = None
        self._refresh()

    def discard_preview(self) -> None:
        if self.has_unsaved_preview:
            answer = QMessageBox.question(
                self, "Discard?", f"Throw away the split of \"{self.preview.sep.title}\" without saving?",
            )
            if answer != QMessageBox.Yes:
                return
        self.drop_preview()
        self.clear_messages()
        self.stages.set_states(None)
        self.bar.setValue(0)
        self.status.setText("Choose a song to get started.")

    def show_error(self, msg: str) -> None:
        self.success.hide()
        self.error.setText(msg)
        self.error.show()

    def clear_messages(self) -> None:
        self.error.hide()
        self.success.hide()

    def open_output(self) -> None:
        saved = self.preview.saved if self.preview else None
        if IS_WINDOWS and saved and saved.vocals.exists():
            # Open Explorer with the vocals file selected.
            subprocess.Popen(["explorer", "/select,", str(saved.vocals)], creationflags=SUBPROCESS_FLAGS)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(saved.vocals.parent if saved else self.out_dir)))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings(APP_NAME, APP_NAME)
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(560, 560)
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry().height() - 60 if screen else 860
        self.resize(640, max(560, min(860, avail)))

        body = QWidget()
        body.setObjectName("scrollBody")
        outer = QVBoxLayout(body)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(16)
        title = QLabel(APP_NAME)
        title.setObjectName("title")
        subtitle = QLabel("Split any song into vocals and instrumental.")
        subtitle.setObjectName("subtitle")
        head = QVBoxLayout()
        head.setSpacing(2)
        head.addWidget(title)
        head.addWidget(subtitle)
        outer.addLayout(head)

        self.stack = QStackedWidget()
        self.setup_page = SetupPage(self)
        self.main_page = MainPage(self)
        self.stack.addWidget(self.setup_page)
        self.stack.addWidget(self.main_page)
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

        # Drops anywhere on the window go to the drop zone.
        self.setAcceptDrops(True)

        if is_model_ready():
            self.show_main()
        else:
            self.stack.setCurrentWidget(self.setup_page)
            self.setup_page.start()

    def show_main(self) -> None:
        self.stack.setCurrentWidget(self.main_page)

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
        if page.worker is not None or page.has_unsaved_preview:
            what = "A song is still being processed." if page.worker else "Your split song hasn't been saved."
            answer = QMessageBox.question(self, "Quit?", f"{what} Quit anyway?")
            if answer != QMessageBox.Yes:
                e.ignore()
                return
        if page.worker is not None:
            page.worker.cancel.set()
            page.worker.wait(10_000)
        dl = self.setup_page.worker
        if dl is not None and dl.isRunning():
            dl.cancel.set()
            dl.wait(5_000)
        page.drop_preview()
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
    pal.setColor(QPalette.Window, QColor("#f4f5fb"))
    pal.setColor(QPalette.WindowText, QColor("#1d1d2b"))
    pal.setColor(QPalette.Base, QColor("white"))
    pal.setColor(QPalette.AlternateBase, QColor("#f4f5fb"))
    pal.setColor(QPalette.Text, QColor("#1d1d2b"))
    pal.setColor(QPalette.Button, QColor("white"))
    pal.setColor(QPalette.ButtonText, QColor("#1d1d2b"))
    pal.setColor(QPalette.Highlight, QColor("#6c4dff"))
    pal.setColor(QPalette.HighlightedText, QColor("white"))
    pal.setColor(QPalette.ToolTipBase, QColor("white"))
    pal.setColor(QPalette.ToolTipText, QColor("#1d1d2b"))
    pal.setColor(QPalette.PlaceholderText, QColor("#9a9db3"))
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
    _clean_stale_previews()
    app = make_app(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()
