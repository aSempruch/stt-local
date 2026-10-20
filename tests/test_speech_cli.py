import io
import json
import os

from stt_local import speech_cli
from stt_local.speech_cli import first_paragraph, plain_speech


def test_plain_speech_strips_markdown():
    text = (
        "# Title\n- Use [the docs](https://example.com) and `uv run`.\n"
        "```python\nprint('x')\n```\n| a | b |\n> See https://x.y/z now"
    )

    assert plain_speech(text) == "Title Use the docs and uv run. See a link now"


def test_fallback_reads_first_prose_paragraph():
    reply = "## Summary\n\n```\ncode\n```\n\nAll done. Tests pass.\n\nDetails follow."

    assert first_paragraph(reply) == "All done. Tests pass."


def test_fallback_cuts_long_paragraph_at_sentence_boundary():
    reply = "First sentence here. " + "Second sentence is much longer. " * 20

    assert first_paragraph(reply, limit=60) == (
        "First sentence here. Second sentence is much longer."
    )


SESSION = "a05be41a-0c08-474e-ac08-1aa681ead601"


def run_hook(monkeypatch, capsys, tmp_path, hook, payload, reply=None):
    sent = []

    def fake_send(request, **_kwargs):
        sent.append(request)
        return reply

    monkeypatch.setattr(speech_cli, "send_command", fake_send)
    monkeypatch.setattr(speech_cli, "VOICE_SESSIONS_DIR", tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    hooks = {
        "claude-stop-hook": speech_cli.claude_stop_hook,
        "claude-prompt-hook": speech_cli.claude_prompt_hook,
        "claude-display-hook": speech_cli.claude_display_hook,
    }
    assert hooks[hook](tmp_path) == 0
    out = capsys.readouterr().out
    return sent, (json.loads(out) if out.strip() else None)


def prompt(monkeypatch, capsys, tmp_path, text, reply={"ok": True}):
    return run_hook(
        monkeypatch,
        capsys,
        tmp_path,
        "claude-prompt-hook",
        {"session_id": SESSION, "prompt": text},
        reply,
    )


def test_voice_mode_command_toggles_only_this_session(monkeypatch, capsys, tmp_path):
    sent, out = prompt(monkeypatch, capsys, tmp_path, "/voice-mode")

    assert sent == [{"command": "stop"}]
    assert out == {"decision": "block", "reason": "Voice mode on for this session."}
    assert [flag.name for flag in tmp_path.iterdir()] == [SESSION]

    _, out = prompt(monkeypatch, capsys, tmp_path, "  /voice-mode  ")
    assert out["reason"] == "Voice mode off for this session."
    assert list(tmp_path.iterdir()) == []


def test_voice_mode_on_and_off_are_explicit(monkeypatch, capsys, tmp_path):
    prompt(monkeypatch, capsys, tmp_path, "/voice-mode on")
    _, out = prompt(monkeypatch, capsys, tmp_path, "/Voice-Mode ON")
    assert out["reason"] == "Voice mode on for this session."
    assert (tmp_path / SESSION).exists()

    prompt(monkeypatch, capsys, tmp_path, "/voice-mode off")
    _, out = prompt(monkeypatch, capsys, tmp_path, "/voice-mode off")
    assert out["reason"] == "Voice mode off for this session."
    assert not (tmp_path / SESSION).exists()


def test_voice_mode_warns_when_app_not_running(monkeypatch, capsys, tmp_path):
    _, out = prompt(monkeypatch, capsys, tmp_path, "/voice-mode", reply=None)

    assert "isn't running" in out["reason"]
    assert (tmp_path / SESSION).exists()


def test_prompt_adds_context_only_in_voice_sessions(monkeypatch, capsys, tmp_path):
    _, out = prompt(monkeypatch, capsys, tmp_path, "fix the bug")
    assert out is None

    (tmp_path / SESSION).touch()
    sent, out = prompt(monkeypatch, capsys, tmp_path, "fix the bug")

    assert sent == [{"command": "stop"}]
    output = out["hookSpecificOutput"]
    assert output["hookEventName"] == "UserPromptSubmit"
    assert "<spoken>" in output["additionalContext"]


def test_other_slash_text_is_not_a_toggle(monkeypatch, capsys, tmp_path):
    _, out = prompt(monkeypatch, capsys, tmp_path, "/voice-mode please")

    assert out is None
    assert list(tmp_path.iterdir()) == []


def test_stop_hook_speaks_only_in_voice_sessions(monkeypatch, capsys, tmp_path):
    payload = {
        "session_id": SESSION,
        "last_assistant_message": "Done.\n\n<spoken>All set.</spoken>",
    }
    sent, _ = run_hook(monkeypatch, capsys, tmp_path, "claude-stop-hook", payload)
    assert sent == []

    (tmp_path / SESSION).touch()
    sent, out = run_hook(monkeypatch, capsys, tmp_path, "claude-stop-hook", payload)

    assert sent == [{"command": "speak", "text": "All set."}]
    assert out is None


def test_stop_hook_speaks_the_last_block(monkeypatch, capsys, tmp_path):
    (tmp_path / SESSION).touch()
    reply = (
        "## Changes\n\nEdited `app.py`.\n\n<spoken>Draft.</spoken>\n\n"
        "<spoken>I fixed the **menu** bug and the tests pass.</spoken>"
    )

    sent = stop(monkeypatch, capsys, tmp_path, reply)

    assert sent == [
        {"command": "speak", "text": "I fixed the menu bug and the tests pass."}
    ]


def test_stop_hook_reads_the_opening_of_a_reply_without_a_block(
    monkeypatch, capsys, tmp_path
):
    (tmp_path / SESSION).touch()

    sent = stop(monkeypatch, capsys, tmp_path, "## Done\n\nAll fixed. Tests pass.\n\nMore.")

    assert sent == [{"command": "speak", "text": "All fixed. Tests pass."}]


def display(monkeypatch, capsys, tmp_path, delta, message="m1", prompt_id="p1", **extra):
    payload = {
        "session_id": SESSION,
        "prompt_id": prompt_id,
        "message_id": message,
        "delta": delta,
        **extra,
    }
    sent, out = run_hook(monkeypatch, capsys, tmp_path, "claude-display-hook", payload)
    assert out is None  # The text is displayed unchanged.
    return sent


def stop(monkeypatch, capsys, tmp_path, reply, prompt_id="p1"):
    payload = {
        "session_id": SESSION,
        "prompt_id": prompt_id,
        "last_assistant_message": reply,
    }
    sent, _ = run_hook(monkeypatch, capsys, tmp_path, "claude-stop-hook", payload)
    return sent


def test_display_hook_speaks_a_block_as_soon_as_it_streams(
    monkeypatch, capsys, tmp_path
):
    (tmp_path / SESSION).touch()

    sent = display(monkeypatch, capsys, tmp_path, "<spoken>Fixed the **bug**.</spoken>\n\n")
    assert sent == [{"command": "speak", "text": "Fixed the bug."}]

    assert display(monkeypatch, capsys, tmp_path, "## Details\n") == []
    reply = "<spoken>Fixed the **bug**.</spoken>\n\n## Details\n"
    assert stop(monkeypatch, capsys, tmp_path, reply) == []


def test_display_hook_joins_a_block_split_across_batches(monkeypatch, capsys, tmp_path):
    (tmp_path / SESSION).touch()

    assert display(monkeypatch, capsys, tmp_path, "Intro.\n<spoken>\nOne.\n") == []
    sent = display(monkeypatch, capsys, tmp_path, "Two.\n</spoken>\nRest.\n")

    assert sent == [{"command": "speak", "text": "One. Two."}]


def test_display_hook_drops_an_unclosed_block_when_the_message_changes(
    monkeypatch, capsys, tmp_path
):
    (tmp_path / SESSION).touch()

    display(monkeypatch, capsys, tmp_path, "Mentions <spoken> in passing.\n")
    sent = display(monkeypatch, capsys, tmp_path, "Later </spoken>.\n", message="m2")

    assert sent == []


def test_display_hook_is_silent_outside_voice_sessions_and_in_subagents(
    monkeypatch, capsys, tmp_path
):
    assert display(monkeypatch, capsys, tmp_path, "<spoken>Hi.</spoken>\n") == []
    assert list(tmp_path.iterdir()) == []

    (tmp_path / SESSION).touch()
    sent = display(monkeypatch, capsys, tmp_path, "<spoken>Hi.</spoken>\n", agent_id="a1")
    assert sent == []


def test_a_new_prompt_starts_a_new_record(monkeypatch, capsys, tmp_path):
    (tmp_path / SESSION).touch()
    display(monkeypatch, capsys, tmp_path, "<spoken>Same words.</spoken>\n")

    # An earlier block keeps the fallback quiet within the turn...
    assert stop(monkeypatch, capsys, tmp_path, "No block here.") == []

    # ...but the next prompt's turn speaks even identical words again.
    sent = display(monkeypatch, capsys, tmp_path, "<spoken>Same words.</spoken>\n", prompt_id="p2")
    assert sent == [{"command": "speak", "text": "Same words."}]


def test_hooks_do_not_recreate_a_flag_removed_mid_turn(monkeypatch, capsys, tmp_path):
    flag = tmp_path / SESSION
    with speech_cli.turn_record(flag, {"prompt_id": "p1"}) as record:
        assert record is None

    assert stop(monkeypatch, capsys, tmp_path, "<spoken>Hi.</spoken>") == []
    assert not flag.exists()


def test_hooks_ignore_unusable_session_ids(monkeypatch, capsys, tmp_path):
    payload = {"session_id": "../escape", "prompt": "/voice-mode"}
    _, out = run_hook(monkeypatch, capsys, tmp_path, "claude-prompt-hook", payload)

    assert out is None
    assert list(tmp_path.iterdir()) == []


def test_stale_flags_are_pruned_when_enabling(tmp_path):
    stale = tmp_path / "old-session"
    stale.touch()
    old = speech_cli.time.time() - speech_cli.STALE_SESSION_SECONDS - 60
    os.utime(stale, (old, old))

    speech_cli.set_voice_mode(tmp_path / SESSION, True)

    assert [flag.name for flag in tmp_path.iterdir()] == [SESSION]


def test_stop_hook_tolerates_bad_input(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    monkeypatch.setattr(speech_cli, "send_command", lambda *_a, **_k: 1 / 0)

    assert speech_cli.main(["claude-stop-hook"]) == 0


def test_say_reports_when_app_not_running(monkeypatch, capsys):
    monkeypatch.setattr(speech_cli, "send_command", lambda *_a, **_k: None)

    assert speech_cli.main(["say", "hello"]) == 1
    assert "not running" in capsys.readouterr().err
