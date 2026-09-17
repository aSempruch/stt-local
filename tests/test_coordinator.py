from unittest.mock import Mock, call

import numpy as np

from stt_local.coordinator import DictationCoordinator, DictationState
from stt_local.processors import ProcessorResult


class ImmediateThread:
    def __init__(self, *, target, daemon=True):
        self.target = target
        self.daemon = daemon

    def start(self):
        self.target()


class HeldThread(ImmediateThread):
    def __init__(self, *, target, daemon=True):
        super().__init__(target=target, daemon=daemon)
        self.started = False

    def start(self):
        self.started = True

    def run(self):
        self.target()


def make_coordinator(*, audio=None, thread_factory=ImmediateThread):
    events = []
    recorder = Mock()
    recorder.stop.return_value = (
        np.asarray(audio, dtype=np.float32)
        if audio is not None
        else np.ones(10, dtype=np.float32)
    )
    worker = Mock()
    worker.is_ready = False
    worker.transcribe.return_value = "raw transcript"
    processors = Mock()
    processors.apply.return_value = ProcessorResult("processed transcript")
    output = Mock()
    sounds = Mock()
    notifications = []
    states = []
    coordinator = DictationCoordinator(
        recorder=recorder,
        worker=worker,
        processors=processors,
        output=output,
        sounds=sounds,
        selected_processor=lambda: "clean_up",
        status_callback=lambda state: states.append(state),
        notification_callback=lambda title, message: notifications.append(
            (title, message)
        ),
        thread_factory=thread_factory,
        event_callback=lambda name: events.append(name),
    )
    return (
        coordinator,
        recorder,
        worker,
        processors,
        output,
        sounds,
        states,
        notifications,
        events,
    )


def test_start_begins_capture_sound_and_worker_loading_in_order():
    parts = make_coordinator()
    coordinator, recorder, worker, _, _, sounds, _, _, events = parts

    coordinator.start_recording()

    assert events == ["cancel_idle", "capture_start", "start_sound", "worker_start"]
    worker.cancel_idle_shutdown.assert_called_once_with()
    recorder.start.assert_called_once_with()
    sounds.play_start.assert_called_once_with()
    worker.ensure_started.assert_called_once_with()
    assert coordinator.state is DictationState.RECORDING_LOADING


def test_refresh_marks_recording_ready_after_worker_warms():
    coordinator, _, worker, *_ = make_coordinator()
    coordinator.start_recording()
    worker.is_ready = True

    coordinator.refresh()

    assert coordinator.state is DictationState.RECORDING_READY


def test_toggle_ignores_requests_while_stop_thread_is_held():
    threads = []

    def thread_factory(**kwargs):
        thread = HeldThread(**kwargs)
        threads.append(thread)
        return thread

    coordinator, recorder, *_ = make_coordinator(thread_factory=thread_factory)
    coordinator.toggle()
    coordinator.toggle()
    assert coordinator.state is DictationState.STOPPING

    coordinator.toggle()

    assert recorder.start.call_count == 1
    threads[0].run()


def test_stop_transcribes_processes_and_pastes_once():
    parts = make_coordinator(audio=[0.1, 0.2])
    coordinator, _, worker, processors, output, sounds, states, _, _ = parts
    coordinator.start_recording()

    coordinator.stop_recording()

    sounds.play_stop.assert_called_once_with()
    worker.transcribe.assert_called_once()
    processors.apply.assert_called_once_with("clean_up", "raw transcript")
    output.send.assert_called_once_with("processed transcript", press_enter=False)
    worker.schedule_idle_shutdown.assert_called_once_with()
    assert DictationState.TRANSCRIBING in states
    assert coordinator.state is DictationState.IDLE


def test_empty_audio_skips_transcription_and_paste():
    coordinator, _, worker, _, output, *_ = make_coordinator(audio=[])
    coordinator.start_recording()

    coordinator.stop_recording()

    worker.transcribe.assert_not_called()
    output.send.assert_not_called()
    worker.schedule_idle_shutdown.assert_called_once_with()
    assert coordinator.state is DictationState.IDLE


def test_processor_failure_notifies_and_pastes_raw_text():
    parts = make_coordinator()
    coordinator, _, _, processors, output, _, _, notifications, _ = parts
    processors.apply.return_value = ProcessorResult("raw transcript", "processor bad")
    coordinator.start_recording()

    coordinator.stop_recording()

    output.send.assert_called_once_with("raw transcript", press_enter=False)
    assert notifications == [("Processor failed", "processor bad")]


def test_transcription_failure_does_not_paste():
    parts = make_coordinator()
    coordinator, _, worker, _, output, _, _, notifications, _ = parts
    worker.transcribe.side_effect = RuntimeError("model died")
    coordinator.start_recording()

    coordinator.stop_recording()

    output.send.assert_not_called()
    assert notifications == [("Transcription failed", "model died")]
    assert coordinator.state is DictationState.IDLE


def test_microphone_start_failure_returns_to_idle():
    parts = make_coordinator()
    coordinator, recorder, worker, _, _, sounds, _, notifications, _ = parts
    recorder.start.side_effect = RuntimeError("no microphone")

    coordinator.start_recording()

    worker.ensure_started.assert_not_called()
    sounds.play_start.assert_not_called()
    assert notifications == [("Recording failed", "no microphone")]
    assert coordinator.state is DictationState.IDLE


def test_cancel_discards_active_recording_without_transcription():
    parts = make_coordinator()
    coordinator, recorder, worker, _, output, sounds, *_ = parts
    coordinator.start_recording()

    coordinator.cancel()

    recorder.abort.assert_called_once_with()
    worker.transcribe.assert_not_called()
    output.send.assert_not_called()
    sounds.play_stop.assert_called_once_with()
    worker.schedule_idle_shutdown.assert_called_once_with()
    assert coordinator.state is DictationState.IDLE


def test_cancel_during_transcription_kills_worker_without_output_or_error():
    parts = make_coordinator()
    coordinator, _, worker, _, output, _, _, notifications, _ = parts

    def cancel_while_transcribing(_audio):
        coordinator.cancel()
        return "must not paste"

    worker.transcribe.side_effect = cancel_while_transcribing
    coordinator.start_recording()

    coordinator.stop_recording()

    worker.cancel.assert_called_once_with()
    output.send.assert_not_called()
    assert notifications == []
    assert coordinator.state is DictationState.IDLE


def test_submit_pastes_then_presses_enter():
    parts = make_coordinator()
    coordinator, _, _, _, output, *_ = parts
    coordinator.start_recording()

    coordinator.stop_recording(submit=True)

    output.send.assert_called_once_with("processed transcript", press_enter=True)


def test_handle_command_dispatches_and_reports_unknown_command():
    parts = make_coordinator()
    coordinator, recorder, _, _, _, _, _, notifications, _ = parts

    assert coordinator.handle_command("toggle")
    recorder.start.assert_called_once_with()
    assert not coordinator.handle_command("mystery")
    assert notifications == [("Unknown command", "mystery")]


def test_shutdown_aborts_recording_and_stops_worker():
    coordinator, recorder, worker, *_ = make_coordinator()
    coordinator.start_recording()

    coordinator.shutdown()

    recorder.abort.assert_called_once_with()
    worker.shutdown.assert_called_once_with()
    assert coordinator.state is DictationState.IDLE
