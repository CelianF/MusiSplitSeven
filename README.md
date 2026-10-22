# MusiSplitSeven

A small Windows app that splits a song into two tracks: **vocals** and
**instrumental**. Give it an audio file or a YouTube link, listen to the result
in the app, then save two MP3s.

Everything runs on your own computer. Nothing is uploaded anywhere.

---

## Using the app

### Install

1. Download `MusiSplitSeven-<version>-win64.zip` and **extract** it. Right-click,
   then *Extract All…*. Running it from inside the zip won't work.
2. Open the extracted folder and double-click **`MusiSplitSeven.exe`**.
3. Windows may show *"Windows protected your PC"* because the app isn't signed.
   Click **More info → Run anyway**.

The first time it starts, the app downloads its separation model (316 MB, once
only). It is stored in `%APPDATA%\StemSplit\models`.

### Split a song

1. **Pick a song** in one of two ways:
   - drag an audio file (MP3, WAV, M4A, FLAC or OGG) onto the window, or click
     *Choose a file…*
   - paste a YouTube link into the box
2. Click **Split song**. The progress bar goes through
   *Downloading → Separating → Exporting*. Separating takes the longest: a few
   minutes per song on an average laptop. You can keep using the computer.
3. **Listen**. Switch between *Vocals* and *Instrumental* while it plays.
4. Choose where to save (the default is `Music\StemSplit`), then click
   **Save MP3s**. You get:
   - `<song title> - vocals.mp3`
   - `<song title> - instrumental.mp3`
5. Click **Open output folder** to see the files.

If a file with the same name already exists, the new one is numbered
(`Song (2) - vocals.mp3`), so nothing is overwritten.

### If something goes wrong

- **"Couldn't connect to the internet"**: YouTube links and the first-time
  model download need internet. Local files don't.
- **YouTube link doesn't work**: private, age-restricted, members-only and
  live videos can't be downloaded, and neither can whole playlists. YouTube
  also changes things from time to time. If links that used to work stop
  working, ask for an updated version of the app.
- **Anything else**: details are written to `%APPDATA%\StemSplit\log.txt`.
  Please send that file along.

---

## Building

Builds are made by GitHub Actions on a Windows machine, so you don't need one.

### With GitHub Actions (recommended)

Every push to `main` (and every `v*` tag) runs
[`.github/workflows/build.yml`](.github/workflows/build.yml) on `windows-latest`:

1. installs Python 3.11 and the pinned dependencies
2. downloads a static LGPL `ffmpeg.exe` ([`scripts/fetch_ffmpeg.ps1`](scripts/fetch_ffmpeg.ps1))
3. runs the unit tests against the real model (cached between runs)
4. builds a one-folder app with PyInstaller ([`MusiSplitSeven.spec`](MusiSplitSeven.spec))
5. copies the build to a clean folder and runs `MusiSplitSeven.exe --self-test`.
   This splits a test track, exports the MP3s, loads the preview player and
   opens the window. It also tries a YouTube download, but only warns if that
   fails, because YouTube often blocks CI machines.
6. zips the build and uploads it as an artifact

To get the zip, open the **Actions** tab, select the latest run, and download
the artifact at the bottom of the page. To start a build by hand, use
**Run workflow** (the workflow has `workflow_dispatch` enabled).

### Locally on Windows

```powershell
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements-build.txt
pwsh scripts\fetch_ffmpeg.ps1
pyinstaller --noconfirm --clean MusiSplitSeven.spec
# result: dist\MusiSplitSeven\MusiSplitSeven.exe  (zip the whole dist\MusiSplitSeven folder)
```

### Development (any OS)

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m musisplit                                   # run the app
python -m musisplit.cli song.mp3 -o out/              # core only, no UI
python -m musisplit.cli "https://youtu.be/..." -o out/
python -m unittest discover -s tests -t . -v          # tests
```

Outside Windows, ffmpeg is taken from `vendor/ffmpeg/` or your `PATH`. Useful
environment variables:

| Variable | Purpose |
|---|---|
| `MUSISPLIT_MODEL_DIR` | use a different model folder (tests, CI) |
| `MUSISPLIT_FFMPEG` | path to a specific ffmpeg binary |
| `MUSISPLIT_NET_TESTS=1` | also run the YouTube tests |

The app icon is generated from `assets/icon_source.jpg` by
`python scripts/make_icon.py`.

---

## How it works

| Module | Job |
|---|---|
| `musisplit/youtube.py` | audio-only download with yt-dlp, with friendly error messages |
| `musisplit/audio.py` | decode any input to 44.1 kHz stereo float, and encode 320 kbps MP3, both with the bundled ffmpeg |
| `musisplit/model.py` | first-launch model download: pinned URL, SHA-256 check, resumes partial downloads |
| `musisplit/separator.py` | runs the `htdemucs` ONNX model in 7.8 s chunks with overlap-add; vocals = stem 3, instrumental = drums + bass + other |
| `musisplit/pipeline.py` | Download → Separate → Export, with no UI code |
| `musisplit/gui.py` | PySide6 window; all work runs on background threads |

**Model.** The single-file [`htdemucs`](https://huggingface.co/StemSplitio/htdemucs-onnx)
4-stem model from [demucs-onnx](https://github.com/StemSplit/demucs-onnx)
(MIT). It runs on the CPU with onnxruntime and does not need PyTorch. The stem
order `[drums, bass, other, vocals]` was checked by
[`scripts/verify_demucs.py`](scripts/verify_demucs.py): each stem correlated
above 0.99 with the matching source in a test mix built from known parts.

**Memory.** onnxruntime's default graph optimizations push peak RAM to
about 3.8 GB. The app turns them off and disables memory-pattern planning,
which brings the peak down to about 1.6 GB and costs about 8% in speed.

**Volume.** Both stems are scaled by the same gain (only when needed), so
neither clips and they still add back up to the original song.

## Licenses

- App code: no license chosen yet (private repo). Pick one before making it public.
- Separation model and demucs-onnx: MIT.
- `ffmpeg.exe`: LGPL v2.1+ build from [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds),
  shipped unmodified in `_internal/ffmpeg/` together with its `LICENSE.txt`.
- Qt / PySide6: LGPL v3.
