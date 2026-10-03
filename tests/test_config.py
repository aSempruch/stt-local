from stt_local.config import AppConfig, ConfigStore


def test_missing_config_returns_defaults(tmp_path):
    assert ConfigStore(tmp_path).load() == AppConfig()


def test_corrupt_config_returns_defaults_without_deleting_source(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text("not-json")

    assert ConfigStore(tmp_path).load() == AppConfig()
    assert config_path.read_text() == "not-json"


def test_invalid_values_return_defaults(tmp_path):
    (tmp_path / "config.json").write_text(
        '{"selected_processor": 42, "idle_unload_seconds": -1}'
    )

    assert ConfigStore(tmp_path).load() == AppConfig()


def test_save_is_round_trippable_and_leaves_no_temporary_file(tmp_path):
    store = ConfigStore(tmp_path)
    expected = AppConfig(
        selected_processor="sentence_case", idle_unload_seconds=30.0
    )

    store.save(expected)

    assert store.load() == expected
    assert not (tmp_path / "config.json.tmp").exists()


def test_bias_prompt_round_trips(tmp_path):
    store = ConfigStore(tmp_path)
    expected = AppConfig(bias_prompt="Komodo, Hermes, Claub")

    store.save(expected)

    assert store.load() == expected


def test_config_without_bias_prompt_keeps_other_values(tmp_path):
    (tmp_path / "config.json").write_text(
        '{"selected_processor": "casual_discord", "idle_unload_seconds": 30}'
    )

    assert ConfigStore(tmp_path).load() == AppConfig(
        selected_processor="casual_discord", idle_unload_seconds=30.0
    )


def test_invalid_bias_prompt_returns_defaults(tmp_path):
    (tmp_path / "config.json").write_text(
        '{"selected_processor": "plain_text", "idle_unload_seconds": 30,'
        ' "bias_prompt": 7}'
    )

    assert ConfigStore(tmp_path).load() == AppConfig()


def test_speech_settings_round_trip(tmp_path):
    store = ConfigStore(tmp_path)
    expected = AppConfig(speak_claude_replies=True, speech_voice="am_michael")

    store.save(expected)

    assert store.load() == expected


def test_config_without_speech_settings_defaults_them(tmp_path):
    (tmp_path / "config.json").write_text(
        '{"selected_processor": "plain_text", "idle_unload_seconds": 30}'
    )

    config = ConfigStore(tmp_path).load()

    assert config.speak_claude_replies is False
    assert config.speech_voice == "af_heart"
