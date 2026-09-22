import threading
import time
from unittest.mock import Mock

import numpy as np
import pytest

from stt_local.audio import AudioRecorder, trim_trailing_silence


def test_trim_removes_only_trailing_silence():
    audio = np.concatenate(
        [np.ones(1600, dtype=np.float32) * 0.2, np.zeros(1600, dtype=np.float32)]
    )

    result = trim_trailing_silence(audio, sample_rate=1600, pad_seconds=0.25)

    assert len(result) == 2000
    assert np.all(result[:1600] == np.float32(0.2))


def test_trim_keeps_all_silence_input_unchanged():
    audio = np.zeros(100, dtype=np.float32)
    assert np.array_equal(trim_trailing_silence(audio, 100), audio)


class FakeStream:
    def __init__(self, callback):
        self.callback = callback
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True

    def emit(self, values):
        source = np.asarray(values, dtype=np.float32).reshape(-1, 1)
        self.callback(source, len(source), None, None)
        source[:] = -99


@pytest.fixture
def recorder_parts():
    streams = []
    kwargs_seen = []

    def stream_factory(**kwargs):
        kwargs_seen.append(kwargs)
        stream = FakeStream(kwargs["callback"])
        streams.append(stream)
        return stream

    refresh = Mock()
    recorder = AudioRecorder(stream_factory=stream_factory, refresh_devices=refresh)
    return recorder, streams, kwargs_seen, refresh


def test_start_refreshes_devices_and_opens_mono_float_stream(recorder_parts):
    recorder, streams, kwargs_seen, refresh = recorder_parts

    recorder.start()

    refresh.assert_called_once_with()
    assert streams[0].started
    assert kwargs_seen[0]["samplerate"] == 16_000
    assert kwargs_seen[0]["channels"] == 1
    assert kwargs_seen[0]["dtype"] == "float32"


def test_stop_returns_copied_flat_audio_and_closes_stream(recorder_parts):
    recorder, streams, _, _ = recorder_parts
    recorder.start()
    streams[0].emit([0.1, 0.2])
    streams[0].emit([0.3])

    result = recorder.stop()

    assert np.allclose(result, [0.1, 0.2, 0.3])
    assert streams[0].stopped
    assert streams[0].closed
    assert not recorder.is_recording


def test_abort_closes_and_discards_audio(recorder_parts):
    recorder, streams, _, _ = recorder_parts
    recorder.start()
    streams[0].emit([0.1])

    recorder.abort()

    assert streams[0].stopped
    assert streams[0].closed
    assert not recorder.is_recording


def test_start_rejects_overlapping_recording(recorder_parts):
    recorder, _, _, _ = recorder_parts
    recorder.start()

    with pytest.raises(RuntimeError, match="already recording"):
        recorder.start()


def test_stop_without_recording_returns_empty_audio(recorder_parts):
    recorder, _, _, _ = recorder_parts
    assert recorder.stop().size == 0


def test_start_times_out_without_blocking_the_app():
    unblock = threading.Event()
    finished = threading.Event()
    streams = []

    def stream_factory(**kwargs):
        unblock.wait()
        stream = FakeStream(kwargs["callback"])
        streams.append(stream)
        return stream

    recorder = AudioRecorder(
        stream_factory=stream_factory,
        refresh_devices=Mock(),
        start_timeout=0.05,
    )
    started = time.monotonic()

    with pytest.raises(TimeoutError, match="microphone"):
        recorder.start()

    assert time.monotonic() - started < 1.0
    assert not recorder.is_recording
    assert recorder.needs_restart
    unblock.set()
    for _ in range(100):
        if streams and streams[0].closed:
            finished.set()
            break
        time.sleep(0.01)
    assert finished.is_set()


class HangingStream(FakeStream):
    """Simulates a CoreAudio stream.stop() that never returns."""

    def __init__(self, callback, unblock: threading.Event):
        super().__init__(callback)
        self._unblock = unblock

    def stop(self):
        self._unblock.wait()
        super().stop()


def test_stop_recovers_when_stream_stop_hangs():
    unblock = threading.Event()
    streams: list[HangingStream] = []

    def stream_factory(**kwargs):
        stream = HangingStream(kwargs["callback"], unblock)
        streams.append(stream)
        return stream

    recorder = AudioRecorder(
        stream_factory=stream_factory,
        refresh_devices=Mock(),
        stop_timeout=0.05,
    )
    recorder.start()
    streams[0].emit([0.1, 0.2])

    started = time.monotonic()
    result = recorder.stop()
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert np.allclose(result, [0.1, 0.2])
    assert not recorder.is_recording
    assert not streams[0].stopped  # the hung native call never completed
    assert recorder.needs_restart

    unblock.set()  # release the abandoned background thread


def test_abort_recovers_when_stream_stop_hangs():
    unblock = threading.Event()
    streams: list[HangingStream] = []

    def stream_factory(**kwargs):
        stream = HangingStream(kwargs["callback"], unblock)
        streams.append(stream)
        return stream

    recorder = AudioRecorder(
        stream_factory=stream_factory,
        refresh_devices=Mock(),
        stop_timeout=0.05,
    )
    recorder.start()

    started = time.monotonic()
    recorder.abort()
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert not recorder.is_recording
    assert recorder.needs_restart

    unblock.set()
