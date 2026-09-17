# STT Local Design

## Purpose

Build a small, local-only macOS menu-bar dictation app that replaces the currently used feature-heavy application. BetterTouchTool toggles recording by touching `/tmp/stt-toggle`. The app records from the current default microphone, transcribes the complete recording with MLX Whisper large-v3-turbo, optionally post-processes the result with a selected Python processor, copies it to the clipboard, and pastes it into the application that has focus when processing finishes.

Accuracy is the primary requirement. The app must not manually split or stitch Whisper transcripts. It fixes recognition to English and does not use a vocabulary-bias prompt.

## Platform and runtime

- macOS 14 or newer on Apple silicon; the development target is an 18 GB M3 Pro running macOS 26.
- Python 3.11 or newer, managed with `uv`.
- `mlx-whisper` with `mlx-community/whisper-large-v3-turbo`.
- `rumps` for the status item and menu, PyObjC/AppKit for the settings window, `sounddevice` for capture, and `pynput` for the synthetic paste.
- All audio and transcription remain on the Mac. Audio stays in memory and is not written to disk.

## User flow

1. BetterTouchTool touches `/tmp/stt-toggle`.
2. When idle, the app removes the trigger file, plays the start sound, starts microphone capture, and starts loading and warming the Whisper worker concurrently.
3. A second trigger removes the file, stops microphone capture, and plays the stop sound immediately.
4. A background thread waits for the already-started worker if necessary and transcribes the complete recording as one input.
5. The selected Python processor transforms the transcript. If it fails or returns an invalid value, the app reports the error and uses the raw transcript.
6. The app puts the final text on the clipboard and sends Command-V once.
7. The app returns to idle. The model remains resident for follow-up dictation and is unloaded after 10 minutes without recording or transcription activity.

No partial transcript is displayed or pasted. Switching applications while speaking is supported; paste targets whichever application is focused when transcription finishes.

## State and concurrency

The coordinator owns the states `idle`, `recording`, `stopping`, and `transcribing`. Only `idle` accepts a start request and only `recording` accepts a stop request. Triggers received during stopping or transcription are ignored.

The audio callback only copies frames into a recording-local list. Potentially blocking PortAudio stop/close calls and all model work happen off the AppKit main thread. UI state changes are dispatched back to the main thread.

Model inference runs in a dedicated `multiprocessing` worker. Starting a recording calls `ensure_started()`, which creates the worker once and returns immediately. The worker loads the configured model, performs a silent warm-up inference, then accepts transcription commands over a multiprocessing pipe. If recording stops before warm-up completes, the transcription request waits in the pipe until the worker is ready.

The worker remains alive after transcription. A 10-minute idle timer starts only after transcription completes; recording cancels it. Timer expiry sends a shutdown command and then terminates the process if it does not exit promptly. Process termination is the memory-release boundary, ensuring MLX and Metal allocations return to the operating system. App quit also stops the worker.

## Components

- `app.py`: rumps application, trigger polling, menu actions, state presentation, and top-level composition.
- `coordinator.py`: recording/transcription state machine and background workflow.
- `audio.py`: default-device refresh, `sounddevice.InputStream`, frame collection, and trailing-silence trimming.
- `model_worker.py`: multiprocessing protocol, Whisper warm-up, full-recording transcription, idle shutdown, and crash recovery.
- `processors.py`: processor discovery, validation, execution, template creation, and selection persistence.
- `settings.py`: native AppKit window for selecting, creating, reloading, and revealing processors.
- `config.py`: paths and atomic JSON configuration persistence under `~/Library/Application Support/STT Local`.
- `output.py`: clipboard write and Command-V injection.
- `sounds.py`: non-blocking playback of distinct bundled/system start and stop sounds.

Each component has a narrow interface and can be tested with injected collaborators. AppKit, PortAudio, MLX, and keyboard integration stay behind adapters so unit tests do not require permissions or hardware.

## Python processors

Processors live in `~/Library/Application Support/STT Local/processors`. Each `.py` file exports:

```python
def process(text: str) -> str:
    return text
```

The built-in `Plain Text` option returns the transcript unchanged and requires no file. A processor filename supplies its display name after converting underscores and hyphens to spaces. Files beginning with `_` are ignored.

The settings window contains a processor pop-up, `New Processor…`, `Reload`, and `Show in Finder` controls. Creating a processor asks for a name, writes a safe starter template only if the target does not exist, selects it, and reveals it in Finder. Selection is stored in `config.json`. Processor exceptions, missing `process`, and non-string results generate a notification and fall back to raw text.

Processor files are explicitly trusted local code and execute in the app's transcription background thread. The UI explains this trust boundary.

## Status and sounds

The menu-bar title and menu status show Idle, Recording, Loading Model, Transcribing, or Error. Start and stop sounds play at the recording boundaries, not at inference boundaries, so the feedback matches when the microphone begins and stops listening. Sound playback must never delay capture or stream shutdown.

## Error handling

- No captured frames: return to idle without invoking Whisper or pasting.
- Microphone start failure: stop any partial stream, show an error notification, and return to idle.
- Worker startup or inference failure: restart once and retry the same in-memory audio. A second failure reports the error and preserves the clipboard.
- Clipboard or paste failure: keep the transcription on the clipboard when possible and notify the user.
- Processor failure: paste the raw transcript and notify the user.
- Empty transcript: return to idle without modifying the clipboard.
- Trigger file cleanup is best-effort at startup and after every observed trigger.

## Configuration

Version-one configuration contains only the selected processor and idle unload duration. The default idle duration is 600 seconds. The model name, sample rate, trigger path, and English language are application constants rather than exposed settings.

## Testing

Unit tests cover:

- legal and ignored coordinator transitions;
- starting capture and worker loading concurrently;
- stop behavior, processor fallback, and single paste;
- trailing-silence trimming;
- worker reuse, idle-timer cancellation, idle shutdown, and one crash retry;
- processor discovery, creation, validation, and persisted selection;
- atomic configuration load/save and corrupt-file fallback.

Integration tests use fake audio and a fake worker; they exercise the complete toggle-to-paste flow without microphone, Accessibility, AppKit, or model access. A manual development check records real speech, confirms the sounds and menu states, verifies paste targeting after switching apps, and observes worker memory disappearing after a shortened idle timeout. The actual model is smoke-tested on a short recording before installation is considered complete.

## Repository and delivery

The repository is `~/repos/stt-local`. It starts with this design and the implementation produced from it; the supplied reference script is not committed as a baseline. Generated audio, model weights, caches, virtual environments, and user configuration are ignored. The README documents `uv sync`, launch, permissions, BetterTouchTool configuration, processor authoring, tests, and the development installation workflow.
