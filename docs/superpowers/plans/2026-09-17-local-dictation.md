# Local Dictation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and install a local macOS menu-bar dictation app that records from a BetterTouchTool file trigger, preloads Whisper during recording, applies a selected Python processor, and pastes the final transcript.

**Architecture:** A rumps/AppKit main process owns capture, UI, configuration, processors, and paste behavior. A spawned multiprocessing worker owns MLX Whisper so a 10-minute idle shutdown reliably releases model and Metal memory. Full recordings are transcribed without manual chunking or stitching.

**Tech Stack:** Python 3.11+, uv, mlx-whisper, NumPy, sounddevice, rumps/PyObjC, pynput, pytest

**Spec:** `docs/superpowers/specs/2026-09-17-local-dictation-design.md`

## Global Constraints

- Target macOS 14+ on Apple silicon and Python 3.11+.
- Use `mlx-community/whisper-large-v3-turbo`, force English, and do not provide a vocabulary prompt.
- Transcribe each complete recording as one input; do not manually chunk or stitch transcripts.
- Begin model load/warm-up when recording begins and unload the worker after 600 idle seconds.
- Keep captured audio in memory and local to the Mac.
- Preserve raw transcription when a Python processor fails.
- Do not commit the user-provided reference script, audio, model weights, caches, virtual environments, or user configuration.

## File structure

- `pyproject.toml`: package metadata, runtime dependencies, test dependencies, and `local-dictation` entry point.
- `src/local_dictation/constants.py`: fixed model, sample rate, trigger, poll interval, and idle timeout values.
- `src/local_dictation/config.py`: application-support paths and atomic persisted settings.
- `src/local_dictation/processors.py`: custom processor discovery, creation, selection, validation, and execution.
- `src/local_dictation/audio.py`: capture adapter and trailing-silence trim.
- `src/local_dictation/model_worker.py`: worker protocol and lifecycle manager.
- `src/local_dictation/coordinator.py`: state machine and full dictation workflow.
- `src/local_dictation/output.py`: clipboard and synthetic paste adapter.
- `src/local_dictation/sounds.py`: asynchronous start/stop sound adapter.
- `src/local_dictation/settings.py`: AppKit processor settings controller.
- `src/local_dictation/app.py`: rumps status app and dependency composition.
- `src/local_dictation/__main__.py`: multiprocessing-safe entry point.
- `tests/`: focused unit and integration tests matching the modules above.
- `scripts/install-launch-agent.sh`: idempotent per-user development installation.
- `README.md`: setup, permissions, BetterTouchTool, processors, testing, installation, and troubleshooting.

---

### Task 1: Project foundation, configuration, and processors

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/local_dictation/__init__.py`
- Create: `src/local_dictation/constants.py`
- Create: `src/local_dictation/config.py`
- Create: `src/local_dictation/processors.py`
- Create: `tests/test_config.py`
- Create: `tests/test_processors.py`

**Interfaces:**
- Produces: `AppConfig(selected_processor: str = "plain_text", idle_unload_seconds: float = 600.0)`.
- Produces: `ConfigStore.load() -> AppConfig` and `ConfigStore.save(config: AppConfig) -> None`.
- Produces: `ProcessorRegistry.list_processors() -> list[ProcessorInfo]`, `create(name: str) -> ProcessorInfo`, and `apply(name: str, text: str) -> ProcessorResult`.

- [ ] **Step 1: Write failing configuration tests**

```python
def test_missing_config_returns_defaults(tmp_path):
    assert ConfigStore(tmp_path).load() == AppConfig()

def test_corrupt_config_is_replaced_by_defaults(tmp_path):
    (tmp_path / "config.json").write_text("not-json")
    assert ConfigStore(tmp_path).load() == AppConfig()

def test_save_is_round_trippable(tmp_path):
    store = ConfigStore(tmp_path)
    expected = AppConfig(selected_processor="sentence_case", idle_unload_seconds=30)
    store.save(expected)
    assert store.load() == expected
```

- [ ] **Step 2: Run configuration tests and verify import failures**

Run: `uv run pytest tests/test_config.py -q`
Expected: collection fails because `local_dictation.config` does not exist.

- [ ] **Step 3: Implement constants and atomic configuration persistence**

Use a frozen dataclass. Write JSON to a sibling temporary file, flush it, and replace `config.json` atomically. Invalid values fall back to defaults without deleting the corrupt file.

- [ ] **Step 4: Run configuration tests**

Run: `uv run pytest tests/test_config.py -q`
Expected: all configuration tests pass.

- [ ] **Step 5: Write failing processor tests**

```python
def test_plain_text_is_always_available(tmp_path):
    assert [p.key for p in ProcessorRegistry(tmp_path).list_processors()] == ["plain_text"]

def test_create_writes_importable_template(tmp_path):
    info = ProcessorRegistry(tmp_path).create("Sentence Case")
    assert info.key == "sentence_case"
    assert "def process(text: str) -> str:" in info.path.read_text()

def test_processor_exception_falls_back_to_raw_text(tmp_path):
    (tmp_path / "broken.py").write_text("def process(text):\n    raise RuntimeError('bad')\n")
    result = ProcessorRegistry(tmp_path).apply("broken", "raw")
    assert result.text == "raw"
    assert result.error is not None
```

- [ ] **Step 6: Run processor tests and verify failures**

Run: `uv run pytest tests/test_processors.py -q`
Expected: failures because `ProcessorRegistry` is absent.

- [ ] **Step 7: Implement processor discovery, safe creation, and fallback execution**

Normalize a new name to lowercase ASCII letters, digits, and underscores; reject an empty result and existing files. Load each selected module with `importlib.util.spec_from_file_location`, require a callable `process`, and require a string result.

- [ ] **Step 8: Run Task 1 tests and commit**

Run: `uv run pytest tests/test_config.py tests/test_processors.py -q`
Expected: all pass.

Commit: `git add .gitignore pyproject.toml src tests && git commit -m "feat: add configuration and processors"`

### Task 2: Audio capture and trimming

**Files:**
- Create: `src/local_dictation/audio.py`
- Create: `tests/test_audio.py`

**Interfaces:**
- Produces: `trim_trailing_silence(audio: np.ndarray, sample_rate: int, pad_seconds: float = 0.3) -> np.ndarray`.
- Produces: `AudioRecorder.start() -> None`, `AudioRecorder.stop() -> np.ndarray`, and `AudioRecorder.abort() -> None`.

- [ ] **Step 1: Write failing pure trimming tests**

```python
def test_trim_removes_only_trailing_silence():
    audio = np.concatenate([np.ones(1600, dtype=np.float32) * 0.2, np.zeros(1600, dtype=np.float32)])
    result = trim_trailing_silence(audio, sample_rate=1600, pad_seconds=0.25)
    assert len(result) == 2000

def test_trim_keeps_all_silence_input_unchanged():
    audio = np.zeros(100, dtype=np.float32)
    assert np.array_equal(trim_trailing_silence(audio, 100), audio)
```

- [ ] **Step 2: Run trimming tests and verify import failure**

Run: `uv run pytest tests/test_audio.py -q`
Expected: collection fails because `local_dictation.audio` does not exist.

- [ ] **Step 3: Implement trimming and injectable recorder**

`AudioRecorder` accepts a stream factory for tests. Production start refreshes PortAudio with `sd._terminate()` and `sd._initialize()`, opens the current default mono input at 16 kHz float32, and copies callback frames. Stop detaches the current stream and frame list before making blocking `stop()` and `close()` calls.

- [ ] **Step 4: Add recorder lifecycle tests using a fake stream**

Assert that start clears old frames, callback data is copied, stop returns a flat independent array, and abort closes without returning audio.

- [ ] **Step 5: Run and commit Task 2**

Run: `uv run pytest tests/test_audio.py -q`
Expected: all pass.

Commit: `git add src/local_dictation/audio.py tests/test_audio.py && git commit -m "feat: add microphone capture"`

### Task 3: Resident Whisper worker with deterministic unload

**Files:**
- Create: `src/local_dictation/model_worker.py`
- Create: `tests/test_model_worker.py`

**Interfaces:**
- Produces: `WorkerManager.ensure_started() -> None`, `transcribe(audio: np.ndarray) -> str`, `cancel_idle_shutdown() -> None`, `schedule_idle_shutdown() -> None`, and `shutdown() -> None`.
- Produces worker commands `transcribe` and `shutdown`; the worker sends `ready`, `result`, and `error` messages.

- [ ] **Step 1: Write failing lifecycle tests with injected process, pipe, and timer factories**

```python
def test_ensure_started_reuses_live_worker(manager, process_factory):
    manager.ensure_started()
    manager.ensure_started()
    assert process_factory.call_count == 1

def test_recording_cancels_idle_shutdown(manager, timer):
    manager.schedule_idle_shutdown()
    manager.cancel_idle_shutdown()
    timer.cancel.assert_called_once()

def test_idle_shutdown_releases_process(manager, process):
    manager.ensure_started()
    manager.shutdown()
    assert not manager.is_running
    process.join.assert_called()
```

- [ ] **Step 2: Run worker tests and verify import failure**

Run: `uv run pytest tests/test_model_worker.py -q`
Expected: collection fails because `local_dictation.model_worker` does not exist.

- [ ] **Step 3: Implement worker entry point and warm-up**

Inside the child only, import `mlx_whisper`, transcribe 0.1 seconds of zero-valued float32 audio with the configured model, `language="en"`, `condition_on_previous_text=False`, and `verbose=None`, then send `ready`. For real requests, trim trailing silence and transcribe the entire array with identical options.

- [ ] **Step 4: Implement manager lifecycle, timer, and one retry**

Use `multiprocessing.get_context("spawn")`. `transcribe` ensures a worker, sends the NumPy array, waits for `ready` and then `result`, and retries once with a fresh worker if the connection closes or returns an error. A daemon `threading.Timer` invokes shutdown after the configured idle duration.

- [ ] **Step 5: Add retry and protocol tests**

Test that a first connection failure creates exactly one replacement, a second failure raises `TranscriptionError`, and an empty returned text is accepted.

- [ ] **Step 6: Run and commit Task 3**

Run: `uv run pytest tests/test_model_worker.py -q`
Expected: all pass without importing MLX in the test process.

Commit: `git add src/local_dictation/model_worker.py tests/test_model_worker.py && git commit -m "feat: manage resident whisper worker"`

### Task 4: Coordinator, sounds, and output

**Files:**
- Create: `src/local_dictation/coordinator.py`
- Create: `src/local_dictation/output.py`
- Create: `src/local_dictation/sounds.py`
- Create: `tests/test_coordinator.py`
- Create: `tests/test_output.py`

**Interfaces:**
- Produces: `DictationCoordinator.toggle() -> None`, `start_recording() -> None`, `stop_recording() -> None`, and `shutdown() -> None`.
- Consumes: recorder, worker, processors, output, sounds, selected-processor provider, status callback, notification callback, and thread factory.
- Produces states from `DictationState`: `IDLE`, `RECORDING_LOADING`, `RECORDING_READY`, `STOPPING`, `TRANSCRIBING`, `ERROR`.

- [ ] **Step 1: Write failing state-transition tests**

Cover idle-to-recording, recording-to-stopping, ignored triggers in stopping/transcribing, worker start concurrent with capture, start/stop sound ordering, no-frames behavior, and shutdown.

- [ ] **Step 2: Run coordinator tests and verify import failure**

Run: `uv run pytest tests/test_coordinator.py -q`
Expected: collection fails because `local_dictation.coordinator` does not exist.

- [ ] **Step 3: Implement the minimal coordinator state machine**

Start order is: cancel idle shutdown, start recorder, play start sound, call worker `ensure_started`, update recording status. Stop order is: set stopping, play stop sound, detach capture work to a background thread, stop recorder, set transcribing, call worker, apply processor, paste non-empty text, schedule idle shutdown, return idle.

- [ ] **Step 4: Add failure-path integration tests**

Assert microphone failure returns idle, processor failure notifies and pastes raw text once, transcription failure does not alter clipboard, empty output does not paste, and background completion schedules unload.

- [ ] **Step 5: Implement clipboard, paste, and asynchronous system sounds**

`MacOutput.send(text)` invokes `pbcopy`, waits 50 ms, and uses `pynput.keyboard.Controller` for Command-V. `MacSounds` launches `afplay` for `/System/Library/Sounds/Tink.aiff` on start and `/System/Library/Sounds/Pop.aiff` on stop without waiting.

- [ ] **Step 6: Run and commit Task 4**

Run: `uv run pytest tests/test_coordinator.py tests/test_output.py -q`
Expected: all pass.

Commit: `git add src/local_dictation/coordinator.py src/local_dictation/output.py src/local_dictation/sounds.py tests && git commit -m "feat: orchestrate dictation workflow"`

### Task 5: Menu-bar app and processor settings window

**Files:**
- Create: `src/local_dictation/settings.py`
- Create: `src/local_dictation/app.py`
- Create: `src/local_dictation/__main__.py`
- Create: `tests/test_app.py`
- Create: `tests/test_settings_model.py`

**Interfaces:**
- Produces: `DictationApp(rumps.App)` polling `/tmp/stt-toggle` every 0.15 seconds.
- Produces: `SettingsModel.refresh()`, `select(key: str)`, and `create(name: str)` independent of AppKit.
- Consumes the Task 1 registry/store and Task 4 coordinator.

- [ ] **Step 1: Write failing trigger and settings-model tests**

Assert one existing trigger file is unlinked and produces one coordinator toggle, a missing trigger is a no-op, refresh preserves a valid selection, missing selections fall back to plain text, and creation persists and selects the new processor.

- [ ] **Step 2: Run tests and verify import failures**

Run: `uv run pytest tests/test_app.py tests/test_settings_model.py -q`
Expected: collection fails because app/settings modules do not exist.

- [ ] **Step 3: Implement the testable app shell and settings model**

Keep filesystem polling in a standalone `TriggerWatcher.poll() -> bool`. Map coordinator states to short status text and menu-bar titles. Compose real collaborators only in `build_app()` so importing modules in tests does not request microphone or Accessibility permissions.

- [ ] **Step 4: Implement the AppKit settings controller**

Create one retained `NSWindowController` with a processor `NSPopUpButton`, status text explaining trusted Python execution, and New, Reload, and Show in Finder buttons. New uses `rumps.Window` for the name; Show invokes `NSWorkspace.sharedWorkspace().activateFileViewerSelectingURLs_`.

- [ ] **Step 5: Implement multiprocessing-safe entry point and quit cleanup**

Set the start method through the manager's explicit spawn context rather than globally. Guard app construction with `if __name__ == "__main__"`. The Quit menu calls coordinator shutdown before terminating the rumps app.

- [ ] **Step 6: Run and commit Task 5**

Run: `uv run pytest tests/test_app.py tests/test_settings_model.py -q`
Expected: all pass in a non-GUI test session.

Commit: `git add src/local_dictation/settings.py src/local_dictation/app.py src/local_dictation/__main__.py tests && git commit -m "feat: add menu bar interface"`

### Task 6: Documentation, development installation, and end-to-end verification

**Files:**
- Create: `README.md`
- Create: `scripts/install-launch-agent.sh`
- Create: `tests/test_install_script.py`

**Interfaces:**
- Produces: launch command `uv run local-dictation`.
- Produces: per-user LaunchAgent label `com.local-dictation.app` with logs under `~/Library/Logs/Local Dictation`.

- [ ] **Step 1: Write a failing installation-script structure test**

Parse the script and generated plist fixture to assert use of absolute `uv` and repository paths, `RunAtLoad`, the expected label, and no model/audio paths in Git.

- [ ] **Step 2: Run the installation test and verify failure**

Run: `uv run pytest tests/test_install_script.py -q`
Expected: fails because the installation script is absent.

- [ ] **Step 3: Implement idempotent LaunchAgent installation**

The script resolves its repository root, locates `uv`, writes the plist through `/usr/libexec/PlistBuddy`, bootouts an existing per-user service if present, bootstraps the new plist, and prints the trigger command `touch /tmp/stt-toggle`. It does not install system-wide files.

- [ ] **Step 4: Write the README**

Document prerequisites, `uv sync`, `uv run pytest`, direct launch, microphone and Accessibility permissions, BetterTouchTool's touch command, processor API and folder, 10-minute model unloading, LaunchAgent installation, log locations, and removal commands.

- [ ] **Step 5: Run automated verification**

Run: `uv sync --all-groups && uv run pytest -q && uv run python -m compileall -q src tests`
Expected: dependency synchronization succeeds, every test passes, and compilation emits no errors.

- [ ] **Step 6: Run real model smoke test**

Launch the app with `LOCAL_DICTATION_IDLE_SECONDS=10` as a development-only override, touch the trigger, speak a short technical sentence, switch to TextEdit, touch the trigger again, and verify one correct paste. Observe the worker process during recording and confirm it exits about 10 seconds after transcription.

- [ ] **Step 7: Install the development LaunchAgent and verify the deployed flow**

Run: `./scripts/install-launch-agent.sh`, then use `touch /tmp/stt-toggle` twice around a short recording. Confirm sounds, paste, settings window, processor selection, and log output.

- [ ] **Step 8: Commit documentation and installation**

Commit: `git add README.md scripts tests/test_install_script.py uv.lock && git commit -m "docs: add setup and launch agent installation"`

- [ ] **Step 9: Final repository verification**

Run: `git status --short && git log --oneline --decorate -8`
Expected: clean working tree; history begins with the design commit and contains only this app's design and implementation.
