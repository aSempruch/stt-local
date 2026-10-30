# Meeting Transcription Design

## Goal

Record a meeting held in any app, mostly Zoom, and when it ends produce a Markdown transcript that labels each line **Me** or **Others** in the order it was spoken. Accuracy matters more than speed: both what was said and the order of turns. Transcription runs after the recording stops and may take several minutes. Telling remote speakers apart is out of scope.

Agreed decisions:

- One Markdown file per meeting, for reading and searching later.
- System audio is captured globally, everything except STT Local's own cues. Per-app capture can come later.
- Controls live in the menu bar only; the status icon shows meeting state.
- Transcription uses IBM Granite Speech 4.1 2B through `mlx-audio`.
- STT Local must never open or reconfigure the output device, because that disturbs multipoint Bluetooth headsets.

## Evidence

Throwaway spikes on the M3 Pro (macOS 26.6.1) decided the design:

- **Capture.** A global Core Audio process tap in a private aggregate device anchored on the built-in speakers captured Zoom audio playing to Bluetooth earbuds without opening the earbuds. It worked from a terminal and under a launchd job spawned by the venv Python, with no permission prompt. Host-time stamps aligned the tap and the microphone; the measured 16 ms start offset was corrected exactly.
- **Accuracy.** On 383 utterances of the AMI meeting corpus (EN2002a, 300–900 s), per-utterance word error rate was 9.1% for Granite Speech 4.1 2B, 14.4% for Parakeet TDT 0.6B v2 and 23.7% for Whisper large-v3-turbo. Whisper produced a 75-word invention on a clip that was just "yeah". The public Open ASR leaderboard ranks Granite and Cohere Transcribe level on AMI (7.06 and 7.02); Cohere's weights are gated behind a Hugging Face login, Granite's are open under Apache-2.0.
- **Ordering.** In a two-track rebuild of that meeting, putting the lines in order cost 3–4 points of error for every model and method, whether ordered by the model's own timestamps or by voice-activity segments. Order comes from the two separate tracks, so a model without timestamps loses nothing.
- **Microphone leakage.** Open earbuds leak the other side into the microphone. An energy-based leakage gate kept all of the user's lines with headphones, cut a −16 dB speaker-leakage case from 37 lines to 12 (all the user's), and turned 17 invented lines on a quiet microphone into none.
- **Prompts.** The punctuation prompt cost nothing in accuracy (9.5% against 9.1%). Adding a keyword list fixed spellings such as "Claub Agent" and "Pogoria" and brought the error rate back to 9.1%. In 383 AMI utterances it put a keyword into one: a 0.4 s mumble at −51 dBFS came out as "Pogoria". Building it showed the cost: with any keyword prompt, Granite drops much of its punctuation (3–12 marks on ten test sentences, against 20 without) and sometimes adds a word, so keywords now only correct spellings in a second pass.
- **Cost.** Granite took 110 s for two 600 s tracks. It runs at about 8× realtime on speech, so an hour of meeting takes roughly 5–8 minutes, depending on how much of it is speech. The download is 4.6 GB. Memory peaks near 8 GB on 20 s segments: 4.6 GB of weights plus a transient that is still 2 GB on 5 s segments.

## Architecture

```text
menu: Start Meeting Recording
  └─ MeetingController (app process)
       ├─ spawns stt-local-meeting-recorder (Swift) ─▶ meetings/<id>/system.pcm, mic.pcm
       │      JSON events on stdout; stops when stdin closes
menu: Stop Meeting Recording
       └─ queues python -m stt_local.meeting_transcribe <id>
              leakage gate → VAD segments → Granite per segment → merge
              ─▶ ~/Documents/Meetings/2026-10-13 14.05 Meeting.md, then deletes the audio
```

New units, each testable alone:

| Unit | Job | Depends on |
|---|---|---|
| `native/meeting-recorder/main.swift` | Capture both tracks to aligned 16 kHz files | Core Audio, AVFoundation |
| `meeting_recorder.py` | `MeetingController`: menu state, helper process, transcription queue, recovery | helper protocol, `meeting_store` |
| `meeting_store.py` | Meeting folders, `meeting.json` state, unfinished-recording discovery | filesystem |
| `meeting_audio.py` | Leakage gate and segmentation (NumPy only, VAD passed in) | NumPy |
| `meeting_transcript.py` | Merge lines, render Markdown, choose the file name | none |
| `meeting_transcribe.py` | One-shot process: load tracks, run the pipeline and Granite, report progress | `meeting_store`, `meeting_audio`, `meeting_transcript`, `mlx-audio` |

The capture helper is a separate process because dictation resets PortAudio in-process on every recording, which would kill a microphone stream in the same process, and because a hung Core Audio call in a child cannot freeze the app. Transcription is a separate one-shot process so it never blocks dictation, and its memory, up to 8 GB, goes back to the system when it exits.

## Recording helper

`scripts/install-launch-agent.sh` compiles `native/meeting-recorder/main.swift` with `swiftc -O` into `build/stt-local-meeting-recorder` (`build/` is already ignored). The Xcode Command Line Tools that provide `git` include `swiftc`. If compiling fails, the installer prints a warning and still installs the app; Start Meeting Recording then reports that the recorder is missing.

The app runs `stt-local-meeting-recorder --out <meeting dir> --exclude-pid <app pid>`.

**System track.**
- The tap is a `CATapDescription(monoGlobalTapButExcludeProcesses:)` that excludes the app's Core Audio process object, so dictation cues stay out. It is private and unmuted.
- If the app has no process object yet, the helper watches the process list and updates the tap's description when the object appears.
- The tap sits in a private aggregate device whose main sub-device is the built-in output, falling back to the default output when there is none. Tap auto-start and drift compensation are on.
- The helper never opens or changes the default output device.

**Microphone track.**
- The default input device gets its own IOProc. It is never added to an aggregate device, so its sample rate is never changed.
- Only channel 0 is kept.
- When the default input changes or the device disappears, the helper stops the old IOProc and starts one on the new default. The same path restarts a microphone that stops delivering buffers for 5 s.

**Files.**
- Each track is converted to 16 kHz mono with `AVAudioConverter`.
- Each is written as raw little-endian int16: `system.pcm` and `mic.pcm`. Raw PCM means a crash leaves a usable file whose length is its size.
- Two tracks take about 230 MB per hour.

**Alignment.**
- Both tracks share one origin: the host time of the first buffer from either.
- For each buffer, the expected write position is (buffer host time − origin) × 16 000.
- If the expected position is more than 10 ms ahead of what is written, the gap is filled with silence. If it is more than 10 ms behind, the overlap is dropped. Otherwise the buffer is appended.
- This one rule covers the start offset, gaps, device switches and clock drift.
- A process tap delivers buffers only while something is playing, so the system track's gaps of silence are written when audio resumes, and a meeting where nothing played leaves it empty.

**Protocol.**
- Events go to stdout, one JSON object per line:
  - `started`, with device names and the microphone's native rate
  - `mic_changed`
  - `mic_stalled`
  - `system_silent`
  - `error`, with a message
- `system_silent` is sent once when the tap has delivered only zeros for 10 s while another, non-excluded process reports running output. That means permission is missing, or it is the macOS 26 zero-buffer bug.
- When stdin closes, on Stop or because the app died, or on SIGTERM, the helper:
  1. stops both IOProcs
  2. destroys the aggregate device and the tap
  3. flushes the files
  4. writes `recording.json` (wall-clock start, track lengths, device names, microphone switches with their offsets)
  5. exits 0
- If the tap, the aggregate device or the first microphone IOProc can't be created, the helper sends `error` and exits 1 before writing audio.

## Meeting storage and state

Each recording lives in `~/Library/Application Support/STT Local/meetings/<YYYY-MM-DD HH.MM.SS>/`.

The helper writes `system.pcm`, `mic.pcm` and `recording.json`. The app writes `meeting.json`, which holds the state and the last error. The states are:

| State | Meaning |
|---|---|
| `recording` | Capture is running. |
| `recorded` | Capture finished; waiting for transcription. |
| `transcribing` | The transcriber is working on it. |
| `failed` | Transcription failed. |

A successful transcript deletes the whole folder, so any meeting folder present when the app starts is an **unfinished recording**, whatever its state. While the app runs, a `failed` meeting is unfinished too. Transcribe Unfinished Recordings queues them all, oldest first.

Starting needs 2 GB of free disk space. A recording stops on its own after 4 hours, with a notification, and is then transcribed.

## App integration

The menu gains these items under the status line:

```text
Recording Meeting · 12:34
─────────────
Stop Meeting Recording          (Start Meeting Recording when not recording)
Open Meetings Folder
Transcribe Unfinished Recordings (2)    only when any exist
─────────────
Processor
…
```

**Status line.**
- While dictation is idle, the status line shows the meeting state:
  - `Recording Meeting · mm:ss`
  - `Transcribing Meeting · 40%`
  - `Downloading Meeting Model · 35%`
  - with a queue: `Transcribing Meeting 1 of 2 · 40%`
- **Icon precedence:**
  1. Active dictation states.
  2. Meeting recording (`record.circle`).
  3. Meeting transcription (`text.bubble`).
  4. Error, then idle.
- There is no pill overlay for meetings, and no sound cues: a click is explicit, and a cue would play into the call.

**Start.**
1. Check that the helper exists and that there is enough free space.
2. Create the folder, write `meeting.json` as `recording`, and spawn the helper with a stdin pipe.
3. A reader thread forwards helper events to the main thread, and the existing timer refreshes the menu.
4. `error` before `started` fails the start with a notification.
5. `system_silent` and `mic_stalled` post one notification each and are noted in the transcript.

**Stop.**
1. Close the helper's stdin and wait up to 5 s, then terminate it.
2. Mark the meeting `recorded` and queue it.
3. If the helper exits on its own mid-recording, the captured audio is kept and transcribed. A notification says the recording ended early, and so does the transcript.

**Transcription queue.**
- Jobs run one at a time, oldest first.
- A new meeting can be recorded while an earlier one transcribes.
- On success, a notification names the new file.
- On failure, the meeting becomes `failed`, and a notification gives the cause, reusing `describe_load_error` for download and certificate failures.

**Interaction with the rest of the app.**
- Dictation keeps working during a meeting; its speech is on the microphone, so it appears in the transcript as Me.
- While a meeting transcribes, dictation shares the GPU and may be slower.
- `restart_if_audio_unusable` also waits until no meeting is recording or transcribing. `exec` would close the helper's pipe and end the recording.
- Quit stops the recording and the transcriber. Both meetings are left unfinished.
- At launch, if unfinished recordings exist, one notification says so, and the menu item appears. Nothing is transcribed without that click, so a login never starts a heavy job unasked.

## Transcription process

The app runs `python -m stt_local.meeting_transcribe <meeting dir> --output-folder <folder> [--keywords <text>]`. Progress goes to stdout as JSON lines, for example `{"stage": "transcribe", "progress": 0.4}`, and the process ends with a `done` line naming the file. It exits if stdin closes, so it never outlives the app. `--download-only` fetches the model and exits.

**Model.**
- `ibm-granite/granite-speech-4.1-2b`, loaded with `mlx_audio.stt.utils.load_model` at the pinned revision `de575db64086f84fdc79da4932d1076e965bc546`. Pinning keeps an upstream change from silently breaking loading.
- Downloads verify HTTPS against the macOS keychain (`use_system_certificates`), as the dictation worker does.
- When a recording starts and the model isn't cached, the app runs the transcriber with `--download-only`, so the 4.5 GB download overlaps the meeting. A notification announces it.

**Pipeline.**
1. **Load.** Read both tracks as float32 and pad the shorter one with silence.
2. **Echo cancellation**, on the microphone track: a partitioned-block frequency-domain NLMS filter (16 ms blocks, 256 ms of room response after the estimated leakage delay) learns how the system audio reaches the microphone on a first pass and subtracts it on a second, then a spectral suppressor turns down frequencies where the echo estimate outweighs what is left. A live test on laptop speakers, whose leakage reached the microphone louder than the user's voice, showed the gate alone kept 87% of leaked frames; with cancellation the transcript had no leaked lines. Leakage reduction measured 25–31 dB where the user was silent.
3. **Leakage gate**, on the echo-cancelled microphone track, over 20 ms frames:
   - Estimate the leakage delay from 0–1.5 s: the lag with the highest correlation between the tracks' log-energy envelopes, over frames where the system plays within 30 dB of its peaks. Between turns the system track is often digitally silent exactly while the user talks, which would otherwise swamp the estimate.
   - Estimate the leakage gain: the 25th percentile of the microphone/system energy ratio, over frames where the delayed system energy is in the top half of those playing frames.
   - Keep frames more than 8 dB above the predicted leakage and more than 12 dB above the microphone's noise floor (its 10th-percentile energy).
   - Fill gaps under 300 ms, drop islands under 120 ms, and pad kept regions by 200 ms.
   - Apply the result with 10 ms fades; everything else becomes silence.
4. **Voice activity.**
   - Run Silero VAD (`mlx-community/silero-vad`) on each track, with a 300 ms minimum silence and 150 ms speech padding.
   - A region longer than 20 s is split at the quietest frame between 15 and 20 s from its start, repeatedly.
5. **Interjection cuts.**
   - A region is cut wherever the other track starts speaking more than 1 s inside it, at the quietest frame within ±0.5 s of that start.
   - A cut within 0.5 s of the region start or the previous cut is skipped, and pieces under 0.2 s are dropped.
   - This keeps a short "yeah" from the other side in its place rather than after a long turn.
6. **Granite.**
   - Each segment is transcribed with the prompt `transcribe the speech with proper punctuation and capitalization.`
   - When the Settings bias prompt is non-empty and that output has a word, or pair of words, that sounds like a bias word, the segment is transcribed again with `Keywords: <bias prompt>. transcribe the speech with proper punctuation and capitalization.` Only keyword spellings are taken from the second output: each keyword phrase it wrote replaces the words it lines up with in the first, if those words sound like the whole phrase (similarity 0.7 or more), so a mumble can't turn into a keyword and "travel agent" stays itself.
   - Segments under 1 s are padded with silence to 1 s.
   - Output is capped at 32 + 10 × segment-seconds tokens, which stops runaway repetition.
   - Quotes around the whole output, or an unbalanced one at either end, are dropped.
   - Empty outputs are dropped.
7. **Merge.**
   - Each segment becomes a line stamped with its start time; the lines are sorted by start.
   - A line joins the previous one when it has the same speaker and starts less than 5 s after the previous one ends.
   - Each merged line's first letter is capitalized; joined pieces keep their own case, as they may continue a sentence.
8. **Write.**
   - Render the Markdown, write it atomically (a temporary file, then rename), report `done`, and the app deletes the meeting folder.

## Transcript

The default folder is `~/Documents/Meetings`, created on first use. Files are named `YYYY-MM-DD HH.MM Meeting.md` from the recording start, with ` 2`, ` 3` added on a clash.

```markdown
# Meeting · Mon 13 Oct 2026, 14:05

47 min · Microphone: Wireless microphone · Granite Speech 4.1 2B

> System audio was silent for the whole recording. Check System Settings → Privacy & Security → Screen & System Audio Recording.

**[00:03] Me:** Hi, can you hear me?

**[00:05] Others:** Yes, loud and clear. Shall we start?
```

- Timestamps count from the recording start: `[mm:ss]`, or `[h:mm:ss]` from one hour on.
- The note block appears only when something needs saying:
  - system audio silent
  - microphone switched or stalled, with the offset
  - recording ended early
  - recovered after a crash

## Settings

- **Meeting transcripts folder:** a new row in Settings, showing the path, with a **Choose…** button (`NSOpenPanel`). Stored as `meetings_folder` in `config.json`, defaulting to `~/Documents/Meetings`, and validated like the other fields.
- **Bias prompt:** the existing field also serves as Granite's keyword list. The README explains that a comma-separated list of names suits both uses.

## Permissions

- **System audio.** Covered by macOS's System Audio Recording permission. In testing under a launchd job spawned by the venv Python, no prompt appeared. On a fresh install macOS may prompt, and the grant may be listed under the Python interpreter. Denial produces silence rather than an error, which the `system_silent` warning catches.
- **Microphone.** Already granted for dictation to the same responsible process.
- **README.** Documents both permissions, the Command Line Tools requirement, the one-time model download, and the processing time.

## Privacy

Audio stays on the Mac and is deleted after a successful transcript. Transcripts stay in the chosen folder. Other participants get no indication that the meeting is being recorded; telling them is the user's job, and some places require their consent.

## Known limitations

- Voice-mode speech plays from a separate player process, so it lands in Others.
- Everything else playing during the meeting is captured too, such as videos or notification sounds. Voice-activity detection drops most non-speech, but anything spoken lands in Others.
- The helper records the default microphone, which may not be the one the meeting app uses.
- Remote speakers are not told apart.
- If a remote participant's microphone picks up the user's voice from their speakers, that echo appears under Others.

## Testing

Unit tests run without audio hardware or models:

- **Leakage gate**, on synthetic signals:
  - headphones: all own speech is kept
  - speaker leakage at −16 dB: leakage dropped, own speech kept
  - quiet microphone with faint leakage: nothing kept
- **Segmentation**, with a fake VAD: long-region splitting at quiet points, interjection cuts, and the minimum-piece rules.
- **Granite call rules**, with a fake model: prompt building, the keyword fallback, the token cap, short-segment padding.
- **Merge and Markdown:** ordering, same-speaker joining, timestamps past one hour, the note block, file-name clashes.
- **`MeetingController`**, with a fake helper (a Python script that speaks the JSON protocol), a fake transcriber and an injected clock:
  - start and stop
  - start failure
  - a helper crash mid-recording
  - the 4-hour stop
  - the queue
  - restart gating
  - unfinished-recording discovery and retry
  - quit
- **Config:** `meetings_folder` load, save and validation.
- **Installer:** compiles the helper through a fake `swiftc` (`STT_LOCAL_SWIFTC`), and warns without failing when compiling fails.

Live checks run outside the default suite:

- **Helper probe.** A 5-second recording:
  - both files grow at 16 kHz
  - `recording.json` is valid
  - the default output device and its sample rate are unchanged
- **End to end, after reinstalling the LaunchAgent.**
  - Play a known recording through the current output while the user is silent, at a time when no call is in progress.
  - Others should match it.
  - Me should have no lines. This checks the leakage gate against the real open earbuds and microphone.

## Delivery

- Work lands as small commits on `main`.
- No new Python dependencies: `mlx-audio`, `huggingface_hub` and NumPy are already installed.
- After the tests pass, reinstall the LaunchAgent and run the live checks.
- Update the README and the context-vault deployment note.
