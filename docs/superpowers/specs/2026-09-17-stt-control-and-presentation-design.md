# STT Control and Presentation Design

## Goal

Make the local dictation app feel like an STT utility rather than a prototype: use a native microphone menu-bar icon, a calmer start cue, a built-in casual-message processor, a single command-mailbox interface for shortcuts, and a repository path named `~/repos/stt-local`.

## Status-bar presentation

The status item uses the macOS SF Symbol `mic.fill` as a template image so it follows light/dark menu-bar appearance. It has no text title. Detailed state remains visible in the first menu item as Idle, Recording · Loading Model, Recording · Model Ready, Stopping Recording, or Transcribing.

The recording-start sound changes from `/System/Library/Sounds/Tink.aiff` to the softer `/System/Library/Sounds/Purr.aiff`. The existing Pop stop sound remains unchanged.

## Built-in processor

`Casual Discord` is a built-in processor with key `casual_discord`; it appears after Plain Text and before user-created processors. Plain Text remains the default selection for new and existing configurations unless the user explicitly selects another processor.

Casual Discord lowercases the first alphabetic character unless it is the standalone pronoun `I` or the start of the contraction `I'm`. It removes sentence-ending periods, including a final run such as an ellipsis, while preserving periods inside decimal numbers, version numbers, domains, URLs, and abbreviations that are not sentence endings. Question marks, exclamation marks, whitespace, capitalization after the first character, and all other text remain unchanged.

## Shortcut command mailbox

The sole shortcut endpoint is `/tmp/stt-command`. A shortcut writes one UTF-8 command word to that path. The app reads, removes, and dispatches the mailbox on its existing polling timer. The supported commands are:

- `toggle`: start recording while idle; stop and transcribe while recording.
- `cancel`: discard an active recording without transcription. During transcription, terminate the Whisper worker, suppress output and cancellation errors, and return to idle. While idle, do nothing.
- `submit`: while recording, stop, transcribe, paste the completed text, then synthesize Return. In other states, do nothing.

Whitespace and letter case around command words are ignored. An unknown nonempty command produces a notification and no state change. The old `/tmp/stt-toggle` endpoint is removed without compatibility handling.

The mailbox intentionally has single-slot overwrite semantics. BetterTouchTool invokes commands one at a time, so a durable queue or socket protocol is unnecessary.

## Cancellation and output

Recording cancellation tears down audio on a background thread because CoreAudio stop/close can block. It plays the existing stop cue as acknowledgement and schedules the already-loaded model for normal idle unloading.

Transcription cancellation marks the request cancelled before terminating the dedicated worker process. The blocked transcription call must not retry, paste, notify an error, or press Return. The next recording creates a fresh worker and reloads the model.

Submit mode presses Return only after nonempty processed text has been copied and the Command-V keystroke has been issued successfully. Normal toggle transcription never presses Return.

## Deployment migration

After the feature branch is verified and merged, stop the installed LaunchAgent, remove its old generated plist, rename `/Users/alan/repos/local-dictation` to `/Users/alan/repos/stt-local`, and reinstall from the new root. Keep the stable launchd label `com.local-dictation.app`, application-support directory, logs, Python import package, and configuration so user settings continue to work.

Update the README, installer tests, BetterTouchTool examples, and context-vault source pointers. Verify tests before and after the move, verify launchd points at the new executable, verify `/tmp/stt-command` is consumed, and ensure the old repository path and trigger file are no longer referenced.

## Testing

Unit tests cover Casual Discord punctuation/capitalization, built-in discovery and Plain Text default, command parsing/consumption, coordinator cancellation in recording and transcription states, submit-only Return behavior, Purr/Pop sound selection, and microphone image construction. The full suite, compile check, shell syntax check, diff check, native settings-window construction, LaunchAgent state, and installed command-mailbox behavior provide final verification.
