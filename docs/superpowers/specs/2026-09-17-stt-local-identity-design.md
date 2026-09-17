# STT Local Identity and Menu Design

## Goal

Make STT Local the only active identity throughout the first-version application and expose processor selection directly in the status-bar menu. No migration code or compatibility aliases are required.

## Naming

All active code, metadata, runtime identifiers, documentation, and deployment artifacts use these names:

| Purpose | Name |
| --- | --- |
| Product/UI | `STT Local` |
| Distribution | `stt-local` |
| Python package | `stt_local` |
| Console command | `stt-local` |
| LaunchAgent | `com.asempruch.stt-local` |
| Application Support | `~/Library/Application Support/STT Local` |
| Logs | `~/Library/Logs/STT Local` |
| Environment prefix | `STT_LOCAL_` |
| Command mailbox | `/tmp/stt-command` |

The repository remains `~/repos/stt-local`. Historical Git commits are not rewritten. Current design/plan filenames and content, README, source, tests, and context-vault notes use STT Local terminology. The old development LaunchAgent, plist, support directory, and log directory are removed during deployment rather than migrated.

## Status item

The status item remains icon-only and uses native monochrome SF Symbols. Its image changes with coordinator state:

- Idle: `mic.fill`
- Recording while the model loads or is ready: `waveform`
- Stopping or transcribing: `ellipsis.circle`
- Error: `exclamationmark.triangle`

Every image is a template image with accessibility description `STT Local`. The first menu row continues to show the detailed state text.

## Processor menu

Processor choices appear directly in the main status menu, not in a submenu. A disabled `Processor` section label is followed by Plain Text, Casual Discord, then discovered custom processors. The active item has a checkmark. Clicking an item validates and persists the selection immediately, rebuilds the menu checkmarks, and keeps the settings model synchronized.

`Reload Processors` refreshes the direct list. `Settings…` remains available for creating processors and revealing their directory; creating/selecting in Settings is reflected in the menu the next time it is rebuilt or reloaded.

## Deployment

The installer generates `com.asempruch.stt-local.plist`, invokes `.venv/bin/stt-local`, logs under `~/Library/Logs/STT Local`, retains bounded launchd bootstrap retries, and prints the existing command-mailbox example. Deployment explicitly stops/removes `com.local-dictation.app`, deletes its generated plist and empty first-iteration support/log artifacts, then installs the new service.

## Verification

TDD covers naming constants, package imports, installer effects, state-symbol mapping, and direct processor selection/checkmarks. Completion requires the complete test suite, Python compilation, shell syntax, native AppKit/settings construction, clean active-identity search, live LaunchAgent state, command consumption, and a clean Git tree.
