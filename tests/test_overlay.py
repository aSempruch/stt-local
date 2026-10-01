import pytest

from stt_local import overlay
from stt_local.coordinator import DictationState


def test_overlay_mode_follows_workflow_state():
    assert overlay.overlay_mode(DictationState.IDLE) is None
    assert overlay.overlay_mode(DictationState.ERROR) is None
    assert overlay.overlay_mode(DictationState.RECORDING_LOADING) == "loading"
    assert overlay.overlay_mode(DictationState.RECORDING_READY) == "recording"
    assert overlay.overlay_mode(DictationState.STOPPING) == "transcribing"
    assert overlay.overlay_mode(DictationState.TRANSCRIBING) == "transcribing"


def test_level_to_unit_maps_decibel_range_and_clamps():
    assert overlay.level_to_unit(0.0) == 0.0
    assert overlay.level_to_unit(1e-4) == 0.0
    assert overlay.level_to_unit(1.0) == 1.0
    quiet, loud = overlay.level_to_unit(0.01), overlay.level_to_unit(0.1)
    assert 0.0 < quiet < loud < 1.0


def test_recording_heights_right_align_recent_levels():
    heights = overlay.recording_heights([0.0, 1.0], bars=4)

    assert len(heights) == 4
    assert heights[:3] == [0.0, 0.0, 0.0]
    assert heights[3] == 1.0


def test_recording_heights_keep_only_latest_levels():
    heights = overlay.recording_heights([1.0, 1.0, 0.0], bars=2)

    assert heights == [1.0, 0.0]


def test_transcribing_heights_animate_within_bounds():
    first = overlay.transcribing_heights(0.0, bars=8)
    later = overlay.transcribing_heights(0.5, bars=8)

    assert len(first) == 8
    assert first != later
    assert all(0.0 <= value <= 1.0 for value in first + later)


def test_panel_frame_centers_above_bottom_of_visible_area():
    x, y, width, height = overlay.panel_frame((100, 50, 1000, 800), (150, 36))

    assert (width, height) == (150, 36)
    assert x == pytest.approx(100 + (1000 - 150) / 2)
    assert y == 50 + overlay.BOTTOM_MARGIN


def test_level_to_unit_scales_against_given_ceiling():
    assert overlay.level_to_unit(10 ** (-36 / 20), ceiling_db=-36.0) == pytest.approx(1.0)
    assert overlay.level_to_unit(10 ** (-36 / 20)) < 1.0


def test_adaptive_ceiling_follows_recent_peak_within_bounds():
    def rms(db):
        return 10 ** (db / 20)

    assert overlay.adaptive_ceiling([rms(-60), rms(-36), rms(-50)]) == pytest.approx(-36.0)
    # Silence must not stretch room noise to full height.
    assert overlay.adaptive_ceiling([rms(-58)]) == overlay.MIN_CEILING_DB
    assert overlay.adaptive_ceiling([rms(-3)]) == overlay.MAX_CEILING_DB
    assert overlay.adaptive_ceiling([]) == overlay.MIN_CEILING_DB


def test_progress_fill_width_is_bounded_and_never_empty():
    assert overlay.progress_fill_width(0.0, 100) == overlay.TRACK_HEIGHT
    assert overlay.progress_fill_width(0.5, 100) == 50
    assert overlay.progress_fill_width(2.0, 100) == 100


def test_download_progress_replaces_waveform_while_set():
    pill = overlay.DictationOverlay(lambda _count: [])

    pill.set_download_progress(0.4)
    assert pill._download_progress == 0.4
    pill.set_download_progress(None)
    assert pill._download_progress is None
