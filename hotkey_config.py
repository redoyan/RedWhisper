"""Supported global recording shortcuts."""

from dataclasses import dataclass


KEY_CODES = {"D": 0x02, "R": 0x0F, "Space": 0x31}
MODIFIER_LABELS = {
    "option": "⌥",
    "control": "⌃",
    "command": "⌘",
    "shift": "⇧",
}
MODIFIER_ORDER = ("control", "option", "shift", "command")
MODIFIER_DISPLAY_ORDER = ("control", "option", "command", "shift")
CUSTOM_PREFIX = "custom:"
MODIFIER_PREFIX = "modifier:"


def modifier_hotkey_value(modifier: str) -> str:
    if modifier not in MODIFIER_ORDER:
        raise ValueError("Invalid modifier-only recording shortcut")
    return f"{MODIFIER_PREFIX}{modifier}"


def custom_hotkey_value(
    keycode: int,
    modifiers: tuple[str, ...],
    key_label: str,
) -> str:
    if not 0 <= keycode <= 255:
        raise ValueError("Invalid shortcut key code")
    normalized = tuple(name for name in MODIFIER_ORDER if name in modifiers)
    if set(modifiers) - set(MODIFIER_ORDER):
        raise ValueError("Invalid shortcut modifiers")
    clean_label = key_label.replace(":", "").strip()[:24] or f"Key {keycode}"
    return f"{CUSTOM_PREFIX}{keycode}:{'+'.join(normalized)}:{clean_label}"


@dataclass(frozen=True)
class HotkeyConfig:
    preset: str = "fn"

    def __post_init__(self) -> None:
        if not isinstance(self.preset, str):
            raise ValueError("Recording shortcut must be text")
        if self.preset in HOTKEY_PRESETS:
            return
        if self.preset.startswith(MODIFIER_PREFIX):
            modifier = self.preset.removeprefix(MODIFIER_PREFIX)
            if modifier_hotkey_value(modifier) != self.preset:
                raise ValueError("Invalid modifier-only recording shortcut")
            return
        self._custom_parts()

    def _custom_parts(self) -> tuple[int, tuple[str, ...], str]:
        if not self.preset.startswith(CUSTOM_PREFIX):
            raise ValueError(f"Unsupported recording shortcut: {self.preset}")
        try:
            keycode_text, modifier_text, key_label = self.preset.removeprefix(
                CUSTOM_PREFIX
            ).split(":", 2)
            keycode = int(keycode_text)
            modifiers = tuple(filter(None, modifier_text.split("+")))
        except (TypeError, ValueError) as error:
            raise ValueError("Invalid custom recording shortcut") from error
        expected = custom_hotkey_value(keycode, modifiers, key_label)
        if expected != self.preset:
            raise ValueError("Invalid custom recording shortcut")
        return keycode, modifiers, key_label

    @property
    def is_fn(self) -> bool:
        return self.preset == "fn"

    @property
    def modifier_only(self) -> str | None:
        if self.is_fn:
            return "function"
        if self.preset.startswith(MODIFIER_PREFIX):
            return self.preset.removeprefix(MODIFIER_PREFIX)
        return None

    @property
    def modifier(self) -> str | None:
        if self.preset in HOTKEY_PRESETS:
            return HOTKEY_PRESETS[self.preset][0]
        return None

    @property
    def modifiers(self) -> tuple[str, ...]:
        if self.preset.startswith(MODIFIER_PREFIX):
            return (self.preset.removeprefix(MODIFIER_PREFIX),)
        if self.preset.startswith(CUSTOM_PREFIX):
            return self._custom_parts()[1]
        modifier = self.modifier
        if modifier == "command_shift":
            return ("shift", "command")
        return (modifier,) if modifier else ()

    @property
    def key(self) -> str | None:
        if self.preset.startswith(MODIFIER_PREFIX):
            return None
        if self.preset.startswith(CUSTOM_PREFIX):
            return self._custom_parts()[2]
        return HOTKEY_PRESETS[self.preset][1]

    @property
    def keycode(self) -> int | None:
        if self.preset.startswith(MODIFIER_PREFIX):
            return None
        if self.preset.startswith(CUSTOM_PREFIX):
            return self._custom_parts()[0]
        return KEY_CODES[self.key] if self.key else None

    @property
    def label(self) -> str:
        if self.is_fn:
            return "Fn"
        if self.preset.startswith(MODIFIER_PREFIX):
            modifier = self.preset.removeprefix(MODIFIER_PREFIX)
            names = {
                "control": "Control",
                "option": "Option",
                "shift": "Shift",
                "command": "Command",
            }
            return f"{MODIFIER_LABELS[modifier]} {names[modifier]}"
        prefix = "".join(
            MODIFIER_LABELS[name]
            for name in MODIFIER_DISPLAY_ORDER
            if name in self.modifiers
        )
        if self.preset.startswith(CUSTOM_PREFIX):
            return f"{prefix}{self.key}"
        key_label = "Space" if self.key == "Space" else self.key
        return f"{prefix}{key_label}"


HOTKEY_PRESETS: dict[str, tuple[str | None, str | None]] = {
    "fn": (None, None),
    "option_d": ("option", "D"),
    "option_space": ("option", "Space"),
    "control_space": ("control", "Space"),
    "command_r": ("command", "R"),
    "command_shift_space": ("command_shift", "Space"),
}

HOTKEY_PRESET_LABELS = {
    "fn": "Fn / Globe — single key",
    "option_d": "⌥D — Option + D",
    "option_space": "⌥Space — Option + Space",
    "control_space": "⌃Space — Control + Space",
    "command_r": "⌘R — Command + R",
    "command_shift_space": "⌘⇧Space — Command + Shift + Space",
}


class PushToTalkLatch:
    """Turns raw key transitions into one start and one stop callback."""

    def __init__(self) -> None:
        self.is_pressed = False

    def press(self) -> bool:
        if self.is_pressed:
            return False
        self.is_pressed = True
        return True

    def release(self) -> bool:
        if not self.is_pressed:
            return False
        self.is_pressed = False
        return True


class ActiveHotkeys:
    """Coordinate multiple shortcuts as one push-to-talk recording gesture."""

    def __init__(self) -> None:
        self._pressed: set[str] = set()

    def press(self, preset: str) -> bool:
        """Return True only when the first active shortcut is pressed."""
        was_empty = not self._pressed
        self._pressed.add(preset)
        return was_empty

    def release(self, preset: str) -> bool:
        """Return True only when the final active shortcut is released."""
        if preset not in self._pressed:
            return False
        self._pressed.remove(preset)
        return not self._pressed
