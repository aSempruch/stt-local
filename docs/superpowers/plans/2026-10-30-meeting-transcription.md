# Meeting Transcription Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record any meeting's system audio and microphone from the menu bar, then write a Markdown transcript whose lines are labelled Me or Others in spoken order.

**Architecture:**
- A Swift helper captures a global process tap and the default microphone into aligned 16 kHz raw PCM files.
- `MeetingController` in the app runs the helper, then queues a one-shot `meeting_transcribe` process. That process gates microphone leakage, cuts both tracks into speech segments with Silero VAD, transcribes each segment with Granite Speech 4.1 2B, and merges the lines by start time.
- Pure NumPy and text modules hold the logic, so tests need no audio hardware or models.

**Tech Stack:** Swift 6 toolchain (Swift 5 language mode), Core Audio process taps, AVFoundation; Python 3.13, NumPy, mlx-audio 0.5.7 (Granite and Silero), rumps and PyObjC; pytest.

**Spec:** `docs/superpowers/specs/2026-10-13-meeting-transcription-design.md`

## Global Constraints

- The helper never opens or reconfigures the output device.
- Model `ibm-granite/granite-speech-4.1-2b`, revision `de575db64086f84fdc79da4932d1076e965bc546`; VAD `mlx-community/silero-vad`.
- Prompt: `transcribe the speech with proper punctuation and capitalization.`, plus ` Keywords: <bias prompt>` when the bias prompt is non-empty.
- Audio is 16 kHz mono, little-endian int16 raw PCM, in `system.pcm` and `mic.pcm`.
- Meeting folders live at `~/Library/Application Support/STT Local/meetings/<YYYY-MM-DD HH.MM.SS>/`. The states are `recording`, `recorded`, `transcribing` and `failed`.
- Transcripts go to `~/Documents/Meetings` by default (config `meetings_folder`), named `YYYY-MM-DD HH.MM Meeting.md`, with ` 2`, ` 3` added on a clash.
- Starting a recording needs 2 GB free. A recording stops itself after 4 hours.
- No new Python dependencies. No sound cues and no overlay for meetings.
- The helper and the transcriber speak JSON lines on stdout. Each exits when its stdin closes.

## Review Focus

- **No speech at all**, such as Stop pressed right after Start or a muted call: still writes a transcript with its header and "_No speech was detected._", and doesn't crash. Owned by Task 5.
- **The helper fails before `started`**, for example no permission or no device: the user sees the error, no meeting folder is left behind, and the menu offers Start again. Owned by Task 7.
- **The app restarts while a meeting is `recording` or `transcribing`**: the meeting shows as unfinished, with a recovery note, and doesn't run until the user clicks. Owned by Tasks 2 and 7.
- **The transcriber dies mid-way**, from an exception, a kill or a model download failure: the meeting becomes `failed`, its audio is kept, and retrying works. Owned by Task 7.
- **The output folder is missing or the name is taken**: the folder is created and the name gets a suffix. An unwritable folder fails the job and keeps the audio. Owned by Tasks 4 and 5.

---

### Task 1: Configuration and constants

**Files:**
- Modify: `src/stt_local/constants.py`, `src/stt_local/config.py`, `src/stt_local/settings.py` (model only)
- Test: `tests/test_config.py`, `tests/test_settings_model.py`

**Interfaces:**
- Produces, in `constants.py`:
  - `MEETINGS_DIR = APPLICATION_SUPPORT_DIR / "meetings"`
  - `DEFAULT_MEETINGS_FOLDER = "~/Documents/Meetings"`
  - `MEETING_MODEL`, `MEETING_MODEL_REVISION`, `MEETING_MODEL_LABEL = "Granite Speech 4.1 2B"`
  - `VAD_MODEL`
  - `MEETING_RECORDER`: the `STT_LOCAL_MEETING_RECORDER` environment variable, else `<repo>/build/stt-local-meeting-recorder`
  - `MEETING_MAX_SECONDS = 4 * 3600`
  - `MEETING_MIN_FREE_BYTES = 2_000_000_000`
- Produces: `AppConfig.meetings_folder: str = DEFAULT_MEETINGS_FOLDER`, `SettingsModel.meetings_folder -> Path` (with `~` expanded), and `SettingsModel.set_meetings_folder(path: str) -> None`.

- [ ] **Step 1: Write failing tests**
  - `test_meetings_folder_round_trips`: save `AppConfig(meetings_folder="/tmp/m")`, load it, and get it back unchanged.
  - `test_blank_meetings_folder_returns_defaults`: a config with `"meetings_folder": ""` loads as `AppConfig()`.
  - `test_config_without_meetings_folder_uses_default`: a config without the field loads with `DEFAULT_MEETINGS_FOLDER`.
  - `test_meetings_folder_expands_home_and_persists`: a fresh model gives `Path.home() / "Documents" / "Meetings"`. `set_meetings_folder("  /tmp/x ")` persists `"/tmp/x"`, and `set_meetings_folder("")` raises `ValueError`.
- [ ] **Step 2:** Run `uv run pytest tests/test_config.py tests/test_settings_model.py -q`. Expect the new tests to fail.
- [ ] **Step 3:** Implement. `ConfigStore.load` validates `meetings_folder` as a non-empty string, as it does `speech_voice`.
- [ ] **Step 4:** Run the same command and expect everything to pass.
- [ ] **Step 5:** Commit: `feat: add meeting transcript folder setting`.

### Task 2: Meeting store

**Files:**
- Create: `src/stt_local/meeting_store.py`
- Test: `tests/test_meeting_store.py`

**Interfaces:**
- Produces:
  - `class MeetingState(str, Enum)`, with the values `RECORDING`, `RECORDED`, `TRANSCRIBING` and `FAILED`.
  - `@dataclass(frozen=True) class Meeting`, with the fields `path: Path`, `started: datetime` (aware, local), `state: MeetingState`, `error: str | None = None` and `notes: tuple[str, ...] = ()`.
  - `class MeetingStore(directory: Path = MEETINGS_DIR)`, with these methods:
    - `create(started: datetime) -> Meeting` makes the folder (adding ` 2` on a clash) and writes `meeting.json`.
    - `update(meeting, **changes) -> Meeting` writes atomically.
    - `load(path) -> Meeting | None`
    - `all() -> list[Meeting]`, oldest first, skipping unreadable folders.
    - `recover() -> list[Meeting]` returns every folder found at launch. It turns `recording` into `recorded` with the note `RECOVERED_NOTE`, and `transcribing` into `recorded`.
    - `delete(meeting) -> None`
    - `free_bytes() -> int`
- Produces: `RECOVERED_NOTE = "Recovered after STT Local stopped during the recording."`

- [ ] **Step 1: Write failing tests**
  - `test_create_writes_state_and_names_folder_by_start_time`: the folder is named `2026-10-13 14.05.09`, and `meeting.json` has state `recording` and `started` in ISO form.
  - `test_create_adds_suffix_when_folder_exists`
  - `test_update_round_trips_notes_and_error`
  - `test_all_is_oldest_first_and_skips_unreadable`: one folder has a corrupt `meeting.json`.
  - `test_recover_marks_interrupted_meetings_recorded`: `recording` becomes `recorded` with `RECOVERED_NOTE`. `transcribing` becomes `recorded` with no new note. `failed` is unchanged. All three are returned.
  - `test_delete_removes_folder`
- [ ] **Step 2:** Run `uv run pytest tests/test_meeting_store.py -q`. Expect a failure (no module).
- [ ] **Step 3:** Implement. JSON keys are `started`, `state`, `error` and `notes`. Write atomically, as `ConfigStore.save` does.
- [ ] **Step 4:** Run it and expect a pass.
- [ ] **Step 5:** Commit: `feat: store meeting recordings and their state`.

### Task 3: Leakage gate and segmentation

**Files:**
- Create: `src/stt_local/meeting_audio.py`
- Test: `tests/test_meeting_audio.py`

**Interfaces:**
- Produces: `FRAME = 320` (20 ms), `Region = tuple[float, float]` (seconds), and these functions:
  - `frame_energy(audio) -> np.ndarray`
  - `leakage_mask(mic, system) -> np.ndarray` (bool per frame)
  - `apply_mask(audio, mask) -> np.ndarray`
  - `cut_at_interjections(regions, other_regions, audio) -> list[Region]`
  - `split_long_regions(regions, audio, max_seconds=20.0, search_seconds=5.0) -> list[Region]`
- The gate values come from the spec:
  - delay search 0–1.5 s
  - gain from the 25th percentile of the ratio, over frames above the 70th percentile of system energy
  - keep frames above +8 dB over predicted leakage and +12 dB over the 10th-percentile floor
  - close gaps under 15 frames, drop islands under 6, dilate by 10
- `apply_mask` zeroes dropped frames and puts 160-sample linear fades inside the edges of kept runs. It runs in O(n), with no full-length convolution.
- Interjection cuts follow the spec: skip other-track starts within 1 s of either end, cut at the quietest frame within ±0.5 s, skip a cut within 0.5 s of the previous cut, and drop pieces under 0.2 s.

- [ ] **Step 1: Write failing tests**, using synthetic signals built in the test: speech is band-limited noise bursts, leakage is the system track × 10^(−16/20), delayed 0.2 s.
  - `test_gate_keeps_own_speech_without_leakage`: more than 95% of own-speech frames are kept.
  - `test_gate_drops_speaker_leakage_but_keeps_own_speech`: under 10% of leakage-only frames are kept, and more than 90% of own-speech frames.
  - `test_gate_keeps_nothing_on_quiet_mic_with_faint_leakage`: under 2% of frames are kept.
  - `test_gate_handles_silent_tracks_and_empty_audio`: all-zero tracks keep nothing, and empty arrays return an empty mask.
  - `test_apply_mask_zeroes_dropped_frames_and_fades_edges`
  - `test_split_long_region_cuts_at_quietest_point_between_15_and_20_s`: a 45 s region with dips at 17 s and 36 s is cut at about 17 s and about 36 s.
  - `test_interjection_cut_lands_on_quiet_frame_and_respects_minimums`
- [ ] **Step 2:** Run `uv run pytest tests/test_meeting_audio.py -q`. Expect a failure.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run it and expect a pass.
- [ ] **Step 5:** Commit: `feat: gate microphone leakage and segment meeting speech`.

### Task 4: Transcript merge and Markdown

**Files:**
- Create: `src/stt_local/meeting_transcript.py`
- Test: `tests/test_meeting_transcript.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class Line`, with `start: float`, `end: float`, `speaker: str` (`"Me"` or `"Others"`) and `text: str`.
- Produces these functions:
  - `merge_lines(lines, join_gap=5.0) -> list[Line]`
  - `format_offset(seconds) -> str`: `"00:03"`, or `"1:02:03"` from one hour on.
  - `render_markdown(lines, *, started, duration, microphone, notes) -> str`
  - `transcript_path(folder, started) -> Path`

- [ ] **Step 1: Write failing tests**
  - `test_merge_sorts_by_start_and_joins_same_speaker_within_gap`: lines are joined with a space, and the start comes from the first line.
  - `test_merge_keeps_same_speaker_lines_apart_after_long_gap`
  - `test_format_offset_switches_to_hours`
  - `test_markdown_header_notes_and_lines`: the output is exactly the following, for `started=2026-10-13 14:05`, duration 2820 s and microphone "Wireless microphone":
    - `# Meeting · Mon 13 Oct 2026, 14:05`
    - a blank line
    - `47 min · Microphone: Wireless microphone · Granite Speech 4.1 2B`
    - a blank line
    - each note as `> …`
    - each line as `**[00:03] Me:** text`, with blank lines between them
  - `test_markdown_without_lines_says_no_speech`: `_No speech was detected._`
  - `test_transcript_path_adds_suffix_and_creates_folder`
- [ ] **Step 2:** Run `uv run pytest tests/test_meeting_transcript.py -q`. Expect a failure.
- [ ] **Step 3:** Implement. The duration label is `N min` (at least 1), or `H h M min` from 60 minutes on. The microphone part is left out when it is `None`.
- [ ] **Step 4:** Run it and expect a pass.
- [ ] **Step 5:** Commit: `feat: render meeting transcripts as Markdown`.

### Task 5: Transcription process

**Files:**
- Create: `src/stt_local/meeting_transcribe.py`
- Modify: `src/stt_local/model_worker.py` (`download_model` gains `revision: str | None = None`)
- Test: `tests/test_meeting_transcribe.py`, `tests/test_model_worker.py`

**Interfaces:**
- Consumes: Tasks 2, 3 and 4; `use_system_certificates`, `download_model` and `describe_load_error`.
- Produces:
  - `PROMPT` and `build_prompt(keywords: str) -> str`
  - `max_tokens_for(seconds: float) -> int`, which is `32 + ceil(10 × seconds)`
  - `transcribe_segment(generate, clip, keywords) -> str`, where `generate(clip, prompt, max_tokens) -> str`
  - `plan_segments(mic, system, speech_regions) -> tuple[np.ndarray, list[tuple[str, float, float]]]`, which returns the gated microphone track and the segments as (speaker, start, end), sorted
  - `transcribe_meeting(meeting_dir, output_folder, *, generate, speech_regions, keywords, progress) -> Path`
  - `model_cached() -> bool`
  - `main(argv) -> int`
- **Command line:** `python -m stt_local.meeting_transcribe <dir> --output-folder <f> [--keywords <k>] [--download-only]`.
- **Protocol lines on stdout:**
  - `{"stage": "download"|"load"|"transcribe", "progress": float|null}`
  - `{"stage": "done", "path": str}`
  - `{"stage": "error", "message": str}`, after which the process exits 1
- **Library output:** before importing any model, the protocol keeps a duplicate of fd 1, and fd 1 is pointed at stderr. A daemon thread exits the process when stdin closes.

- [ ] **Step 1: Write failing tests** (fake `generate`, fake `speech_regions`, temporary meeting folders built with `MeetingStore`):
  - `test_prompt_adds_keywords_only_when_present`
  - `test_short_clip_is_padded_and_token_cap_follows_length`: a 0.4 s clip reaches `generate` as 16 000 samples, with `max_tokens == 36`.
  - `test_keyword_only_output_is_retried_without_keywords`: the first call returns `"Pogoria"`, the retry returns `"Hallo"`, and the result is `"Hallo"`. Output that only contains a keyword among other words is kept.
  - `test_plan_segments_orders_both_sides_and_gates_mic`
  - `test_transcribe_meeting_writes_ordered_markdown_and_reports_progress`: two speakers, progress values rise to 1.0, and the file sits in the output folder.
  - `test_transcribe_meeting_without_speech_writes_header_only`: tracks of zero length.
  - `test_transcribe_meeting_notes_when_system_track_is_silent`: the system track is all zeros. The note `System audio was silent for the whole recording. Check System Settings → Privacy & Security → Screen & System Audio Recording.` appears, and it replaces earlier notes that start with "System audio".
  - `test_main_reports_error_line_and_exit_code_for_unwritable_folder`
  - `test_download_model_passes_revision`, in `tests/test_model_worker.py`
- [ ] **Step 2:** Run `uv run pytest tests/test_meeting_transcribe.py tests/test_model_worker.py -q`. Expect a failure.
- [ ] **Step 3:** Implement.
  - `main` loads Granite with `load_model(MEETING_MODEL, revision=MEETING_MODEL_REVISION)` and Silero with `mlx_audio.vad.load(VAD_MODEL)`.
  - `speech_regions(audio)` calls `get_speech_timestamps(audio, sample_rate=16000, min_silence_duration_ms=300, speech_pad_ms=150, return_seconds=True)`.
  - The microphone name comes from `recording.json` (`mic_device`), and the notes come from `meeting.json`.
- [ ] **Step 4:** Run it and expect a pass.
- [ ] **Step 5: Live check.** Run `uv run python -m stt_local.meeting_transcribe <dir built from the AMI scratch tracks> --output-folder <scratch>`. Expect a `done` line and a readable transcript in about 2 minutes.
- [ ] **Step 6:** Commit: `feat: transcribe recorded meetings with Granite`.

### Task 6: Recording helper and installer

**Files:**
- Create: `native/meeting-recorder/main.swift`
- Modify: `scripts/install-launch-agent.sh`, `tests/test_install_script.py`

**Interfaces:**
- **Invocation:** `stt-local-meeting-recorder --out <dir> --exclude-pid <pid>`.
- **Events, one JSON object per line on stdout:**
  - `{"event":"started","system_device":s,"mic_device":s,"mic_rate":n}`
  - `{"event":"mic_changed","device":s,"at":secs}`
  - `{"event":"mic_stalled","at":secs}`
  - `{"event":"system_silent","at":secs}`
  - `{"event":"error","message":s}`
- **Files:**
  - `system.pcm` and `mic.pcm`
  - on exit, `recording.json` with the keys `started` (ISO), `system_device`, `mic_device`, `mic_rate`, `system_samples`, `mic_samples` and `mic_switches` (a list of `{device, at}`)
- **Installer:**
  - builds into `${STT_LOCAL_BUILD_DIR:-<repo>/build}/stt-local-meeting-recorder`
  - with `${STT_LOCAL_SWIFTC:-swiftc} -O -swift-version 5 -target $(uname -m)-apple-macos14.2`
  - on failure, prints a warning to stderr and keeps going

- [ ] **Step 1: Write failing installer tests.** Both existing tests set a fake `swiftc` and `STT_LOCAL_BUILD_DIR`.
  - `test_install_script_builds_meeting_recorder`: the fake receives the source path and `-o <build dir>/stt-local-meeting-recorder`.
  - `test_install_script_continues_when_recorder_build_fails`: the fake exits 1, the script exits 0, stderr contains "meeting recorder", and the plist is still written.
- [ ] **Step 2:** Run `uv run pytest tests/test_install_script.py -q`. Expect the new tests to fail.
- [ ] **Step 3: Implement the installer change.**
- [ ] **Step 4: Implement `main.swift`**, starting from the spike `probe/dual_record.swift`.
  - The IOProc blocks copy channel 0 and the host time, then hand off to one serial queue. That queue converts with `AVAudioConverter` and positions samples by host time, with a 10 ms tolerance.
  - A 1 s timer checks for a microphone stall (5 s) and for system silence: 10 s of zeros while a process other than the helper or the excluded one has `kAudioProcessPropertyIsRunningOutput`.
  - A listener on `kAudioHardwarePropertyDefaultInputDevice` handles microphone changes.
  - A listener on `kAudioHardwarePropertyProcessObjectList` adds the excluded PID to the tap's `kAudioTapPropertyDescription` once its process object exists.
  - The process ignores SIGPIPE. Stdin EOF and SIGTERM both run the same shutdown.
- [ ] **Step 5:** Run `uv run pytest tests/test_install_script.py -q` and expect a pass. Then build for real with `./scripts/install-launch-agent.sh` and expect "Built the meeting recorder".
- [ ] **Step 6: Live probe.** Run the helper for 5 s, closing stdin with `sleep 5 | build/stt-local-meeting-recorder --out <scratch> --exclude-pid $$`.
  - Expect `started`.
  - Expect both files to hold about 80 000 samples (160 000 bytes) each.
  - Expect `recording.json` to be valid.
  - Expect the default output device and its nominal rate to be the same before and after.
- [ ] **Step 7:** Commit: `feat: add the meeting recorder helper and build it on install`.

### Task 7: Meeting controller

**Files:**
- Create: `src/stt_local/meeting_recorder.py`
- Test: `tests/test_meeting_recorder.py`

**Interfaces:**
- Consumes: `MeetingStore` (Task 2), the transcriber command line (Task 5) and the helper protocol (Task 6).
- Produces `class JsonLinesProcess(args: list[str])`. It runs `Popen` with stdin and stdout as pipes, stderr inherited. A reader thread queues the parsed lines and skips anything that isn't JSON. Methods:
  - `drain() -> list[dict]`
  - `close_input()`
  - `exit_code() -> int | None`
  - `terminate()`
  - `kill()`
  - `wait(timeout) -> int | None`
- Produces `@dataclass(frozen=True) class MeetingStatus`, with `kind: str` (`"recording"`, `"transcribing"` or `"downloading"`) and `text: str`.
- Produces `class MeetingController`. Constructor arguments:
  - `store`
  - `recorder_path: Path`
  - `output_folder: Callable[[], Path]`
  - `keywords: Callable[[], str]`
  - `notify(title, message)`
  - `report_error(title, message)`
  - `exclude_pid: int`
  - `clock=time.monotonic`
  - `spawn=JsonLinesProcess`
  - `model_cached=meeting_transcribe.model_cached`
- `MeetingController` exposes:
  - properties `is_recording`, `busy` and `unfinished_count`
  - `status() -> MeetingStatus | None`
  - `start_recording()`, `stop_recording()` and `transcribe_unfinished()`
  - `refresh()`, called on every app tick and the only place state changes
  - `shutdown()`
- **Status text:**
  - `Recording Meeting · mm:ss` (`h:mm:ss` from one hour on)
  - `Transcribing Meeting · 40%`, or `Transcribing Meeting 1 of 2 · 40%` when more are queued
  - `Downloading Meeting Model · 35%`
  - `Transcribing Meeting` while loading, when no percentage is known yet
- **Notes from helper events:**
  - `Microphone switched to {device} at {mm:ss}.`
  - `The microphone stopped delivering audio at {mm:ss}.`
  - `System audio was silent from {mm:ss} while other apps were playing sound. Check System Settings → Privacy & Security → Screen & System Audio Recording.`
  - `Recording ended early at {mm:ss}.`
  - `Recording stopped when STT Local quit.`

- [ ] **Step 1: Write failing tests**, driving a real `JsonLinesProcess` with small Python scripts as the fake helper and transcriber. Each fake is driven by arguments, writes tiny PCM files, and prints protocol lines. The clock is injected.
  - `test_start_and_stop_records_then_queues_transcription_and_deletes_folder_on_done`: a success notification names the file.
  - `test_start_fails_cleanly_when_helper_reports_error_before_started`: `report_error` is called, the folder is gone, and `is_recording` is False.
  - `test_start_refuses_when_recorder_missing_or_disk_low`
  - `test_helper_exit_mid_recording_keeps_audio_and_notes_early_end`
  - `test_helper_events_become_notes_and_one_notification_each`
  - `test_recording_stops_after_four_hours`
  - `test_stop_terminates_helper_that_ignores_stdin_after_five_seconds`
  - `test_transcriber_failure_marks_failed_and_retry_succeeds`: the error message comes from the `error` line, the meeting stays, and `unfinished_count` is 1. `transcribe_unfinished()` then succeeds.
  - `test_queue_runs_one_job_at_a_time_and_status_counts_them`
  - `test_unfinished_meetings_found_at_launch_notify_once_and_wait_for_click`
  - `test_prefetches_model_at_start_when_not_cached`
  - `test_busy_while_recording_or_transcribing`
  - `test_shutdown_stops_helper_and_transcriber_and_leaves_meetings_unfinished`
- [ ] **Step 2:** Run `uv run pytest tests/test_meeting_recorder.py -q`. Expect a failure.
- [ ] **Step 3:** Implement. Stopping is never blocking: `stop_recording` closes stdin, and `refresh` waits for the exit, terminating after 5 s.
- [ ] **Step 4:** Run it and expect a pass.
- [ ] **Step 5:** Commit: `feat: run meeting recordings and their transcription from the app`.

### Task 8: Menu, status and settings window

**Files:**
- Modify: `src/stt_local/app.py`, `src/stt_local/settings.py` (window)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `MeetingController` and `MeetingStatus` (Task 7), and `SettingsModel.meetings_folder` (Task 1).
- Produces:
  - `status_display(state, error, *, model_loaded, meeting: MeetingStatus | None = None) -> tuple[str, str]`
  - `meeting_symbol_name(kind) -> str`, mapping recording to `record.circle` and the other kinds to `text.bubble`
  - `restart_if_audio_unusable(coordinator, *, busy: Callable[[], bool] = lambda: False, ...)`
- **Precedence:** active dictation, then the meeting status, then the error, then idle.

- [ ] **Step 1: Write failing tests**
  - `test_meeting_status_shows_while_dictation_idle_and_yields_to_dictation`
  - `test_audio_restart_waits_for_meetings`
  - `test_meeting_menu_toggles_start_stop_and_shows_unfinished_count`, using `make_dictation_app` extended with a fake controller.
  - `test_quit_shuts_down_meetings`
- [ ] **Step 2:** Run `uv run pytest tests/test_app.py -q`. Expect a failure.
- [ ] **Step 3: Implement the menu.**
  - The menu reads: status, separator, Start/Stop Meeting Recording, Open Meetings Folder, `Transcribe Unfinished Recordings (n)` (hidden at 0), separator, then the existing items.
  - `_tick` calls `meetings.refresh()` and re-renders the status and menu when they change.
  - `build_app` wires the controller: `notify=post_notification`, `report_error=notify`, `exclude_pid=os.getpid()`.
- [ ] **Step 4: Implement the settings window.**
  - The window grows by 70 pt.
  - A top row reads "Meeting transcripts", showing the folder path (with `~` abbreviated) and a **Choose…** button. The button opens `NSOpenPanel` for directories, with directory creation allowed.
  - A hint below reads "Where meeting transcripts are saved."
  - The bias hint mentions that meeting transcripts use the same words.
- [ ] **Step 5:** Run the full suite with `uv run pytest -q` and expect a pass. Also construct the real settings window headlessly with `uv run python -c` (`SettingsWindowController(...)._make_controller()`) and expect no exception.
- [ ] **Step 6:** Commit: `feat: add meeting recording to the menu bar and settings`.

### Task 9: Documentation, deployment and live verification

**Files:**
- Modify: `README.md` and the vault note `Deployments/STT-Local-on-Mac.md`

- [ ] **Step 1: README.** Add a "Recording meetings" section covering:
  - the controls and the transcript format
  - System Audio Recording permission and the silent-audio warning
  - the Command Line Tools requirement
  - the 4.6 GB model, about 8 GB of memory, and 5–8 minutes per hour
  - where unfinished recordings are kept, privacy, and the limitations
- [ ] **Step 2: Deploy.** Run `./scripts/install-launch-agent.sh`. Expect the recorder to build, the agent to be bootstrapped, and `launchctl print gui/$(id -u)/com.asempruch.stt-local` to show it running.
- [ ] **Step 3: End to end without a call in progress.**
  - Run the installed helper while `say` plays speech at low volume, with nobody speaking. Then run the transcriber on that folder.
  - Expect Others lines that match the speech, and no Me lines.
  - Expect the default output device to be unchanged.
- [ ] **Step 4:** Commit the README: `docs: explain meeting recording`. Update the vault note with the feature, the paths, the model and the permission. Delete the scratch AMI clips and the unused Parakeet cache.
