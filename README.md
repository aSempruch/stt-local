# Local Dictation

A deliberately small, fully local macOS menu-bar dictation app. BetterTouchTool toggles recording by touching a file; Local Dictation records the current default microphone, transcribes the complete recording with MLX Whisper large-v3-turbo, optionally runs a trusted Python processor, and pastes the result once.

Accuracy is the priority. Recordings are not split or stitched, audio is never sent to a server, and partial text is never typed while you speak.

## Requirements

- Apple-silicon Mac running macOS 14 or newer
- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/)
- Microphone and Accessibility permission for the Python executable

Install dependencies and run the tests:

```bash
uv sync --all-groups
uv run pytest -q
```

Run directly during development:

```bash
uv run local-dictation
```

The first recording downloads and warms `mlx-community/whisper-large-v3-turbo`. Model loading begins immediately after microphone capture starts. The worker remains available for follow-up dictation and exits after 10 idle minutes so macOS can reclaim all model and Metal memory.

## Permissions

macOS should request Microphone permission on first capture. Pasting also requires Accessibility permission:

1. Open **System Settings → Privacy & Security → Accessibility**.
2. Add or enable this repository's `.venv/bin/python` executable. When running from Terminal during development, enable Terminal as well.
3. Open **Privacy & Security → Microphone** and enable the Python process when prompted.

If capture works but Command-V does not, Accessibility permission is the likely cause. The completed transcript is still copied with `pbcopy` before the paste attempt.

## BetterTouchTool

Configure the desired BetterTouchTool gesture or hotkey to run:

```bash
/usr/bin/touch /tmp/stt-toggle
```

The first touch starts recording and plays the start sound. The second stops recording and plays the stop sound immediately. Triggers received while the previous recording is stopping or transcribing are ignored.

The transcript is pasted into whichever application has focus when transcription finishes, so changing applications while speaking is safe.

## Python processors

Choose **Settings…** from the status-bar menu. The window selects the active processor and provides **New Processor…**, **Reload**, and **Show in Finder** controls.

Processor files live in:

```text
~/Library/Application Support/Local Dictation/processors
```

Each processor is ordinary trusted Python:

```python
def process(text: str) -> str:
    return text
```

Files beginning with `_` are ignored. A missing function, exception, or non-string return value produces a notification and falls back to the unmodified transcript. Processor code is intentionally unsandboxed and should be treated like any other local script you run.

## Install as a per-user service

The installer synchronizes the environment, writes `~/Library/LaunchAgents/com.local-dictation.app.plist`, and starts the status-bar app:

```bash
./scripts/install-launch-agent.sh
```

Logs are written to:

```text
~/Library/Logs/Local Dictation/stdout.log
~/Library/Logs/Local Dictation/stderr.log
```

Restart after source or dependency changes by running the installer again.

To stop and remove the service without deleting configuration or processors:

```bash
launchctl bootout "gui/$(id -u)/com.local-dictation.app"
rm "$HOME/Library/LaunchAgents/com.local-dictation.app.plist"
```

## Development controls

For idle-unload testing only, override the configured timeout when launching directly:

```bash
LOCAL_DICTATION_IDLE_SECONDS=10 uv run local-dictation
```

The persisted configuration is `~/Library/Application Support/Local Dictation/config.json`. The default timeout is 600 seconds. The model, language (`en`), sample rate (16 kHz), and trigger path are fixed application constants.

## Troubleshooting

- **No sound or recording:** Verify Microphone permission and the current macOS default input device.
- **Text copies but does not paste:** Grant Accessibility permission to `.venv/bin/python`.
- **First transcription is slow:** The model may still be downloading or warming. Later recordings reuse the resident worker.
- **Processor is not listed:** Use Reload and confirm the file ends in `.py` and does not begin with `_`.
- **Model RAM remains allocated:** Wait 10 idle minutes or quit Local Dictation; the dedicated model process then exits.
