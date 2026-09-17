#!/bin/zsh
set -euo pipefail

script_dir="${0:A:h}"
repository="${script_dir:h}"
label="com.local-dictation.app"
plist_directory="${LOCAL_DICTATION_PLIST_DIR:-${HOME}/Library/LaunchAgents}"
log_directory="${LOCAL_DICTATION_LOG_DIR:-${HOME}/Library/Logs/Local Dictation}"
plist_path="${plist_directory}/${label}.plist"
uv_path="${LOCAL_DICTATION_UV_PATH:-$(command -v uv)}"
executable="${repository}/.venv/bin/local-dictation"

mkdir -p "${plist_directory}" "${log_directory}"
"${uv_path}" sync --all-groups

plutil -create xml1 "${plist_path}"
/usr/libexec/PlistBuddy -c "Add :Label string ${label}" "${plist_path}"
/usr/libexec/PlistBuddy -c "Add :ProgramArguments array" "${plist_path}"
/usr/libexec/PlistBuddy -c "Add :ProgramArguments:0 string ${executable}" "${plist_path}"
/usr/libexec/PlistBuddy -c "Add :WorkingDirectory string ${repository}" "${plist_path}"
/usr/libexec/PlistBuddy -c "Add :RunAtLoad bool true" "${plist_path}"
/usr/libexec/PlistBuddy -c "Add :ProcessType string Interactive" "${plist_path}"
/usr/libexec/PlistBuddy -c "Add :StandardOutPath string ${log_directory}/stdout.log" "${plist_path}"
/usr/libexec/PlistBuddy -c "Add :StandardErrorPath string ${log_directory}/stderr.log" "${plist_path}"

if [[ "${LOCAL_DICTATION_SKIP_LAUNCHCTL:-0}" != "1" ]]; then
    launchctl bootout "gui/$(id -u)/${label}" 2>/dev/null || true
    bootstrap_attempt=1
    until launchctl bootstrap "gui/$(id -u)" "${plist_path}"; do
        if (( bootstrap_attempt >= 10 )); then
            echo "Failed to start ${label} after ${bootstrap_attempt} attempts" >&2
            exit 1
        fi
        sleep 0.25
        (( bootstrap_attempt += 1 ))
    done
fi

echo "Installed ${label}"
print -r -- "Toggle dictation with: printf '%s\n' toggle > /tmp/stt-command"
