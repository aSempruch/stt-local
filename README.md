# STT Local

A deliberately small, fully local macOS menu-bar dictation app. BetterTouchTool writes commands to a local mailbox; STT Local records the current default microphone, transcribes the complete recording with MLX Whisper large-v3-turbo, optionally runs a trusted Python processor, and pastes the result once.

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
uv run stt-local
```

The first recording downloads and warms `mlx-community/whisper-large-v3-turbo`. Model loading begins immediately after microphone capture starts. The worker remains available for follow-up dictation and exits after 10 idle minutes so macOS can reclaim all model and Metal memory.

## Permissions

macOS should request Microphone permission on first capture. Pasting also requires Accessibility permission:

1. Open **System Settings → Privacy & Security → Accessibility**.
2. Add or enable `~/repos/stt-local/.venv/bin/python`. When running from Terminal during development, enable Terminal as well.
3. Open **Privacy & Security → Microphone** and enable the Python process when prompted.

If capture works but Command-V does not, Accessibility permission is the likely cause. The completed transcript is still copied with `pbcopy` before the paste attempt.

## BetterTouchTool

The command mailbox is `/tmp/stt-command`. Configure BetterTouchTool gestures or hotkeys with these shell commands:

```bash
# Start recording, or stop and transcribe normally
printf '%s\n' toggle > /tmp/stt-command

# Discard an active recording or cancel an active transcription
printf '%s\n' cancel > /tmp/stt-command

# Stop, transcribe, paste, then press Return
printf '%s\n' submit > /tmp/stt-command
```

Toggle starts recording with a compact double chirp while idle and stops with the Ping cue while recording. Cancel uses Pop; submit uses its own low confirmation cue before transcription. Cancelled recordings are discarded. Cancelling active transcription terminates the model worker, so the next recording reloads the model. Unknown commands produce a notification and do nothing.

The transcript is pasted into whichever application has focus when transcription finishes, so changing applications while speaking is safe.

## Python processors

Choose **Settings…** from the status-bar menu. The window selects the active processor and provides **New Processor…**, **Reload**, and **Show in Finder** controls.

Two processors are built in. **Plain Text** is selected by default and returns the transcript unchanged. **Casual Discord** lowercases the first letter unless it is the pronoun “I” and removes sentence-ending periods while preserving periods inside URLs, decimals, and versions.

Processor files live in:

```text
~/Library/Application Support/STT Local/processors
```

Each processor is ordinary trusted Python:

```python
def process(text: str) -> str:
    return text
```

Files beginning with `_` are ignored. A missing function, exception, or non-string return value produces a notification and falls back to the unmodified transcript. Processor code is intentionally unsandboxed and should be treated like any other local script you run.

## Install as a per-user service

The installer synchronizes the environment, writes `~/Library/LaunchAgents/com.asempruch.stt-local.plist`, and starts the status-bar app:

```bash
./scripts/install-launch-agent.sh
```

Logs are written to:

```text
/tmp/stt-local.stdout.log
/tmp/stt-local.stderr.log
```

These are temporary diagnostic files rather than persistent application data.

Restart after source or dependency changes by running the installer again.

`launchctl bootout` only stops and unloads the service. To start an installed
service after booting it out, either rerun the installer above or bootstrap its
existing plist directly:

```bash
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.asempruch.stt-local.plist"
```

To stop and remove the service without deleting configuration or processors:

```bash
launchctl bootout "gui/$(id -u)/com.asempruch.stt-local"
rm "$HOME/Library/LaunchAgents/com.asempruch.stt-local.plist"
```

## License

STT Local is available under the [MIT License](LICENSE).

## Development controls

For idle-unload testing only, override the configured timeout when launching directly:

```bash
STT_LOCAL_IDLE_SECONDS=10 uv run stt-local
```

The persisted configuration is `~/Library/Application Support/STT Local/config.json`. The default timeout is 600 seconds. The model, language (`en`), sample rate (16 kHz), and command path are fixed application constants.

## Troubleshooting

- **No sound or recording:** Verify Microphone permission and the current macOS default input device.
- **Text copies but does not paste:** Grant Accessibility permission to `.venv/bin/python`.
- **First transcription is slow:** The model may still be downloading or warming. Later recordings reuse the resident worker.
- **Processor is not listed:** Use Reload and confirm the file ends in `.py` and does not begin with `_`.
- **Model RAM remains allocated:** Wait 10 idle minutes or quit STT Local; the dedicated model process then exits.
