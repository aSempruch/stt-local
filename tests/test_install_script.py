import os
import plistlib
import subprocess
from pathlib import Path


def test_install_script_generates_expected_launch_agent(tmp_path):
    repository = Path(__file__).parents[1]
    plist_directory = tmp_path / "LaunchAgents"
    log_directory = tmp_path / "Logs"
    fake_uv = tmp_path / "uv"
    fake_uv.write_text("#!/bin/sh\nexit 0\n")
    fake_uv.chmod(0o755)
    env = os.environ | {
        "LOCAL_DICTATION_PLIST_DIR": str(plist_directory),
        "LOCAL_DICTATION_LOG_DIR": str(log_directory),
        "LOCAL_DICTATION_UV_PATH": str(fake_uv),
        "LOCAL_DICTATION_SKIP_LAUNCHCTL": "1",
    }

    subprocess.run(
        ["zsh", str(repository / "scripts" / "install-launch-agent.sh")],
        cwd=repository,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    with (plist_directory / "com.local-dictation.app.plist").open("rb") as handle:
        plist = plistlib.load(handle)
    assert plist["Label"] == "com.local-dictation.app"
    assert plist["RunAtLoad"] is True
    assert plist["ProgramArguments"] == [
        str(repository / ".venv" / "bin" / "local-dictation")
    ]
    assert plist["WorkingDirectory"] == str(repository)
    assert plist["StandardOutPath"].startswith(str(log_directory))
    assert plist["StandardErrorPath"].startswith(str(log_directory))
