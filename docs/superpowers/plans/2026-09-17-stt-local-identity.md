# STT Local Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename every active application identity to STT Local and add direct processor selection plus stateful menu-bar symbols.

**Architecture:** Rename the import package atomically, centralize UI/state symbol naming in the existing app composition module, and rebuild the small rumps menu whenever processor choices change. Keep deployment cleanup operational rather than carrying legacy compatibility in application code.

**Tech Stack:** Python 3.11+, pytest, rumps/PyObjC/AppKit, uv, launchd

**Spec:** `docs/superpowers/specs/2026-09-17-stt-local-identity-design.md`

## Global Constraints

- Active identity is exactly STT Local / `stt-local` / `stt_local`.
- Command mailbox remains `/tmp/stt-command`.
- No migration layer, compatibility aliases, or dual service identifiers.
- Plain Text remains the default processor.

---

### Task 1: Package and runtime identity

**Files:**
- Rename: `src/local_dictation/` to `src/stt_local/`
- Modify: `pyproject.toml`
- Modify: all `tests/test_*.py`
- Modify: package source strings and environment names

**Interfaces:**
- Produces: import package `stt_local`, CLI `stt-local`, application name `STT Local`, and environment override `STT_LOCAL_IDLE_SECONDS`.

- [ ] Change tests to import `stt_local` and assert STT Local paths/names.
- [ ] Run focused tests and confirm collection/naming failures against the old package.
- [ ] Rename the package directory and update metadata/source identifiers.
- [ ] Run the complete suite and compileall.
- [ ] Commit with `refactor: rename package to STT Local`.

### Task 2: Stateful status icon and flat processor list

**Files:**
- Modify: `tests/test_app.py`
- Modify: `tests/test_settings_model.py`
- Modify: `src/stt_local/app.py`

**Interfaces:**
- Produces: `status_symbol_name(state: DictationState) -> str`, `make_status_icon(symbol_name: str) -> NSImage`, and direct checked processor menu items.

- [ ] Add failing tests for the four state-symbol groups and processor item/checkmark selection behavior.
- [ ] Run focused tests and confirm failures for missing mapping/menu behavior.
- [ ] Implement symbol replacement on status changes and a menu rebuild using the shared settings model.
- [ ] Run focused tests, full tests, and native AppKit construction.
- [ ] Commit with `feat: add processor menu and stateful icons`.

### Task 3: Installer, documentation, and clean deployment

**Files:**
- Modify: `scripts/install-launch-agent.sh`
- Modify: `tests/test_install_script.py`
- Modify: `README.md`
- Rename/update: current docs under `docs/superpowers/`
- Rename/update after deployment: vault deployment note and area link

**Interfaces:**
- Produces: LaunchAgent `com.asempruch.stt-local`, executable `.venv/bin/stt-local`, logs/support paths named `STT Local`.

- [ ] Change installer tests first to expect only the new label, executable, logs, and environment overrides; verify red.
- [ ] Update installer and README/current docs; run focused and full verification.
- [ ] Commit with `docs: complete STT Local identity`.
- [ ] Merge locally and rerun the full suite on main.
- [ ] Stop/remove the old development agent and old named artifacts, then install the new agent.
- [ ] Verify live state, command consumption, active-identity search, and Git cleanliness.
- [ ] Update and commit the context-vault note with final evidence.
