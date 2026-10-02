import unittest
import time
from unittest.mock import patch

import AppKit

from audio_devices import InputDevice
from native_settings import NativeSettingsController, TRANSCRIPTION_MODEL_VALUES
from settings_store import AppSettings


class NativeSettingsTests(unittest.TestCase):
    def setUp(self):
        # Unit tests never access the user's subscription or launch OAuth.
        self.run_chatgpt = NativeSettingsController._run_chatgpt
        patcher = patch.object(NativeSettingsController, "_run_chatgpt")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_background_model_sync_delivers_results_on_main_thread(self):
        controller = NativeSettingsController.alloc().init()
        controller.configure(AppSettings(), [], lambda value: None)
        state = {"connected": True, "active": "test-account", "models": [
            {"slug": "first", "display_name": "Same name"},
            {"slug": "second", "display_name": "Same name"},
        ]}
        with patch.object(controller.chatgpt, "snapshot", return_value=state), patch.object(controller.chatgpt, "sync_models"):
            self.run_chatgpt(controller, "refresh")
            deadline = time.monotonic() + 3
            while controller._chatgpt_busy and time.monotonic() < deadline:
                AppKit.NSRunLoop.currentRunLoop().runUntilDate_(AppKit.NSDate.dateWithTimeIntervalSinceNow_(0.01))
        self.assertFalse(controller._chatgpt_busy)
        self.assertEqual(controller.chatgpt_model_values, ["", "first", "second"])
        self.assertEqual(controller.chatgpt_model_popup.numberOfItems(), 3)

    @classmethod
    def setUpClass(cls) -> None:
        AppKit.NSApplication.sharedApplication()

    def test_window_loads_and_saves_secondary_shortcut(self) -> None:
        saved = []
        controller = NativeSettingsController.alloc().init()
        controller.configure(
            AppSettings(secondary_hotkey_preset="control_space", input_device=1),
            [InputDevice(1, "MacBook Pro Microphone", 1)],
            saved.append,
        )

        controller.secondary_shortcut_value = "control_space"
        controller.saveSettings_(None)

        self.assertEqual(controller.window.title(), "Red Whisper")
        self.assertEqual(saved[0].hotkey_preset, "fn")
        self.assertEqual(saved[0].secondary_hotkey_preset, "control_space")
        self.assertEqual(saved[0].input_device, 1)

    def test_cloud_restructuring_model_is_saved(self) -> None:
        saved = []
        controller = NativeSettingsController.alloc().init()
        controller.configure(AppSettings(), [], saved.append)
        controller.rewrite_engine_popup.selectItemAtIndex_(2)
        controller.openai_rewrite_model_popup.selectItemAtIndex_(0)

        with patch(
            "native_settings.openai_key_from_environment",
            return_value="test-key",
        ):
            controller.saveSettings_(None)

        self.assertTrue(saved[0].post_process_openai)
        self.assertFalse(saved[0].post_process_local)
        self.assertEqual(saved[0].openai_rewrite_model, "gpt-5-nano")

    def test_subscription_model_sync_preserves_transcription_and_selected_model(self):
        saved = []
        controller = NativeSettingsController.alloc().init()
        controller.configure(AppSettings(engine="openai", openai_model="gpt-4o-mini-transcribe", post_process_chatgpt=True, chatgpt_rewrite_model="future-model"), [], saved.append)
        controller.chatGPTFinished_({"state": {"connected": True, "active": "test-account", "models": [
            {"slug": "new-model", "display_name": "New Model"},
            {"slug": "future-model", "display_name": "Future Model"},
        ]}, "message": ""})
        with patch("native_settings.openai_key_from_environment", return_value="test-key"):
            controller.saveSettings_(None)
        self.assertEqual(saved[0].engine, "openai")
        self.assertEqual(saved[0].openai_model, "gpt-4o-mini-transcribe")
        self.assertEqual(saved[0].chatgpt_rewrite_model, "future-model")
        self.assertTrue(saved[0].post_process_chatgpt)
        self.assertFalse(saved[0].post_process_openai)
        self.assertEqual(controller.chatgpt_model_values, ["", "new-model", "future-model"])

    def test_subscription_settings_reject_missing_connection_and_removed_model(self):
        controller = NativeSettingsController.alloc().init()
        saved = []
        controller.configure(AppSettings(post_process_chatgpt=True, chatgpt_rewrite_model="removed"), [], saved.append)
        with patch.object(NativeSettingsController, "_show_error") as error:
            controller.saveSettings_(None)
            self.assertIn("Connect", error.call_args.args[0])
            controller.chatGPTFinished_({"state": {"connected": True, "models": [{"slug": "available", "display_name": "Available"}]}, "message": ""})
            controller.saveSettings_(None)
            self.assertIn("unavailable", error.call_args.args[0])
        self.assertEqual(saved, [])

    def test_subscription_account_window_and_switch_reset_selection(self):
        controller = NativeSettingsController.alloc().init()
        controller.configure(AppSettings(chatgpt_rewrite_model="old-model"), [], lambda value: None)
        controller.showChatGPTAccount_(None)
        controller._chatgpt_state = {"active": "old-account"}
        controller.chatGPTFinished_({"state": {"connected": True, "email": "test@example.com", "active": "new-account", "accounts": [{"id": "new-account", "label": "Test account"}], "models": [{"slug": "new-model", "display_name": "New Model"}]}, "message": ""})
        self.assertEqual(controller.chatgpt_model_values, ["", "new-model"])
        self.assertEqual(controller.chatgpt_model_popup.indexOfSelectedItem(), 0)
        self.assertIn("1 models", controller.chatgpt_status.stringValue())
        controller.cancelSettings_(None)

    def test_transcription_model_selection_sets_provider_and_model(self) -> None:
        saved = []
        controller = NativeSettingsController.alloc().init()
        controller.configure(AppSettings(), [], saved.append)
        controller.transcription_model_popup.selectItemAtIndex_(
            TRANSCRIPTION_MODEL_VALUES.index(
                ("openai", "gpt-4o-mini-transcribe")
            )
        )

        with patch(
            "native_settings.openai_key_from_environment",
            return_value="test-key",
        ):
            controller.saveSettings_(None)

        self.assertEqual(saved[0].engine, "openai")
        self.assertEqual(saved[0].openai_model, "gpt-4o-mini-transcribe")

    def test_openrouter_provider_key_and_model_are_saved(self) -> None:
        saved = []
        saved_keys = []
        controller = NativeSettingsController.alloc().init()
        controller.configure(
            AppSettings(
                openrouter_models=(
                    "openrouter/free",
                    "google/gemma-3-27b-it:free",
                )
            ),
            [],
            saved.append,
        )
        controller.rewrite_engine_popup.selectItemAtIndex_(3)
        controller.rewriteEngineChanged_(None)
        controller.openrouter_model_field.setStringValue_(
            "meta-llama/llama-3.3-70b-instruct:free"
        )
        controller.openrouter_key_field.setStringValue_("router-key")

        with patch(
            "native_settings.save_openrouter_key",
            side_effect=saved_keys.append,
        ), patch(
            "native_settings.openrouter_key_from_environment",
            return_value="router-key",
        ):
            controller.saveSettings_(None)

        self.assertEqual(saved_keys, ["router-key"])
        self.assertIn(
            "google/gemma-3-27b-it:free",
            controller.openrouter_model_field.objectValues(),
        )
        self.assertTrue(saved[0].post_process_openrouter)
        self.assertFalse(saved[0].post_process_openai)
        self.assertEqual(
            saved[0].openrouter_model,
            "meta-llama/llama-3.3-70b-instruct:free",
        )
        self.assertEqual(
            saved[0].openrouter_models,
            (
                "openrouter/free",
                "google/gemma-3-27b-it:free",
                "meta-llama/llama-3.3-70b-instruct:free",
            ),
        )

    def test_elevenlabs_transcription_key_and_model_are_saved(self) -> None:
        saved = []
        saved_keys = []
        controller = NativeSettingsController.alloc().init()
        controller.configure(AppSettings(), [], saved.append)
        model_labels = [
            item.title()
            for item in controller.transcription_model_popup.itemArray()
        ]
        self.assertIn("Scribe v2 — ElevenLabs", model_labels)
        self.assertIn(
            "Scribe v2 Realtime — ElevenLabs streaming",
            model_labels,
        )
        self.assertIn("Scribe v1 — ElevenLabs legacy", model_labels)
        self.assertIn("GPT Transcribe — OpenAI recommended", model_labels)
        self.assertNotIn("Eleven v3", model_labels)
        controller.transcription_model_popup.selectItemAtIndex_(
            TRANSCRIPTION_MODEL_VALUES.index(("elevenlabs", "scribe_v2"))
        )
        controller.elevenlabs_key_field.setStringValue_("eleven-key")

        with patch(
            "native_settings.save_elevenlabs_key",
            side_effect=saved_keys.append,
        ), patch(
            "native_settings.elevenlabs_key_from_environment",
            return_value="eleven-key",
        ):
            controller.saveSettings_(None)

        self.assertEqual(saved_keys, ["eleven-key"])
        self.assertEqual(saved[0].engine, "elevenlabs")
        self.assertEqual(saved[0].elevenlabs_model, "scribe_v2")

    def test_control_copy_and_paste_are_mapped_for_text_editing(self) -> None:
        controller = NativeSettingsController.alloc().init()
        controller.configure(AppSettings(), [], lambda _settings: None)

        def event(character: str):
            return AppKit.NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
                AppKit.NSEventTypeKeyDown,
                AppKit.NSMakePoint(0, 0),
                AppKit.NSEventModifierFlagControl,
                0,
                0,
                None,
                character,
                character,
                False,
                0,
            )

        self.assertEqual(controller._control_edit_action(event("c")), "copy:")
        self.assertEqual(controller._control_edit_action(event("v")), "paste:")

    def test_shortcut_buttons_display_saved_custom_bindings(self) -> None:
        saved = []
        custom = "custom:15:command:R"
        controller = NativeSettingsController.alloc().init()
        controller.configure(AppSettings(hotkey_preset=custom), [], saved.append)

        self.assertIn("⌘R", controller.primary_shortcut_button.title())
        self.assertIn("click, then press", controller.secondary_shortcut_button.title())

    def test_click_then_key_press_captures_custom_shortcut(self) -> None:
        controller = NativeSettingsController.alloc().init()
        controller.configure(AppSettings(), [], lambda _settings: None)
        event = AppKit.NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
            AppKit.NSEventTypeKeyDown,
            AppKit.NSMakePoint(0, 0),
            AppKit.NSEventModifierFlagControl,
            0,
            0,
            None,
            "r",
            "r",
            False,
            15,
        )

        controller.captureSecondary_(None)
        returned = controller._handle_shortcut_event(event)

        self.assertIsNone(returned)
        self.assertEqual(
            controller.secondary_shortcut_value,
            "custom:15:control:R",
        )
        self.assertIn("⌃R", controller.secondary_shortcut_button.title())

    def test_press_then_release_captures_modifier_only_shortcut(self) -> None:
        class FlagsEvent:
            def __init__(self, flags):
                self.flags = flags

            def type(self):
                return AppKit.NSEventTypeFlagsChanged

            def modifierFlags(self):
                return self.flags

        controller = NativeSettingsController.alloc().init()
        controller.configure(AppSettings(), [], lambda _settings: None)
        controller.captureSecondary_(None)

        controller._handle_shortcut_event(
            FlagsEvent(AppKit.NSEventModifierFlagOption)
        )
        self.assertIsNone(controller.secondary_shortcut_value)
        self.assertIn("Release Option", controller.shortcut_help.stringValue())

        controller._handle_shortcut_event(FlagsEvent(0))
        self.assertEqual(
            controller.secondary_shortcut_value,
            "modifier:option",
        )
        self.assertIn("⌥ Option", controller.secondary_shortcut_button.title())

    def test_closing_settings_restores_global_shortcuts(self) -> None:
        closed = []
        controller = NativeSettingsController.alloc().init()
        controller.configure(
            AppSettings(),
            [],
            lambda _settings: None,
            lambda: closed.append(True),
        )

        controller.show()
        controller.cancelSettings_(None)
        controller.windowWillClose_(None)

        self.assertEqual(closed, [True])

    def test_bluetooth_microphone_appears_and_is_mapped_by_name(self) -> None:
        available = [InputDevice(1, "MacBook Pro Microphone", 1)]
        saved = []
        controller = NativeSettingsController.alloc().init()
        controller.configure(
            AppSettings(
                input_device=4,
                input_device_name="AirPods Microphone",
            ),
            list(available),
            saved.append,
            device_provider=lambda: list(available),
        )

        available.append(InputDevice(7, "AirPods Microphone", 1))
        controller.refreshAudioDevices_(None)
        controller.saveSettings_(None)

        self.assertIn(
            "AirPods Microphone",
            [item.title() for item in controller.device_popup.itemArray()],
        )
        self.assertEqual(saved[0].input_device, 7)
        self.assertEqual(saved[0].input_device_name, "AirPods Microphone")

    def test_show_brands_python_process_as_red_whisper(self) -> None:
        controller = NativeSettingsController.alloc().init()
        controller.configure(AppSettings(), [], lambda _settings: None)

        controller.show()

        self.assertEqual(
            AppKit.NSProcessInfo.processInfo().processName(),
            "Red Whisper",
        )
        edit_menu = next(
            item.submenu()
            for item in AppKit.NSApplication.sharedApplication().mainMenu().itemArray()
            if item.title() == "Edit"
        )
        self.assertIn("Copy", [item.title() for item in edit_menu.itemArray()])
        self.assertIn("Paste", [item.title() for item in edit_menu.itemArray()])
        controller.cancelSettings_(None)


if __name__ == "__main__":
    unittest.main()
