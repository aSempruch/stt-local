"""Signal processing for meeting transcription: remove the other side's
voice that leaks from open earbuds or speakers into the microphone, silence
whatever leakage is left, and cut both tracks into segments short enough to
transcribe one at a time."""

from __future__ import annotations

import numpy as np

from .constants import SAMPLE_RATE

FRAME = 320  # 20 ms at 16 kHz
FADE = 160  # 10 ms
Region = tuple[float, float]  # seconds

ECHO_BLOCK = 256  # 16 ms
ECHO_PARTITIONS = 16  # models 256 ms of room response after the delay
ECHO_STEP = 0.5
SUPPRESSION_FFT = 512


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
    floor = float(np.percentile(e_mic, 10))
    system_floor = _system_floor(e_sys)
    lag = _lag_from_energies(e_mic, e_sys, max_lag_seconds)
    shifted = np.concatenate([np.full(lag, 1e-12), e_sys[: n - lag]])
    # Leakage gain from a low percentile of the energy ratio while the system
    # is loud, so stretches where both talk don't inflate it.
    playing = shifted[shifted > system_floor]
    loud = shifted > np.percentile(playing, 50) if len(playing) else np.zeros(n, bool)
    gain = float(np.percentile(e_mic[loud] / shifted[loud], 25)) if loud.any() else 0.0
    predicted = floor + gain * shifted
    mask = e_mic > np.maximum(predicted * 10 ** (8 / 10), floor * 10 ** (12 / 10))
    # Fill gaps under 300 ms, drop islands under 120 ms, pad by 200 ms.
    mask = _close_gaps(mask, 15)
    mask = _drop_islands(mask, 6)
    return _dilate(mask, 10)


def _system_floor(e_sys: np.ndarray) -> float:
    """Frames above this play something, within 30 dB of the system's peaks.
    Between turns the system track is often digitally silent, exactly while
    the user talks, so frames below it say nothing about leakage."""
    return max(float(np.percentile(e_sys, 99)) * 1e-3, 1e-10)


def _lag_from_energies(
    e_mic: np.ndarray, e_sys: np.ndarray, max_lag_seconds: float
) -> int:
    n = min(len(e_mic), len(e_sys))
    if n < 2:
        return 0
    e_mic, e_sys = e_mic[:n], e_sys[:n]
    system_floor = _system_floor(e_sys)
    return _leakage_lag(
        np.log(np.maximum(e_mic, float(np.percentile(e_mic, 10)))),
        np.log(np.maximum(e_sys, system_floor)),
        e_sys > system_floor,
        min(int(max_lag_seconds * SAMPLE_RATE / FRAME), n - 1),
    )


def estimate_lag(
    mic: np.ndarray, system: np.ndarray, max_lag_seconds: float = 1.5
) -> int:
    """How many 20 ms frames the system audio takes to reach the microphone."""
    return _lag_from_energies(frame_energy(mic), frame_energy(system), max_lag_seconds)


def cancel_echo(mic: np.ndarray, system: np.ndarray, delay: int = 0) -> np.ndarray:
    """The microphone track with the system audio it picked up subtracted,
    as a call app's echo canceller does. delay is the leakage delay in
    samples. Recordings are processed after the fact, so the filter learns
    the room on a first pass and cleans the whole track on a second."""
    mic = np.asarray(mic, dtype=np.float32)
    n = len(mic)
    if n < 2 * ECHO_BLOCK or not np.any(system):
        return mic.copy()
    # Keep 32 ms before the estimated delay so early arrivals stay modelled.
    shift = max(0, delay - 2 * ECHO_BLOCK)
    reference = np.zeros(n, np.float32)
    reference[shift:] = np.asarray(system[: n - shift], dtype=np.float32)
    _, _, weights = _adaptive_filter(mic, reference)
    residual, echo, _ = _adaptive_filter(mic, reference, weights)
    return _suppress_residual(residual, echo)


def _adaptive_filter(
    mic: np.ndarray, reference: np.ndarray, weights: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Partitioned-block frequency-domain NLMS: returns the residual, the
    echo estimate and the learned filter."""
    block, partitions = ECHO_BLOCK, ECHO_PARTITIONS
    bins = block + 1
    weights = (
        np.zeros((partitions, bins), complex) if weights is None else weights.copy()
    )
    history = np.zeros((partitions, bins), complex)
    power = np.full(bins, 1e-8)
    residual = mic.copy()
    echo = np.zeros_like(mic)
    previous = np.zeros(block, np.float32)
    zeros = np.zeros(block, np.float32)
    head = 0
    for k in range(len(mic) // block):
        span = slice(k * block, (k + 1) * block)
        current = reference[span]
        head = (head - 1) % partitions
        history[head] = np.fft.rfft(np.concatenate([previous, current]))
        previous = current
        ordered = history[(head + np.arange(partitions)) % partitions]
        estimate = np.fft.irfft((weights * ordered).sum(axis=0))[block:]
        error = mic[span] - estimate
        residual[span] = error
        echo[span] = estimate
        power = 0.95 * power + 0.05 * np.abs(history[head]) ** 2
        if np.dot(current, current) < 1e-7:
            continue  # Nothing playing, nothing to learn.
        step = ECHO_STEP
        if np.dot(mic[span], mic[span]) > 4 * np.dot(estimate, estimate) + 1e-7:
            step *= 0.05  # Probably the user talking: barely adapt.
        gradient = np.conj(ordered) * np.fft.rfft(np.concatenate([zeros, error]))
        weights += (step / (partitions * power + 1e-6)) * gradient
    return residual, echo, weights


def _suppress_residual(
    residual: np.ndarray, echo: np.ndarray, overestimate: float = 2.0, floor: float = 0.03
) -> np.ndarray:
    """Turn down the frequencies where the echo estimate outweighs what is
    left, which removes the leakage a linear filter can't (loudspeaker
    distortion) while keeping the user's voice where it dominates."""
    size, hop = SUPPRESSION_FFT, SUPPRESSION_FFT // 2
    # Square-root Hann windows on both sides sum to one at half overlap.
    window = np.sqrt(np.hanning(size + 1)[:-1]).astype(np.float32)
    padded_residual = np.pad(residual, (hop, hop + size))
    padded_echo = np.pad(echo, (hop, hop + size))
    out = np.zeros(len(padded_residual), np.float32)
    frames = (len(padded_residual) - size) // hop + 1
    for first in range(0, frames, 2048):  # bounded memory for long meetings
        starts = np.arange(first, min(frames, first + 2048)) * hop
        index = starts[:, None] + np.arange(size)[None, :]
        e = np.fft.rfft(padded_residual[index] * window, axis=1)
        y = np.fft.rfft(padded_echo[index] * window, axis=1)
        gain = np.clip(1 - overestimate * np.abs(y) ** 2 / (np.abs(e) ** 2 + 1e-10), floor, 1.0)
        cleaned = np.fft.irfft(e * gain, n=size, axis=1).astype(np.float32) * window
        for start, row in zip(starts, cleaned):
            out[start : start + size] += row
    return out[hop : hop + len(residual)]


def _leakage_lag(
    l_mic: np.ndarray, l_sys: np.ndarray, active: np.ndarray, max_lag: int
) -> int:
    """The delay, in frames, at which the microphone's log energy best follows
    the system's while the system is playing (Bluetooth output latency)."""
    n = len(l_mic)
    best, best_corr = 0, -np.inf
    for lag in range(max_lag + 1):
        selected = active[: n - lag]
        if selected.sum() < 25:
            continue
        x, y = l_mic[lag:][selected], l_sys[: n - lag][selected]
        if x.std() == 0 or y.std() == 0:
            continue
        corr = float(np.corrcoef(x, y)[0, 1])
        if corr > best_corr:
            best, best_corr = lag, corr
    return best


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
