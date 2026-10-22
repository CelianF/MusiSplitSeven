"""Step 1 sanity check: does demucs-onnx's single-file htdemucs model work,
and what is its real stem order?

Usage:
    python scripts/verify_demucs.py MODEL.onnx SONG.mp3 [--sources vocals.wav drums.wav bass.wav]

- Prints the ONNX graph inputs/outputs.
- Runs the public API ``demucs_onnx.separate(model="htdemucs")``.
- If the ground-truth source files that were mixed into SONG are given,
  prints the correlation of every output stem with every source, so the
  stem order is checked empirically instead of trusted from the docs.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
import soundfile as sf

import demucs_onnx


def corr(a: np.ndarray, b: np.ndarray) -> float:
    n = min(a.shape[-1], b.shape[-1])
    a, b = a[..., :n].ravel(), b[..., :n].ravel()
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("model", type=Path)
    p.add_argument("song", type=Path)
    p.add_argument("--sources", nargs="*", type=Path, default=[])
    p.add_argument("--cache-dir", type=Path, default=None)
    args = p.parse_args()

    print(f"demucs-onnx {demucs_onnx.__version__}, onnxruntime {ort.__version__}")
    print(f"declared SOURCES: {demucs_onnx.SOURCES}")

    sess = ort.InferenceSession(str(args.model), providers=["CPUExecutionProvider"])
    for i in sess.get_inputs():
        print(f"  input  {i.name}: {i.shape} {i.type}")
    for o in sess.get_outputs():
        print(f"  output {o.name}: {o.shape} {o.type}")
    meta = sess.get_modelmeta().custom_metadata_map
    if meta:
        print(f"  metadata: {meta}")

    t0 = time.perf_counter()
    stems = demucs_onnx.separate(
        args.song, model="htdemucs", providers="cpu",
        cache_dir=args.cache_dir, progress=False,
    )
    dt = time.perf_counter() - t0
    mix, sr = sf.read(str(args.song), dtype="float32", always_2d=True)
    print(f"separate() took {dt:.1f}s for {mix.shape[0] / sr:.1f}s of audio")
    for name, arr in stems.items():
        print(f"  {name:7s} shape={arr.shape} peak={np.abs(arr).max():.3f} "
              f"rms={np.sqrt(np.mean(arr ** 2)):.4f}")

    recon = sum(stems.values())
    print(f"corr(sum of stems, mix) = {corr(recon, mix.T):.4f}")

    if args.sources:
        print("\ncorrelation  stem \\ source: " + "  ".join(s.stem for s in args.sources))
        refs = [sf.read(str(s), dtype="float32", always_2d=True)[0].T for s in args.sources]
        for name, arr in stems.items():
            row = "  ".join(f"{corr(arr, r):+.3f}" for r in refs)
            print(f"  {name:7s} {row}")


if __name__ == "__main__":
    main()
