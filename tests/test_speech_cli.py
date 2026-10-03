import io
import json
import os

from stt_local import speech_cli
from stt_local.speech_cli import first_paragraph, plain_speech, spoken_text


def test_spoken_block_is_preferred_and_last_one_wins():
    reply = (
        "## Changes\n\nEdited `app.py`.\n\n<spoken>Draft.</spoken>\n\n"
        "<spoken>I fixed the **menu** bug and the tests pass.</spoken>"
    )

    assert spoken_text(reply) == "I fixed the menu bug and the tests pass."


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
