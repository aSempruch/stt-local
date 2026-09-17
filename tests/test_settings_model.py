import pytest

from stt_local.config import AppConfig, ConfigStore
from stt_local.processors import ProcessorRegistry
from stt_local.settings import SettingsModel


def make_model(tmp_path, selected="plain_text"):
    store = ConfigStore(tmp_path / "config")
    store.save(AppConfig(selected_processor=selected, idle_unload_seconds=25))
    registry = ProcessorRegistry(tmp_path / "processors")
    return SettingsModel(store=store, registry=registry), store, registry


def test_refresh_preserves_valid_selection(tmp_path):
    model, store, registry = make_model(tmp_path, selected="clean_up")
    registry.create("Clean Up")

    processors = model.refresh()

    assert [item.key for item in processors] == [
        "plain_text",
        "casual_discord",
        "clean_up",
    ]
    assert model.selected_processor == "clean_up"
    assert store.load().idle_unload_seconds == 25


def test_missing_selection_falls_back_and_persists_plain_text(tmp_path):
    model, store, _ = make_model(tmp_path, selected="missing")

    model.refresh()

    assert model.selected_processor == "plain_text"
    assert store.load().selected_processor == "plain_text"


def test_select_validates_and_persists(tmp_path):
    model, store, registry = make_model(tmp_path)
    registry.create("Clean Up")
    model.refresh()

    model.select("clean_up")

    assert model.selected_processor == "clean_up"
    assert store.load().selected_processor == "clean_up"
    with pytest.raises(ValueError, match="Unknown processor"):
        model.select("missing")


def test_create_selects_new_processor(tmp_path):
    model, store, _ = make_model(tmp_path)

    created = model.create("Markdown Cleanup")

    assert created.key == "markdown_cleanup"
    assert model.selected_processor == "markdown_cleanup"
    assert store.load().selected_processor == "markdown_cleanup"
