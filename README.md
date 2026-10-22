# MusiSplitSeven

A small Windows app that splits a song into two tracks: **vocals** and
**instrumental**. Give it an audio file or a YouTube link, listen to both tracks
in the app, then save them as two MP3s. Every split is kept in **History** so
you can come back to it later.

Everything runs on your own computer. Nothing is uploaded anywhere.

---

## Using the app

### Install

1. Download **`MusiSplitSeven-<version>-Setup.exe`** from the
   [Releases](../../releases) page and double-click it. No administrator rights
   are needed.
2. Windows may show *"Windows protected your PC"* because the app isn't signed.
   Click **More info → Run anyway**.
3. The installer adds MusiSplitSeven to the Start menu and the Desktop, then
   opens it.

The first time it starts, the app downloads its separation model (316 MB, once
only). It is stored in `%APPDATA%\StemSplit\models`.

To update, run the newer Setup.exe; it replaces the old version and keeps your
History. To uninstall, use *Settings → Apps*. The uninstaller asks whether to
also delete the model and the History.

There is also a `-portable.zip` with the same app and no installer: extract it
and run `MusiSplitSeven.exe`.

### Split a song

1. **Pick a song** in one of two ways:
   - drag an audio file (MP3, WAV, M4A, FLAC or OGG) onto the window, or click
     *Choose a file…*
   - paste a YouTube link into the box
2. Click **Split song**. The progress bar goes through
   *Downloading → Separating → Exporting*. Separating takes the longest: a few
   minutes per song on an average laptop. You can keep using the computer.
3. **Listen.** When the split is done, you see one row per track:

   ```
   (▶) Vocals        ▮▮▮▯▯▯▯▯▯▯▯▯   (Save)
   (▶) Instrumental  ▮▮▮▮▮▮▯▯▯▯▯▯   (Save)
   ```

   Press ▶ to play a track; starting one pauses the other. The bars show how
   loud the track is over time, and the dark part is what has already played.
   Click or drag on the bars to jump.
4. **Save** the track you want. The suggested name is
   `<song title> - vocals.mp3` (or `- instrumental.mp3`) in `Music\StemSplit`,
   and you can pick another place. Existing files are never overwritten: a
   number is added instead (`Song (2) - vocals.mp3`). **Open folder** shows the
   saved file.
5. Click **New split** (top right) to do another song.

### Lyrics

Next to **Tracks**, the **Lyrics** tab shows the song's lyrics. They are
found automatically on [LRCLIB](https://lrclib.net), a free community lyrics
database, using the YouTube title or the file's artist/title tags. When the
lyrics are timed, the current line lights up while you play the instrumental or
the vocals: good for karaoke. Click a line to jump to it. If the wrong song
shows up, type the artist and title in the search box. Lyrics are saved with
the song in History.

Only the song's artist and title (and length) are sent to lrclib.net.

While a song is being split, the window only shows the progress and a Cancel
button.

### History

Every song you split is kept on your computer, so you can listen to it again
or save it again later without re-splitting. Click **History** (top right) to
see them all:

- **Open** loads a song back into the player.
- **Delete** removes one song. **Delete all** clears the whole history.

Deleting from History never touches MP3s you already saved. The history uses
about 5 MB per minute of music. It lives in
`%APPDATA%\StemSplit\library`.

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
6. builds `MusiSplitSeven-<version>-Setup.exe` with Inno Setup
   ([`packaging/installer.iss`](packaging/installer.iss); Inno Setup is
   preinstalled on GitHub's Windows runners). It then installs it silently,
   runs the self-test on the installed app, and uninstalls it again.
7. uploads the Setup.exe and the portable app folder as artifacts
8. for a `v*` tag (for example `v0.1.3`), also publishes a GitHub Release with
   the Setup.exe and a portable zip. The tag must match `__version__` in
   `musisplit/__init__.py`.

To release a new version, bump `__version__` (and `packaging/version_info.txt`),
then push a tag: `git tag v0.1.2 && git push origin v0.1.2`. The zip then shows
up under **Releases**. For untagged builds, open the **Actions** tab, select the latest run, and download
the artifact at the bottom of the page. To start a build by hand, use
**Run workflow** (the workflow has `workflow_dispatch` enabled).

### Locally on Windows

```powershell
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements-build.txt
pwsh scripts\fetch_ffmpeg.ps1
pyinstaller --noconfirm --clean MusiSplitSeven.spec
# app folder: dist\MusiSplitSeven\MusiSplitSeven.exe
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" /DAppVersion=0.1.3 packaging\installer.iss
# installer: installer\MusiSplitSeven-0.1.3-Setup.exe
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
| `MUSISPLIT_LIBRARY_DIR` | use a different History folder |
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
| `musisplit/library.py` | the History: each split is kept as two MP3s plus waveform bars, written atomically |
| `musisplit/waveform.py` | peak levels for the bar waveforms (numpy only) |
| `musisplit/lyrics.py` | lyrics lookup on LRCLIB: title clean-up, exact match then search, LRC parsing |
| `musisplit/pipeline.py` | Download → Separate → Export (into History), then copy to a folder; no UI code |
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
