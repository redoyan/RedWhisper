import unittest
from unittest.mock import Mock

from settings_store import AppSettings
from windows_settings import (
    WINDOWS_HOTKEYS,
    disable_combobox_mouse_wheel,
    windows_custom_hotkey_value,
    windows_default_settings,
    windows_virtual_key_label,
)


class WindowsSettingsTests(unittest.TestCase):
    def test_mouse_wheel_does_not_change_dropdown_values(self) -> None:
        root = Mock()

        disable_combobox_mouse_wheel(root)

        root.bind_class.assert_called_once()
        class_name, sequence, handler = root.bind_class.call_args.args
        self.assertEqual((class_name, sequence), ("TCombobox", "<MouseWheel>"))
        # None rather than "break", so the settings page still scrolls.
        self.assertIsNone(handler(Mock(delta=-120)))

    def test_untouched_mac_default_becomes_windows_shortcut(self) -> None:
        converted = windows_default_settings(AppSettings())

        self.assertEqual(converted.hotkey_preset, "right_control")
        self.assertIn(converted.hotkey_preset, WINDOWS_HOTKEYS)

    def test_existing_compatible_shortcut_is_preserved(self) -> None:
        settings = AppSettings(hotkey_preset="f8")

        self.assertEqual(windows_default_settings(settings), settings)

    def test_old_multi_key_windows_shortcut_is_migrated(self) -> None:
        settings = AppSettings(hotkey_preset="control_alt_space")

        converted = windows_default_settings(settings)

        self.assertEqual(converted.hotkey_preset, "right_control")
        self.assertNotIn("control_alt_space", WINDOWS_HOTKEYS)

    def test_custom_single_key_is_preserved(self) -> None:
        custom = windows_custom_hotkey_value(0x7B)
        settings = AppSettings(hotkey_preset=custom)

        self.assertEqual(windows_default_settings(settings), settings)
        self.assertEqual(windows_virtual_key_label(0x7B), "F12")
        self.assertIn("F12", custom)

    def test_saved_microphone_is_preserved(self) -> None:
        settings = AppSettings(
            hotkey_preset="right_control",
            input_device=9,
            input_device_name="Duplicate microphone",
        )

        converted = windows_default_settings(settings)

        self.assertEqual(converted.input_device, 9)
        self.assertEqual(converted.input_device_name, "Duplicate microphone")

    def test_cloud_restructuring_is_preserved_on_windows(self) -> None:
        settings = AppSettings(
            hotkey_preset="right_control",
            post_process_openai=True,
            openai_rewrite_model="gpt-5-mini",
        )

        self.assertEqual(windows_default_settings(settings), settings)

    def test_macos_local_restructuring_is_disabled_on_windows(self) -> None:
        converted = windows_default_settings(
            AppSettings(
                hotkey_preset="right_control",
                post_process_local=True,
            )
        )

        self.assertFalse(converted.post_process_local)


if __name__ == "__main__":
    unittest.main()
