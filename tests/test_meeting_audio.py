import numpy as np

from stt_local.meeting_audio import (
    FRAME,
    apply_mask,
    cancel_echo,
    estimate_lag,
    cut_at_interjections,
    leakage_mask,
    split_long_regions,
)

SR = 16_000


def speech(seconds, spans, level=0.1, seed=0):
    """Noise bursts with a syllable-rate envelope that never reaches zero."""
    rng = np.random.default_rng(seed)
    audio = np.zeros(int(seconds * SR), np.float32)
    for start, end in spans:
        a, b = int(start * SR), int(end * SR)
        t = np.arange(b - a) / SR
        envelope = 0.6 + 0.4 * np.sin(2 * np.pi * 4 * t)
        audio[a:b] = rng.normal(0, level, b - a) * envelope
    return audio


def delayed(audio, seconds, gain_db):
    shift = int(seconds * SR)
    out = np.zeros_like(audio)
    out[shift:] = audio[:-shift] * 10 ** (gain_db / 20)
    return out


def with_word_pauses(audio, word=0.3, pause=0.15, seed=5):
    """Pauses between words hold only 16-bit quantization noise, while the
    gaps between turns stay exactly zero, as in synthesized speech."""
    rng = np.random.default_rng(seed)
    out = audio.copy()
    talking = np.abs(audio) > 0
    period = int((word + pause) * SR)
    for start in range(int(word * SR), len(out), period):
        end = min(len(out), start + int(pause * SR))
        lsb = rng.integers(-1, 2, end - start) / 32768
        out[start:end] = np.where(talking[start:end], lsb, 0.0)
    return out


def floor_noise(seconds, db=-60, seed=1):
    rng = np.random.default_rng(seed)
    return rng.normal(0, 10 ** (db / 20), int(seconds * SR)).astype(np.float32)


def frames_in(spans, n, margin=0.0):
    selected = np.zeros(n, bool)
    for start, end in spans:
        a = max(0, int((start - margin) * SR / FRAME))
        b = min(n, int((end + margin) * SR / FRAME))
        selected[a:b] = True
    return selected


MINE = [(2, 6), (14, 18), (26, 29)]
THEIRS = [(7, 13), (19, 25), (30, 38)]


def test_gate_keeps_own_speech_without_leakage():
    mic = speech(40, MINE, seed=2) + floor_noise(40)
    system = speech(40, THEIRS, seed=3)

    mask = leakage_mask(mic, system)

    own = frames_in(MINE, len(mask))
    assert mask[own].mean() > 0.95


def test_gate_drops_speaker_leakage_but_keeps_own_speech():
    system = speech(40, THEIRS, seed=3)
    mic = speech(40, MINE, seed=2) + delayed(system, 0.2, -16) + floor_noise(40)

    mask = leakage_mask(mic, system)

    own = frames_in(MINE, len(mask))
    near_own = frames_in(MINE, len(mask), margin=0.5)
    leak_only = frames_in(THEIRS, len(mask)) & ~near_own
    assert mask[own].mean() > 0.90
    assert mask[leak_only].mean() < 0.10


def test_gate_drops_faint_leakage_when_turns_alternate_with_digital_silence():
    # Turn-taking makes the mic loud exactly while the system track is
    # digitally silent, which must not throw off the leakage delay.
    mine = [(1.0, 3.0), (5.5, 7.0), (10.5, 13.0), (16.0, 17.0), (20.5, 23.0)]
    theirs = [(3.2, 5.3), (7.2, 10.3), (13.2, 15.8), (17.2, 20.3), (23.2, 27.0)]
    system = with_word_pauses(speech(30, theirs, seed=3))
    mic = (
        with_word_pauses(speech(30, mine, seed=2))
        + delayed(system, 0.2, -30)
        + floor_noise(30, db=-75)
    )

    mask = leakage_mask(mic, system)

    own = frames_in(mine, len(mask))
    leak_only = frames_in(theirs, len(mask)) & ~frames_in(mine, len(mask), margin=0.5)
    assert mask[own].mean() > 0.90
    assert mask[leak_only].mean() < 0.10


def test_gate_keeps_nothing_on_quiet_mic_with_faint_leakage():
    system = speech(40, THEIRS, seed=3)
    mic = floor_noise(40, db=-53) + delayed(system, 0.2, -46)

    mask = leakage_mask(mic, system)

    assert mask.mean() < 0.02


def test_gate_handles_silent_tracks_and_empty_audio():
    silent = np.zeros(10 * SR, np.float32)

    assert not leakage_mask(silent, silent).any()
    assert leakage_mask(np.zeros(0, np.float32), np.zeros(0, np.float32)).shape == (0,)
    talking = speech(10, [(2, 6)], seed=4)
    assert leakage_mask(talking, silent)[frames_in([(2, 6)], 500)].mean() > 0.95


def test_apply_mask_zeroes_dropped_frames_and_fades_edges():
    audio = np.ones(10 * FRAME, np.float32)
    mask = np.array([False, True, True, True, False, False, True, True, True, True])

    out = apply_mask(audio, mask)

    assert not out[:FRAME].any()
    assert out[FRAME] == 0.0
    assert 0.0 < out[FRAME + 80] < 1.0
    assert np.all(out[2 * FRAME : 3 * FRAME] == 1.0)
    assert out[4 * FRAME - 1] < 0.01
    assert not out[4 * FRAME : 6 * FRAME].any()
    # A run reaching the end of the audio has no fade-out.
    assert out[-1] == 1.0


def with_dips(seconds, dips):
    audio = np.full(int(seconds * SR), 0.1, np.float32)
    for at in dips:
        audio[int(at * SR) : int((at + 0.1) * SR)] = 0.0
    return audio


def test_split_long_region_cuts_at_quietest_point_between_15_and_20_s():
    audio = with_dips(46, [17.0, 36.0])

    regions = split_long_regions([(0.0, 45.0), (45.0, 46.0)], audio)

    assert [(round(a, 2), round(b, 2)) for a, b in regions] == [
        (0.0, 17.0),
        (17.0, 36.0),
        (36.0, 45.0),
        (45.0, 46.0),
    ]


def test_interjection_cut_lands_on_quiet_frame_and_respects_minimums():
    audio = with_dips(30, [4.8])
    others = [(0.5, 0.8), (5.0, 5.4), (5.2, 5.6), (9.5, 12.0)]

    regions = cut_at_interjections([(0.0, 10.0), (20.0, 20.15)], others, audio)

    # 0.5 and 9.5 are within a second of the region's edges, 5.2 lands on the
    # same quiet frame as 5.0, and the 0.15 s region is too short to keep.
    assert [(round(a, 2), round(b, 2)) for a, b in regions] == [
        (0.0, 4.8),
        (4.8, 10.0),
    ]



def room_echo(system, delay_seconds, gain_db, seed=7):
    """System audio as a laptop mic hears it: delayed, quieter, and smeared
    by a decaying room response."""
    rng = np.random.default_rng(seed)
    taps = int(0.15 * SR)
    response = rng.normal(0, 1, taps) * np.exp(-np.arange(taps) / (0.03 * SR))
    response *= 10 ** (gain_db / 20) / np.sqrt(np.sum(response**2))
    echo = np.convolve(system, response)[: len(system)]
    return delayed(echo.astype(np.float32), delay_seconds, 0) if delay_seconds else echo.astype(np.float32)


def level_db(audio, spans):
    selected = np.concatenate([audio[int(a * SR) : int(b * SR)] for a, b in spans])
    return 10 * np.log10(np.mean(np.square(selected)) + 1e-12)


ECHO_THEIRS = [(1, 7), (9, 15), (19, 26), (30, 37)]
ECHO_MINE = [(7.5, 8.8), (15.5, 18.5), (26.5, 29.5)]


def test_echo_cancellation_removes_loud_reverberant_speaker_leakage():
    system = speech(40, ECHO_THEIRS, seed=3)
    mine = speech(40, ECHO_MINE, level=0.03, seed=2)
    mic = mine + room_echo(system, 0.04, -14) + floor_noise(40, db=-70)

    cleaned = cancel_echo(mic, system)

    assert len(cleaned) == len(mic)
    assert level_db(mic, ECHO_THEIRS) - level_db(cleaned, ECHO_THEIRS) > 20
    assert abs(level_db(cleaned, ECHO_MINE) - level_db(mine, ECHO_MINE)) < 1.5


def test_echo_cancellation_handles_bluetooth_latency():
    system = speech(40, ECHO_THEIRS, seed=3)
    mic = room_echo(system, 0.25, -20) + floor_noise(40, db=-70)

    cleaned = cancel_echo(mic, system, delay=int(0.25 * SR))

    assert level_db(mic, ECHO_THEIRS) - level_db(cleaned, ECHO_THEIRS) > 20


def test_echo_cancellation_leaves_mic_alone_without_leakage():
    system = speech(20, [(1, 6), (9, 14)], seed=3)
    mic = speech(20, [(6.5, 8.5), (14.5, 18)], level=0.03, seed=2) + floor_noise(20, db=-70)

    cleaned = cancel_echo(mic, system)

    assert abs(level_db(cleaned, [(6.5, 8.5), (14.5, 18)]) - level_db(mic, [(6.5, 8.5), (14.5, 18)])) < 0.5
    assert np.allclose(cancel_echo(mic, np.zeros_like(mic)), mic, atol=1e-3)
    assert len(cancel_echo(np.zeros(100, np.float32), np.zeros(100, np.float32))) == 100


def test_estimate_lag_finds_the_leakage_delay():
    system = with_word_pauses(speech(30, ECHO_THEIRS[:3], seed=3))
    mic = speech(30, ECHO_MINE, level=0.03, seed=2) + delayed(system, 0.26, -20) + floor_noise(30, db=-75)

    assert abs(estimate_lag(mic, system) * FRAME / SR - 0.26) <= 0.02
