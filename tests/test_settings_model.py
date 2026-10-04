from unittest.mock import Mock

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


def test_set_bias_prompt_strips_and_persists(tmp_path):
    model, store, _ = make_model(tmp_path)

    model.set_bias_prompt("  Komodo, Hermes \n")

    assert model.bias_prompt == "Komodo, Hermes"
    assert store.load().bias_prompt == "Komodo, Hermes"
    assert store.load().idle_unload_seconds == 25


class FakeApplication:
    def __init__(self, policy, *, has_activate=True):
        self.policy = policy
        self.calls = []
        if has_activate:
            self.activate = lambda: self.calls.append("activate")

    def activationPolicy(self):
        return self.policy

    def setActivationPolicy_(self, policy):
        self.calls.append(("policy", policy))
        self.policy = policy

    def activateIgnoringOtherApps_(self, flag):
        self.calls.append(("activateIgnoringOtherApps", flag))


def test_bring_to_front_promotes_background_only_app_and_activates():
    import AppKit

    from stt_local.settings import bring_to_front

    application = FakeApplication(AppKit.NSApplicationActivationPolicyProhibited)
    window = Mock()

    bring_to_front(application, window)

    assert application.calls == [
        ("policy", AppKit.NSApplicationActivationPolicyAccessory),
        "activate",
    ]
    window.makeKeyAndOrderFront_.assert_called_once_with(None)
    window.orderFrontRegardless.assert_called_once_with()


def test_bring_to_front_keeps_existing_policy_and_falls_back_on_old_macos():
    import AppKit

    from stt_local.settings import bring_to_front

    application = FakeApplication(
        AppKit.NSApplicationActivationPolicyAccessory, has_activate=False
    )

    bring_to_front(application, Mock())

    assert application.calls == [("activateIgnoringOtherApps", True)]


def test_idle_unload_delay_validates_and_persists(tmp_path):
    model, store, _ = make_model(tmp_path)

    model.set_idle_unload_seconds(1800)

    assert model.idle_unload_seconds == 1800
    assert store.load().idle_unload_seconds == 1800
    with pytest.raises(ValueError, match="positive"):
        model.set_idle_unload_seconds(0)


def test_idle_unload_options_label_presets_and_keep_custom_value():
    from stt_local.settings import idle_unload_options

    options = idle_unload_options(600)
    assert options == [
        (60.0, "1 minute"),
        (300.0, "5 minutes"),
        (600.0, "10 minutes"),
        (1800.0, "30 minutes"),
        (3600.0, "1 hour"),
        (14400.0, "4 hours"),
    ]

    custom = idle_unload_options(25)
    assert custom[0] == (25.0, "25 seconds")
    assert len(custom) == 7


def test_gesture_timings_validate_and_persist(tmp_path):
    model, store, _ = make_model(tmp_path)
    assert model.long_press_seconds == 0.5
    assert model.double_tap_seconds == 0.2

    model.set_long_press_seconds(0.4)
    model.set_double_tap_seconds(0.3)

    assert store.load().long_press_seconds == 0.4
    assert store.load().double_tap_seconds == 0.3
    assert store.load().idle_unload_seconds == 25
    with pytest.raises(ValueError, match="positive"):
        model.set_long_press_seconds(0)
    with pytest.raises(ValueError, match="positive"):
        model.set_double_tap_seconds(-0.1)


def test_milliseconds_options_label_presets_and_keep_custom_value():
    from stt_local.settings import LONG_PRESS_CHOICES, milliseconds_options

    options = milliseconds_options(LONG_PRESS_CHOICES, 0.5)
    assert options[0] == (0.3, "300 ms")
    assert (0.5, "500 ms") in options
    assert len(options) == len(LONG_PRESS_CHOICES)

    custom = milliseconds_options(LONG_PRESS_CHOICES, 0.45)
    assert (0.45, "450 ms") in custom
    assert len(custom) == len(LONG_PRESS_CHOICES) + 1
