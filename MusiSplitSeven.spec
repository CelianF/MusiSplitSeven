# PyInstaller spec: one-folder build.  pyinstaller MusiSplitSeven.spec
# Expects ffmpeg in vendor/ffmpeg/ (see scripts/fetch_ffmpeg.ps1).
import sys
from pathlib import Path

ROOT = Path(SPECPATH)
FFMPEG = ROOT / "vendor" / "ffmpeg" / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
FFMPEG_LICENSE = FFMPEG.parent / "LICENSE.txt"
if not FFMPEG.is_file():
    raise SystemExit(f"missing {FFMPEG}: run scripts/fetch_ffmpeg.ps1 first")

a = Analysis(
    [str(ROOT / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[(str(FFMPEG), "ffmpeg")],
    datas=[
        (str(ROOT / "assets" / "icon.ico"), "assets"),
        (str(ROOT / "assets" / "icon.png"), "assets"),
    ] + ([(str(FFMPEG_LICENSE), "ffmpeg")] if FFMPEG_LICENSE.is_file() else []),
    hiddenimports=[],
    excludes=[
        # Not used at runtime; demucs-onnx's model export and Hub download paths.
        "torch", "demucs_onnx.export", "demucs_onnx.browser", "huggingface_hub",
        "tkinter", "matplotlib", "scipy", "pandas", "IPython", "pytest",
        # Qt modules we don't use (keeps the folder much smaller).
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.Qt3DCore",
        "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtGraphs", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner",
        "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors",
        "PySide6.QtSerialPort", "PySide6.QtWebSockets", "PySide6.QtWebChannel", "PySide6.QtXml",
        "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MusiSplitSeven",
    console=False,
    icon=str(ROOT / "assets" / "icon.ico"),
    version=str(ROOT / "packaging" / "version_info.txt") if sys.platform == "win32" else None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="MusiSplitSeven", upx=False)
