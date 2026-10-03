import io
import json

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


def run_hook(monkeypatch, capsys, hook, payload, reply):
    sent = []

    def fake_send(request, **_kwargs):
        sent.append(request)
        return reply

    monkeypatch.setattr(speech_cli, "send_command", fake_send)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert speech_cli.main([hook]) == 0
    return sent, capsys.readouterr().out


def test_stop_hook_sends_conditional_speak(monkeypatch, capsys):
    sent, out = run_hook(
        monkeypatch,
        capsys,
        "claude-stop-hook",
        {"last_assistant_message": "Done.\n\n<spoken>All set.</spoken>"},
        {"ok": True, "enabled": True},
    )

    assert sent == [{"command": "speak", "text": "All set.", "if_enabled": True}]
    assert out == ""


def test_stop_hook_tolerates_app_not_running(monkeypatch, capsys):
    sent, _ = run_hook(
        monkeypatch,
        capsys,
        "claude-stop-hook",
        {"last_assistant_message": "Done."},
        None,
    )

    assert sent == [{"command": "speak", "text": "Done.", "if_enabled": True}]


def test_stop_hook_tolerates_bad_input(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    monkeypatch.setattr(speech_cli, "send_command", lambda *_a, **_k: 1 / 0)

    assert speech_cli.main(["claude-stop-hook"]) == 0


def test_prompt_hook_stops_speech_and_adds_context_when_enabled(monkeypatch, capsys):
    sent, out = run_hook(
        monkeypatch, capsys, "claude-prompt-hook", {"prompt": "hi"},
        {"ok": True, "enabled": True},
    )

    assert sent == [{"command": "stop"}]
    output = json.loads(out)["hookSpecificOutput"]
    assert output["hookEventName"] == "UserPromptSubmit"
    assert "<spoken>" in output["additionalContext"]


def test_prompt_hook_adds_nothing_when_disabled_or_not_running(monkeypatch, capsys):
    for reply in ({"ok": True, "enabled": False}, None):
        _, out = run_hook(monkeypatch, capsys, "claude-prompt-hook", {}, reply)
        assert out == ""


def test_toggle_command_reports_state(monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(
        speech_cli,
        "send_command",
        lambda request, **_k: sent.append(request) or {"ok": True, "enabled": True},
    )

    assert speech_cli.main(["toggle"]) == 0

    assert sent == [{"command": "set_enabled", "enabled": "toggle"}]
    assert "on" in capsys.readouterr().out


def test_say_reports_when_app_not_running(monkeypatch, capsys):
    monkeypatch.setattr(speech_cli, "send_command", lambda *_a, **_k: None)

    assert speech_cli.main(["say", "hello"]) == 1
    assert "not running" in capsys.readouterr().err
