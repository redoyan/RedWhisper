import unittest

from hotkey_config import (
    ActiveHotkeys,
    HotkeyConfig,
    PushToTalkLatch,
    custom_hotkey_value,
    modifier_hotkey_value,
)


class HotkeyConfigTests(unittest.TestCase):
    def test_default_shortcut_is_single_fn_key(self) -> None:
        hotkey = HotkeyConfig()
        self.assertEqual(hotkey.label, "Fn")
        self.assertTrue(hotkey.is_fn)
        self.assertIsNone(hotkey.keycode)

    def test_command_shift_space_label(self) -> None:
        hotkey = HotkeyConfig("command_shift_space")
        self.assertEqual(hotkey.label, "⌘⇧Space")
        self.assertEqual(hotkey.keycode, 0x31)

    def test_invalid_shortcut_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            HotkeyConfig("option_q")

    def test_push_to_talk_ignores_key_repeat(self) -> None:
        latch = PushToTalkLatch()
        self.assertTrue(latch.press())
        self.assertFalse(latch.press())
        self.assertTrue(latch.release())
        self.assertFalse(latch.release())

    def test_secondary_shortcut_does_not_stop_while_primary_is_held(self) -> None:
        active = ActiveHotkeys()

        self.assertTrue(active.press("fn"))
        self.assertFalse(active.press("control_space"))
        self.assertFalse(active.release("control_space"))
        self.assertTrue(active.release("fn"))

    def test_custom_shortcut_round_trip(self) -> None:
        value = custom_hotkey_value(49, ("control", "shift"), "Space")
        shortcut = HotkeyConfig(value)

        self.assertEqual(shortcut.keycode, 49)
        self.assertEqual(shortcut.modifiers, ("control", "shift"))
        self.assertEqual(shortcut.label, "⌃⇧Space")

    def test_modifier_only_shortcuts_round_trip(self) -> None:
        control = HotkeyConfig(modifier_hotkey_value("control"))
        option = HotkeyConfig(modifier_hotkey_value("option"))

        self.assertEqual(control.modifier_only, "control")
        self.assertEqual(control.label, "⌃ Control")
        self.assertIsNone(control.keycode)
        self.assertEqual(option.modifier_only, "option")
        self.assertEqual(option.label, "⌥ Option")


if __name__ == "__main__":
    unittest.main()
