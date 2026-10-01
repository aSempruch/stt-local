import io
import subprocess

from stt_local.alerts import menu_error_title, notification_command, report_error


def test_notification_passes_text_as_arguments_not_script():
    command = notification_command('Paste "failed"', "it's broken")

    assert command[0] == "osascript"
    assert 'Paste "failed"' not in command[2]
    assert command[-2:] == ['STT Local: Paste "failed"', "it's broken"]


def test_report_error_logs_and_posts_notification():
    calls = []
    stream = io.StringIO()

    report_error(
        "Transcription failed",
        "no model",
        popen=lambda *args, **kwargs: calls.append((args, kwargs)),
        stream=stream,
    )

    assert stream.getvalue() == "STT Local error: Transcription failed: no model\n"
    assert calls[0][0][0] == notification_command("Transcription failed", "no model")
    assert calls[0][1]["stdout"] is subprocess.DEVNULL


def test_report_error_never_raises():
    def broken_popen(*_args, **_kwargs):
        raise OSError("no osascript")

    report_error("Title", "message", popen=broken_popen, stream=io.StringIO())


def test_menu_error_title_is_single_line_and_bounded():
    title = menu_error_title("Transcription failed", "line one\nline two " + "x" * 200)

    assert "\n" not in title
    assert title.startswith("Transcription failed: line one line two")
    assert len(title) == 120
    assert title.endswith("…")
