"""Native AppKit settings window for RedWhisper."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Callable

import AppKit
import objc

from audio_devices import InputDevice
from hotkey_config import HotkeyConfig, custom_hotkey_value, modifier_hotkey_value
from mlx_whisper_core import (
    ConfigurationError,
    DEFAULT_OPENROUTER_MODEL,
    OPENAI_REWRITE_MODELS,
    elevenlabs_key_from_environment,
    openai_key_from_environment,
    openrouter_key_from_environment,
    save_elevenlabs_key,
    save_openai_key,
    save_openrouter_key,
)
from settings_store import AppSettings


TRANSCRIPTION_MODEL_VALUES = [
    ("local", None),
    ("openai", "gpt-4o-transcribe"),
    ("openai", "gpt-4o-mini-transcribe"),
    ("elevenlabs", "scribe_v2"),
    ("elevenlabs", "scribe_v2_realtime"),
]
TRANSCRIPTION_MODEL_LABELS = [
    "Whisper Large v3 Turbo — Local",
    "GPT-4o Transcribe — OpenAI accuracy",
    "GPT-4o Mini Transcribe — OpenAI speed",
    "Scribe v2 — ElevenLabs",
    "Scribe v2 Realtime — ElevenLabs streaming",
]
REWRITE_ENGINE_VALUES = ["off", "local", "openai", "openrouter"]
REWRITE_ENGINE_LABELS = [
    "Off — transcription only",
    "Local Llama 3B — free & private",
    "OpenAI API — automatic tone",
    "OpenRouter — free models",
]
OPENAI_REWRITE_MODEL_VALUES = list(OPENAI_REWRITE_MODELS)
OPENAI_REWRITE_MODEL_LABELS = [
    "GPT-5 Nano — cheapest",
    "GPT-5 Mini — balanced value",
    "GPT-5.6 Luna — fast",
    "GPT-5.6 Terra — balanced",
    "GPT-5.6 Sol — maximum quality",
]
GAIN_VALUES = [1.0, 2.0, 4.0, 8.0]
GAIN_LABELS = ["Standard — 1×", "Sensitive — 2×", "High — 4×", "Very high — 8×"]


def _selected_value(popup, values):
    return values[popup.indexOfSelectedItem()]


def _select_value(popup, values, value) -> None:
    try:
        popup.selectItemAtIndex_(values.index(value))
    except ValueError:
        popup.selectItemAtIndex_(0)


class NativeSettingsController(AppKit.NSObject):
    """Owns a reusable native settings window and its controls."""

    @objc.python_method
    def configure(
        self,
        settings: AppSettings,
        input_devices: list[InputDevice],
        on_save: Callable[[AppSettings], None],
        on_close: Callable[[], None] | None = None,
        device_provider: Callable[[], list[InputDevice]] | None = None,
    ) -> None:
        self.settings = settings
        self.input_devices = input_devices
        self.on_save = on_save
        self.on_close = on_close or (lambda: None)
        self.device_provider = device_provider or (lambda: list(self.input_devices))
        self._device_timer = None
        self._device_signature = None
        self._editing_shortcut_monitor = None
        self._preferred_device_name = settings.input_device_name or next(
            (
                device.name
                for device in input_devices
                if device.id == settings.input_device
            ),
            None,
        )
        self._close_notified = False
        self._build_window()

    @objc.python_method
    def _build_window(self) -> None:
        width, height = 700, 800
        rect = AppKit.NSMakeRect(0, 0, width, height)
        style = (
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskMiniaturizable
        )
        self.window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, AppKit.NSBackingStoreBuffered, False
        )
        self.window.setTitle_("Red Whisper")
        self.window.setReleasedWhenClosed_(False)
        self.window.setDelegate_(self)
        self.window.center()
        content = AppKit.NSVisualEffectView.alloc().initWithFrame_(rect)
        content.setMaterial_(AppKit.NSVisualEffectMaterialUnderWindowBackground)
        content.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
        content.setState_(AppKit.NSVisualEffectStateActive)
        self.window.setContentView_(content)

        content.addSubview_(self._panel(24, 492, 652, 174))
        content.addSubview_(self._panel(24, 318, 652, 128))
        content.addSubview_(self._panel(24, 137, 652, 136))
        content.addSubview_(self._panel(24, 54, 652, 72))

        icon_path = self._brand_icon_path()
        if icon_path:
            icon = AppKit.NSImage.alloc().initWithContentsOfFile_(str(icon_path))
            icon_view = AppKit.NSImageView.alloc().initWithFrame_(
                AppKit.NSMakeRect(30, 718, 52, 52)
            )
            icon_view.setImage_(icon)
            icon_view.setImageScaling_(AppKit.NSImageScaleProportionallyUpOrDown)
            content.addSubview_(icon_view)

        title = self._text("Settings", 94, 742, 560, 30, 23, bold=True)
        subtitle = self._text(
            "Configure transcription, shortcuts, and audio input.",
            94,
            718,
            560,
            20,
            13,
            color=AppKit.NSColor.secondaryLabelColor(),
        )
        content.addSubview_(title)
        content.addSubview_(subtitle)
        content.addSubview_(self._section_title("Transcription", 28, 679))
        content.addSubview_(self._section_title("Keyboard Shortcuts", 28, 459))
        content.addSubview_(self._section_title("Audio", 28, 286))
        content.addSubview_(self._section_title("Enhancements", 28, 128))

        selected_transcription_model = (
            (self.settings.engine, self.settings.openai_model)
            if self.settings.engine == "openai"
            else (self.settings.engine, self.settings.elevenlabs_model)
            if self.settings.engine == "elevenlabs"
            else ("local", None)
        )
        self.transcription_model_popup = self._popup(
            235, 625, 420, TRANSCRIPTION_MODEL_LABELS
        )
        _select_value(
            self.transcription_model_popup,
            TRANSCRIPTION_MODEL_VALUES,
            selected_transcription_model,
        )
        self._add_row(
            content, "Transcription model", self.transcription_model_popup, 625
        )

        self.language_field = self._field(235, 584, 420, self.settings.language)
        self._add_row(content, "Language", self.language_field, 584)

        self.api_key_field = AppKit.NSSecureTextField.alloc().initWithFrame_(
            AppKit.NSMakeRect(235, 543, 133, 26)
        )
        key_placeholder = (
            "OpenAI saved"
            if openai_key_from_environment()
            else "OpenAI API key"
        )
        self.api_key_field.setPlaceholderString_(key_placeholder)
        self._add_row(content, "API keys", self.api_key_field, 543)

        self.openrouter_key_field = AppKit.NSSecureTextField.alloc().initWithFrame_(
            AppKit.NSMakeRect(374, 543, 133, 26)
        )
        openrouter_placeholder = (
            "OpenRouter saved"
            if openrouter_key_from_environment()
            else "OpenRouter API key"
        )
        self.openrouter_key_field.setPlaceholderString_(openrouter_placeholder)
        content.addSubview_(self.openrouter_key_field)

        self.elevenlabs_key_field = AppKit.NSSecureTextField.alloc().initWithFrame_(
            AppKit.NSMakeRect(513, 543, 142, 26)
        )
        elevenlabs_placeholder = (
            "ElevenLabs saved"
            if elevenlabs_key_from_environment()
            else "ElevenLabs API key"
        )
        self.elevenlabs_key_field.setPlaceholderString_(elevenlabs_placeholder)
        content.addSubview_(self.elevenlabs_key_field)

        self.primary_shortcut_value = self.settings.hotkey_preset
        self.secondary_shortcut_value = self.settings.secondary_hotkey_preset
        self._shortcut_monitor = None
        self._capturing_shortcut = None
        self._pending_modifier = None
        self.primary_shortcut_button = self._shortcut_button(
            235, 401, "capturePrimary:"
        )
        self.secondary_shortcut_button = self._shortcut_button(
            235, 360, "captureSecondary:"
        )
        self.shortcut_help = self._text(
            "Click a shortcut field, then press the key or combination you want.",
            235,
            331,
            420,
            18,
            11,
            color=AppKit.NSColor.secondaryLabelColor(),
        )
        self._refresh_shortcut_buttons()
        self._add_row(
            content, "Primary", self.primary_shortcut_button, 401
        )
        self._add_row(
            content, "Secondary", self.secondary_shortcut_button, 360
        )
        content.addSubview_(self.shortcut_help)

        self.device_values = []
        self.device_popup = self._popup(235, 232, 420, ["Searching for microphones…"])
        self.device_popup.setTarget_(self)
        self.device_popup.setAction_("microphoneChanged:")
        self._add_row(content, "Microphone", self.device_popup, 232)
        self.device_help = self._text(
            "Bluetooth headset microphones appear here automatically.",
            235,
            215,
            420,
            16,
            10,
            color=AppKit.NSColor.secondaryLabelColor(),
        )
        content.addSubview_(self.device_help)
        self._replace_input_devices(self.input_devices)

        self.gain_popup = self._popup(235, 181, 420, GAIN_LABELS)
        _select_value(self.gain_popup, GAIN_VALUES, self.settings.microphone_gain)
        self._add_row(content, "Sensitivity", self.gain_popup, 181)

        self.replacements_field = self._field(
            235, 146, 420, self.settings.replacements or ""
        )
        self.replacements_field.setPlaceholderString_("Optional replacements.json path")
        self._add_row(content, "Replacement file", self.replacements_field, 146)

        self.maximum_accuracy = self._checkbox(
            "Maximum local accuracy", 44, 94
        )
        self.maximum_accuracy.setState_(
            AppKit.NSControlStateValueOn
            if self.settings.maximum_accuracy
            else AppKit.NSControlStateValueOff
        )
        content.addSubview_(self.maximum_accuracy)

        rewrite_engine = (
            "openrouter"
            if self.settings.post_process_openrouter
            else "openai"
            if self.settings.post_process_openai
            else "local" if self.settings.post_process_local else "off"
        )
        self.rewrite_engine_popup = self._popup(
            235, 61, 205, REWRITE_ENGINE_LABELS
        )
        _select_value(
            self.rewrite_engine_popup,
            REWRITE_ENGINE_VALUES,
            rewrite_engine,
        )
        if importlib.util.find_spec("mlx_lm") is None:
            self.rewrite_engine_popup.itemAtIndex_(1).setEnabled_(False)
        self.rewrite_engine_popup.setTarget_(self)
        self.rewrite_engine_popup.setAction_("rewriteEngineChanged:")
        self._add_row(content, "Restructuring", self.rewrite_engine_popup, 61)

        self.openai_rewrite_model_popup = self._popup(
            446, 61, 209, OPENAI_REWRITE_MODEL_LABELS
        )
        _select_value(
            self.openai_rewrite_model_popup,
            OPENAI_REWRITE_MODEL_VALUES,
            self.settings.openai_rewrite_model,
        )
        content.addSubview_(self.openai_rewrite_model_popup)

        remembered_openrouter_models = list(
            dict.fromkeys(
                (
                    DEFAULT_OPENROUTER_MODEL,
                    *self.settings.openrouter_models,
                    self.settings.openrouter_model,
                )
            )
        )
        self.openrouter_model_field = self._combo_box(
            446,
            61,
            209,
            remembered_openrouter_models,
            self.settings.openrouter_model,
        )
        self.openrouter_model_field.setPlaceholderString_("openrouter/free")
        content.addSubview_(self.openrouter_model_field)
        self.rewriteEngineChanged_(None)

        cancel = AppKit.NSButton.buttonWithTitle_target_action_(
            "Cancel", self, "cancelSettings:"
        )
        cancel.setFrame_(AppKit.NSMakeRect(470, 11, 90, 32))
        content.addSubview_(cancel)

        save = AppKit.NSButton.buttonWithTitle_target_action_(
            "Save Settings", self, "saveSettings:"
        )
        save.setFrame_(AppKit.NSMakeRect(566, 11, 110, 32))
        save.setKeyEquivalent_("\r")
        content.addSubview_(save)

    @staticmethod
    @objc.python_method
    def _brand_icon_path() -> Path | None:
        source = Path(__file__).resolve()
        candidates = (
            source.parent.parent / "RedWhisper-icon.png",
            source.parent / "assets" / "RedWhisper-icon.png",
        )
        return next((path for path in candidates if path.exists()), None)

    @classmethod
    @objc.python_method
    def _panel(cls, x: float, y: float, width: float, height: float):
        panel = AppKit.NSBox.alloc().initWithFrame_(
            AppKit.NSMakeRect(x, y, width, height)
        )
        panel.setBoxType_(AppKit.NSBoxCustom)
        panel.setFillColor_(AppKit.NSColor.controlBackgroundColor())
        panel.setBorderColor_(AppKit.NSColor.separatorColor())
        panel.setBorderWidth_(0.5)
        panel.setCornerRadius_(12.0)
        return panel

    @classmethod
    @objc.python_method
    def _section_title(cls, value: str, x: float, y: float):
        return cls._text(
            value,
            x,
            y,
            300,
            18,
            12,
            bold=True,
            color=AppKit.NSColor.secondaryLabelColor(),
        )

    @staticmethod
    @objc.python_method
    def _color(rgb: int, alpha: float = 1.0):
        return AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(
            ((rgb >> 16) & 0xFF) / 255,
            ((rgb >> 8) & 0xFF) / 255,
            (rgb & 0xFF) / 255,
            alpha,
        )

    @objc.python_method
    def _add_row(self, content, label: str, control, y: float) -> None:
        content.addSubview_(
            self._text(label, 44, y + 3, 170, 22, 13)
        )
        content.addSubview_(control)

    @staticmethod
    @objc.python_method
    def _text(
        value: str,
        x: float,
        y: float,
        width: float,
        height: float,
        size: float,
        *,
        bold: bool = False,
        color=None,
    ):
        label = AppKit.NSTextField.labelWithString_(value)
        label.setFrame_(AppKit.NSMakeRect(x, y, width, height))
        weight = 0.6 if bold else 0.0
        label.setFont_(AppKit.NSFont.systemFontOfSize_weight_(size, weight))
        if color is not None:
            label.setTextColor_(color)
        return label

    @staticmethod
    @objc.python_method
    def _popup(x: float, y: float, width: float, labels: list[str]):
        popup = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(
            AppKit.NSMakeRect(x, y, width, 28), False
        )
        popup.addItemsWithTitles_(labels)
        return popup

    @staticmethod
    @objc.python_method
    def _field(x: float, y: float, width: float, value: str):
        field = AppKit.NSTextField.alloc().initWithFrame_(
            AppKit.NSMakeRect(x, y, width, 26)
        )
        field.setStringValue_(value)
        return field

    @staticmethod
    @objc.python_method
    def _combo_box(
        x: float,
        y: float,
        width: float,
        values: list[str],
        value: str,
    ):
        combo = AppKit.NSComboBox.alloc().initWithFrame_(
            AppKit.NSMakeRect(x, y, width, 26)
        )
        combo.addItemsWithObjectValues_(values)
        combo.setStringValue_(value)
        combo.setCompletes_(True)
        combo.setNumberOfVisibleItems_(min(8, max(1, len(values))))
        return combo

    @staticmethod
    @objc.python_method
    def _checkbox(title: str, x: float, y: float):
        checkbox = AppKit.NSButton.checkboxWithTitle_target_action_(title, None, None)
        checkbox.setFrame_(AppKit.NSMakeRect(x, y, 395, 24))
        return checkbox

    @objc.python_method
    def _shortcut_button(self, x: float, y: float, action: str):
        button = AppKit.NSButton.buttonWithTitle_target_action_("", self, action)
        button.setFrame_(AppKit.NSMakeRect(x, y, 420, 28))
        button.setButtonType_(AppKit.NSButtonTypeMomentaryPushIn)
        button.setBezelStyle_(AppKit.NSBezelStyleRounded)
        button.setAlignment_(AppKit.NSTextAlignmentLeft)
        return button

    @staticmethod
    @objc.python_method
    def _shortcut_title(value: str | None, *, secondary: bool = False) -> str:
        if value is None:
            return "Not set — click, then press a key"
        suffix = "  ·  Click to change"
        return HotkeyConfig(value).label + suffix

    @objc.python_method
    def _refresh_shortcut_buttons(self) -> None:
        self.primary_shortcut_button.setTitle_(
            self._shortcut_title(self.primary_shortcut_value)
        )
        self.secondary_shortcut_button.setTitle_(
            self._shortcut_title(self.secondary_shortcut_value, secondary=True)
        )
        if hasattr(self, "shortcut_help"):
            self.shortcut_help.setStringValue_(
                "Click a shortcut field, then press the key or combination you want."
            )

    def capturePrimary_(self, _sender) -> None:
        self._begin_shortcut_capture("primary")

    def captureSecondary_(self, _sender) -> None:
        self._begin_shortcut_capture("secondary")

    @objc.python_method
    def _begin_shortcut_capture(self, which: str) -> None:
        self._end_shortcut_capture()
        self._capturing_shortcut = which
        button = (
            self.primary_shortcut_button
            if which == "primary"
            else self.secondary_shortcut_button
        )
        hint = "Press your shortcut now · Esc cancels"
        if which == "secondary":
            hint = "Press shortcut · Delete clears · Esc cancels"
        button.setTitle_(hint)
        self.shortcut_help.setStringValue_(
            "Listening for your keyboard…" if which == "primary" else
            "Listening… Press Delete to remove the secondary shortcut."
        )
        self.window.makeFirstResponder_(button)

        mask = AppKit.NSEventMaskKeyDown | AppKit.NSEventMaskFlagsChanged
        self._shortcut_event_handler = self._handle_shortcut_event
        self._shortcut_monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            mask, self._shortcut_event_handler
        )

    @objc.python_method
    def _handle_shortcut_event(self, event):
        if self._capturing_shortcut is None:
            return event

        if event.type() == AppKit.NSEventTypeFlagsChanged:
            flags = event.modifierFlags()
            modifier_flags = (
                ("function", AppKit.NSEventModifierFlagFunction),
                ("control", AppKit.NSEventModifierFlagControl),
                ("option", AppKit.NSEventModifierFlagOption),
                ("shift", AppKit.NSEventModifierFlagShift),
                ("command", AppKit.NSEventModifierFlagCommand),
            )
            if self._pending_modifier is None:
                active = next(
                    (name for name, flag in modifier_flags if flags & flag),
                    None,
                )
                if active is not None:
                    self._pending_modifier = active
                    display = "Fn" if active == "function" else active.title()
                    self.shortcut_help.setStringValue_(
                        f"Release {display} to use it alone, or press another key for a combination."
                    )
                    return None
                return event

            pending_flag = dict(modifier_flags)[self._pending_modifier]
            if not flags & pending_flag:
                value = (
                    "fn"
                    if self._pending_modifier == "function"
                    else modifier_hotkey_value(self._pending_modifier)
                )
                self._accept_shortcut(value)
                return None
            return None

        keycode = int(event.keyCode())
        if keycode == 53:  # Escape
            self._end_shortcut_capture()
            self._refresh_shortcut_buttons()
            return None
        if keycode in {51, 117} and self._capturing_shortcut == "secondary":
            self._accept_shortcut(None)
            return None

        flags = event.modifierFlags()
        modifiers = tuple(
            name
            for name, flag in (
                ("control", AppKit.NSEventModifierFlagControl),
                ("option", AppKit.NSEventModifierFlagOption),
                ("shift", AppKit.NSEventModifierFlagShift),
                ("command", AppKit.NSEventModifierFlagCommand),
            )
            if flags & flag
        )
        label = self._key_label(event, keycode)
        self._accept_shortcut(custom_hotkey_value(keycode, modifiers, label))
        return None

    @staticmethod
    @objc.python_method
    def _key_label(event, keycode: int) -> str:
        named_keys = {
            36: "Return",
            48: "Tab",
            49: "Space",
            115: "Home",
            116: "Page Up",
            119: "End",
            121: "Page Down",
            123: "Left Arrow",
            124: "Right Arrow",
            125: "Down Arrow",
            126: "Up Arrow",
        }
        if keycode in named_keys:
            return named_keys[keycode]
        characters = event.charactersIgnoringModifiers() or ""
        if len(characters) == 1 and characters.isprintable():
            return characters.upper()
        return f"Key {keycode}"

    @objc.python_method
    def _accept_shortcut(self, value: str | None) -> None:
        other = (
            self.secondary_shortcut_value
            if self._capturing_shortcut == "primary"
            else self.primary_shortcut_value
        )
        if value is not None and value == other:
            AppKit.NSBeep()
            return
        if self._capturing_shortcut == "primary":
            self.primary_shortcut_value = value
        else:
            self.secondary_shortcut_value = value
        self._end_shortcut_capture()
        self._refresh_shortcut_buttons()

    @objc.python_method
    def _end_shortcut_capture(self) -> None:
        if getattr(self, "_shortcut_monitor", None) is not None:
            AppKit.NSEvent.removeMonitor_(self._shortcut_monitor)
            self._shortcut_monitor = None
        self._capturing_shortcut = None
        self._pending_modifier = None

    def show(self) -> None:
        self._close_notified = False
        self._brand_application_menu()
        self._ensure_edit_menu()
        self._start_editing_shortcut_monitor()
        self.refreshAudioDevices_(None)
        self._start_device_timer()
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    @staticmethod
    @objc.python_method
    def _brand_application_menu() -> None:
        AppKit.NSProcessInfo.processInfo().setProcessName_("Red Whisper")
        menu = AppKit.NSApplication.sharedApplication().mainMenu()
        if menu is not None and menu.numberOfItems() > 0:
            menu.itemAtIndex_(0).setTitle_("Red Whisper")

    @staticmethod
    @objc.python_method
    def _ensure_edit_menu() -> None:
        application = AppKit.NSApplication.sharedApplication()
        main_menu = application.mainMenu()
        if main_menu is None:
            main_menu = AppKit.NSMenu.alloc().initWithTitle_("Main")
            application.setMainMenu_(main_menu)
        if any(item.title() == "Edit" for item in main_menu.itemArray()):
            return

        edit_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Edit", None, ""
        )
        edit_menu = AppKit.NSMenu.alloc().initWithTitle_("Edit")
        for title, action, key, modifiers in (
            ("Undo", "undo:", "z", AppKit.NSEventModifierFlagCommand),
            (
                "Redo",
                "redo:",
                "z",
                AppKit.NSEventModifierFlagCommand | AppKit.NSEventModifierFlagShift,
            ),
            ("Cut", "cut:", "x", AppKit.NSEventModifierFlagCommand),
            ("Copy", "copy:", "c", AppKit.NSEventModifierFlagCommand),
            ("Paste", "paste:", "v", AppKit.NSEventModifierFlagCommand),
            ("Select All", "selectAll:", "a", AppKit.NSEventModifierFlagCommand),
        ):
            item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                title, action, key
            )
            item.setKeyEquivalentModifierMask_(modifiers)
            edit_menu.addItem_(item)
            if title in {"Redo", "Paste"}:
                edit_menu.addItem_(AppKit.NSMenuItem.separatorItem())
        edit_item.setSubmenu_(edit_menu)
        main_menu.insertItem_atIndex_(edit_item, min(1, main_menu.numberOfItems()))

    @staticmethod
    @objc.python_method
    def _control_edit_action(event) -> str | None:
        if event.type() != AppKit.NSEventTypeKeyDown:
            return None
        modifiers = event.modifierFlags()
        if not modifiers & AppKit.NSEventModifierFlagControl:
            return None
        if modifiers & (
            AppKit.NSEventModifierFlagCommand | AppKit.NSEventModifierFlagOption
        ):
            return None
        character = (event.charactersIgnoringModifiers() or "").lower()
        return {"c": "copy:", "v": "paste:"}.get(character)

    @objc.python_method
    def _start_editing_shortcut_monitor(self) -> None:
        self._stop_editing_shortcut_monitor()
        self._editing_shortcut_handler = self._handle_editing_shortcut_event
        self._editing_shortcut_monitor = (
            AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                AppKit.NSEventMaskKeyDown,
                self._editing_shortcut_handler,
            )
        )

    @objc.python_method
    def _stop_editing_shortcut_monitor(self) -> None:
        if self._editing_shortcut_monitor is not None:
            AppKit.NSEvent.removeMonitor_(self._editing_shortcut_monitor)
            self._editing_shortcut_monitor = None

    @objc.python_method
    def _handle_editing_shortcut_event(self, event):
        if self._capturing_shortcut is not None:
            return event
        action = self._control_edit_action(event)
        if action is None:
            return event
        responder = self.window.firstResponder()
        if responder is None or not responder.isKindOfClass_(AppKit.NSTextView):
            return event
        handled = AppKit.NSApplication.sharedApplication().sendAction_to_from_(
            action, None, self
        )
        return None if handled else event

    @objc.python_method
    def _start_device_timer(self) -> None:
        self._stop_device_timer()
        self._device_timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            2.0,
            self,
            "refreshAudioDevices:",
            None,
            True,
        )

    @objc.python_method
    def _stop_device_timer(self) -> None:
        if self._device_timer is not None:
            self._device_timer.invalidate()
            self._device_timer = None

    def refreshAudioDevices_(self, _timer) -> None:
        try:
            devices = self.device_provider()
        except Exception:
            self.device_help.setStringValue_(
                "Could not refresh microphones. Red Whisper will try again."
            )
            return
        self._replace_input_devices(devices)

    def microphoneChanged_(self, _sender) -> None:
        if not self.device_values:
            return
        selected_id = _selected_value(self.device_popup, self.device_values)
        self._preferred_device_name = next(
            (
                device.name
                for device in self.input_devices
                if device.id == selected_id
            ),
            None,
        )

    @objc.python_method
    def _replace_input_devices(self, devices: list[InputDevice]) -> None:
        signature = tuple((device.id, device.name) for device in devices)
        if signature == self._device_signature:
            return

        selected_id = (
            _selected_value(self.device_popup, self.device_values)
            if self.device_values
            else self.settings.input_device
        )
        selected_name = self._preferred_device_name
        self.input_devices = list(devices)
        self.device_values = [device.id for device in devices]
        self._device_signature = signature
        self.device_popup.removeAllItems()

        if not devices:
            self.device_popup.addItemWithTitle_("No microphone found")
            self.device_popup.setEnabled_(False)
            self.device_help.setStringValue_(
                "Connect a Bluetooth headset with a microphone to use it here."
            )
            return

        self.device_popup.addItemsWithTitles_([device.name for device in devices])
        self.device_popup.setEnabled_(True)
        matching_name = next(
            (device.id for device in devices if device.name == selected_name),
            None,
        )
        matching_id = next(
            (device.id for device in devices if device.id == selected_id),
            None,
        )
        target_id = matching_name if matching_name is not None else matching_id
        if target_id is None:
            target_id = self.device_values[0]
        _select_value(
            self.device_popup,
            self.device_values,
            target_id,
        )
        if self._preferred_device_name is None:
            self._preferred_device_name = next(
                device.name for device in devices if device.id == target_id
            )
        self.device_help.setStringValue_(
            f"{len(devices)} microphone input{'s' if len(devices) != 1 else ''} detected · Bluetooth updates automatically."
        )

    def cancelSettings_(self, _sender) -> None:
        self._end_shortcut_capture()
        self._notify_closed()
        self.window.orderOut_(None)

    def windowWillClose_(self, _notification) -> None:
        self._end_shortcut_capture()
        self._notify_closed()

    @objc.python_method
    def _notify_closed(self) -> None:
        if self._close_notified:
            return
        self._close_notified = True
        self._stop_device_timer()
        self._stop_editing_shortcut_monitor()
        self.on_close()

    def saveSettings_(self, _sender) -> None:
        self._end_shortcut_capture()
        primary = self.primary_shortcut_value
        secondary = self.secondary_shortcut_value
        input_device = (
            _selected_value(self.device_popup, self.device_values)
            if self.device_values
            else None
        )
        input_device_name = next(
            (
                device.name
                for device in self.input_devices
                if device.id == input_device
            ),
            None,
        )
        rewrite_engine = _selected_value(
            self.rewrite_engine_popup, REWRITE_ENGINE_VALUES
        )
        openrouter_model = (
            self.openrouter_model_field.stringValue().strip()
            or DEFAULT_OPENROUTER_MODEL
        )
        openrouter_models = tuple(
            dict.fromkeys((*self.settings.openrouter_models, openrouter_model))
        )
        transcription_engine, transcription_model = _selected_value(
            self.transcription_model_popup, TRANSCRIPTION_MODEL_VALUES
        )
        updated = AppSettings(
            engine=transcription_engine,
            language=self.language_field.stringValue().strip() or "auto",
            maximum_accuracy=(
                self.maximum_accuracy.state() == AppKit.NSControlStateValueOn
            ),
            replacements=self.replacements_field.stringValue().strip() or None,
            post_process_local=rewrite_engine == "local",
            post_process_openai=rewrite_engine == "openai",
            post_process_openrouter=rewrite_engine == "openrouter",
            hotkey_preset=primary,
            secondary_hotkey_preset=secondary,
            microphone_gain=_selected_value(self.gain_popup, GAIN_VALUES),
            input_device=input_device,
            input_device_name=input_device_name,
            openai_model=(
                transcription_model
                if transcription_engine == "openai"
                else self.settings.openai_model
            ),
            elevenlabs_model=(
                transcription_model
                if transcription_engine == "elevenlabs"
                else self.settings.elevenlabs_model
            ),
            openai_rewrite_model=_selected_value(
                self.openai_rewrite_model_popup,
                OPENAI_REWRITE_MODEL_VALUES,
            ),
            openrouter_model=openrouter_model,
            openrouter_models=openrouter_models,
        )
        try:
            updated.validate()
            provided_key = self.api_key_field.stringValue().strip()
            if provided_key:
                save_openai_key(provided_key)
            provided_openrouter_key = self.openrouter_key_field.stringValue().strip()
            if provided_openrouter_key:
                save_openrouter_key(provided_openrouter_key)
            provided_elevenlabs_key = self.elevenlabs_key_field.stringValue().strip()
            if provided_elevenlabs_key:
                save_elevenlabs_key(provided_elevenlabs_key)
            if (
                updated.engine == "openai" or updated.post_process_openai
            ) and not openai_key_from_environment():
                raise ConfigurationError(
                    "Enter an OpenAI API key before selecting an OpenAI feature."
                )
            if (
                updated.post_process_openrouter
                and not openrouter_key_from_environment()
            ):
                raise ConfigurationError(
                    "Enter an OpenRouter API key before selecting OpenRouter."
                )
            if (
                updated.engine == "elevenlabs"
                and not elevenlabs_key_from_environment()
            ):
                raise ConfigurationError(
                    "Enter an ElevenLabs API key before selecting ElevenLabs."
                )
            self._stop_device_timer()
            self._stop_editing_shortcut_monitor()
            self.on_save(updated)
        except (ConfigurationError, OSError, ValueError) as error:
            self._show_error(str(error))

    def rewriteEngineChanged_(self, _sender) -> None:
        selected = _selected_value(
            self.rewrite_engine_popup, REWRITE_ENGINE_VALUES
        )
        self.openai_rewrite_model_popup.setEnabled_(selected == "openai")
        self.openai_rewrite_model_popup.setHidden_(selected == "openrouter")
        self.openrouter_model_field.setHidden_(selected != "openrouter")

    @staticmethod
    @objc.python_method
    def _show_error(message: str) -> None:
        alert = AppKit.NSAlert.alloc().init()
        alert.setAlertStyle_(AppKit.NSAlertStyleCritical)
        alert.setMessageText_("Could not save RedWhisper settings")
        alert.setInformativeText_(message)
        alert.runModal()
