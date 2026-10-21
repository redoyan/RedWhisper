"""Native Windows settings dialog for RedWhisper."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from audio_devices import InputDevice, windows_input_devices
from hotkey_config import HotkeyConfig, custom_hotkey_value
from mlx_whisper_core import (
    ConfigurationError,
    DEFAULT_OPENROUTER_MODEL,
    ELEVENLABS_TRANSCRIPTION_MODELS,
    OPENAI_REWRITE_MODELS,
    OPENAI_TRANSCRIPTION_MODELS,
    elevenlabs_key_from_environment,
    openai_key_from_environment,
    openrouter_key_from_environment,
    save_elevenlabs_key,
    save_openai_key,
    save_openrouter_key,
    validate_openrouter_model,
)
from settings_store import AppSettings


WINDOWS_HOTKEYS = {
    "right_control": "Right Ctrl (recommended)",
    "right_alt": "Right Alt",
    "f8": "F8",
}
WINDOWS_HOTKEY_VIRTUAL_KEYS = {
    "right_control": 0xA3,
    "right_alt": 0xA5,
    "f8": 0x77,
}


def windows_virtual_key_label(virtual_key: int) -> str:
    known = {
        0x08: "Backspace",
        0x09: "Tab",
        0x0D: "Enter",
        0x10: "Shift",
        0x11: "Ctrl",
        0x12: "Alt",
        0x13: "Pause",
        0x14: "Caps Lock",
        0x1B: "Esc",
        0x20: "Space",
        0x21: "Page Up",
        0x22: "Page Down",
        0x23: "End",
        0x24: "Home",
        0x25: "Left Arrow",
        0x26: "Up Arrow",
        0x27: "Right Arrow",
        0x28: "Down Arrow",
        0x2D: "Insert",
        0x2E: "Delete",
        0x5B: "Left Windows",
        0x5C: "Right Windows",
        0x90: "Num Lock",
        0x91: "Scroll Lock",
        0xA0: "Left Shift",
        0xA1: "Right Shift",
        0xA2: "Left Ctrl",
        0xA3: "Right Ctrl",
        0xA4: "Left Alt",
        0xA5: "Right Alt",
        0xAD: "Volume Mute",
        0xAE: "Volume Down",
        0xAF: "Volume Up",
        0xB0: "Next Track",
        0xB1: "Previous Track",
        0xB2: "Stop Media",
        0xB3: "Play/Pause Media",
    }
    if virtual_key in known:
        return known[virtual_key]
    if 0x30 <= virtual_key <= 0x39 or 0x41 <= virtual_key <= 0x5A:
        return chr(virtual_key)
    if 0x70 <= virtual_key <= 0x87:
        return f"F{virtual_key - 0x6F}"
    return f"Key 0x{virtual_key:02X}"


def windows_custom_hotkey_value(virtual_key: int) -> str:
    if not 1 <= virtual_key <= 254:
        raise ValueError("That key is not exposed as a usable Windows keyboard key.")
    return custom_hotkey_value(
        virtual_key,
        (),
        windows_virtual_key_label(virtual_key),
    )


def is_supported_windows_hotkey(value: str) -> bool:
    if value in WINDOWS_HOTKEYS:
        return True
    try:
        config = HotkeyConfig(value)
    except ValueError:
        return False
    return (
        value.startswith("custom:")
        and not config.modifiers
        and config.keycode is not None
    )


def windows_default_settings(settings: AppSettings) -> AppSettings:
    """Migrate macOS-only options while preserving a Windows microphone choice."""
    shortcut_supported = is_supported_windows_hotkey(settings.hotkey_preset)
    if (
        shortcut_supported
        and settings.secondary_hotkey_preset is None
        and not settings.post_process_local
    ):
        return settings
    values = {
        field: getattr(settings, field)
        for field in AppSettings.__dataclass_fields__
    }
    if not shortcut_supported:
        values["hotkey_preset"] = "right_control"
    values["secondary_hotkey_preset"] = None
    values["post_process_local"] = False
    return AppSettings(**values)


def disable_combobox_mouse_wheel(root) -> None:
    """Let the wheel scroll the settings page without changing dropdown values.

    Tk's default combobox binding cycles the selection on every wheel tick, so
    scrolling past the shortcut or microphone silently changed them.
    """
    # Returning None (not "break") lets the page-level scroll binding run.
    root.bind_class("TCombobox", "<MouseWheel>", lambda _event: None)


def configure_windows_settings(
    current: AppSettings,
    input_devices: list[InputDevice],
    *,
    first_run: bool = False,
    parent=None,
    on_open: Callable[[], None] | None = None,
) -> AppSettings | None:
    """Show the settings dialog and return validated settings when saved."""
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    current = windows_default_settings(current)
    input_devices = windows_input_devices(input_devices)
    root = tk.Tk() if parent is None else tk.Toplevel(parent)
    root.title("RedWhisper Settings")
    root.geometry("760x780")
    root.minsize(680, 620)
    try:
        root.iconbitmap(default=str(Path(__file__).resolve().parent / "assets" / "RedWhisper.ico"))
    except tk.TclError:
        pass

    selected: AppSettings | None = None
    has_openai_key = bool(openai_key_from_environment())
    has_openrouter_key = bool(openrouter_key_from_environment())
    has_elevenlabs_key = bool(elevenlabs_key_from_environment())

    container = ttk.Frame(root)
    container.pack(fill="both", expand=True)
    canvas = tk.Canvas(
        container,
        highlightthickness=0,
        background=root.cget("background"),
    )
    scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    main = ttk.Frame(canvas, padding=24)
    main_window = canvas.create_window((0, 0), window=main, anchor="nw")
    main.bind(
        "<Configure>",
        lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
    )
    canvas.bind(
        "<Configure>",
        lambda event: canvas.itemconfigure(main_window, width=event.width),
    )
    canvas.bind_all(
        "<MouseWheel>",
        lambda event: canvas.yview_scroll(int(-event.delta / 120), "units"),
    )
    disable_combobox_mouse_wheel(root)
    ttk.Label(main, text="RedWhisper", font=("Segoe UI", 20, "bold")).pack(anchor="w")
    ttk.Label(
        main,
        text=(
            "Hold your shortcut to record. Release it to transcribe and insert "
            "text into the active application."
        ),
        wraplength=610,
    ).pack(anchor="w", pady=(4, 18))

    form = ttk.Frame(main)
    form.pack(fill="both", expand=True)
    form.columnconfigure(1, weight=1)

    engine = tk.StringVar(value=current.engine)
    language = tk.StringVar(value=current.language)
    accuracy = tk.BooleanVar(value=current.maximum_accuracy)
    openai_model = tk.StringVar(value=current.openai_model)
    openrouter_model = tk.StringVar(value=current.openrouter_transcription_model)
    elevenlabs_model = tk.StringVar(value=current.elevenlabs_model)
    rewrite_engine = tk.StringVar(
        value=(
            "openai"
            if current.post_process_openai
            else "openrouter"
            if current.post_process_openrouter
            else "off"
        )
    )
    openai_rewrite_model = tk.StringVar(value=current.openai_rewrite_model)
    openrouter_rewrite_model = tk.StringVar(
        value=current.openrouter_model
    )
    openrouter_rewrite_models = tuple(
        dict.fromkeys(
            (
                *current.openrouter_models,
                current.openrouter_model,
                DEFAULT_OPENROUTER_MODEL,
            )
        )
    )
    initial_hotkey = (
        current.hotkey_preset
        if is_supported_windows_hotkey(current.hotkey_preset)
        else "right_control"
    )
    hotkey_value_by_label = {
        label: value for value, label in WINDOWS_HOTKEYS.items()
    }
    if initial_hotkey in WINDOWS_HOTKEYS:
        initial_hotkey_label = WINDOWS_HOTKEYS[initial_hotkey]
    else:
        initial_hotkey_label = f"Custom: {HotkeyConfig(initial_hotkey).key}"
        hotkey_value_by_label[initial_hotkey_label] = initial_hotkey
    hotkey = tk.StringVar(value=initial_hotkey_label)
    automatic_microphone_label = "Auto-detect (recommended)"
    microphone_by_label: dict[str, InputDevice | None] = {
        automatic_microphone_label: None,
    }
    for device in input_devices:
        microphone_by_label[device.name] = device
    initial_microphone_label = automatic_microphone_label
    if current.input_device_name:
        initial_microphone_label = next(
            (
                label
                for label, device in microphone_by_label.items()
                if device
                and device.name.casefold()
                == current.input_device_name.casefold()
            ),
            automatic_microphone_label,
        )
    elif current.input_device is not None:
        initial_microphone_label = next(
            (
                label
                for label, device in microphone_by_label.items()
                if device and device.id == current.input_device
            ),
            automatic_microphone_label,
        )
    microphone = tk.StringVar(value=initial_microphone_label)
    gain = tk.StringVar(value=str(current.microphone_gain))
    replacement_path = tk.StringVar(value=current.replacements or "")
    openai_api_key = tk.StringVar()
    openrouter_api_key = tk.StringVar()
    elevenlabs_api_key = tk.StringVar()

    row = 0

    def add_label(text: str) -> None:
        nonlocal row
        ttk.Label(form, text=text, font=("Segoe UI", 10, "bold")).grid(
            row=row, column=0, sticky="nw", padx=(0, 18), pady=8
        )

    add_label("Transcription")
    engine_frame = ttk.Frame(form)
    engine_frame.grid(row=row, column=1, sticky="ew", pady=4)
    ttk.Radiobutton(
        engine_frame,
        text="Local Whisper (private, first use downloads the model)",
        variable=engine,
        value="local",
    ).pack(anchor="w")
    ttk.Radiobutton(
        engine_frame,
        text="OpenAI transcription (recordings are sent to OpenAI)",
        variable=engine,
        value="openai",
    ).pack(anchor="w", pady=(4, 0))
    ttk.Radiobutton(
        engine_frame,
        text="OpenRouter transcription (choose an OpenRouter STT model)",
        variable=engine,
        value="openrouter",
    ).pack(anchor="w", pady=(4, 0))
    ttk.Radiobutton(
        engine_frame,
        text="ElevenLabs Scribe transcription",
        variable=engine,
        value="elevenlabs",
    ).pack(anchor="w", pady=(4, 0))
    row += 1

    add_label("Language")
    ttk.Combobox(
        form,
        textvariable=language,
        values=("auto", "en", "es", "fr", "de", "it", "pt", "ja", "ko", "zh"),
    ).grid(row=row, column=1, sticky="ew", pady=4)
    row += 1

    add_label("Local accuracy")
    ttk.Checkbutton(
        form,
        text="Maximum accuracy (slower)",
        variable=accuracy,
    ).grid(row=row, column=1, sticky="w", pady=4)
    row += 1

    add_label("OpenAI model")
    ttk.Combobox(
        form,
        textvariable=openai_model,
        values=OPENAI_TRANSCRIPTION_MODELS,
        state="readonly",
    ).grid(row=row, column=1, sticky="ew", pady=4)
    row += 1

    add_label("OpenAI API key")
    key_frame = ttk.Frame(form)
    key_frame.grid(row=row, column=1, sticky="ew", pady=4)
    key_frame.columnconfigure(0, weight=1)
    ttk.Entry(key_frame, textvariable=openai_api_key, show="*").grid(
        row=0, column=0, sticky="ew"
    )
    ttk.Label(
        key_frame,
        text=(
            "A key is already stored in Windows Credential Manager; leave blank to keep it."
            if has_openai_key
            else "Required for OpenAI transcription or restructuring. Stored in Windows Credential Manager."
        ),
        wraplength=440,
    ).grid(row=1, column=0, sticky="w", pady=(3, 0))
    row += 1

    add_label("OpenRouter model")
    ttk.Combobox(
        form,
        textvariable=openrouter_model,
        values=("openai/whisper-large-v3",),
    ).grid(row=row, column=1, sticky="ew", pady=4)
    row += 1

    add_label("OpenRouter API key")
    openrouter_key_frame = ttk.Frame(form)
    openrouter_key_frame.grid(row=row, column=1, sticky="ew", pady=4)
    openrouter_key_frame.columnconfigure(0, weight=1)
    ttk.Entry(
        openrouter_key_frame, textvariable=openrouter_api_key, show="*"
    ).grid(row=0, column=0, sticky="ew")
    ttk.Label(
        openrouter_key_frame,
        text=(
            "A key is already stored in Windows Credential Manager; leave blank to keep it."
            if has_openrouter_key
            else "Required for OpenRouter transcription or restructuring. Stored in Windows Credential Manager."
        ),
        wraplength=480,
    ).grid(row=1, column=0, sticky="w", pady=(3, 0))
    row += 1

    add_label("ElevenLabs model")
    ttk.Combobox(
        form,
        textvariable=elevenlabs_model,
        values=ELEVENLABS_TRANSCRIPTION_MODELS,
        state="readonly",
    ).grid(row=row, column=1, sticky="ew", pady=4)
    row += 1

    add_label("ElevenLabs API key")
    elevenlabs_key_frame = ttk.Frame(form)
    elevenlabs_key_frame.grid(row=row, column=1, sticky="ew", pady=4)
    elevenlabs_key_frame.columnconfigure(0, weight=1)
    ttk.Entry(
        elevenlabs_key_frame, textvariable=elevenlabs_api_key, show="*"
    ).grid(row=0, column=0, sticky="ew")
    ttk.Label(
        elevenlabs_key_frame,
        text=(
            "A key is already stored in Windows Credential Manager; leave blank to keep it."
            if has_elevenlabs_key
            else "Required only for ElevenLabs transcription. Stored in Windows Credential Manager."
        ),
        wraplength=480,
    ).grid(row=1, column=0, sticky="w", pady=(3, 0))
    row += 1

    add_label("Restructuring")
    rewrite_frame = ttk.Frame(form)
    rewrite_frame.grid(row=row, column=1, sticky="ew", pady=4)
    ttk.Radiobutton(
        rewrite_frame,
        text="Off (insert the transcript without an LLM rewrite)",
        variable=rewrite_engine,
        value="off",
    ).pack(anchor="w")
    ttk.Radiobutton(
        rewrite_frame,
        text="OpenAI (send transcript text to OpenAI)",
        variable=rewrite_engine,
        value="openai",
    ).pack(anchor="w", pady=(4, 0))
    ttk.Radiobutton(
        rewrite_frame,
        text="OpenRouter (send transcript text to the selected model)",
        variable=rewrite_engine,
        value="openrouter",
    ).pack(anchor="w", pady=(4, 0))
    ttk.Label(
        rewrite_frame,
        text=(
            "The rewrite fixes grammar and spoken phrasing and creates coherent paragraphs. "
            "Begin a recording with ‘professional’ to request a more formal tone."
        ),
        wraplength=480,
    ).pack(anchor="w", pady=(5, 0))
    row += 1

    add_label("OpenAI rewrite model")
    ttk.Combobox(
        form,
        textvariable=openai_rewrite_model,
        values=OPENAI_REWRITE_MODELS,
        state="readonly",
    ).grid(row=row, column=1, sticky="ew", pady=4)
    row += 1

    add_label("OpenRouter rewrite model")
    ttk.Combobox(
        form,
        textvariable=openrouter_rewrite_model,
        values=openrouter_rewrite_models,
    ).grid(row=row, column=1, sticky="ew", pady=4)
    row += 1

    def capture_push_to_talk_key() -> None:
        from pynput.keyboard import Listener

        capture = tk.Toplevel(root)
        capture.title("Choose push-to-talk key")
        capture.geometry("460x170")
        capture.resizable(False, False)
        capture.transient(root)
        capture.grab_set()
        ttk.Label(
            capture,
            text="Press one key now",
            font=("Segoe UI", 16, "bold"),
        ).pack(pady=(24, 8))
        ttk.Label(
            capture,
            text=(
                "The key will be reserved for dictation. Press Esc to cancel.\n"
                "Fn works only when the keyboard exposes it to Windows; if this "
                "screen does not react, that Fn key is handled by keyboard firmware."
            ),
            justify="center",
            wraplength=410,
        ).pack()

        listener_holder: dict[str, Listener] = {}
        completed = False

        def close_capture() -> None:
            listener = listener_holder.get("listener")
            if listener:
                listener.stop()
            if capture.winfo_exists():
                capture.grab_release()
                capture.destroy()

        def accept_key(virtual_key: int) -> None:
            if virtual_key == 0x1B:
                close_capture()
                return
            preset = next(
                (
                    name
                    for name, key_code in WINDOWS_HOTKEY_VIRTUAL_KEYS.items()
                    if key_code == virtual_key
                ),
                None,
            )
            if preset:
                value = preset
                label = WINDOWS_HOTKEYS[preset]
            else:
                try:
                    value = windows_custom_hotkey_value(virtual_key)
                except ValueError as error:
                    messagebox.showerror("RedWhisper", str(error), parent=capture)
                    close_capture()
                    return
                label = f"Custom: {windows_virtual_key_label(virtual_key)}"
            hotkey_value_by_label[label] = value
            hotkey_box.configure(values=tuple(hotkey_value_by_label))
            hotkey.set(label)
            close_capture()

        def event_filter(message, data) -> bool:
            nonlocal completed
            if int(message) in (0x0100, 0x0101, 0x0104, 0x0105) and not completed:
                completed = True
                root.after(0, lambda: accept_key(int(data.vkCode)))
            return False

        capture.protocol("WM_DELETE_WINDOW", close_capture)
        listener = Listener(
            on_press=lambda _key: None,
            on_release=lambda _key: None,
            win32_event_filter=event_filter,
        )
        listener_holder["listener"] = listener
        listener.start()
        capture.focus_force()

    add_label("Push-to-talk")
    hotkey_frame = ttk.Frame(form)
    hotkey_frame.grid(row=row, column=1, sticky="ew", pady=4)
    hotkey_frame.columnconfigure(0, weight=1)
    hotkey_box = ttk.Combobox(
        hotkey_frame,
        textvariable=hotkey,
        values=tuple(hotkey_value_by_label),
        state="readonly",
    )
    hotkey_box.grid(row=0, column=0, sticky="ew")
    ttk.Button(
        hotkey_frame,
        text="Capture key…",
        command=capture_push_to_talk_key,
    ).grid(row=0, column=1, padx=(8, 0))
    ttk.Label(
        form,
        text="This key is reserved for dictation while RedWhisper is running, so it is not typed into other apps.",
        wraplength=480,
    ).grid(row=row + 1, column=1, sticky="w", pady=(0, 4))
    row += 1
    row += 1

    add_label("Microphone")
    ttk.Combobox(
        form,
        textvariable=microphone,
        values=tuple(microphone_by_label),
        state="readonly",
    ).grid(row=row, column=1, sticky="ew", pady=4)
    ttk.Label(
        form,
        text=(
            "Auto-detect prefers a connected headset, then a webcam microphone, "
            "then another physical input. It checks again before each recording. "
            "A selected microphone is preferred when available; virtual inputs "
            "are never chosen automatically."
        ),
        wraplength=480,
        justify="left",
    ).grid(row=row + 1, column=1, sticky="w", pady=(0, 4))
    row += 1
    row += 1

    add_label("Sensitivity")
    ttk.Combobox(
        form,
        textvariable=gain,
        values=("1.0", "2.0", "4.0", "8.0"),
        state="readonly",
    ).grid(row=row, column=1, sticky="ew", pady=4)
    row += 1

    add_label("Replacements")
    replacement_frame = ttk.Frame(form)
    replacement_frame.grid(row=row, column=1, sticky="ew", pady=4)
    replacement_frame.columnconfigure(0, weight=1)
    ttk.Entry(replacement_frame, textvariable=replacement_path).grid(
        row=0, column=0, sticky="ew"
    )

    def browse_replacements() -> None:
        path = filedialog.askopenfilename(
            title="Choose replacements JSON",
            filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
        )
        if path:
            replacement_path.set(path)

    ttk.Button(replacement_frame, text="Browse...", command=browse_replacements).grid(
        row=0, column=1, padx=(8, 0)
    )
    row += 1

    ttk.Label(
        main,
        text=(
            "Local transcription uses faster-whisper on NVIDIA CUDA when available, "
            "or an optimized CPU path otherwise. Cloud restructuring sends transcript "
            "text—not the recording—to the selected provider."
        ),
        wraplength=610,
    ).pack(anchor="w", pady=(12, 10))

    buttons = ttk.Frame(main)
    buttons.pack(fill="x", pady=(8, 0))

    def close_dialog() -> None:
        canvas.unbind_all("<MouseWheel>")
        root.destroy()

    def save() -> None:
        nonlocal selected
        nonlocal has_openai_key, has_openrouter_key, has_elevenlabs_key
        try:
            clean_openai_key = openai_api_key.get().strip()
            clean_openrouter_key = openrouter_api_key.get().strip()
            clean_elevenlabs_key = elevenlabs_api_key.get().strip()
            if clean_openai_key:
                save_openai_key(clean_openai_key)
                has_openai_key = True
            if clean_openrouter_key:
                save_openrouter_key(clean_openrouter_key)
                has_openrouter_key = True
            if clean_elevenlabs_key:
                save_elevenlabs_key(clean_elevenlabs_key)
                has_elevenlabs_key = True
            if (
                engine.get() == "openai" or rewrite_engine.get() == "openai"
            ) and not has_openai_key:
                raise ValueError(
                    "Enter an OpenAI API key before selecting an OpenAI feature."
                )
            if (
                engine.get() == "openrouter" or rewrite_engine.get() == "openrouter"
            ) and not has_openrouter_key:
                raise ValueError(
                    "Enter an OpenRouter API key before selecting an OpenRouter feature."
                )
            if engine.get() == "elevenlabs" and not has_elevenlabs_key:
                raise ValueError(
                    "Enter an ElevenLabs API key before selecting ElevenLabs transcription."
                )
            if not input_devices:
                raise ValueError("Windows does not currently report a microphone.")
            replacements = replacement_path.get().strip() or None
            if replacements and not Path(replacements).is_file():
                raise ValueError("The replacements file does not exist.")
            clean_language = language.get().strip()
            if not clean_language:
                raise ValueError("Enter a language code or 'auto'.")
            clean_openrouter_rewrite_model = validate_openrouter_model(
                openrouter_rewrite_model.get()
            )
            selected_microphone = microphone_by_label[microphone.get()]
            selected = AppSettings(
                engine=engine.get(),
                language=clean_language,
                maximum_accuracy=accuracy.get(),
                replacements=replacements,
                post_process_local=False,
                post_process_openai=rewrite_engine.get() == "openai",
                post_process_openrouter=rewrite_engine.get() == "openrouter",
                hotkey_preset=hotkey_value_by_label[hotkey.get()],
                secondary_hotkey_preset=None,
                microphone_gain=float(gain.get()),
                input_device=(
                    selected_microphone.id if selected_microphone else None
                ),
                input_device_name=(
                    selected_microphone.name if selected_microphone else None
                ),
                openai_model=openai_model.get(),
                openrouter_transcription_model=openrouter_model.get().strip(),
                elevenlabs_model=elevenlabs_model.get(),
                openai_rewrite_model=openai_rewrite_model.get(),
                openrouter_model=clean_openrouter_rewrite_model,
                openrouter_models=tuple(
                    dict.fromkeys(
                        (
                            *current.openrouter_models,
                            clean_openrouter_rewrite_model,
                        )
                    )
                ),
            )
            selected.validate()
        except (ConfigurationError, KeyError, OSError, StopIteration, ValueError) as error:
            messagebox.showerror("RedWhisper", str(error), parent=root)
            return
        close_dialog()

    def cancel() -> None:
        close_dialog()

    ttk.Button(buttons, text="Cancel", command=cancel).pack(side="right")
    ttk.Button(
        buttons,
        text="Save and Start" if first_run else "Save and Restart",
        command=save,
    ).pack(side="right", padx=(0, 8))

    root.protocol("WM_DELETE_WINDOW", cancel)
    if on_open:
        root.after(0, on_open)
    if parent is None:
        root.mainloop()
    else:
        root.grab_set()
        root.focus_force()
        parent.wait_window(root)
    return selected
