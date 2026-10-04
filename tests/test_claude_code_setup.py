import json

import pytest

from stt_local import claude_code_setup
from stt_local.claude_code_setup import SetupError, install, uninstall


@pytest.fixture
def executable(tmp_path):
    path = tmp_path / "repo" / ".venv" / "bin" / "stt-local-speech"
    path.parent.mkdir(parents=True)
    path.touch()
    return path


def read_settings(config_dir):
    return json.loads((config_dir / "settings.json").read_text())


def our_commands(settings):
    return {
        event: [hook["command"] for group in groups for hook in group["hooks"]]
        for event, groups in settings["hooks"].items()
    }


def test_install_into_empty_config_adds_skill_and_both_hooks(tmp_path, executable):
    config = tmp_path / "claude"

    install(config, executable)

    assert our_commands(read_settings(config)) == {
        "Stop": [f"{executable} claude-stop-hook"],
        "UserPromptSubmit": [f"{executable} claude-prompt-hook"],
    }
    hook = read_settings(config)["hooks"]["Stop"][0]["hooks"][0]
    assert hook["type"] == "command" and hook["timeout"] == 5
    skill = config / "skills" / "voice-mode"
    source = claude_code_setup.SKILL_SOURCE
    assert (skill / "SKILL.md").read_text() == (source / "SKILL.md").read_text()
    assert "name: voice-mode" in (skill / "SKILL.md").read_text()
    assert "disable-model-invocation: true" in (skill / "SKILL.md").read_text()
    assert "allow_implicit_invocation: false" in (
        skill / "agents" / "openai.yaml"
    ).read_text()


def test_install_keeps_other_settings_and_hooks(tmp_path, executable):
    config = tmp_path / "claude"
    config.mkdir()
    other = {"hooks": [{"type": "command", "command": "notify-me"}]}
    original = {"model": "opus", "hooks": {"Stop": [other], "PreToolUse": [other]}}
    (config / "settings.json").write_text(json.dumps(original))

    install(config, executable)

    settings = read_settings(config)
    assert settings["model"] == "opus"
    assert settings["hooks"]["PreToolUse"] == [other]
    assert our_commands(settings)["Stop"] == [
        "notify-me",
        f"{executable} claude-stop-hook",
    ]
    assert json.loads((config / "settings.json.bak").read_text()) == original


def test_reinstall_replaces_old_checkout_hooks_without_duplicating(
    tmp_path, executable
):
    config = tmp_path / "claude"
    config.mkdir()
    old = "~/old/stt-local/.venv/bin/stt-local-speech claude-stop-hook"
    (config / "settings.json").write_text(
        json.dumps({"hooks": {"Stop": [{"hooks": [{"command": old}]}]}})
    )

    install(config, executable)
    first = (config / "settings.json").read_text()
    messages = install(config, executable)

    assert (config / "settings.json").read_text() == first
    assert our_commands(read_settings(config))["Stop"] == [
        f"{executable} claude-stop-hook"
    ]
    assert messages[1].startswith("Kept")


def test_paths_with_spaces_are_quoted(tmp_path):
    executable = tmp_path / "My Repos" / "stt-local-speech"
    executable.parent.mkdir()
    executable.touch()
    config = tmp_path / "claude"

    install(config, executable)
    install(config, executable)

    assert our_commands(read_settings(config))["Stop"] == [
        f"'{executable}' claude-stop-hook"
    ]


def test_invalid_settings_are_left_alone(tmp_path, executable):
    config = tmp_path / "claude"
    config.mkdir()
    (config / "settings.json").write_text("{not json")

    with pytest.raises(SetupError, match="not valid JSON"):
        install(config, executable)

    assert (config / "settings.json").read_text() == "{not json"
    assert not (config / "skills").exists()


def test_install_replaces_the_older_command_file(tmp_path, executable):
    config = tmp_path / "claude"
    legacy = config / "commands" / "voice-mode.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("The STT Local UserPromptSubmit hook normally intercepts...")
    unrelated = config / "commands" / "other.md"
    unrelated.write_text("mine")

    messages = install(config, executable)

    assert not legacy.exists() and unrelated.exists()
    assert any("Removed the older" in message for message in messages)


def test_install_refuses_to_replace_someone_elses_voice_mode_skill(
    tmp_path, executable
):
    config = tmp_path / "claude"
    skill = config / "skills" / "voice-mode" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: voice-mode\n---\nMy own thing.")

    with pytest.raises(SetupError, match="not STT Local's"):
        install(config, executable)

    assert skill.read_text().endswith("My own thing.")
    assert not (config / "settings.json").exists()


def test_skills_directory_may_be_a_symlink(tmp_path, executable):
    shared = tmp_path / "agents" / "skills"
    shared.mkdir(parents=True)
    config = tmp_path / "claude"
    config.mkdir()
    (config / "skills").symlink_to(shared)

    install(config, executable)

    assert (shared / "voice-mode" / "SKILL.md").exists()


def test_missing_executable_points_at_the_installer(tmp_path):
    with pytest.raises(SetupError, match="install-launch-agent.sh"):
        install(tmp_path / "claude", tmp_path / "missing")


def test_uninstall_removes_only_this_apps_pieces(tmp_path, executable):
    config = tmp_path / "claude"
    config.mkdir()
    other = {"hooks": [{"type": "command", "command": "notify-me"}]}
    (config / "settings.json").write_text(
        json.dumps({"model": "opus", "hooks": {"Stop": [other]}})
    )
    install(config, executable)

    uninstall(config)

    assert read_settings(config) == {"model": "opus", "hooks": {"Stop": [other]}}
    assert not (config / "skills" / "voice-mode").exists()
    assert uninstall(config) == ["Nothing to remove"]


def test_claude_config_dir_follows_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "custom"))

    assert claude_code_setup.claude_config_dir() == tmp_path / "custom"

    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    monkeypatch.setenv("HOME", str(tmp_path))

    assert claude_code_setup.claude_config_dir() == tmp_path / ".claude"
