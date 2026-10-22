"""Build the app icon from assets/icon_source.jpg.

Writes assets/icon.png (256 px) and a multi-size assets/icon.ico
(16-256 px, PNG-compressed entries) with rounded corners.

    python scripts/make_icon.py
"""
import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QPainterPath

ASSETS = Path(__file__).resolve().parent.parent / "assets"
SOURCE = ASSETS / "icon_source.jpg"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(src: QImage, size: int) -> QImage:
    side = min(src.width(), src.height())
    square = src.copy((src.width() - side) // 2, (src.height() - side) // 2, side, side)
    scaled = square.scaled(size, size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    path = QPainterPath()
    radius = size * 0.22 if size >= 32 else size * 0.15
    path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    p.setClipPath(path)
    p.drawImage(0, 0, scaled)
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(data)


def write_ico(images: list[tuple[int, bytes]], dest: Path) -> None:
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, blobs = b"", b""
    for size, blob in images:
        dim = 0 if size >= 256 else size  # 0 means 256 in the ICO format
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        blobs += blob
        offset += len(blob)
    dest.write_bytes(header + entries + blobs)


if __name__ == "__main__":
    app = QGuiApplication([])
    src = QImage(str(SOURCE))
    if src.isNull():
        raise SystemExit(f"couldn't read {SOURCE}")
    render(src, 256).save(str(ASSETS / "icon.png"))
    write_ico([(s, png_bytes(render(src, s))) for s in SIZES], ASSETS / "icon.ico")
    print("wrote", ASSETS / "icon.png", ASSETS / "icon.ico")
