import os
import plistlib
import subprocess
from pathlib import Path


def test_install_script_generates_expected_launch_agent(tmp_path):
    repository = Path(__file__).parents[1]
    plist_directory = tmp_path / "LaunchAgents"
    fake_uv = tmp_path / "uv"
    fake_uv.write_text("#!/bin/sh\nexit 0\n")
    fake_uv.chmod(0o755)
    env = os.environ | fake_recorder_build(tmp_path) | {
        "STT_LOCAL_PLIST_DIR": str(plist_directory),
        "STT_LOCAL_UV_PATH": str(fake_uv),
        "STT_LOCAL_SKIP_LAUNCHCTL": "1",
    }

    completed = subprocess.run(
        ["zsh", str(repository / "scripts" / "install-launch-agent.sh")],
        cwd=repository,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    with (plist_directory / "com.asempruch.stt-local.plist").open("rb") as handle:
        plist = plistlib.load(handle)
    assert plist["Label"] == "com.asempruch.stt-local"
    assert plist["RunAtLoad"] is True
    # Restart after a crash, but not after Quit from the menu.
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
    assert plist["ProgramArguments"] == [
        str(repository / ".venv" / "bin" / "stt-local")
    ]
    assert plist["WorkingDirectory"] == str(repository)
    assert plist["StandardOutPath"] == "/tmp/stt-local.stdout.log"
    assert plist["StandardErrorPath"] == "/tmp/stt-local.stderr.log"
    assert "Control dictation with Right Command" in completed.stdout
    assert "/tmp/stt-command" not in completed.stdout


def test_install_script_retries_transient_launchctl_bootstrap_failure(tmp_path):
    repository = Path(__file__).parents[1]
    plist_directory = tmp_path / "LaunchAgents"
    fake_uv = tmp_path / "uv"
    fake_uv.write_text("#!/bin/sh\nexit 0\n")
    fake_uv.chmod(0o755)
    attempts = tmp_path / "bootstrap-attempts"
    fake_launchctl = tmp_path / "launchctl"
    fake_launchctl.write_text(
        "#!/bin/zsh\n"
        "if [[ $1 == bootout ]]; then exit 0; fi\n"
        "if [[ $1 == bootstrap ]]; then\n"
        f"  count=$(cat {attempts!s} 2>/dev/null || echo 0)\n"
        f"  print $((count + 1)) > {attempts!s}\n"
        "  (( count >= 1 ))\n"
        "fi\n"
    )
    fake_launchctl.chmod(0o755)
    env = os.environ | fake_recorder_build(tmp_path) | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "STT_LOCAL_PLIST_DIR": str(plist_directory),
        "STT_LOCAL_UV_PATH": str(fake_uv),
    }

    subprocess.run(
        ["zsh", str(repository / "scripts" / "install-launch-agent.sh")],
        cwd=repository,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert attempts.read_text().strip() == "2"


def fake_recorder_build(tmp_path, exit_code=0):
    """A swiftc stand-in that records its arguments and writes the -o file."""
    swiftc = tmp_path / "fake-swiftc"
    swiftc.write_text(
        "#!/bin/zsh\n"
        f"print -r -- \"$*\" > {tmp_path / 'swiftc-args'}\n"
        "while (( $# )); do\n"
        "  if [[ $1 == -o ]]; then print built > $2; fi\n"
        "  shift\n"
        "done\n"
        f"exit {exit_code}\n"
    )
    swiftc.chmod(0o755)
    return {
        "STT_LOCAL_SWIFTC": str(swiftc),
        "STT_LOCAL_BUILD_DIR": str(tmp_path / "build"),
    }


def run_installer(tmp_path, exit_code=0):
    repository = Path(__file__).parents[1]
    fake_uv = tmp_path / "uv"
    fake_uv.write_text("#!/bin/sh\nexit 0\n")
    fake_uv.chmod(0o755)
    env = os.environ | fake_recorder_build(tmp_path, exit_code) | {
        "STT_LOCAL_PLIST_DIR": str(tmp_path / "LaunchAgents"),
        "STT_LOCAL_UV_PATH": str(fake_uv),
        "STT_LOCAL_SKIP_LAUNCHCTL": "1",
    }
    return repository, subprocess.run(
        ["zsh", str(repository / "scripts" / "install-launch-agent.sh")],
        cwd=repository,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_install_script_builds_meeting_recorder(tmp_path):
    repository, completed = run_installer(tmp_path)

    arguments = (tmp_path / "swiftc-args").read_text()
    recorder = tmp_path / "build" / "stt-local-meeting-recorder"
    assert str(repository / "native" / "meeting-recorder" / "main.swift") in arguments
    assert f"-o {recorder}" in arguments
    assert "-target arm64-apple-macos14.2" in arguments or "x86_64" in arguments
    assert recorder.read_text().strip() == "built"
    assert "Built the meeting recorder" in completed.stdout


def test_install_script_continues_when_recorder_build_fails(tmp_path):
    _, completed = run_installer(tmp_path, exit_code=1)

    assert "meeting recorder" in completed.stderr
    assert (tmp_path / "LaunchAgents" / "com.asempruch.stt-local.plist").exists()
