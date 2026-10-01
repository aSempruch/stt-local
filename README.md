# STT Local

A deliberately small, fully local macOS menu-bar dictation app. A built-in Right Command listener controls recording; STT Local records the current default microphone, transcribes the complete recording with MLX Whisper large-v3-turbo, optionally runs a trusted Python processor, and pastes the result once.

Accuracy is the priority. Recordings are not split or stitched, audio is never sent to a server, and partial text is never typed while you speak.

## Installation

### 1. Check requirements

- Apple-silicon Mac running macOS 14 or newer
- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/) installed and available in your terminal
- Git (if macOS prompts you to install Command Line Tools when running `git`, complete that first)
- An internet connection to download dependencies and the model on first use; transcription runs locally

### 2. Clone and install

Open Terminal and run:

```bash
mkdir -p ~/repos
cd ~/repos
git clone https://github.com/aSempruch/stt-local.git
cd stt-local
./scripts/install-launch-agent.sh
```

The installer installs dependencies, creates a per-user LaunchAgent, and starts STT Local. Look for the microphone icon in your menu bar. The app will also start automatically when you log in.

Keep the repository in this location: the installed service runs from its `.venv`. If you choose a different location, use that path in the permission steps below.

### 3. Permissions

macOS should request Microphone permission on first capture. Monitoring Right Command and pasting require Accessibility permission:

1. Open **System Settings → Privacy & Security → Accessibility**.
2. Add or enable `~/repos/stt-local/.venv/bin/python`. In the file picker, press **Command-Shift-G** to enter the path. When running from Terminal during development, enable Terminal as well.
3. Open **Privacy & Security → Microphone** and enable the Python process when prompted.

If Right Command does nothing or Command-V is not synthesized, Accessibility permission is the likely cause. macOS may also list the interpreter under **Privacy & Security → Input Monitoring**. The completed transcript is still copied with `pbcopy` before the paste attempt.

### 4. Try your first dictation

1. Focus a text field, such as a new TextEdit document.
2. Tap and release **Right Command**, then speak. Approve Microphone access if prompted; if recording did not start, tap again after granting access.
3. Tap and release **Right Command** again to stop. Wait for the transcript to appear in the focused app.

The first recording downloads and warms `mlx-community/whisper-large-v3-turbo`, so it takes longer. Model loading begins immediately after microphone capture starts. The worker remains available for follow-up dictation and exits after 10 idle minutes so macOS can reclaim all model and Metal memory.

See [Troubleshooting](#troubleshooting) if recording or pasting does not work.

## Keyboard controls

STT Local monitors the Right Command key directly. Taps take effect after Right Command is released. A hold cancels as soon as it reaches 700 ms. Pressing another key before a gesture activates makes it a normal keyboard shortcut and does not control dictation:

- Tap while idle: start recording immediately on release.
- Tap while recording: stop, transcribe, and paste after a 200 ms double-tap window.
- Double-tap while recording: stop, transcribe, paste, then press Return.
- Hold for 700 ms: discard an active recording or cancel active transcription.

Starting uses a compact double chirp and normal stop uses the Ping cue. Cancel uses Pop; submit uses its own low confirmation cue before transcription. Cancelled recordings are discarded. Cancelling active transcription terminates the model worker, so the next recording reloads the model.

The transcript is pasted into whichever application has focus when transcription finishes, so changing applications while speaking is safe.

## Bias prompt

**Settings…** also has a **Bias prompt** field for names, jargon and spellings Whisper should expect, such as `Komodo, Hermes, Claub`. It is passed to Whisper as `initial_prompt`, which nudges recognition toward those words without adding them to the transcript. It is saved when you press Return, leave the field or close the window, and applies from the next recording on; leave it empty to disable biasing. Keep it short and natural: Whisper only conditions the first 30 seconds of a recording on it, and an overlong or sentence-like prompt can occasionally leak its style into the output.

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

## Updating and managing the service

To update an installation made with the steps above, run:

```bash
cd ~/repos/stt-local
git pull
./scripts/install-launch-agent.sh
```

Run the installer again after source or dependency changes to synchronize the environment and restart the app. It writes `~/Library/LaunchAgents/com.asempruch.stt-local.plist`.

Logs are written to:

```text
/tmp/stt-local.stdout.log
/tmp/stt-local.stderr.log
```

These are temporary diagnostic files rather than persistent application data.

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

## Development

From the repository directory, install development dependencies and run the tests:

```bash
uv sync --all-groups
uv run pytest -q
```

Quit the menu-bar app before running a development instance directly:

```bash
uv run stt-local
```

For idle-unload testing only, override the configured timeout when launching directly:

```bash
STT_LOCAL_IDLE_SECONDS=10 uv run stt-local
```

The persisted configuration is `~/Library/Application Support/STT Local/config.json`. The default timeout is 600 seconds. The model, language (`en`), sample rate (16 kHz), and command path are fixed application constants.

## Troubleshooting

- **No sound or recording:** Verify Microphone permission and the current macOS default input device.
- **Microphone opening times out:** A CoreAudio device refresh can stall after a stuck audio stream. STT Local automatically restarts once the current dictation workflow returns to idle; the next recording may need to reload the model.
- **Text copies but does not paste:** Grant Accessibility permission to `.venv/bin/python`.
- **First transcription is slow:** The model may still be downloading or warming. Later recordings reuse the resident worker.
- **Processor is not listed:** Use Reload and confirm the file ends in `.py` and does not begin with `_`.
- **Model RAM remains allocated:** Wait 10 idle minutes or quit STT Local; the dedicated model process then exits.

## License

STT Local is available under the [MIT License](LICENSE).
