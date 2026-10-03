import wave

import numpy as np

from stt_local.speech import SpeechManager, split_sentences, write_wav


class FakeEvent:
    def is_set(self):
        return True


class FakeConnection:
    def __init__(self, responses, on_send=None):
        self.responses = list(responses)
        self.sent = []
        self.closed = False
        self.on_send = on_send

    def send(self, value):
        self.sent.append(value)
        if self.on_send is not None:
            self.on_send(value)

    def recv(self):
        response = self.responses.pop(0)
        if callable(response):
            return response()
        if isinstance(response, BaseException):
            raise response
        return response

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self):
        self.alive = False
        self.terminated = False

    def start(self):
        self.alive = True

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.alive = False

    def terminate(self):
        self.terminated = True
        self.alive = False


class FakeContext:
    def __init__(self, response_batches):
        self.response_batches = list(response_batches)
        self.connections = []
        self.processes = []

    def Pipe(self):
        parent = FakeConnection(self.response_batches.pop(0))
        self.connections.append(parent)
        return parent, object()

    def Event(self):
        return FakeEvent()

    def Process(self, **_kwargs):
        process = FakeProcess()
        self.processes.append(process)
        return process


class FakeTimer:
    def __init__(self, interval, callback):
        self.interval = interval
        self.callback = callback
        self.cancelled = False
        self.daemon = False

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


class FakePlayer:
    def __init__(self, path):
        self.path = path
        self.existed = path.exists()
        self.terminated = False
        self.waited = False

    def poll(self):
        return 0 if self.terminated or self.waited else None

    def wait(self):
        self.waited = True

    def terminate(self):
        self.terminated = True


class InlineThread:
    def __init__(self, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


def audio(seconds=0.1):
    return {"type": "audio", "samples": np.zeros(int(24_000 * seconds), np.float32)}


def make_manager(tmp_path, responses, errors=None, thread_factory=InlineThread):
    context = FakeContext([responses])
    timers = []
    players = []

    def timer_factory(interval, callback):
        timer = FakeTimer(interval, callback)
        timers.append(timer)
        return timer

    def player_factory(path):
        player = FakePlayer(path)
        players.append(player)
        return player

    manager = SpeechManager(
        context=context,
        timer_factory=timer_factory,
        thread_factory=thread_factory,
        player_factory=player_factory,
        clip_directory=tmp_path,
        idle_seconds=30,
        on_error=(errors.append if errors is not None else lambda _m: None),
    )
    return manager, context, timers, players


def test_split_sentences_puts_each_sentence_on_its_own():
    assert split_sentences("Done. All tests pass!\nWant me to commit?  ") == [
        "Done.",
        "All tests pass!",
        "Want me to commit?",
    ]


def test_write_wav_writes_mono_16_bit(tmp_path):
    path = tmp_path / "clip.wav"

    write_wav(path, np.array([0.0, 2.0, -2.0], np.float32), 24_000)

    with wave.open(str(path)) as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 24_000
        frames = np.frombuffer(handle.readframes(3), "<i2")
    assert frames.tolist() == [0, 32767, -32767]


def test_speak_plays_each_clip_in_order_then_schedules_unload(tmp_path):
    manager, context, timers, players = make_manager(
        tmp_path, [{"type": "ready"}, audio(), audio(), {"type": "done"}]
    )

    manager.speak("Hello. Goodbye.")

    assert context.connections[0].sent == [
        {"command": "speak", "text": "Hello. Goodbye.", "voice": "af_heart"}
    ]
    assert len(players) == 2
    assert all(player.existed and player.waited for player in players)
    assert not any(player.path.exists() for player in players)
    assert timers[-1].interval == 30 and not timers[-1].cancelled


def test_blank_text_does_not_start_worker(tmp_path):
    manager, context, _, _ = make_manager(tmp_path, [])

    manager.speak("   ")

    assert context.processes == []


def test_stop_during_reply_skips_remaining_clips_and_tells_worker(tmp_path):
    manager = None

    def stop_then_audio():
        manager.stop()
        return audio()

    manager, context, _, players = make_manager(
        tmp_path,
        [{"type": "ready"}, audio(), stop_then_audio, audio(), {"type": "done"}],
    )

    manager.speak("One. Two. Three.")

    assert len(players) == 1
    assert players[0].terminated
    assert context.connections[0].sent[1:] == [{"command": "stop"}]
    assert list(tmp_path.iterdir()) == []


def test_worker_reused_for_later_replies(tmp_path):
    manager, context, _, players = make_manager(
        tmp_path,
        [{"type": "ready"}, audio(), {"type": "done"}, audio(), {"type": "done"}],
    )

    manager.speak("First.")
    manager.speak("Second.")

    assert len(context.processes) == 1
    assert len(players) == 2


def test_load_failure_is_reported_and_worker_stopped(tmp_path):
    errors = []
    manager, context, _, _ = make_manager(
        tmp_path,
        [{"type": "error", "fatal": True, "error": "Couldn't load the voice model"}],
        errors,
    )

    manager.speak("Hello.")

    assert errors == ["Couldn't load the voice model"]
    assert not manager.is_running


def test_synthesis_error_is_reported_after_reply(tmp_path):
    errors = []
    manager, _, _, players = make_manager(
        tmp_path,
        [{"type": "ready"}, audio(), {"type": "error", "error": "Boom"}, {"type": "done"}],
        errors,
    )

    manager.speak("Hello.")

    assert errors == ["Boom"]
    assert len(players) == 1
    assert manager.is_running


def test_crashed_worker_is_reported_and_restarted_next_time(tmp_path):
    errors = []
    manager, context, _, _ = make_manager(tmp_path, [EOFError()], errors)
    context.response_batches.append([{"type": "ready"}, {"type": "done"}])

    manager.speak("Hello.")
    manager.speak("Again.")

    assert errors and "stopped unexpectedly" in errors[0]
    assert len(context.processes) == 2


class DeferredThread:
    started = []

    def __init__(self, target, **_kwargs):
        self.target = target

    def start(self):
        DeferredThread.started.append(self.target)


def test_superseded_request_never_reaches_worker(tmp_path):
    DeferredThread.started = []
    manager, context, _, _ = make_manager(
        tmp_path, [], thread_factory=DeferredThread
    )

    manager.speak("Old.")
    manager.speak("New.")
    DeferredThread.started[0]()

    assert context.processes == []


def test_shutdown_unloads_worker(tmp_path):
    manager, context, timers, _ = make_manager(
        tmp_path, [{"type": "ready"}, {"type": "done"}]
    )
    manager.speak("Hello.")

    timers[-1].callback()

    assert not manager.is_running
    assert context.connections[0].sent[-1] == {"command": "shutdown"}


def test_unexpected_failure_is_reported_not_swallowed(tmp_path):
    errors = []
    manager, context, _, _ = make_manager(tmp_path, [], errors)
    context.Pipe = lambda: (_ for _ in ()).throw(RuntimeError("spawn failed"))

    manager.speak("Hello.")

    assert errors == ["RuntimeError: spawn failed"]
