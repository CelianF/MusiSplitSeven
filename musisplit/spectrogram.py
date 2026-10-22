"""Small log-frequency spectrograms for the player view (numpy only).

The result is a ``(rows, columns)`` uint8 intensity map, highest frequency in
row 0, which the UI colours with its own palette.
"""
from __future__ import annotations

import numpy as np

COLUMNS = 1200
ROWS = 112
FFT_SIZE = 4096
F_MIN = 40.0
F_MAX = 16000.0
DYNAMIC_RANGE_DB = 80.0


def _band_power(audio: np.ndarray, sr: int, columns: int, rows: int) -> np.ndarray:
    """Average power per log-spaced band for ``columns`` evenly spaced frames."""
    mono = audio.mean(axis=0, dtype=np.float32) if audio.ndim == 2 else audio.astype(np.float32)
    if len(mono) < FFT_SIZE:
        mono = np.pad(mono, (0, FFT_SIZE - len(mono)))
    total = len(mono)
    centers = (np.arange(columns) + 0.5) * total / columns
    starts = np.clip(centers - FFT_SIZE / 2, 0, total - FFT_SIZE).astype(np.int64)
    window = np.hanning(FFT_SIZE).astype(np.float32)

    freqs = np.fft.rfftfreq(FFT_SIZE, 1 / sr)
    edges = np.geomspace(F_MIN, min(F_MAX, sr / 2), rows + 1)
    lo = np.searchsorted(freqs, edges[:-1])
    hi = np.maximum(np.searchsorted(freqs, edges[1:]), lo + 1)

    out = np.empty((rows, columns), dtype=np.float32)
    offsets = np.arange(FFT_SIZE)
    for b in range(0, columns, 128):  # batches keep memory small
        idx = starts[b:b + 128, None] + offsets
        power = np.abs(np.fft.rfft(mono[idx] * window, axis=1)) ** 2
        csum = np.concatenate([np.zeros((power.shape[0], 1)), np.cumsum(power, axis=1)], axis=1)
        out[:, b:b + 128] = ((csum[:, hi] - csum[:, lo]) / (hi - lo)).T
    return out


def compute_pair(vocals: np.ndarray, instrumental: np.ndarray, sr: int = 44100,
                 columns: int = COLUMNS, rows: int = ROWS) -> tuple[np.ndarray, np.ndarray]:
    """Spectrograms of both stems on one shared loudness scale, so quiet parts look quiet."""
    pv = _band_power(vocals, sr, columns, rows)
    pi = _band_power(instrumental, sr, columns, rows)
    db_v, db_i = 10 * np.log10(pv + 1e-12), 10 * np.log10(pi + 1e-12)
    ref = max(float(db_v.max()), float(db_i.max()))

    def to_u8(db: np.ndarray) -> np.ndarray:
        level = np.clip((db - (ref - DYNAMIC_RANGE_DB)) / DYNAMIC_RANGE_DB, 0, 1) ** 1.4
        return np.ascontiguousarray((level[::-1] * 255).astype(np.uint8))

    return to_u8(db_v), to_u8(db_i)
