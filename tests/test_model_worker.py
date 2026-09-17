from unittest.mock import Mock

import numpy as np
import pytest

from local_dictation.model_worker import TranscriptionError, WorkerManager


class FakeEvent:
    def __init__(self):
        self.value = False

    def is_set(self):
        return self.value


class FakeConnection:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []
        self.closed = False

    def send(self, value):
        self.sent.append(value)

    def recv(self):
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self):
        self.started = False
        self.alive = False
        self.join_calls = []
        self.terminated = False

    def start(self):
        self.started = True
        self.alive = True

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.join_calls.append(timeout)
        self.alive = False

    def terminate(self):
        self.terminated = True
        self.alive = False


class FakeContext:
    def __init__(self, response_batches):
        self.response_batches = list(response_batches)
        self.parent_connections = []
        self.processes = []
        self.events = []

    def Pipe(self):
        parent = FakeConnection(self.response_batches.pop(0))
        child = object()
        self.parent_connections.append(parent)
        return parent, child

    def Event(self):
        event = FakeEvent()
        self.events.append(event)
        return event

    def Process(self, **_kwargs):
        process = FakeProcess()
        self.processes.append(process)
        return process


class FakeTimer:
    def __init__(self, interval, callback):
        self.interval = interval
        self.callback = callback
        self.cancelled = False
        self.started = False
        self.daemon = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


def make_manager(response_batches=None, idle_seconds=10):
    context = FakeContext(response_batches or [[{"type": "ready"}]])
    timers = []

    def timer_factory(interval, callback):
        timer = FakeTimer(interval, callback)
        timers.append(timer)
        return timer

    manager = WorkerManager(
        context=context,
        timer_factory=timer_factory,
        idle_seconds=idle_seconds,
    )
    return manager, context, timers


def test_ensure_started_reuses_live_worker():
    manager, context, _ = make_manager()

    manager.ensure_started()
    manager.ensure_started()

    assert len(context.processes) == 1
    assert context.processes[0].started
    assert manager.is_running


def test_ready_state_comes_from_worker_event():
    manager, context, _ = make_manager()
    manager.ensure_started()
    assert not manager.is_ready
    context.events[0].value = True
    assert manager.is_ready


def test_schedule_and_cancel_idle_shutdown():
    manager, _, timers = make_manager(idle_seconds=42)

    manager.schedule_idle_shutdown()

    assert timers[0].interval == 42
    assert timers[0].started
    assert timers[0].daemon

    manager.cancel_idle_shutdown()
    assert timers[0].cancelled


def test_shutdown_joins_worker_and_clears_state():
    manager, context, _ = make_manager()
    manager.ensure_started()

    manager.shutdown()

    assert not manager.is_running
    assert context.parent_connections[0].sent == [{"command": "shutdown"}]
    assert context.processes[0].join_calls
    assert context.parent_connections[0].closed


def test_transcribe_sends_audio_and_returns_result():
    manager, context, timers = make_manager(
        [[{"type": "ready"}, {"type": "result", "text": "hello"}]]
    )
    audio = np.array([0.1, 0.2], dtype=np.float32)

    assert manager.transcribe(audio) == "hello"

    message = context.parent_connections[0].sent[0]
    assert message["command"] == "transcribe"
    assert np.array_equal(message["audio"], audio)
    assert timers[0].started


def test_first_connection_failure_restarts_once():
    manager, context, _ = make_manager(
        [
            [EOFError()],
            [{"type": "ready"}, {"type": "result", "text": "recovered"}],
        ]
    )

    assert manager.transcribe(np.ones(2, dtype=np.float32)) == "recovered"
    assert len(context.processes) == 2


def test_second_connection_failure_raises_transcription_error():
    manager, context, _ = make_manager([[EOFError()], [EOFError()]])

    with pytest.raises(TranscriptionError, match="failed after retry"):
        manager.transcribe(np.ones(2, dtype=np.float32))

    assert len(context.processes) == 2


def test_worker_error_is_retried_then_reported():
    manager, _, _ = make_manager(
        [
            [{"type": "error", "error": "bad model"}],
            [{"type": "error", "error": "still bad"}],
        ]
    )

    with pytest.raises(TranscriptionError, match="still bad"):
        manager.transcribe(np.ones(2, dtype=np.float32))
