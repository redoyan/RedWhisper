import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from audio_devices import InputDevice
from hotkey_config import HotkeyConfig
from settings_store import AppSettings
from voxtape import (
    MLXWhisperApp,
    QuartzHotkeyListener,
    quartz_single_modifier_mask,
    restructuring_label,
)


class MLXWhisperAppTests(unittest.TestCase):
    def test_restructuring_status_reflects_saved_provider(self) -> None:
        self.assertEqual(restructuring_label(AppSettings()), "Off")
        self.assertEqual(
            restructuring_label(
                AppSettings(
                    post_process_openrouter=True,
                    openrouter_model="openrouter/free",
                )
            ),
            "OpenRouter openrouter/free",
        )

    def test_missing_accessibility_permission_does_not_exit_app(self) -> None:
        listener = QuartzHotkeyListener(HotkeyConfig("fn"), Mock(), Mock())

        with patch("voxtape.Quartz.CGEventTapCreate", return_value=None):
            self.assertFalse(listener.start())

    def test_modifier_only_shortcuts_use_quartz_modifier_flags(self) -> None:
        from voxtape import Quartz

        self.assertEqual(
            quartz_single_modifier_mask("control"),
            Quartz.kCGEventFlagMaskControl,
        )
        self.assertEqual(
            quartz_single_modifier_mask("option"),
            Quartz.kCGEventFlagMaskAlternate,
        )

    def test_disabled_event_tap_reenables_and_releases_held_shortcut(self) -> None:
        from voxtape import Quartz

        callback = None
        tap = object()
        on_release = Mock()

        def create_tap(_location, _placement, _options, _mask, handler, _refcon):
            nonlocal callback
            callback = handler
            return tap

        listener = QuartzHotkeyListener(HotkeyConfig("fn"), Mock(), on_release)
        with (
            patch("voxtape.Quartz.CGEventTapCreate", side_effect=create_tap),
            patch(
                "voxtape.Quartz.CFMachPortCreateRunLoopSource",
                return_value=object(),
            ),
            patch("voxtape.Quartz.CFRunLoopGetCurrent", return_value=object()),
            patch("voxtape.Quartz.CFRunLoopAddSource"),
            patch("voxtape.Quartz.CGEventTapEnable") as enable_tap,
        ):
            self.assertTrue(listener.start())
            self.assertTrue(listener._latch.press())
            enable_tap.reset_mock()

            callback(
                None,
                Quartz.kCGEventTapDisabledByTimeout,
                object(),
                None,
            )

        enable_tap.assert_called_once_with(tap, True)
        on_release.assert_called_once_with()
        self.assertFalse(listener._latch.is_pressed)

    def test_disabling_listener_releases_held_shortcut(self) -> None:
        listener = QuartzHotkeyListener(HotkeyConfig("fn"), Mock(), Mock())
        listener._tap = object()
        self.assertTrue(listener._latch.press())

        with patch("voxtape.Quartz.CGEventTapEnable"):
            listener.set_enabled(False)

        listener.on_release.assert_called_once_with()
        self.assertFalse(listener._latch.is_pressed)

    def test_stale_audio_device_is_refreshed_and_retried(self) -> None:
        app = object.__new__(MLXWhisperApp)
        app.is_recording = False
        app._transcribing = False
        app._ready = True
        app.audio_chunks = []
        app.stream = None
        app.input_device = 1
        app.input_device_name = "MacBook Pro Microphone"
        app.input_devices = [
            InputDevice(1, "MacBook Pro Microphone", 1),
        ]
        app._audio_callback = Mock()
        app._set_menu_status = Mock()
        app.record_menu_item = SimpleNamespace(title="Start Recording")
        app.microphone_menu_item = SimpleNamespace(
            title="Microphone: MacBook Pro Microphone"
        )
        app.recording_indicator = Mock()
        app._latest_audio_level = 0.0
        app._level_timer = Mock()
        app._play_sound = Mock()

        recovered_stream = Mock()
        refreshed_devices = [
            InputDevice(4, "MacBook Pro Microphone", 1),
        ]

        with (
            patch(
                "voxtape.sd.InputStream",
                side_effect=[RuntimeError("stale Core Audio device"), recovered_stream],
            ) as input_stream,
            patch(
                "voxtape.refresh_input_devices",
                return_value=refreshed_devices,
            ) as refresh_devices,
            patch("voxtape.rumps.notification") as notification,
        ):
            app._start_recording("Fn")

        self.assertEqual(
            [item.kwargs["device"] for item in input_stream.call_args_list],
            [1, 4],
        )
        refresh_devices.assert_called_once_with(unittest.mock.ANY)
        recovered_stream.start.assert_called_once_with()
        self.assertTrue(app.is_recording)
        self.assertEqual(app.input_device, 4)
        self.assertEqual(app.input_device_name, "MacBook Pro Microphone")
        self.assertEqual(
            app.microphone_menu_item.title,
            "Microphone: MacBook Pro Microphone",
        )
        notification.assert_not_called()

    def test_disconnected_microphone_falls_back_to_available_physical_input(
        self,
    ) -> None:
        app = object.__new__(MLXWhisperApp)
        app.input_device = 5
        app.input_device_name = "Bose QC Ultra Headphones"
        app.preferred_input_device_name = "Bose QC Ultra Headphones"
        app.input_devices = []
        app.microphone_menu_item = SimpleNamespace(
            title="Microphone: Bose QC Ultra Headphones"
        )
        built_in = InputDevice(1, "MacBook Pro Microphone", 1)

        with (
            patch("voxtape.refresh_input_devices", return_value=[built_in]),
            patch("voxtape.sd.default", SimpleNamespace(device=[1, 2])),
        ):
            app._refresh_selected_input_device()

        self.assertEqual(app.input_device, 1)
        self.assertEqual(app.input_device_name, "MacBook Pro Microphone")
        self.assertEqual(
            app.microphone_menu_item.title,
            "Microphone: MacBook Pro Microphone",
        )

    def test_settings_restart_quits_child_for_bundle_supervisor(self) -> None:
        fake_app = SimpleNamespace(
            recording_indicator=Mock(),
            _level_timer=Mock(),
            stream=None,
            title="🎙️",
            _set_menu_status=Mock(),
            record_menu_item=SimpleNamespace(title="Start Recording"),
        )

        with patch("voxtape.os._exit") as exit_process:
            MLXWhisperApp._restart_after_settings_save(fake_app)

        fake_app.recording_indicator.hide.assert_called_once_with()
        fake_app._level_timer.stop.assert_called_once_with()
        fake_app._set_menu_status.assert_called_once_with("⏳")
        exit_process.assert_called_once_with(75)

    def test_settings_can_suspend_and_restore_global_shortcuts(self) -> None:
        listeners = [Mock(), Mock()]
        fake_app = SimpleNamespace(_hotkey_listeners=listeners)

        MLXWhisperApp._set_hotkeys_enabled(fake_app, False)
        MLXWhisperApp._set_hotkeys_enabled(fake_app, True)

        for listener in listeners:
            self.assertEqual(
                listener.set_enabled.call_args_list,
                [unittest.mock.call(False), unittest.mock.call(True)],
            )

    def test_settings_import_failure_restores_global_shortcuts(self) -> None:
        fake_app = SimpleNamespace(
            is_recording=False,
            _transcribing=False,
            _set_hotkeys_enabled=Mock(),
            _resume_hotkeys=Mock(),
        )

        with (
            patch.dict("sys.modules", {"native_settings": None}),
            self.assertRaises(ModuleNotFoundError),
        ):
            MLXWhisperApp._open_settings(fake_app)

        self.assertEqual(
            fake_app._set_hotkeys_enabled.call_args_list,
            [call(False)],
        )
        fake_app._resume_hotkeys.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
