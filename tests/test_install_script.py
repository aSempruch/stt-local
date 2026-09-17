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
    env = os.environ | {
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
    assert plist["ProgramArguments"] == [
        str(repository / ".venv" / "bin" / "stt-local")
    ]
    assert plist["WorkingDirectory"] == str(repository)
    assert plist["StandardOutPath"] == "/tmp/stt-local.stdout.log"
    assert plist["StandardErrorPath"] == "/tmp/stt-local.stderr.log"
    assert "printf '%s\\n' toggle > /tmp/stt-command" in completed.stdout
    assert "/tmp/stt-toggle" not in completed.stdout


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
    env = os.environ | {
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
