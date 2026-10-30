"""Signal processing for meeting transcription: silence the microphone where
it only hears the other side leaking from open earbuds or speakers, and cut
both tracks into segments short enough to transcribe one at a time."""

from __future__ import annotations

import numpy as np

from .constants import SAMPLE_RATE

FRAME = 320  # 20 ms at 16 kHz
FADE = 160  # 10 ms
Region = tuple[float, float]  # seconds


def frame_energy(audio: np.ndarray) -> np.ndarray:
    n = len(audio) // FRAME
    frames = np.asarray(audio[: n * FRAME], dtype=np.float32).reshape(n, FRAME)
    return np.mean(np.square(frames, dtype=np.float64), axis=1) + 1e-12


def leakage_mask(
    mic: np.ndarray, system: np.ndarray, max_lag_seconds: float = 1.5
) -> np.ndarray:
    """Per 20 ms frame, whether the microphone holds more than leakage of the
    system track."""
    e_mic, e_sys = frame_energy(mic), frame_energy(system)
    n = min(len(e_mic), len(e_sys))
    if n == 0:
        return np.zeros(0, bool)
    e_mic, e_sys = e_mic[:n], e_sys[:n]
    # Leakage arrives late (Bluetooth output latency): find the lag at which
    # the microphone's log-energy envelope best follows the system's.
    l_mic = np.log(e_mic) - np.log(e_mic).mean()
    l_sys = np.log(e_sys) - np.log(e_sys).mean()
    max_lag = min(int(max_lag_seconds * SAMPLE_RATE / FRAME), n - 1)
    scores = [np.dot(l_mic[lag:], l_sys[: n - lag]) / (n - lag) for lag in range(max_lag + 1)]
    lag = int(np.argmax(scores))
    shifted = np.concatenate([np.full(lag, 1e-12), e_sys[: n - lag]])
    # Leakage gain from a low percentile of the energy ratio while the system
    # is loud, so stretches where both talk don't inflate it.
    loud = shifted > np.percentile(shifted, 70)
    gain = float(np.percentile(e_mic[loud] / shifted[loud], 25)) if loud.any() else 0.0
    floor = float(np.percentile(e_mic, 10))
    predicted = floor + gain * shifted
    mask = e_mic > np.maximum(predicted * 10 ** (8 / 10), floor * 10 ** (12 / 10))
    # Fill gaps under 300 ms, drop islands under 120 ms, pad by 200 ms.
    mask = _close_gaps(mask, 15)
    mask = _drop_islands(mask, 6)
    return _dilate(mask, 10)


def _runs(mask: np.ndarray, value: bool) -> list[tuple[int, int]]:
    padded = np.concatenate([[False], mask == value, [False]]).astype(np.int8)
    edges = np.flatnonzero(np.diff(padded))
    return list(zip(edges[::2].tolist(), edges[1::2].tolist()))


def _close_gaps(mask: np.ndarray, width: int) -> np.ndarray:
    out = mask.copy()
    for i, j in _runs(mask, False):
        if i > 0 and j < len(mask) and j - i < width:
            out[i:j] = True
    return out


def _drop_islands(mask: np.ndarray, width: int) -> np.ndarray:
    out = mask.copy()
    for i, j in _runs(mask, True):
        if j - i < width:
            out[i:j] = False
    return out


def _dilate(mask: np.ndarray, width: int) -> np.ndarray:
    out = mask.copy()
    for i, j in _runs(mask, True):
        out[max(0, i - width) : j + width] = True
    return out


def apply_mask(audio: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Silence dropped frames, fading each kept run in and out over 10 ms."""
    out = np.zeros(len(audio), np.float32)
    ramp = np.linspace(0.0, 1.0, FADE, endpoint=False, dtype=np.float32)
    for i, j in _runs(mask, True):
        start, end = i * FRAME, min(j * FRAME, len(audio))
        out[start:end] = audio[start:end]
        fade = min(FADE, end - start)
        if i > 0:
            out[start : start + fade] *= ramp[:fade]
        if j < len(mask):
            out[end - fade : end] *= ramp[::-1][FADE - fade :]
    return out


def _quietest(energy: np.ndarray, start: float, end: float) -> float | None:
    lo = max(0, int(start * SAMPLE_RATE / FRAME))
    hi = min(len(energy), int(end * SAMPLE_RATE / FRAME))
    if hi <= lo:
        return None
    return (lo + int(np.argmin(energy[lo:hi]))) * FRAME / SAMPLE_RATE


def cut_at_interjections(
    regions: list[Region], other_regions: list[Region], audio: np.ndarray
) -> list[Region]:
    """Cut a region where the other side starts talking inside it, at the
    quietest moment within half a second, so a short reply keeps its place."""
    energy = frame_energy(audio)
    out: list[Region] = []
    for start, end in regions:
        pieces, current = [], start
        for other_start, _ in other_regions:
            if not start + 1.0 < other_start < end - 1.0:
                continue
            cut = _quietest(energy, other_start - 0.5, other_start + 0.5)
            if cut is not None and cut - current > 0.5:
                pieces.append((current, cut))
                current = cut
        pieces.append((current, end))
        out.extend(piece for piece in pieces if piece[1] - piece[0] > 0.2)
    return out


def split_long_regions(
    regions: list[Region],
    audio: np.ndarray,
    max_seconds: float = 20.0,
    search_seconds: float = 5.0,
) -> list[Region]:
    """Split regions longer than max_seconds at their quietest frame in the
    last search_seconds before the limit, repeatedly."""
    energy = frame_energy(audio)
    out: list[Region] = []
    for start, end in regions:
        while end - start > max_seconds:
            limit = start + max_seconds
            cut = _quietest(energy, limit - search_seconds, limit) or limit
            out.append((start, cut))
            start = cut
        out.append((start, end))
    return out
