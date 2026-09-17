from local_dictation.config import AppConfig, ConfigStore


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
