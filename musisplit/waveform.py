"""Waveform overviews for the player (numpy only).

Each stem becomes ``BUCKETS`` peak levels (uint8, 0-255) which the UI draws
as bars, Audacity/SoundCloud style.
"""
from __future__ import annotations

import numpy as np

BUCKETS = 1200


def _peaks(audio: np.ndarray, buckets: int) -> np.ndarray:
    mono = np.abs(audio).max(axis=0) if audio.ndim == 2 else np.abs(audio)
    if len(mono) == 0:
        return np.zeros(buckets, np.float32)
    if len(mono) < buckets:
        mono = np.pad(mono, (0, buckets - len(mono)))
    edges = np.linspace(0, len(mono), buckets + 1).astype(np.int64)[:-1]
    return np.maximum.reduceat(mono, edges).astype(np.float32)


def compute_pair(vocals: np.ndarray, instrumental: np.ndarray,
                 buckets: int = BUCKETS) -> tuple[np.ndarray, np.ndarray]:
    """Peak bars for both stems on one shared scale, so a quiet stem looks quiet."""
    pv, pi = _peaks(vocals, buckets), _peaks(instrumental, buckets)
    ref = max(float(pv.max()), float(pi.max()), 1e-6)

    def to_u8(p: np.ndarray) -> np.ndarray:
        # A gentle curve so quiet passages are still visible.
        return (np.clip(p / ref, 0, 1) ** 0.7 * 255).astype(np.uint8)

    return to_u8(pv), to_u8(pi)
