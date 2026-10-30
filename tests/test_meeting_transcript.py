from datetime import datetime

from stt_local.meeting_transcript import (
    Line,
    format_offset,
    merge_lines,
    render_markdown,
    transcript_path,
)

STARTED = datetime(2026, 10, 13, 14, 5)


def test_merge_sorts_by_start_and_joins_same_speaker_within_gap():
    lines = [
        Line(9.0, 11.0, "Me", "And then"),
        Line(0.5, 3.0, "Me", "Hi."),
        Line(3.5, 6.0, "Me", "Can you hear me?"),
        Line(6.2, 8.0, "Others", "Yes."),
        Line(12.0, 14.0, "Me", "we start."),
    ]

    assert merge_lines(lines) == [
        Line(0.5, 6.0, "Me", "Hi. Can you hear me?"),
        Line(6.2, 8.0, "Others", "Yes."),
        Line(9.0, 14.0, "Me", "And then we start."),
    ]


def test_merge_keeps_same_speaker_lines_apart_after_long_gap():
    lines = [Line(0.0, 2.0, "Me", "One."), Line(7.0, 8.0, "Me", "Two.")]

    assert merge_lines(lines) == lines


def test_format_offset_switches_to_hours():
    assert format_offset(3.9) == "00:03"
    assert format_offset(754) == "12:34"
    assert format_offset(3723) == "1:02:03"


def test_markdown_header_notes_and_lines():
    lines = [
        Line(3.0, 4.0, "Me", "Hi, can you hear me?"),
        Line(5.0, 7.0, "Others", "Yes, loud and clear."),
    ]

    text = render_markdown(
        lines,
        started=STARTED,
        duration=2820,
        microphone="Wireless microphone",
        notes=["Microphone switched to X at 12:34.", "Second note."],
    )

    assert text == (
        "# Meeting · Mon 13 Oct 2026, 14:05\n"
        "\n"
        "47 min · Microphone: Wireless microphone · Granite Speech 4.1 2B\n"
        "\n"
        "> Microphone switched to X at 12:34.\n"
        "\n"
        "> Second note.\n"
        "\n"
        "**[00:03] Me:** Hi, can you hear me?\n"
        "\n"
        "**[00:05] Others:** Yes, loud and clear.\n"
    )


def test_markdown_without_lines_says_no_speech():
    text = render_markdown(
        [], started=STARTED, duration=4, microphone=None, notes=[]
    )

    assert text == (
        "# Meeting · Mon 13 Oct 2026, 14:05\n"
        "\n"
        "1 min · Granite Speech 4.1 2B\n"
        "\n"
        "_No speech was detected._\n"
    )


def test_markdown_duration_switches_to_hours():
    text = render_markdown(
        [], started=STARTED, duration=4380, microphone=None, notes=[]
    )

    assert "\n1 h 13 min · " in text


def test_transcript_path_adds_suffix_and_creates_folder(tmp_path):
    folder = tmp_path / "Meetings"

    first = transcript_path(folder, STARTED)
    first.write_text("x")
    second = transcript_path(folder, STARTED)

    assert first == folder / "2026-10-13 14.05 Meeting.md"
    assert second == folder / "2026-10-13 14.05 Meeting 2.md"
