"""Native AppKit settings window for RedWhisper."""

from __future__ import annotations

import importlib.util
import threading
import webbrowser
from typing import Callable

import AppKit
import objc

from audio_devices import InputDevice
from chatgpt_subscription import ChatGPTError, ChatGPTSubscription, USAGE_URL
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
    ("openai", "gpt-transcribe"),
    ("openai", "gpt-4o-transcribe"),
    ("openai", "gpt-4o-mini-transcribe"),
    ("openai", "gpt-4o-transcribe-diarize"),
    ("openai", "whisper-1"),
    ("elevenlabs", "scribe_v2"),
    ("elevenlabs", "scribe_v2_realtime"),
    ("elevenlabs", "scribe_v1"),
]
TRANSCRIPTION_MODEL_LABELS = [
    "Whisper Large v3 Turbo — Local",
    "GPT Transcribe — OpenAI recommended",
    "GPT-4o Transcribe — OpenAI accuracy",
    "GPT-4o Mini Transcribe — OpenAI speed",
    "GPT-4o Transcribe Diarize — OpenAI speakers",
    "Whisper-1 — OpenAI legacy",
    "Scribe v2 — ElevenLabs",
    "Scribe v2 Realtime — ElevenLabs streaming",
    "Scribe v1 — ElevenLabs legacy",
]
REWRITE_ENGINE_VALUES = ["off", "local", "openai", "openrouter", "chatgpt"]
REWRITE_ENGINE_LABELS = [
    "Off — transcription only",
    "Local Llama 3B — free & private",
    "OpenAI API — automatic tone",
    "OpenRouter — free models",
    "ChatGPT subscription",
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
REWRITE_ENGINE_SYMBOLS = {
    "off": "slash.circle",
    "local": "laptopcomputer",
    "openai": "cloud",
    "openrouter": "cloud",
    "chatgpt": "cloud",
}
REWRITE_HELP = {
    "off": "Transcripts are inserted exactly as they were recognized.",
    "local": "Rewrites on this Mac with Llama 3.2 3B: free and private.",
    "openai": "Sends transcript text, never audio, to OpenAI. Billed to your API key.",
    "openrouter": "Sends transcript text to OpenRouter. Free-model availability varies.",
    "chatgpt": "Sends transcript text to ChatGPT using your plan allowance, with no API charges.",
}
PROFESSIONAL_TIP = "Start a dictation with “professional” for a more formal tone."
SHORTCUT_HELP = "Hold a shortcut to dictate, then release to insert. Click a shortcut to change it."
SETTINGS_PANES = (
    ("general", "General", "gearshape"),
    ("transcription", "Transcription", "waveform"),
    ("rephrasing", "Rephrasing", "wand.and.stars"),
    ("keys", "API Keys", "key"),
)

WINDOW_WIDTH = 640.0
PANE_INSET = 24.0
GROUP_WIDTH = WINDOW_WIDTH - 2 * PANE_INSET
GROUP_CORNER_RADIUS = 10.0
SECTION_SPACING = 22.0
ROW_HEIGHT = 40.0
ROW_PADDING = 14.0
ROW_SPACING = 12.0
ROW_INNER_WIDTH = GROUP_WIDTH - 2 * ROW_PADDING
CONTROL_WIDTH = 340.0
SHORTCUT_BUTTON_WIDTH = 240.0
KEY_FIELD_WIDTH = 260.0
LANGUAGE_FIELD_WIDTH = 120.0
REPLACEMENTS_FIELD_WIDTH = 210.0
ACCOUNT_POPUP_WIDTH = 240.0
ACTION_BAR_HEIGHT = 60.0


def _symbol(name: str, description: str | None = None):
    return AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
        name, description
    )


class SettingsGroupView(AppKit.NSStackView):
    """A rounded settings group that draws separators between its visible rows."""

    def drawRect_(self, _dirty_rect) -> None:
        bounds = self.bounds()
        dark = (
            self.effectiveAppearance().bestMatchFromAppearancesWithNames_(
                [AppKit.NSAppearanceNameAqua, AppKit.NSAppearanceNameDarkAqua]
            )
            == AppKit.NSAppearanceNameDarkAqua
        )
        window = self.window()
        hairline = 1.0 / (window.backingScaleFactor() if window else 2.0)
        outline = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            AppKit.NSInsetRect(bounds, hairline / 2, hairline / 2),
            GROUP_CORNER_RADIUS,
            GROUP_CORNER_RADIUS,
        )
        AppKit.NSColor.colorWithWhite_alpha_(1.0, 0.055 if dark else 0.85).setFill()
        outline.fill()
        AppKit.NSColor.colorWithWhite_alpha_(
            1.0 if dark else 0.0, 0.09 if dark else 0.07
        ).setStroke()
        outline.setLineWidth_(hairline)
        outline.stroke()

        rows = sorted(
            (view.frame() for view in self.arrangedSubviews() if not view.isHidden()),
            key=lambda frame: frame.origin.y,
        )
        AppKit.NSColor.separatorColor().setFill()
        for lower, upper in zip(rows, rows[1:]):
            boundary = (lower.origin.y + lower.size.height + upper.origin.y) / 2
            AppKit.NSBezierPath.fillRect_(
                AppKit.NSMakeRect(
                    ROW_PADDING,
                    boundary - hairline / 2,
                    bounds.size.width - 2 * ROW_PADDING,
                    hairline,
                )
            )

    def layout(self) -> None:
        objc.super(SettingsGroupView, self).layout()
        self.setNeedsDisplay_(True)

    def viewDidChangeEffectiveAppearance(self) -> None:
        self.setNeedsDisplay_(True)


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
        self.chatgpt = ChatGPTSubscription()
        self._chatgpt_busy = False
        self._chatgpt_cancel = threading.Event()
        self._chatgpt_state = {}
        self._chatgpt_operation = ""
        self.chatgpt_model_values = [""]
        self._build_window()

    @objc.python_method
    def _build_window(self) -> None:
        rect = AppKit.NSMakeRect(0, 0, WINDOW_WIDTH, 480)
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
        self.window.setContentView_(AppKit.NSView.alloc().initWithFrame_(rect))

        self._saved_keys = {
            "openai": bool(openai_key_from_environment()),
            "openrouter": bool(openrouter_key_from_environment()),
            "elevenlabs": bool(elevenlabs_key_from_environment()),
        }
        self._current_pane = None
        self._panes = {
            "general": self._build_general_pane(),
            "transcription": self._build_transcription_pane(),
            "rephrasing": self._build_rephrasing_pane(),
            "keys": self._build_keys_pane(),
        }
        self._build_action_bar()

        self._toolbar = AppKit.NSToolbar.alloc().initWithIdentifier_(
            "RedWhisperSettings"
        )
        self._toolbar.setDelegate_(self)
        self._toolbar.setAllowsUserCustomization_(False)
        self._toolbar.setDisplayMode_(AppKit.NSToolbarDisplayModeIconAndLabel)
        self.window.setToolbar_(self._toolbar)
        self.window.setToolbarStyle_(AppKit.NSWindowToolbarStylePreference)

        self.transcriptionModelChanged_(None)
        self.rewriteEngineChanged_(None)
        self._update_chatgpt_controls()
        self._select_pane("general", animate=False)
        self.window.center()

    @objc.python_method
    def _build_general_pane(self):
        self.primary_shortcut_value = self.settings.hotkey_preset
        self.secondary_shortcut_value = self.settings.secondary_hotkey_preset
        self._shortcut_monitor = None
        self._capturing_shortcut = None
        self._pending_modifier = None
        self.primary_shortcut_button = self._shortcut_button("capturePrimary:")
        self.secondary_shortcut_button = self._shortcut_button("captureSecondary:")
        self.shortcut_help = self._footer(SHORTCUT_HELP)
        self._refresh_shortcut_buttons()
        shortcuts = self._section(
            "Shortcuts",
            [
                self._row("Primary shortcut", self.primary_shortcut_button),
                self._row(
                    "Secondary shortcut",
                    self.secondary_shortcut_button,
                    subtitle="Optional, e.g. for an external keyboard",
                ),
            ],
            self.shortcut_help,
        )

        self.device_values = []
        self.device_popup = self._popup(["Searching for microphones…"])
        self.device_popup.setTarget_(self)
        self.device_popup.setAction_("microphoneChanged:")
        self.device_help = self._footer("")
        self._replace_input_devices(self.input_devices)
        self.gain_popup = self._popup(GAIN_LABELS)
        _select_value(self.gain_popup, GAIN_VALUES, self.settings.microphone_gain)
        microphone = self._section(
            "Microphone",
            [
                self._row("Input device", self.device_popup),
                self._row("Sensitivity", self.gain_popup),
            ],
            self.device_help,
        )
        return self._pane([shortcuts, microphone])

    @objc.python_method
    def _build_transcription_pane(self):
        selected_transcription_model = (
            (self.settings.engine, self.settings.openai_model)
            if self.settings.engine == "openai"
            else (self.settings.engine, self.settings.elevenlabs_model)
            if self.settings.engine == "elevenlabs"
            else ("local", None)
        )
        self.transcription_model_popup = self._popup(TRANSCRIPTION_MODEL_LABELS)
        for item, (engine, _model) in zip(
            self.transcription_model_popup.itemArray(), TRANSCRIPTION_MODEL_VALUES
        ):
            item.setImage_(_symbol("laptopcomputer" if engine == "local" else "cloud"))
        _select_value(
            self.transcription_model_popup,
            TRANSCRIPTION_MODEL_VALUES,
            selected_transcription_model,
        )
        self.transcription_model_popup.setTarget_(self)
        self.transcription_model_popup.setAction_("transcriptionModelChanged:")
        self.language_field = self._field(self.settings.language, LANGUAGE_FIELD_WIDTH)
        self.language_field.setPlaceholderString_("auto")
        self.transcription_help = self._footer("")
        engine = self._section(
            "Engine",
            [
                self._row("Model", self.transcription_model_popup),
                self._row(
                    "Language",
                    self.language_field,
                    subtitle="A code such as “en”, or “auto” to detect it",
                ),
            ],
            self.transcription_help,
        )

        self.maximum_accuracy = AppKit.NSSwitch.alloc().init()
        self.maximum_accuracy.setState_(
            AppKit.NSControlStateValueOn
            if self.settings.maximum_accuracy
            else AppKit.NSControlStateValueOff
        )
        self.replacements_field = self._field(
            self.settings.replacements or "", REPLACEMENTS_FIELD_WIDTH
        )
        self.replacements_field.setPlaceholderString_("replacements.json")
        choose = AppKit.NSButton.buttonWithTitle_target_action_(
            "Choose…", self, "chooseReplacements:"
        )
        accuracy = self._section(
            "Accuracy",
            [
                self._row(
                    "Maximum local accuracy",
                    self.maximum_accuracy,
                    subtitle="Best of five decodings. Slower; local model only.",
                ),
                self._row(
                    "Replacement file",
                    self._stack([self.replacements_field, choose], vertical=False, spacing=8),
                    subtitle="Fix names and jargon after transcription",
                ),
            ],
        )
        return self._pane([engine, accuracy])

    @objc.python_method
    def _build_rephrasing_pane(self):
        rewrite_engine = (
            "chatgpt"
            if self.settings.post_process_chatgpt
            else "openrouter"
            if self.settings.post_process_openrouter
            else "openai"
            if self.settings.post_process_openai
            else "local" if self.settings.post_process_local else "off"
        )
        self.rewrite_engine_popup = self._popup(REWRITE_ENGINE_LABELS)
        self.rewrite_engine_popup.setAutoenablesItems_(False)
        for item, value in zip(
            self.rewrite_engine_popup.itemArray(), REWRITE_ENGINE_VALUES
        ):
            item.setImage_(_symbol(REWRITE_ENGINE_SYMBOLS[value]))
        _select_value(
            self.rewrite_engine_popup,
            REWRITE_ENGINE_VALUES,
            rewrite_engine,
        )
        if importlib.util.find_spec("mlx_lm") is None:
            local_item = self.rewrite_engine_popup.itemAtIndex_(1)
            local_item.setEnabled_(False)
            local_item.setToolTip_("Install with ./install.sh --with-local-llm")
        self.rewrite_engine_popup.setTarget_(self)
        self.rewrite_engine_popup.setAction_("rewriteEngineChanged:")

        self.openai_rewrite_model_popup = self._popup(OPENAI_REWRITE_MODEL_LABELS)
        _select_value(
            self.openai_rewrite_model_popup,
            OPENAI_REWRITE_MODEL_VALUES,
            self.settings.openai_rewrite_model,
        )
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
            remembered_openrouter_models,
            self.settings.openrouter_model,
        )
        self.openrouter_model_field.setPlaceholderString_("openrouter/free")
        self.chatgpt_model_popup = self._popup(["Automatic — account default"])
        if self.settings.chatgpt_rewrite_model:
            self.chatgpt_model_values.append(self.settings.chatgpt_rewrite_model)
            self.chatgpt_model_popup.addItemWithTitle_(self.settings.chatgpt_rewrite_model + " — sync needed")
            self.chatgpt_model_popup.selectItemAtIndex_(1)
        self.local_rewrite_model_label = self._label(
            "Llama 3.2 3B · on this Mac",
            color=AppKit.NSColor.secondaryLabelColor(),
        )
        self.rewrite_model_row = self._row(
            "Model",
            self._slot(
                [
                    self.openai_rewrite_model_popup,
                    self.openrouter_model_field,
                    self.chatgpt_model_popup,
                    self.local_rewrite_model_label,
                ]
            ),
        )
        self.rewrite_help = self._footer("")
        rephrasing = self._section(
            "Engine",
            [self._row("Rephrase with", self.rewrite_engine_popup), self.rewrite_model_row],
            self.rewrite_help,
        )

        self.chatgpt_title = self._label("ChatGPT plan")
        self.chatgpt_status = self._label(
            "",
            11.0,
            color=AppKit.NSColor.secondaryLabelColor(),
            wraps=True,
            width=ROW_INNER_WIDTH - ACCOUNT_POPUP_WIDTH - ROW_SPACING,
        )
        self.chatgpt_accounts = self._popup(["No saved accounts"], ACCOUNT_POPUP_WIDTH)
        self.chatgpt_accounts.setTarget_(self)
        self.chatgpt_accounts.setAction_("chatGPTAccountChanged:")
        self.chatgpt_connect_button = self._button(
            "Continue with ChatGPT", "connectChatGPT:"
        )
        self.chatgpt_cancel_button = self._button("Cancel Sign-In", "cancelChatGPTSignIn:")
        self.chatgpt_refresh_button = self._button("Refresh Models", "refreshChatGPT:")
        self.chatgpt_add_button = self._button("Add Account", "addChatGPTAccount:")
        self.chatgpt_usage_button = self._button("Manage Usage", "manageChatGPTUsage:")
        self.chatgpt_disconnect_button = self._button("Disconnect", "disconnectChatGPT:")
        self.chatgpt_section = self._section(
            "ChatGPT Account",
            [
                self._row(
                    self._stack(
                        [self.chatgpt_title, self.chatgpt_status],
                        vertical=True,
                        spacing=2,
                    ),
                    self.chatgpt_accounts,
                ),
                self._row(
                    "",
                    self._stack(
                        [
                            self.chatgpt_connect_button,
                            self.chatgpt_cancel_button,
                            self.chatgpt_refresh_button,
                            self.chatgpt_add_button,
                            self.chatgpt_usage_button,
                            self.chatgpt_disconnect_button,
                        ],
                        vertical=False,
                        spacing=8,
                    ),
                ),
            ],
            self._footer(
                "Uses your ChatGPT plan allowance for rephrasing only. "
                "Transcription keeps its own API billing."
            ),
        )
        return self._pane([rephrasing, self.chatgpt_section])

    @objc.python_method
    def _build_keys_pane(self):
        self.api_key_field, openai = self._key_control("openai")
        self.elevenlabs_key_field, elevenlabs = self._key_control("elevenlabs")
        self.openrouter_key_field, openrouter = self._key_control("openrouter")
        keys = self._section(
            "Provider Keys",
            [
                self._row("OpenAI", openai, subtitle="Transcription and rephrasing"),
                self._row("ElevenLabs", elevenlabs, subtitle="Transcription"),
                self._row("OpenRouter", openrouter, subtitle="Rephrasing"),
            ],
            self._footer(
                "Keys are stored in your macOS Keychain, never in settings files. "
                "Paste a new key to replace a saved one."
            ),
        )
        return self._pane([keys])

    @objc.python_method
    def _build_action_bar(self) -> None:
        root = self.window.contentView()
        bar = AppKit.NSView.alloc().initWithFrame_(AppKit.NSZeroRect)
        bar.setTranslatesAutoresizingMaskIntoConstraints_(False)
        hint = self._label(
            "Saving restarts Red Whisper in the background.",
            11.0,
            color=AppKit.NSColor.secondaryLabelColor(),
        )
        cancel = self._button("Cancel", "cancelSettings:")
        cancel.setKeyEquivalent_("\x1b")
        save = self._button("Save", "saveSettings:")
        save.setKeyEquivalent_("\r")
        for view in (hint, cancel, save):
            bar.addSubview_(view)
        root.addSubview_(bar)
        AppKit.NSLayoutConstraint.activateConstraints_(
            [
                bar.leadingAnchor().constraintEqualToAnchor_(root.leadingAnchor()),
                bar.trailingAnchor().constraintEqualToAnchor_(root.trailingAnchor()),
                bar.bottomAnchor().constraintEqualToAnchor_(root.bottomAnchor()),
                bar.heightAnchor().constraintEqualToConstant_(ACTION_BAR_HEIGHT),
                save.trailingAnchor().constraintEqualToAnchor_constant_(
                    bar.trailingAnchor(), -PANE_INSET
                ),
                save.centerYAnchor().constraintEqualToAnchor_(bar.centerYAnchor()),
                save.widthAnchor().constraintGreaterThanOrEqualToConstant_(84),
                cancel.trailingAnchor().constraintEqualToAnchor_constant_(
                    save.leadingAnchor(), -10
                ),
                cancel.centerYAnchor().constraintEqualToAnchor_(bar.centerYAnchor()),
                cancel.widthAnchor().constraintEqualToAnchor_(save.widthAnchor()),
                hint.leadingAnchor().constraintEqualToAnchor_constant_(
                    bar.leadingAnchor(), PANE_INSET
                ),
                hint.centerYAnchor().constraintEqualToAnchor_(bar.centerYAnchor()),
                hint.trailingAnchor().constraintLessThanOrEqualToAnchor_constant_(
                    cancel.leadingAnchor(), -ROW_SPACING
                ),
            ]
        )

    @objc.python_method
    def _select_pane(self, identifier: str, animate: bool = True) -> None:
        pane = self._panes.get(identifier)
        if pane is None:
            return
        if pane is not self._current_pane:
            root = self.window.contentView()
            if self._current_pane is not None:
                self._current_pane.removeFromSuperview()
            root.addSubview_(pane)
            AppKit.NSLayoutConstraint.activateConstraints_(
                [
                    pane.topAnchor().constraintEqualToAnchor_(root.topAnchor()),
                    pane.leadingAnchor().constraintEqualToAnchor_(root.leadingAnchor()),
                ]
            )
            self._current_pane = pane
        self._toolbar.setSelectedItemIdentifier_(identifier)
        self._fit_window(animate)

    @objc.python_method
    def _fit_window(self, animate: bool = True) -> None:
        """Resize the window to the selected pane, keeping its top edge fixed."""
        pane = getattr(self, "_current_pane", None)
        if pane is None:
            return
        pane.layoutSubtreeIfNeeded()
        height = pane.fittingSize().height + ACTION_BAR_HEIGHT
        delta = height - self.window.contentView().frame().size.height
        if abs(delta) < 0.5:
            return
        frame = self.window.frame()
        self.window.setFrame_display_animate_(
            AppKit.NSMakeRect(
                frame.origin.x,
                frame.origin.y - delta,
                frame.size.width,
                frame.size.height + delta,
            ),
            True,
            bool(animate and self.window.isVisible()),
        )

    def toolbarAllowedItemIdentifiers_(self, _toolbar):
        return [identifier for identifier, _label, _symbol_name in SETTINGS_PANES]

    def toolbarDefaultItemIdentifiers_(self, _toolbar):
        return [identifier for identifier, _label, _symbol_name in SETTINGS_PANES]

    def toolbarSelectableItemIdentifiers_(self, _toolbar):
        return [identifier for identifier, _label, _symbol_name in SETTINGS_PANES]

    def toolbar_itemForItemIdentifier_willBeInsertedIntoToolbar_(
        self, _toolbar, identifier, _will_insert
    ):
        label, symbol_name = next(
            (label, symbol_name)
            for pane_id, label, symbol_name in SETTINGS_PANES
            if pane_id == identifier
        )
        item = AppKit.NSToolbarItem.alloc().initWithItemIdentifier_(identifier)
        item.setLabel_(label)
        item.setPaletteLabel_(label)
        item.setImage_(_symbol(symbol_name, label))
        item.setTarget_(self)
        item.setAction_("selectPane:")
        return item

    def selectPane_(self, sender) -> None:
        self._select_pane(sender.itemIdentifier())

    def transcriptionModelChanged_(self, _sender) -> None:
        engine, _model = _selected_value(
            self.transcription_model_popup, TRANSCRIPTION_MODEL_VALUES
        )
        self.maximum_accuracy.setEnabled_(engine == "local")
        if engine == "local":
            help_text = "Runs entirely on this Mac. Audio never leaves your device."
        else:
            provider = "OpenAI" if engine == "openai" else "ElevenLabs"
            help_text = f"Each recording is sent to {provider} and billed to your API key."
            if not self._saved_keys.get(engine):
                help_text += f" Add a {provider} key under API Keys."
        self.transcription_help.setStringValue_(help_text)

    def chooseReplacements_(self, _sender) -> None:
        panel = AppKit.NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(True)
        panel.setCanChooseDirectories_(False)
        panel.setAllowsMultipleSelection_(False)
        panel.setAllowedFileTypes_(["json"])
        panel.setMessage_("Choose a JSON file of term replacements.")

        def finished(response):
            if response == AppKit.NSModalResponseOK and panel.URL() is not None:
                self.replacements_field.setStringValue_(panel.URL().path())

        panel.beginSheetModalForWindow_completionHandler_(self.window, finished)

    @objc.python_method
    def _pane(self, sections):
        pane = self._stack(sections, vertical=True, spacing=SECTION_SPACING)
        pane.setEdgeInsets_(AppKit.NSEdgeInsets(22, PANE_INSET, 6, PANE_INSET))
        pane.widthAnchor().constraintEqualToConstant_(WINDOW_WIDTH).setActive_(True)
        return pane

    @objc.python_method
    def _section(self, title: str, rows, footer=None):
        header = self._label(title, 13.0, weight=AppKit.NSFontWeightSemibold)
        group = SettingsGroupView.alloc().initWithFrame_(AppKit.NSZeroRect)
        group.setOrientation_(AppKit.NSUserInterfaceLayoutOrientationVertical)
        group.setAlignment_(AppKit.NSLayoutAttributeLeading)
        group.setSpacing_(0)
        group.setEdgeInsets_(AppKit.NSEdgeInsets(3, 0, 3, 0))
        group.setTranslatesAutoresizingMaskIntoConstraints_(False)
        for row in rows:
            group.addArrangedSubview_(row)
        group.widthAnchor().constraintEqualToConstant_(GROUP_WIDTH).setActive_(True)
        views = [header, group] + ([footer] if footer is not None else [])
        section = self._stack(views, vertical=True, spacing=7)
        section.widthAnchor().constraintEqualToConstant_(GROUP_WIDTH).setActive_(True)
        return section

    @objc.python_method
    def _row(self, title, control, *, subtitle: str | None = None):
        """A label on the left and a control on the right, System Settings style."""
        row = AppKit.NSView.alloc().initWithFrame_(AppKit.NSZeroRect)
        row.setTranslatesAutoresizingMaskIntoConstraints_(False)
        control.setTranslatesAutoresizingMaskIntoConstraints_(False)
        if isinstance(title, str):
            texts = [self._label(title)]
            if subtitle:
                texts.append(
                    self._label(
                        subtitle,
                        11.0,
                        color=AppKit.NSColor.secondaryLabelColor(),
                        wraps=True,
                        width=ROW_INNER_WIDTH - control.fittingSize().width - ROW_SPACING,
                    )
                )
            text = self._stack(texts, vertical=True, spacing=2)
            if title and isinstance(control, AppKit.NSControl):
                control.setAccessibilityLabel_(title)
        else:
            text = title
        row.addSubview_(text)
        row.addSubview_(control)
        preferred_height = row.heightAnchor().constraintEqualToConstant_(ROW_HEIGHT)
        preferred_height.setPriority_(AppKit.NSLayoutPriorityDefaultLow)
        AppKit.NSLayoutConstraint.activateConstraints_(
            [
                row.widthAnchor().constraintEqualToConstant_(GROUP_WIDTH),
                row.heightAnchor().constraintGreaterThanOrEqualToConstant_(ROW_HEIGHT),
                row.heightAnchor().constraintGreaterThanOrEqualToAnchor_constant_(
                    text.heightAnchor(), 18
                ),
                row.heightAnchor().constraintGreaterThanOrEqualToAnchor_constant_(
                    control.heightAnchor(), 14
                ),
                text.leadingAnchor().constraintEqualToAnchor_constant_(
                    row.leadingAnchor(), ROW_PADDING
                ),
                text.centerYAnchor().constraintEqualToAnchor_(row.centerYAnchor()),
                control.trailingAnchor().constraintEqualToAnchor_constant_(
                    row.trailingAnchor(), -ROW_PADDING
                ),
                control.centerYAnchor().constraintEqualToAnchor_(row.centerYAnchor()),
                control.leadingAnchor().constraintGreaterThanOrEqualToAnchor_constant_(
                    text.trailingAnchor(), ROW_SPACING
                ),
                preferred_height,
            ]
        )
        return row

    @staticmethod
    @objc.python_method
    def _slot(controls):
        """Overlay alternative controls in one trailing-aligned position."""
        slot = AppKit.NSView.alloc().initWithFrame_(AppKit.NSZeroRect)
        slot.setTranslatesAutoresizingMaskIntoConstraints_(False)
        constraints = [
            slot.widthAnchor().constraintEqualToConstant_(CONTROL_WIDTH),
            slot.heightAnchor().constraintEqualToConstant_(26),
        ]
        for control in controls:
            control.setTranslatesAutoresizingMaskIntoConstraints_(False)
            slot.addSubview_(control)
            constraints += [
                control.trailingAnchor().constraintEqualToAnchor_(slot.trailingAnchor()),
                control.centerYAnchor().constraintEqualToAnchor_(slot.centerYAnchor()),
            ]
        AppKit.NSLayoutConstraint.activateConstraints_(constraints)
        return slot

    @staticmethod
    @objc.python_method
    def _stack(views, *, vertical: bool, spacing: float):
        stack = AppKit.NSStackView.stackViewWithViews_(views)
        stack.setOrientation_(
            AppKit.NSUserInterfaceLayoutOrientationVertical
            if vertical
            else AppKit.NSUserInterfaceLayoutOrientationHorizontal
        )
        stack.setAlignment_(
            AppKit.NSLayoutAttributeLeading if vertical else AppKit.NSLayoutAttributeCenterY
        )
        stack.setSpacing_(spacing)
        stack.setTranslatesAutoresizingMaskIntoConstraints_(False)
        return stack

    @staticmethod
    @objc.python_method
    def _label(
        value: str,
        size: float = 13.0,
        *,
        weight: float = 0.0,
        color=None,
        wraps: bool = False,
        width: float = GROUP_WIDTH,
    ):
        label = (
            AppKit.NSTextField.wrappingLabelWithString_(value)
            if wraps
            else AppKit.NSTextField.labelWithString_(value)
        )
        label.setFont_(AppKit.NSFont.systemFontOfSize_weight_(size, weight))
        label.setTranslatesAutoresizingMaskIntoConstraints_(False)
        if color is not None:
            label.setTextColor_(color)
        if wraps:
            label.setPreferredMaxLayoutWidth_(width)
        else:
            label.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        return label

    @classmethod
    @objc.python_method
    def _footer(cls, value: str):
        return cls._label(
            value, 11.0, color=AppKit.NSColor.secondaryLabelColor(), wraps=True
        )

    @objc.python_method
    def _button(self, title: str, action: str):
        button = AppKit.NSButton.buttonWithTitle_target_action_(title, self, action)
        button.setTranslatesAutoresizingMaskIntoConstraints_(False)
        return button

    @staticmethod
    @objc.python_method
    def _popup(labels: list[str], width: float = CONTROL_WIDTH):
        popup = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(
            AppKit.NSMakeRect(0, 0, width, 25), False
        )
        popup.addItemsWithTitles_(labels)
        popup.setTranslatesAutoresizingMaskIntoConstraints_(False)
        popup.widthAnchor().constraintEqualToConstant_(width).setActive_(True)
        return popup

    @staticmethod
    @objc.python_method
    def _field(value: str, width: float):
        field = AppKit.NSTextField.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, width, 22)
        )
        field.setStringValue_(value)
        field.setBezelStyle_(AppKit.NSTextFieldRoundedBezel)
        field.setUsesSingleLineMode_(True)
        field.setLineBreakMode_(AppKit.NSLineBreakByTruncatingMiddle)
        field.setTranslatesAutoresizingMaskIntoConstraints_(False)
        field.widthAnchor().constraintEqualToConstant_(width).setActive_(True)
        return field

    @staticmethod
    @objc.python_method
    def _combo_box(values: list[str], value: str):
        combo = AppKit.NSComboBox.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, CONTROL_WIDTH, 26)
        )
        combo.addItemsWithObjectValues_(values)
        combo.setStringValue_(value)
        combo.setCompletes_(True)
        combo.setNumberOfVisibleItems_(min(8, max(1, len(values))))
        combo.setTranslatesAutoresizingMaskIntoConstraints_(False)
        combo.widthAnchor().constraintEqualToConstant_(CONTROL_WIDTH).setActive_(True)
        return combo

    @objc.python_method
    def _key_control(self, provider: str):
        """Return a provider key field and the row control that shows its status."""
        saved = self._saved_keys[provider]
        field = AppKit.NSSecureTextField.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, KEY_FIELD_WIDTH, 22)
        )
        field.setPlaceholderString_("Saved in Keychain" if saved else "Paste API key")
        field.setBezelStyle_(AppKit.NSTextFieldRoundedBezel)
        field.setTranslatesAutoresizingMaskIntoConstraints_(False)
        status = AppKit.NSImageView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 16, 16))
        status.setImage_(_symbol("checkmark.circle.fill" if saved else "circle.dashed"))
        status.setContentTintColor_(
            AppKit.NSColor.systemGreenColor()
            if saved
            else AppKit.NSColor.tertiaryLabelColor()
        )
        status.setToolTip_("Saved in your Keychain" if saved else "No key saved")
        status.setTranslatesAutoresizingMaskIntoConstraints_(False)
        AppKit.NSLayoutConstraint.activateConstraints_(
            [
                field.widthAnchor().constraintEqualToConstant_(KEY_FIELD_WIDTH),
                status.widthAnchor().constraintEqualToConstant_(16),
                status.heightAnchor().constraintEqualToConstant_(16),
            ]
        )
        return field, self._stack([field, status], vertical=False, spacing=8)

    @objc.python_method
    def _shortcut_button(self, action: str):
        button = self._button("", action)
        button.setBezelStyle_(AppKit.NSBezelStyleRounded)
        button.setFont_(
            AppKit.NSFont.systemFontOfSize_weight_(13.0, AppKit.NSFontWeightMedium)
        )
        button.widthAnchor().constraintEqualToConstant_(SHORTCUT_BUTTON_WIDTH).setActive_(True)
        return button

    @staticmethod
    @objc.python_method
    def _shortcut_title(value: str | None) -> str:
        if value is None:
            return "Not set · click, then press keys"
        if value == "fn":
            return "Fn / Globe"
        return HotkeyConfig(value).label

    @objc.python_method
    def _refresh_shortcut_buttons(self) -> None:
        self.primary_shortcut_button.setTitle_(
            self._shortcut_title(self.primary_shortcut_value)
        )
        self.secondary_shortcut_button.setTitle_(
            self._shortcut_title(self.secondary_shortcut_value)
        )
        if hasattr(self, "shortcut_help"):
            self.shortcut_help.setStringValue_(SHORTCUT_HELP)

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
        hint = "Press keys… · Esc cancels"
        if which == "secondary":
            hint = "Press keys · Delete clears"
        button.setTitle_(hint)
        button.setBezelColor_(AppKit.NSColor.controlAccentColor())
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
        for button in (
            getattr(self, "primary_shortcut_button", None),
            getattr(self, "secondary_shortcut_button", None),
        ):
            if button is not None:
                button.setBezelColor_(None)

    def show(self) -> None:
        self._close_notified = False
        self._brand_application_menu()
        self._ensure_edit_menu()
        self._start_editing_shortcut_monitor()
        self.refreshAudioDevices_(None)
        self._start_device_timer()
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)
        self._run_chatgpt("refresh")

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
                "Connect a microphone or Bluetooth headset to use it here."
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
            f"{len(devices)} microphone{'s' if len(devices) != 1 else ''} available. Bluetooth headsets appear automatically."
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
        self._chatgpt_cancel.set()

    def saveSettings_(self, _sender) -> None:
        if self._chatgpt_busy:
            self._show_error("Wait for the ChatGPT account operation to finish, or cancel sign-in.")
            return
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
            post_process_chatgpt=rewrite_engine == "chatgpt",
            chatgpt_rewrite_model=_selected_value(self.chatgpt_model_popup, self.chatgpt_model_values),
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
            if updated.post_process_chatgpt:
                models = {item["slug"] for item in self._chatgpt_state.get("models", [])}
                if not self._chatgpt_state.get("connected") or not models:
                    raise ConfigurationError("Connect your ChatGPT subscription and refresh models using ChatGPT account… first.")
                if updated.chatgpt_rewrite_model and updated.chatgpt_rewrite_model not in models:
                    raise ConfigurationError("The selected ChatGPT model is unavailable. Choose a model from the synchronized list.")
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
        self.openai_rewrite_model_popup.setHidden_(selected != "openai")
        self.openrouter_model_field.setHidden_(selected != "openrouter")
        self.chatgpt_model_popup.setHidden_(selected != "chatgpt")
        self.local_rewrite_model_label.setHidden_(selected != "local")
        self.rewrite_model_row.setHidden_(selected == "off")
        self.chatgpt_section.setHidden_(selected != "chatgpt")
        self.rewrite_help.setStringValue_(
            REWRITE_HELP[selected]
            + ("" if selected == "off" else "\n" + PROFESSIONAL_TIP)
        )
        self._fit_window()

    def showChatGPTAccount_(self, _sender) -> None:
        self._select_pane("rephrasing")
        self._update_chatgpt_controls()
        self._run_chatgpt("refresh")

    @objc.python_method
    def _run_chatgpt(self, operation, account_id=None):
        if self._chatgpt_busy:
            return
        self._chatgpt_busy = True
        self._chatgpt_operation = operation
        self._chatgpt_cancel = threading.Event()
        self._update_chatgpt_controls("Connecting in your browser…" if operation in {"connect", "add"} else "Synchronizing ChatGPT…")

        def work():
            message = ""
            try:
                if operation in {"connect", "add"}:
                    self.chatgpt.sign_in(new_account=operation == "add", cancel=self._chatgpt_cancel)
                    message = "You're using your ChatGPT plan. Choose a model, then Save Settings."
                elif operation == "disconnect":
                    if not self.chatgpt.disconnect():
                        message = "Disconnected locally. Remote revocation was not confirmed; disconnect in ChatGPT settings too."
                elif operation == "select":
                    self.chatgpt.select_account(account_id)
                elif self.chatgpt.snapshot()["connected"]:
                    self.chatgpt.sync_models()
            except ChatGPTError as error:
                message = str(error)
            except Exception:
                message = "Could not update the ChatGPT connection. Please try again."
            try:
                state = self.chatgpt.snapshot()
            except Exception:
                state = {}
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "chatGPTFinished:", {"state": state, "message": message, "operation": operation}, False)

        threading.Thread(target=work, daemon=True).start()

    def chatGPTFinished_(self, result) -> None:
        self._chatgpt_busy = False
        if self._close_notified:
            return
        previous_account = self._chatgpt_state.get("active")
        self._chatgpt_state = dict(result["state"])
        selected = _selected_value(self.chatgpt_model_popup, self.chatgpt_model_values)
        if previous_account and previous_account != self._chatgpt_state.get("active"):
            selected = ""
        models = self._chatgpt_state.get("models", [])
        self.chatgpt_model_values = [""] + [model["slug"] for model in models]
        labels = ["Automatic — account default"] + [model["display_name"] for model in models]
        # NSPopUpButton coalesces duplicate titles; keep one item per model.
        labels = [labels[0]] + [
            label if labels.count(label) == 1 else f"{label} ({model['slug']})"
            for label, model in zip(labels[1:], models)
        ]
        if selected and selected not in self.chatgpt_model_values:
            self.chatgpt_model_values.append(selected)
            labels.append(selected + " — unavailable")
        self.chatgpt_model_popup.removeAllItems()
        self.chatgpt_model_popup.addItemsWithTitles_(labels)
        _select_value(self.chatgpt_model_popup, self.chatgpt_model_values, selected)
        if result.get("operation") in {"connect", "add"} and self._chatgpt_state.get("connected") and models:
            _select_value(self.rewrite_engine_popup, REWRITE_ENGINE_VALUES, "chatgpt")
            self.rewriteEngineChanged_(None)
        self._update_chatgpt_controls(result["message"])

    @objc.python_method
    def _update_chatgpt_controls(self, message=""):
        if getattr(self, "chatgpt_status", None) is None:
            return
        state = self._chatgpt_state
        connected = bool(state.get("connected"))
        model_count = len(state.get("models", []))
        if connected:
            status = f"Connected · {model_count} model{'s' if model_count != 1 else ''} available"
        else:
            status = "Not connected. Sign in to rephrase with your plan."
        self.chatgpt_status.setStringValue_(status + ("\n" + message if message else ""))
        self.chatgpt_account_ids = [account["id"] for account in state.get("accounts", [])]
        self.chatgpt_accounts.removeAllItems()
        self.chatgpt_accounts.addItemsWithTitles_([account["label"] for account in state.get("accounts", [])] or ["No saved accounts"])
        _select_value(self.chatgpt_accounts, self.chatgpt_account_ids, state.get("active"))
        self.chatgpt_accounts.setEnabled_(not self._chatgpt_busy and bool(self.chatgpt_account_ids))
        self.chatgpt_accounts.setHidden_(not self.chatgpt_account_ids)
        signing_in = self._chatgpt_busy and self._chatgpt_operation in {"connect", "add"}
        self.chatgpt_connect_button.setHidden_(connected or signing_in)
        self.chatgpt_connect_button.setEnabled_(not self._chatgpt_busy)
        self.chatgpt_cancel_button.setHidden_(not signing_in)
        for button in (
            self.chatgpt_refresh_button,
            self.chatgpt_add_button,
            self.chatgpt_usage_button,
            self.chatgpt_disconnect_button,
        ):
            button.setHidden_(not connected or signing_in)
            button.setEnabled_(not self._chatgpt_busy)
        self._fit_window()

    def connectChatGPT_(self, _sender):
        self._run_chatgpt("connect")

    def addChatGPTAccount_(self, _sender):
        self._run_chatgpt("add")

    def refreshChatGPT_(self, _sender):
        self._run_chatgpt("refresh")

    def disconnectChatGPT_(self, _sender):
        self._run_chatgpt("disconnect")

    def cancelChatGPTSignIn_(self, _sender):
        self._chatgpt_cancel.set()

    def manageChatGPTUsage_(self, _sender):
        webbrowser.open(USAGE_URL)

    def chatGPTAccountChanged_(self, _sender):
        if self.chatgpt_account_ids:
            self._run_chatgpt("select", _selected_value(self.chatgpt_accounts, self.chatgpt_account_ids))

    @staticmethod
    @objc.python_method
    def _show_error(message: str) -> None:
        alert = AppKit.NSAlert.alloc().init()
        alert.setAlertStyle_(AppKit.NSAlertStyleCritical)
        alert.setMessageText_("Could not save RedWhisper settings")
        alert.setInformativeText_(message)
        alert.runModal()
