# STT Control and Presentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the microphone presentation, Casual Discord processor, command mailbox, cancellation/submit controls, and `stt-local` deployment migration.

**Architecture:** Keep UI command ingestion in `app.py`, workflow state in `coordinator.py`, keystroke output in `output.py`, and worker-process lifecycle in `model_worker.py`. Use a single consumed command file and an explicit cancellation exception so killing Whisper cannot be mistaken for a retryable crash.

**Tech Stack:** Python 3.11+, pytest, rumps/PyObjC/AppKit, pynput, multiprocessing, launchd, uv

**Spec:** `docs/superpowers/specs/2026-09-17-stt-control-and-presentation-design.md`

## Global Constraints

- The shortcut endpoint is exactly `/tmp/stt-command`; `/tmp/stt-toggle` is removed.
- Plain Text remains the selected default.
- The launchd label and application-support directory remain stable across the repository rename.
- Feature behavior is developed test-first and all local commits exclude runtime/generated artifacts.

---

### Task 1: Built-in Casual Discord processor

**Files:**
- Modify: `tests/test_processors.py`
- Modify: `src/local_dictation/processors.py`

**Interfaces:**
- Produces: `casual_discord(text: str) -> str` and built-in registry key `casual_discord`.

- [ ] Write table-driven failing tests asserting first-letter handling, terminal-period removal, and preservation of decimals, versions, URLs, questions, and exclamations.
- [ ] Run `uv run pytest tests/test_processors.py -q` and confirm failures are caused by the missing built-in.
- [ ] Implement the built-in transform and registry dispatch without creating a user processor file.
- [ ] Run `uv run pytest tests/test_processors.py tests/test_settings_model.py -q` and confirm green.
- [ ] Commit with `feat: add casual discord processor`.

### Task 2: Command mailbox and workflow controls

**Files:**
- Modify: `tests/test_app.py`
- Modify: `tests/test_coordinator.py`
- Modify: `tests/test_model_worker.py`
- Modify: `tests/test_output.py`
- Modify: `src/local_dictation/constants.py`
- Modify: `src/local_dictation/app.py`
- Modify: `src/local_dictation/coordinator.py`
- Modify: `src/local_dictation/model_worker.py`
- Modify: `src/local_dictation/output.py`

**Interfaces:**
- Produces: `CommandWatcher.poll() -> str | None`, `DictationCoordinator.handle_command(command: str) -> bool`, `DictationCoordinator.cancel() -> None`, `WorkerManager.cancel() -> None`, and `MacOutput.send(text: str, press_enter: bool = False) -> None`.

- [ ] Add failing tests for mailbox normalization/deletion and unknown command reporting.
- [ ] Add failing coordinator tests for recording cancellation, transcription cancellation, toggle behavior, and submit propagation.
- [ ] Add failing worker tests proving explicit cancellation suppresses retry and output tests proving Return is submit-only.
- [ ] Run the focused tests and confirm each failure is caused by missing command/cancellation behavior.
- [ ] Implement command dispatch, asynchronous recording abort, worker cancellation generation, and optional Return.
- [ ] Run `uv run pytest tests/test_app.py tests/test_coordinator.py tests/test_model_worker.py tests/test_output.py -q` and confirm green.
- [ ] Commit with `feat: add shortcut command mailbox`.

### Task 3: Native microphone icon and calmer start cue

**Files:**
- Modify: `tests/test_app.py`
- Modify: `tests/test_output.py`
- Modify: `src/local_dictation/app.py`
- Modify: `src/local_dictation/sounds.py`

**Interfaces:**
- Produces: `make_status_icon() -> NSImage` configured as template `mic.fill`.

- [ ] Add failing tests that construct the real AppKit image, assert its accessibility description/template behavior, and expect Purr/Pop playback.
- [ ] Run the two focused test files and confirm expected failures against the old title and Tink cue.
- [ ] Implement the SF Symbol status image, remove dynamic text titles, and select Purr for start.
- [ ] Run the focused tests and native settings-window smoke test.
- [ ] Commit with `feat: refine menu bar presentation`.

### Task 4: Documentation and deployment migration

**Files:**
- Modify: `README.md`
- Modify: `tests/test_install_script.py`
- Modify after deployment: context-vault `Areas/Applications.md`
- Modify after deployment: context-vault `Deployments/Local-Dictation-on-Mac.md`

**Interfaces:**
- Consumes: commands and paths from Tasks 1–3.
- Produces: user-facing command examples and installed `/Users/alan/repos/stt-local` LaunchAgent.

- [ ] Update README commands, processor documentation, sound/icon behavior, and repository-root permission path.
- [ ] Update installer behavior tests where repository paths are observable.
- [ ] Run `uv run pytest -q`, compileall, shell syntax, and `git diff --check`.
- [ ] Commit with `docs: update STT controls and deployment`.
- [ ] Merge the verified feature branch locally, rerun the suite on main, stop launchd, remove linked worktrees, and rename the repository directory to `/Users/alan/repos/stt-local`.
- [ ] Reinstall the LaunchAgent from the new root and verify its running executable and `RunAtLoad` property.
- [ ] Write a command to `/tmp/stt-command`, verify it is consumed, and verify there are no tracked or installed references to `/tmp/stt-toggle` or the old repository root.
- [ ] Update and commit the context-vault pointers using the live deployment evidence.
