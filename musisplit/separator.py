"""2-stem separation with the single-file htdemucs ONNX model.

The chunking mirrors ``demucs_onnx.inference._chunked_separate_single``
(fixed 7.8 s segments, quarter overlap, triangular cross-fade). We run our
own loop for two reasons: demucs-onnx only reports progress through tqdm,
and we only need two outputs, so we accumulate vocals and
drums + bass + other directly instead of keeping four full-length stems in
memory.
"""
from __future__ import annotations

import os
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np
import onnxruntime as ort
from demucs_onnx.inference import N_SAMPLES, SOURCES

from .errors import Cancelled

VOCALS = SOURCES.index("vocals")
ACCOMPANIMENT = tuple(SOURCES.index(s) for s in ("drums", "bass", "other"))

# Leave the output just under full scale; MP3 encoding can add small overshoots.
PEAK_TARGET = 0.95


def _transition_window(segment: int, overlap_frac: float = 0.25) -> np.ndarray:
    transition = int(segment * overlap_frac)
    window = np.ones(segment, dtype=np.float32)
    fade = np.linspace(0, 1, transition, dtype=np.float32)
    window[:transition] = fade
    window[-transition:] = fade[::-1]
    return window


class Separator:
    def __init__(self, model_path: Path, threads: int | None = None) -> None:
        opts = ort.SessionOptions()
        # Measured on htdemucs.onnx: ORT_ENABLE_ALL peaks at ~3.8 GB RSS (the
        # optimizer copies the weights while loading), while no graph
        # optimization plus no memory-pattern planning peaks at ~1.6 GB and is
        # within ~8% on speed. RAM matters more than that on a low-end PC.
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
        opts.enable_mem_pattern = False
        # Keep one core free so the window stays responsive on small machines.
        opts.intra_op_num_threads = threads or max(1, (os.cpu_count() or 2) - 1)
        self.session = ort.InferenceSession(
            str(model_path), sess_options=opts, providers=["CPUExecutionProvider"],
        )

    def separate(self, mix: np.ndarray,
                 on_progress: Callable[[float], None] | None = None,
                 cancel: threading.Event | None = None) -> tuple[np.ndarray, np.ndarray]:
        """Split ``(2, samples)`` 44.1 kHz audio into ``(vocals, instrumental)``."""
        if mix.ndim != 2 or mix.shape[0] != 2:
            raise ValueError(f"expected (2, samples), got {mix.shape}")
        total = mix.shape[1]
        stride = N_SAMPLES - N_SAMPLES // 4
        n_chunks = max(1, (total + stride - 1) // stride)
        window = _transition_window(N_SAMPLES)

        vocals = np.zeros((2, total), dtype=np.float32)
        instrumental = np.zeros((2, total), dtype=np.float32)
        weight = np.zeros(total, dtype=np.float32)

        for i in range(n_chunks):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            start = i * stride
            end = min(start + N_SAMPLES, total)
            length = end - start
            chunk = np.zeros((1, 2, N_SAMPLES), dtype=np.float32)
            chunk[0, :, :length] = mix[:, start:end]

            stems = self.session.run(["stems"], {"mix": chunk})[0][0]  # (4, 2, N)
            w = window[:length]
            vocals[:, start:end] += stems[VOCALS, :, :length] * w
            instrumental[:, start:end] += stems[list(ACCOMPANIMENT), :, :length].sum(axis=0) * w
            weight[start:end] += w
            if on_progress:
                on_progress((i + 1) / n_chunks)

        weight = np.maximum(weight, 1e-8)
        vocals /= weight
        instrumental /= weight
        return vocals, instrumental


def normalize_pair(vocals: np.ndarray, instrumental: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Scale both stems by the same gain so neither clips.

    One shared gain keeps the vocal/instrumental balance, so the two files
    still add back up to the original song. Quiet stems are left untouched.
    """
    peak = max(float(np.abs(vocals).max(initial=0.0)), float(np.abs(instrumental).max(initial=0.0)))
    if peak > PEAK_TARGET:
        gain = PEAK_TARGET / peak
        vocals = vocals * gain
        instrumental = instrumental * gain
    return vocals, instrumental
