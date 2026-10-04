import subprocess

import numpy as np

from stt_local.speech import (
    SENTENCE_PAUSE_SECONDS,
    SpeechManager,
    shape_pauses,
    split_sentences,
    to_pcm,
)


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


class FakeStdin:
    def __init__(self, player):
        self.player = player
        self.written = b""
        self.closed = False

    def write(self, data):
        if self.player.terminated or self.closed:
            raise BrokenPipeError
        self.written += data

    def flush(self):
        pass

    def close(self):
        self.closed = True


class FakePlayer:
    def __init__(self, sample_rate, exit_status=0, hangs=False):
        self.sample_rate = sample_rate
        self.stdin = FakeStdin(self)
        self.terminated = False
        self.killed = False
        self.hangs = hangs
        self.exit_status = exit_status
        self.returncode = None
        self.wait_timeouts = []

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_timeouts.append(timeout)
        if self.hangs and not self.killed:
            raise subprocess.TimeoutExpired("player", timeout)
        if self.returncode is None:
            self.returncode = self.exit_status
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9


class InlineThread:
    def __init__(self, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


def tone(seconds=0.1, lead=0.3, trail=0.45):
    rate = 24_000
    return np.concatenate(
        [
            np.zeros(int(rate * lead), np.float32),
            np.full(int(rate * seconds), 0.5, np.float32),
            np.zeros(int(rate * trail), np.float32),
        ]
    )


def audio(seconds=0.1):
    return {"type": "audio", "samples": tone(seconds)}


def make_manager(
    tmp_path, responses, errors=None, thread_factory=InlineThread, **player_options
):
    context = FakeContext([responses])
    timers = []
    players = []

    def timer_factory(interval, callback):
        timer = FakeTimer(interval, callback)
        timers.append(timer)
        return timer

    def player_factory(sample_rate):
        player = FakePlayer(sample_rate, **player_options)
        players.append(player)
        return player

    manager = SpeechManager(
        context=context,
        timer_factory=timer_factory,
        thread_factory=thread_factory,
        player_factory=player_factory,
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


def test_to_pcm_writes_clipped_16_bit_little_endian():
    pcm = to_pcm(np.array([0.0, 2.0, -2.0], np.float32))

    assert np.frombuffer(pcm, "<i2").tolist() == [0, 32767, -32767]


def test_shape_pauses_replaces_kokoro_edge_silence_with_fixed_pause():
    shaped = shape_pauses(tone(seconds=1.0), 24_000)

    loud = np.flatnonzero(shaped)
    assert loud[0] == int(24_000 * 0.02)
    assert len(shaped) - 1 - loud[-1] == int(24_000 * SENTENCE_PAUSE_SECONDS) - int(
        24_000 * 0.02
    )


def test_shape_pauses_drops_silent_sentences():
    assert shape_pauses(np.zeros(2_400, np.float32), 24_000).size == 0


def test_speak_streams_every_sentence_through_one_player(tmp_path):
    manager, context, timers, players = make_manager(
        tmp_path, [{"type": "ready"}, audio(), audio(), {"type": "done"}]
    )

    manager.speak("Hello. Goodbye.")

    assert context.connections[0].sent == [
        {"command": "speak", "text": "Hello. Goodbye.", "voice": "af_heart"}
    ]
    assert len(players) == 1
    player = players[0]
    assert player.sample_rate == 24_000
    expected = to_pcm(shape_pauses(tone(), 24_000)) * 2
    assert player.stdin.written == expected
    assert player.stdin.closed and player.returncode == 0
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
    assert len(players[0].stdin.written) == len(to_pcm(shape_pauses(tone(), 24_000)))
    assert context.connections[0].sent[1:] == [{"command": "stop"}]


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


def test_player_failure_is_reported(tmp_path):
    errors = []
    manager, _, _, players = make_manager(
        tmp_path, [{"type": "ready"}, audio(), {"type": "done"}], errors, exit_status=1
    )

    manager.speak("Hello.")

    assert errors == ["Speech playback failed (player exit status 1)"]
    assert manager.is_running


def test_stuck_player_is_killed_after_its_audio_plus_grace(tmp_path):
    errors = []
    manager, _, timers, players = make_manager(
        tmp_path, [{"type": "ready"}, audio(), {"type": "done"}], errors, hangs=True
    )

    manager.speak("Hello.")

    player = players[0]
    assert player.killed
    seconds = len(shape_pauses(tone(), 24_000)) / 24_000
    assert abs(player.wait_timeouts[0] - (seconds + 10.0)) < 1e-6
    assert errors == ["Speech playback failed (player exit status -9)"]
    assert not timers[-1].cancelled


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
