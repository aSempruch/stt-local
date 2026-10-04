---
description: Read this session's replies aloud with STT Local (on, off, or toggle)
argument-hint: "[on|off]"
disable-model-invocation: true
---
The STT Local UserPromptSubmit hook normally intercepts /voice-mode before it reaches you, so you are seeing this because that hook is not installed or failed. Tell the user in one sentence that voice mode was not changed and that the STT Local Claude Code hooks need to be set up: run `.venv/bin/stt-local-speech install-claude-code` from the STT Local checkout, then start a new session.
