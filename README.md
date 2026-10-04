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

The first recording downloads (about 1.6 GB) and warms `mlx-community/whisper-large-v3-turbo`, so it takes longer: a notification announces the download and the floating pill shows its progress instead of the waveform. Model loading begins immediately after microphone capture starts. The worker remains available for follow-up dictation and exits after 10 idle minutes so macOS can reclaim all model and Metal memory; change the delay with **Unload model after** in **Settings…**.

See [Troubleshooting](#troubleshooting) if recording or pasting does not work.

## Keyboard controls

STT Local monitors the Right Command key directly. Taps take effect after Right Command is released. A hold cancels as soon as it reaches 500 ms. Pressing another key before a gesture activates makes it a normal keyboard shortcut and does not control dictation:

- Tap while idle: start recording immediately on release.
- Tap while recording: stop, transcribe, and paste after a 200 ms double-tap window.
- Double-tap while recording: stop, transcribe, paste, then press Return.
- Hold for 500 ms: discard an active recording or cancel active transcription.

Change both timings in **Settings…**: **Long press to cancel** sets the hold, and **Double-tap window** sets how long a stop waits for a second tap. They apply from the next press.

Starting uses a compact double chirp and normal stop uses the Ping cue. Cancel uses Pop; submit uses its own low confirmation cue before transcription. Cancelled recordings are discarded. Cancelling active transcription terminates the model worker, so the next recording reloads the model.

The transcript is pasted into whichever application has focus when transcription finishes, so changing applications while speaking is safe.

While dictation is active, a small floating pill appears at the bottom centre of the screen under the pointer, above other windows and in full-screen spaces. It shows a live microphone waveform with a red dot while recording (orange while the model is still loading) and a gentle animated wave while transcribing, then disappears. The pill never takes focus or intercepts clicks.

## Bias prompt

**Settings…** also has a **Bias prompt** field for names, jargon and spellings Whisper should expect, such as `Komodo, Hermes, Claub`. It is passed to Whisper as `initial_prompt`, which nudges recognition toward those words without adding them to the transcript. It is saved when you press Return, leave the field or close the window, and applies from the next recording on; leave it empty to disable biasing. Keep it short and natural: Whisper only conditions the first 30 seconds of a recording on it, and an overlong or sentence-like prompt can occasionally leak its style into the output.

## Reading Claude Code replies aloud

STT Local can also speak, using the local [Kokoro-82M](https://huggingface.co/mlx-community/Kokoro-82M-bf16) voice through MLX. It works like the Whisper model: a separate worker loads on the first request (about 2.5 seconds, plus a one-time download of about 370 MB), stays warm for follow-ups (about 0.4 seconds to the first sentence), and exits after the **Unload model after** delay. Replies are rendered a sentence at a time and played as one continuous stream, so playback starts before the whole reply is rendered and sentences follow each other with a short fixed pause. The output device opens only while a reply is playing.

Voice mode is per Claude Code session: type `/voice-mode` in a session to toggle it, or `/voice-mode on` / `/voice-mode off`. The hook handles the command itself, so it never reaches the model. Other sessions stay silent. **Stop Speaking** in the menu bar cuts off the current reply; starting a dictation or submitting a new prompt in any session does too. A newer reply replaces one still being spoken.

To set it up, install the app as above, then run this from the checkout:

```bash
.venv/bin/stt-local-speech install-claude-code
```

It copies the `/voice-mode` skill, which lets Claude Code recognize and complete the command, into `~/.claude/skills/voice-mode/`, and removes the `~/.claude/commands/voice-mode.md` that earlier versions used. The skill has model invocation disabled for Claude Code, and its `agents/openai.yaml` does the same for Codex, in case your Claude Code skills directory is shared with Codex's `~/.agents/skills`. It also adds a `Stop` and a `UserPromptSubmit` hook to `~/.claude/settings.json` that run this checkout's `stt-local-speech` by absolute path. It respects `CLAUDE_CONFIG_DIR` and leaves your other settings and hooks alone. The previous file is kept as `settings.json.bak`. Running it again is safe: after moving the checkout, it replaces the old hook paths instead of adding duplicates. New Claude Code sessions pick it up; restart any that are already running. `install-claude-code --uninstall` removes the hooks and the skill.

To set it up by hand instead, copy the `integrations/claude-code/voice-mode` directory into `~/.claude/skills/` and add these hooks, with the path changed to your checkout:

```json
{
  "hooks": {
    "Stop": [{"hooks": [{"type": "command", "timeout": 5,
      "command": "~/repos/stt-local/.venv/bin/stt-local-speech claude-stop-hook"}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "timeout": 5,
      "command": "~/repos/stt-local/.venv/bin/stt-local-speech claude-prompt-hook"}]}]
  }
}
```

In a voice session, the prompt hook asks Claude to end each reply with a short `<spoken>…</spoken>` block written for listening, and the stop hook speaks only that block. A reply without one has its first prose paragraph read instead. Voice sessions are remembered as empty files named after the session ID in `~/Library/Application Support/STT Local/voice-sessions/`, so a resumed session keeps its setting; files untouched for 30 days are cleaned up. The hooks never block Claude Code, and do nothing when STT Local is not running. To show voice mode in a custom Claude Code status line, check whether the file named by the status line input's `session_id` exists in that directory.

`stt-local-speech` also works on its own: `say "text"` (or text on standard input) and `stop`. It talks to the app over a private Unix socket at `~/Library/Application Support/STT Local/control.sock`. Set a different Kokoro voice, such as `am_michael` or `bf_emma`, with `speech_voice` in `config.json`.

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

The persisted configuration is `~/Library/Application Support/STT Local/config.json`. The default unload timeout is 600 seconds, set from Settings as `idle_unload_seconds`. The model, language (`en`), sample rate (16 kHz), and command path are fixed application constants.

## Troubleshooting

- **No sound or recording:** Verify Microphone permission and the current macOS default input device.
- **Microphone opening times out:** A CoreAudio device refresh can stall after a stuck audio stream. STT Local automatically restarts once the current dictation workflow returns to idle; the next recording may need to reload the model.
- **Text copies but does not paste:** Grant Accessibility permission to `.venv/bin/python`.
- **Nothing is copied or pasted after transcribing:** Look for an STT Local notification or a warning triangle in the menu bar; the menu shows the last error until the next dictation. Errors are also written to `/tmp/stt-local.stderr.log`.
- **Model download fails on a work network:** The model is downloaded over HTTPS, which STT Local verifies against the macOS keychain. Behind a TLS-inspecting proxy such as Zscaler, its root certificate must be trusted there. This is usually already done on managed Macs; otherwise run `security add-trusted-cert -r trustRoot -k ~/Library/Keychains/login.keychain-db /path/to/root-certificate.crt`, then dictate again.
- **First transcription is slow:** The model may still be downloading or warming. Later recordings reuse the resident worker.
- **Processor is not listed:** Use Reload and confirm the file ends in `.py` and does not begin with `_`.
- **Model RAM remains allocated:** Wait for the unload delay set in Settings (10 minutes by default) or quit STT Local; the dedicated Whisper and voice processes then exit.

## License

STT Local is available under the [MIT License](LICENSE).
