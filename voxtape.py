#!/usr/bin/env python3
"""
RedWhisper — voice-to-text for macOS, optimized for Apple Silicon.

Open-source alternative to Wispr Flow and SuperWhisper.
Uses local MLX Whisper by default, with explicit opt-in cloud transcription.

Usage:
    python voxtape.py                     # Default: English, whisper-large-v3-turbo
    python voxtape.py --language en        # Explicit English
    python voxtape.py --language auto      # Auto-detect language
    python voxtape.py --model mlx-community/whisper-large-v3-mlx
"""

import os
import sys
import time
import argparse
import tempfile
import threading
import subprocess
from collections import deque
from pathlib import Path
import numpy as np
import sounddevice as sd
import soundfile as sf
import rumps
import AppKit

from audio_processing import apply_microphone_gain, root_mean_square
from audio_devices import (
    InputDevice,
    device_by_id,
    device_by_name,
    input_devices_from_sounddevice,
    preferred_input_device,
    refresh_input_devices,
)
from hotkey_config import ActiveHotkeys, HotkeyConfig, PushToTalkLatch
from settings_store import AppSettings, SettingsStore
from chatgpt_subscription import ChatGPTTextPostProcessor

from mlx_whisper_core import (
    ConfigurationError,
    DEFAULT_LOCAL_LLM,
    DEFAULT_LOCAL_MODEL,
    DEFAULT_OPENROUTER_MODEL,
    DEFAULT_OPENAI_REWRITE_MODEL,
    ELEVENLABS_TRANSCRIPTION_MODELS,
    ElevenLabsTranscriber,
    ElevenLabsTranscriptionOptions,
    LocalLLMPostProcessor,
    LocalWhisperOptions,
    MLXWhisperTranscriber,
    OPENAI_REWRITE_MODELS,
    OPENAI_TRANSCRIPTION_MODELS,
    OpenAIRewriteOptions,
    OpenAITextPostProcessor,
    OpenAITranscriber,
    OpenAITranscriptionOptions,
    OpenRouterRewriteOptions,
    OpenRouterTextPostProcessor,
    TermReplacer,
    TranscriptPipeline,
    elevenlabs_key_from_environment,
    openai_key_from_environment,
    openrouter_key_from_environment,
)

# ─── macOS Quartz for global hotkey ──────────────────────────────────────────

import Quartz

# ─── Defaults ────────────────────────────────────────────────────────────────

DEFAULT_MODEL = DEFAULT_LOCAL_MODEL
DEFAULT_LANGUAGE = "en"
SAMPLE_RATE = 16000


def restructuring_label(settings: AppSettings) -> str:
    if settings.post_process_chatgpt:
        return f"ChatGPT plan · {settings.chatgpt_rewrite_model or 'Automatic'}"
    if settings.post_process_local:
        return "Local Llama 3B"
    if settings.post_process_openai:
        return f"OpenAI {settings.openai_rewrite_model}"
    if settings.post_process_openrouter:
        return f"OpenRouter {settings.openrouter_model}"
    return "Off"


def recording_indicator_origin(
    visible_x: float,
    visible_y: float,
    visible_width: float,
    panel_width: float,
    bottom_margin: float = 24,
) -> tuple[float, float]:
    """Return a bottom-center panel origin in macOS screen coordinates."""
    return (
        visible_x + (visible_width - panel_width) / 2,
        visible_y + bottom_margin,
    )


def recording_status_text(elapsed_seconds: float) -> str:
    elapsed = max(0, int(elapsed_seconds))
    minutes, seconds = divmod(elapsed, 60)
    return f"{minutes:02d}:{seconds:02d}"


MIN_DURATION_S = 0.8  # Ignore clips shorter than 800ms
MIN_AUDIO_ENERGY = 0.0002  # Conservative silence guard; Whisper also detects no-speech
SETTINGS_RESTART_EXIT_CODE = 75
AUDIO_SHUTDOWN_TIMEOUT_S = 5.0

# ─── Quartz Hotkey Listener ──────────────────────────────────────────────────


class QuartzHotkeyListener:
    """
    Intercepts key events at the Quartz CGEventTap level.
    Catches the configured shortcut before the active application receives it.
    """

    def __init__(self, hotkey: HotkeyConfig, on_press, on_release):
        self.hotkey = hotkey
        self.on_press = on_press
        self.on_release = on_release
        self._latch = PushToTalkLatch()
        self._tap = None
        self._source = None

    def start(self):
        def _event_callback(proxy, event_type, event, refcon):
            if event_type in (
                Quartz.kCGEventTapDisabledByTimeout,
                Quartz.kCGEventTapDisabledByUserInput,
            ):
                if self._tap is not None:
                    Quartz.CGEventTapEnable(self._tap, True)
                if self._latch.release():
                    self.on_release()
                return event

            modifier_only = self.hotkey.modifier_only
            if modifier_only and event_type == Quartz.kCGEventFlagsChanged:
                flags = Quartz.CGEventGetFlags(event)
                modifier_mask = quartz_single_modifier_mask(modifier_only)
                modifier_is_down = bool(flags & modifier_mask)
                was_pressed = self._latch.is_pressed
                relevant = flags & quartz_all_modifier_mask()
                if (
                    modifier_is_down
                    and relevant == modifier_mask
                    and self._latch.press()
                ):
                    self.on_press()
                elif not modifier_is_down and self._latch.release():
                    self.on_release()
                return None if modifier_is_down != was_pressed else event

            if event_type not in (Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp):
                return event

            kc = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode
            )
            if modifier_only or kc != self.hotkey.keycode:
                return event

            if event_type == Quartz.kCGEventKeyUp and self._latch.release():
                self.on_release()
                return None

            # Match the configured modifiers exactly on the initial key-down.
            flags = Quartz.CGEventGetFlags(event)
            relevant = flags & (
                Quartz.kCGEventFlagMaskAlternate
                | Quartz.kCGEventFlagMaskShift
                | Quartz.kCGEventFlagMaskControl
                | Quartz.kCGEventFlagMaskCommand
            )

            if (
                event_type == Quartz.kCGEventKeyDown
                and relevant == quartz_modifier_mask(self.hotkey.modifiers)
            ):
                if self._latch.press():
                    self.on_press()
                return None  # Swallow the event — no ∂

            return event

        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,
            (
                Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
                | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
                | Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
            ),
            _event_callback,
            None,
        )

        if tap is None:
            print("❌ Could not create event tap.")
            print("   Grant Accessibility permission to RedWhisper:")
            print("   System Settings → Privacy & Security → Accessibility")
            return False

        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(loop, source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
        self._tap = tap
        self._source = source
        return True

    def set_enabled(self, enabled: bool) -> None:
        """Pause event interception while the native shortcut recorder is active."""
        if self._tap is not None:
            Quartz.CGEventTapEnable(self._tap, enabled)
        if not enabled and self._latch.release():
            self.on_release()


def quartz_modifier_mask(modifiers: tuple[str, ...]) -> int:
    masks = {
        "option": Quartz.kCGEventFlagMaskAlternate,
        "control": Quartz.kCGEventFlagMaskControl,
        "command": Quartz.kCGEventFlagMaskCommand,
        "shift": Quartz.kCGEventFlagMaskShift,
    }
    result = 0
    for modifier in modifiers:
        result |= masks[modifier]
    return result


def quartz_single_modifier_mask(modifier: str) -> int:
    if modifier == "function":
        return Quartz.kCGEventFlagMaskSecondaryFn
    return quartz_modifier_mask((modifier,))


def quartz_all_modifier_mask() -> int:
    return (
        Quartz.kCGEventFlagMaskAlternate
        | Quartz.kCGEventFlagMaskShift
        | Quartz.kCGEventFlagMaskControl
        | Quartz.kCGEventFlagMaskCommand
        | Quartz.kCGEventFlagMaskSecondaryFn
    )


WAVEFORM_LINE_RGBA = (0.22, 0.17, 0.14, 0.72)
RECORDING_GLASS_ALPHA = 0.72


class WaveformView(AppKit.NSView):
    """Draw a compact, continuous line that responds to live audio levels."""

    def initWithFrame_(self, frame):
        self = AppKit.NSView.initWithFrame_(self, frame)
        if self is not None:
            self._levels = [0.0] * 12
        return self

    def setLevels_(self, levels) -> None:
        self._levels = list(levels)
        self.setNeedsDisplay_(True)

    def drawRect_(self, _dirty_rect) -> None:
        bounds = self.bounds()
        count = len(self._levels)
        if count < 2:
            return

        horizontal_padding = 8.0
        step = (bounds.size.width - horizontal_padding * 2) / (count - 1)
        center_y = bounds.size.height / 2
        amplitude = max(1.0, center_y - 5.0)
        points = []
        for index, level in enumerate(self._levels):
            normalized = max(0.0, min(1.0, level))
            direction = -1.0 if index % 2 else 1.0
            points.append(
                AppKit.NSMakePoint(
                    horizontal_padding + index * step,
                    center_y + direction * normalized * amplitude,
                )
            )

        AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(
            *WAVEFORM_LINE_RGBA
        ).setStroke()
        path = AppKit.NSBezierPath.bezierPath()
        path.setLineWidth_(2.0)
        path.setLineCapStyle_(AppKit.NSRoundLineCapStyle)
        path.setLineJoinStyle_(AppKit.NSRoundLineJoinStyle)
        path.moveToPoint_(points[0])
        for previous, point in zip(points, points[1:]):
            midpoint_x = (previous.x + point.x) / 2
            path.curveToPoint_controlPoint1_controlPoint2_(
                point,
                AppKit.NSMakePoint(midpoint_x, previous.y),
                AppKit.NSMakePoint(midpoint_x, point.y),
            )
        path.stroke()


class RecordingIndicator:
    """Compact liquid-glass capsule shown only while audio is recording."""

    def __init__(self):
        self.width, self.height = 214, 46
        rect = AppKit.NSMakeRect(0, 0, self.width, self.height)
        self.panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            AppKit.NSWindowStyleMaskBorderless
            | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        self.panel.setLevel_(AppKit.NSStatusWindowLevel)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        self.panel.setHasShadow_(True)
        self.panel.setIgnoresMouseEvents_(True)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setReleasedWhenClosed_(False)
        self.panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            | AppKit.NSWindowCollectionBehaviorStationary
            | AppKit.NSWindowCollectionBehaviorIgnoresCycle
        )

        content = AppKit.NSView.alloc().initWithFrame_(rect)
        glass = AppKit.NSVisualEffectView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, self.width, self.height)
        )
        glass.setMaterial_(AppKit.NSVisualEffectMaterialHUDWindow)
        glass.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
        glass.setState_(AppKit.NSVisualEffectStateActive)
        glass.setAlphaValue_(RECORDING_GLASS_ALPHA)
        glass.setWantsLayer_(True)
        glass.layer().setCornerRadius_(self.height / 2)
        glass.layer().setMasksToBounds_(True)
        glass.layer().setBorderWidth_(0.75)
        glass.layer().setBorderColor_(
            AppKit.NSColor.whiteColor().colorWithAlphaComponent_(0.24).CGColor()
        )
        content.addSubview_(glass)
        self.glass = glass

        self.waveform = WaveformView.alloc().initWithFrame_(
            AppKit.NSMakeRect(20, 10, 128, 26)
        )
        content.addSubview_(self.waveform)

        self.time_label = AppKit.NSTextField.labelWithString_("00:00")
        self.time_label.setFrame_(AppKit.NSMakeRect(158, 12, 44, 20))
        self.time_label.setTextColor_(
            AppKit.NSColor.labelColor().colorWithAlphaComponent_(0.9)
        )
        self.time_label.setFont_(
            AppKit.NSFont.monospacedDigitSystemFontOfSize_weight_(12, 0.4)
        )
        self.time_label.setAlignment_(AppKit.NSTextAlignmentRight)
        content.addSubview_(self.time_label)
        self.panel.setContentView_(content)
        self._levels = deque([0.0] * 12, maxlen=12)
        self._started_at = 0.0

    @staticmethod
    def _screen_under_pointer():
        pointer = AppKit.NSEvent.mouseLocation()
        for screen in AppKit.NSScreen.screens():
            if AppKit.NSPointInRect(pointer, screen.frame()):
                return screen
        return AppKit.NSScreen.mainScreen()

    def _position_at_bottom_center(self) -> None:
        screen = self._screen_under_pointer()
        visible = (
            screen.visibleFrame()
            if screen
            else AppKit.NSMakeRect(0, 0, 1440, 900)
        )
        x, y = recording_indicator_origin(
            visible.origin.x,
            visible.origin.y,
            visible.size.width,
            self.width,
        )
        self.panel.setFrameOrigin_(AppKit.NSMakePoint(x, y))

    def show(self) -> None:
        self._levels = deque([0.0] * 12, maxlen=12)
        self.waveform.setLevels_(self._levels)
        self.time_label.setStringValue_("00:00")
        self._started_at = time.monotonic()
        self._position_at_bottom_center()
        self.panel.orderFrontRegardless()

    def update_level(self, level: float) -> None:
        self._levels.append(max(0.0, min(1.0, level * 18.0)))
        self.waveform.setLevels_(self._levels)
        self.time_label.setStringValue_(
            recording_status_text(time.monotonic() - self._started_at)
        )

    def reset_status(self) -> None:
        self.time_label.setStringValue_("00:00")

    def hide(self) -> None:
        self.panel.orderOut_(None)


class MLXWhisperApp(rumps.App):
    """Menu bar app with a configurable recording toggle and auto-paste."""

    def __init__(
        self,
        pipeline: TranscriptPipeline,
        engine_label: str,
        language: str,
        hotkey: HotkeyConfig,
        secondary_hotkey: HotkeyConfig | None,
        microphone_gain: float,
        input_device: int,
        input_device_name: str,
        local_transcriber: MLXWhisperTranscriber | None = None,
        local_post_processor: LocalLLMPostProcessor | None = None,
        settings_store: SettingsStore | None = None,
        current_settings: AppSettings | None = None,
        input_devices: list[InputDevice] | None = None,
    ):
        module_dir = Path(__file__).resolve().parent
        bundled_menu_icon = module_dir.parent / "RedWhisper-menu.png"
        if not bundled_menu_icon.exists():
            bundled_menu_icon = module_dir / "assets" / "RedWhisper-menu.png"
        has_bundled_menu_icon = bundled_menu_icon.exists()
        super().__init__(
            "RedWhisper",
            title=None if has_bundled_menu_icon else "🎙️",
            icon=str(bundled_menu_icon) if has_bundled_menu_icon else None,
            template=True,
            quit_button=None,
        )
        self._has_bundled_menu_icon = has_bundled_menu_icon
        self.pipeline = pipeline
        self.local_transcriber = local_transcriber
        self.local_post_processor = local_post_processor
        self._ready = local_transcriber is None and local_post_processor is None
        self.hotkey = hotkey
        self.secondary_hotkey = secondary_hotkey
        self._active_hotkeys = ActiveHotkeys()
        self.microphone_gain = microphone_gain
        self.input_device = input_device
        self.input_device_name = input_device_name
        self.preferred_input_device_name = input_device_name
        self.settings_store = settings_store or SettingsStore()
        self.current_settings = current_settings or AppSettings()
        self.input_devices = input_devices or []
        self._settings_controller = None
        self.recording_indicator = RecordingIndicator()

        self.is_recording = False
        self.audio_chunks: list[np.ndarray] = []
        self.stream: sd.InputStream | None = None
        self._transcribing = False
        self._latest_audio_level = 0.0
        self._level_timer = rumps.Timer(self._refresh_indicator, 0.08)

        hotkeys = [hotkey] + ([secondary_hotkey] if secondary_hotkey else [])
        self._hotkey_listeners = [
            QuartzHotkeyListener(
                hotkey=config,
                on_press=lambda config=config: self._press_safe(config),
                on_release=lambda config=config: self._release_safe(config),
            )
            for config in hotkeys
        ]
        listener_results = [listener.start() for listener in self._hotkey_listeners]
        hotkey_available = all(listener_results)
        shortcut_label = " + ".join(config.label for config in hotkeys)
        self.shortcut_label = shortcut_label

        # Menu bar items
        lang_display = language if language != "auto" else "auto-detect"
        self.record_menu_item = rumps.MenuItem(
            f"Start Recording  ({shortcut_label})",
            callback=self._on_toggle_click,
        )
        self.microphone_menu_item = rumps.MenuItem(
            f"Microphone: {input_device_name}",
            callback=None,
        )
        self.menu = [
            self.record_menu_item,
            None,
            rumps.MenuItem(f"Engine: {engine_label}", callback=None),
            rumps.MenuItem(
                f"Restructuring: {restructuring_label(self.current_settings)}",
                callback=None,
            ),
            rumps.MenuItem(f"Language: {lang_display}", callback=None),
            self.microphone_menu_item,
            rumps.MenuItem(
                f"Shortcuts: {shortcut_label}"
                if hotkey_available
                else "Shortcuts unavailable — grant Accessibility",
                callback=None,
            ),
            None,
            rumps.MenuItem("Settings…", callback=self._open_settings),
            rumps.MenuItem("Quit RedWhisper", callback=self._quit),
        ]

        # Warmup: load every selected local model before accepting dictation.
        if not self._ready:
            self._set_menu_status("⏳")
            self.record_menu_item.title = "Preparing selected models…"
            threading.Thread(target=self._warmup, daemon=True).start()

    # ── Toggle (thread-safe via rumps timer) ──────────────────────────────

    def _set_menu_status(self, fallback_title: str) -> None:
        """Keep the native template icon visible when one is bundled."""
        self.title = None if self._has_bundled_menu_icon else fallback_title

    def _press_safe(self, hotkey: HotkeyConfig):
        """Called from Quartz callback — schedule recording start on main."""
        if self._active_hotkeys.press(hotkey.preset):
            rumps.Timer(
                lambda timer: self._do_start(timer, hotkey.label), 0.01
            ).start()

    def _release_safe(self, hotkey: HotkeyConfig):
        """Called from Quartz callback — schedule recording stop on main."""
        if self._active_hotkeys.release(hotkey.preset):
            rumps.Timer(self._do_stop, 0.01).start()

    def _do_start(self, timer, hotkey_label: str):
        timer.stop()
        self._start_recording(hotkey_label)

    def _do_stop(self, timer):
        timer.stop()
        self._stop_recording()

    def _on_toggle_click(self, _sender=None):
        self._toggle()

    def _toggle(self):
        if self._transcribing:
            return
        if not self._ready:
            rumps.notification("RedWhisper", "Model loading", "Please try again shortly.")
            return
        if self.is_recording:
            self._stop_recording()
        else:
            self._start_recording()

    # ── Recording ─────────────────────────────────────────────────────────

    def _start_recording(self, hotkey_label: str | None = None):
        if self.is_recording or self._transcribing:
            return
        if not self._ready:
            rumps.notification("RedWhisper", "Model loading", "Please try again shortly.")
            return
        try:
            self.audio_chunks = []
            try:
                self._open_input_stream()
            except Exception as initial_error:
                self._close_input_stream()
                print(
                    "⚠️  Microphone stream failed; refreshing macOS audio devices: "
                    f"{initial_error}"
                )
                try:
                    self._refresh_selected_input_device()
                    self._open_input_stream()
                except Exception as recovery_error:
                    self._close_input_stream()
                    raise RuntimeError(
                        "Could not recover the selected microphone: "
                        f"{recovery_error}"
                    ) from recovery_error

            self.is_recording = True
            self._set_menu_status("🔴")
            active_label = hotkey_label or self.hotkey.label
            self.record_menu_item.title = f"Stop Recording  ({active_label})"
            self.recording_indicator.reset_status()
            self.recording_indicator.show()
            self._latest_audio_level = 0.0
            self._level_timer.start()
            self._play_sound("Tink")
        except Exception as error:
            self.is_recording = False
            self._close_input_stream()
            self._set_menu_status("🎙️")
            self.recording_indicator.hide()
            rumps.notification("RedWhisper", "Microphone error", str(error)[:200])

    def _open_input_stream(self) -> None:
        self.stream = sd.InputStream(
            device=self.input_device,
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=1024,
            callback=self._audio_callback,
        )
        self.stream.start()

    def _close_input_stream(self, timeout_exit_code: int = SETTINGS_RESTART_EXIT_CODE) -> None:
        stream, self.stream = self.stream, None
        if stream is None:
            return

        def shutdown_timed_out():
            # CoreAudio can deadlock inside stop/close while holding native
            # locks. Only a fresh runtime can safely recover that audio state.
            print("⚠️  Microphone shutdown stalled; exiting audio runtime.", flush=True)
            os._exit(timeout_exit_code)

        watchdog = threading.Timer(AUDIO_SHUTDOWN_TIMEOUT_S, shutdown_timed_out)
        watchdog.daemon = True
        watchdog.start()
        try:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        finally:
            watchdog.cancel()

    def _refresh_selected_input_device(self) -> None:
        devices = refresh_input_devices(sd)
        preferred_name = getattr(
            self,
            "preferred_input_device_name",
            self.input_device_name,
        )
        try:
            selected = device_by_name(devices, preferred_name)
        except (TypeError, ValueError):
            default_device = sd.default.device
            try:
                default_input_id = int(default_device[0])
            except (TypeError, IndexError):
                default_input_id = int(default_device)
            selected = preferred_input_device(devices, default_input_id)

        self.input_devices = devices
        self.input_device = selected.id
        self.input_device_name = selected.name
        self.microphone_menu_item.title = f"Microphone: {selected.name}"
        print(f"✅ Microphone recovered: {selected.name}")

    def _audio_callback(self, indata, frames, time_info, status):
        if self.is_recording:
            self.audio_chunks.append(indata.copy())
            self._latest_audio_level = root_mean_square(indata) * self.microphone_gain

    def _refresh_indicator(self, _timer):
        if self.is_recording:
            self.recording_indicator.update_level(self._latest_audio_level)

    def _stop_recording(self):
        if not self.is_recording:
            return
        self.is_recording = False
        self._level_timer.stop()
        self.recording_indicator.hide()
        self.record_menu_item.title = f"Start Recording  ({self.shortcut_label})"
        self._close_input_stream()
        self._play_sound("Pop")
        self._set_menu_status("⏳")

        self._transcribing = True
        threading.Thread(target=self._transcribe, daemon=True).start()

    # ── Transcription (MLX Whisper on Metal GPU) ──────────────────────────

    def _transcribe(self):
        self._transcribing = True
        try:
            if not self.audio_chunks:
                return

            audio = np.concatenate(self.audio_chunks, axis=0).flatten()

            # Skip very short clips
            if len(audio) < SAMPLE_RATE * MIN_DURATION_S:
                print("⚠️  Too short, skipped")
                return

            # Apply configurable digital input gain without allowing clipping.
            audio = apply_microphone_gain(audio, self.microphone_gain)

            # Keep only an extremely conservative silence guard. Whisper's
            # no-speech probability performs the primary speech detection.
            energy = root_mean_square(audio)
            if energy < MIN_AUDIO_ENERGY:
                print(f"⚠️  Audio too quiet (energy={energy:.4f}), skipped")
                return

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            sf.write(tmp_path, audio, SAMPLE_RATE)

            try:
                t0 = time.perf_counter()

                text = self.pipeline.process(tmp_path)
                post_process_warning = self.pipeline.consume_post_process_warning()
                elapsed = time.perf_counter() - t0
                duration = len(audio) / SAMPLE_RATE

                if text:
                    self._paste_at_cursor(text)
                    self._play_sound("Morse")
                    print(f"✅ {duration:.1f}s audio → {elapsed:.1f}s processing")
                    if post_process_warning:
                        print(
                            "⚠️  Restructuring failed; pasted the original transcript: "
                            f"{post_process_warning}"
                        )
                        rumps.notification(
                            "RedWhisper",
                            "Restructuring unavailable",
                            "The original transcript was pasted instead.",
                        )
                else:
                    print("⚠️  No speech detected")

            finally:
                tmp_path.unlink(missing_ok=True)

        except Exception as e:
            print(f"❌ Error: {e}")
            rumps.notification("RedWhisper", "Transcription error", str(e)[:200])
        finally:
            self._transcribing = False
            AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                self._finish_transcription_ui
            )

    def _finish_transcription_ui(self):
        self._set_menu_status("🎙️")
        self.recording_indicator.hide()

    # ── Paste at cursor ───────────────────────────────────────────────────

    def _paste_at_cursor(self, text: str):
        """Copy text to clipboard and simulate Cmd+V."""
        proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
        proc.communicate(text.encode("utf-8"))

        time.sleep(0.05)

        subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events" to keystroke "v" using command down',
            ],
            capture_output=True,
        )

    # ── Sound feedback ────────────────────────────────────────────────────

    @staticmethod
    def _play_sound(name: str):
        path = f"/System/Library/Sounds/{name}.aiff"
        if os.path.exists(path):
            subprocess.Popen(
                ["afplay", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    # ── Warmup ────────────────────────────────────────────────────────────

    def _warmup(self):
        tmp_path = None
        try:
            if self.local_transcriber:
                print("⏳ Loading local MLX Whisper model...")
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tmp_path = Path(tmp.name)
                sf.write(tmp_path, np.zeros(SAMPLE_RATE, dtype="float32"), SAMPLE_RATE)
                self.local_transcriber.transcribe(tmp_path)
            if self.local_post_processor:
                print("⏳ Preparing local rewrite model...")
                self.local_post_processor.warmup()
            self._ready = True
            print("✅ Selected models loaded — ready to dictate!")
            AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                self._finish_warmup_ui
            )
        except Exception as e:
            print(f"⚠️  Warmup error: {e}")
            rumps.notification("RedWhisper", "Model failed to load", str(e)[:200])
        finally:
            if tmp_path:
                tmp_path.unlink(missing_ok=True)

    def _finish_warmup_ui(self):
        self._set_menu_status("🎙️")
        self.record_menu_item.title = f"Start Recording  ({self.shortcut_label})"

    # ── Quit ──────────────────────────────────────────────────────────────

    def _open_settings(self, _sender=None):
        if self.is_recording or self._transcribing:
            rumps.notification(
                "RedWhisper",
                "Settings unavailable",
                "Finish the current dictation first.",
            )
            return
        self._set_hotkeys_enabled(False)
        try:
            from native_settings import NativeSettingsController

            if self._settings_controller is None:
                self._settings_controller = NativeSettingsController.alloc().init()
                self._settings_controller.configure(
                    self.current_settings,
                    self.input_devices,
                    self._save_native_settings,
                    self._resume_hotkeys,
                    self._available_input_devices,
                )
            self._settings_controller.show()
        except Exception:
            self._resume_hotkeys()
            raise

    @staticmethod
    def _available_input_devices() -> list[InputDevice]:
        return refresh_input_devices(sd)

    def _set_hotkeys_enabled(self, enabled: bool) -> None:
        for listener in self._hotkey_listeners:
            listener.set_enabled(enabled)

    def _resume_hotkeys(self) -> None:
        self._set_hotkeys_enabled(True)

    def _save_native_settings(self, updated: AppSettings) -> None:
        self.settings_store.save(updated)
        self.current_settings = updated
        self._restart_after_settings_save()

    def _restart_after_settings_save(self):
        self.recording_indicator.hide()
        self._level_timer.stop()
        self._close_input_stream()
        self._set_menu_status("⏳")
        self.record_menu_item.title = "Applying settings…"
        os._exit(SETTINGS_RESTART_EXIT_CODE)

    def _quit(self, _sender=None):
        self.recording_indicator.hide()
        self._level_timer.stop()
        self._close_input_stream(timeout_exit_code=0)
        rumps.quit_application()


# ─── CLI ──────────────────────────────────────────────────────────────────────


def configure_bundle_activation_policy() -> None:
    """Keep the bundled Python runtime out of the Dock."""
    if os.environ.get("REDWHISPER_BUNDLED") == "1":
        AppKit.NSApplication.sharedApplication().setActivationPolicy_(
            AppKit.NSApplicationActivationPolicyAccessory
        )


def _watch_launcher(launcher_pid: int) -> None:
    while os.getppid() == launcher_pid:
        time.sleep(1)
    os._exit(0)


def start_launcher_watchdog() -> None:
    """Exit the bundled runtime if Force Quit kills its native launcher."""
    if os.environ.get("REDWHISPER_BUNDLED") != "1":
        return
    try:
        launcher_pid = int(os.environ["REDWHISPER_LAUNCHER_PID"])
    except (KeyError, ValueError):
        return
    if launcher_pid <= 1:
        return
    threading.Thread(
        target=_watch_launcher,
        args=(launcher_pid,),
        name="redwhisper-launcher-watchdog",
        daemon=True,
    ).start()


def main():
    configure_bundle_activation_policy()
    start_launcher_watchdog()
    parser = argparse.ArgumentParser(
        prog="mlx-whisper-app",
        description="RedWhisper — voice-to-text for Apple Silicon",
    )
    parser.add_argument(
        "--model", "-m",
        default=DEFAULT_MODEL,
        help=f"MLX Whisper model from HuggingFace (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--engine",
        choices=("local", "openai", "elevenlabs"),
        default=None,
        help="Transcription engine; cloud engines explicitly send audio to their provider",
    )
    parser.add_argument(
        "--language", "-l",
        default=None,
        help=f"ISO language code or 'auto' (default: {DEFAULT_LANGUAGE})",
    )
    parser.add_argument(
        "--maximum-accuracy",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Use slower best-of-five decoding with the local MLX model",
    )
    parser.add_argument(
        "--initial-prompt",
        default="Voice dictation. Use accurate punctuation and paragraph breaks.",
        help="Vocabulary/context hint for the transcription engine",
    )
    parser.add_argument(
        "--replacements",
        type=Path,
        help="JSON object containing deterministic local term replacements",
    )
    parser.add_argument(
        "--post-process-local",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Explicitly enable local MLX-LM rewriting (off by default)",
    )
    parser.add_argument(
        "--post-process-openai",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable automatic casual/professional rewriting through the OpenAI API",
    )
    parser.add_argument(
        "--post-process-openrouter",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable automatic casual/professional rewriting through OpenRouter",
    )
    parser.add_argument(
        "--post-process-chatgpt", action=argparse.BooleanOptionalAction,
        default=None, help="Rewrite using your connected ChatGPT subscription",
    )
    parser.add_argument(
        "--chatgpt-rewrite-model", default=None,
        help="Account model slug; empty selects the first available account model",
    )
    parser.add_argument(
        "--local-llm-model",
        default=DEFAULT_LOCAL_LLM,
        help=f"MLX-LM model used only with --post-process-local (default: {DEFAULT_LOCAL_LLM})",
    )
    parser.add_argument(
        "--openai-model",
        choices=OPENAI_TRANSCRIPTION_MODELS,
        default=None,
        help="OpenAI audio transcription model used only with --engine openai",
    )
    parser.add_argument(
        "--elevenlabs-model",
        choices=ELEVENLABS_TRANSCRIPTION_MODELS,
        default=None,
        help="ElevenLabs transcription model used only with --engine elevenlabs",
    )
    parser.add_argument(
        "--openai-rewrite-model",
        choices=OPENAI_REWRITE_MODELS,
        default=None,
        help="OpenAI text model used only with --post-process-openai",
    )
    parser.add_argument(
        "--openrouter-model",
        default=None,
        help=f"OpenRouter model used only with --post-process-openrouter (default: {DEFAULT_OPENROUTER_MODEL})",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List available audio input devices",
    )
    parser.add_argument(
        "--no-launch-gui",
        action="store_true",
        help="Skip the browser settings screen and use command-line options",
    )
    parser.add_argument(
        "--hotkey",
        choices=(
            "fn",
            "option_d",
            "option_space",
            "control_space",
            "command_r",
            "command_shift_space",
        ),
        default=None,
        help="Global recording toggle shortcut (default: Fn / Globe key)",
    )
    parser.add_argument(
        "--secondary-hotkey",
        choices=(
            "fn",
            "option_d",
            "option_space",
            "control_space",
            "command_r",
            "command_shift_space",
        ),
        help="Optional second push-to-talk shortcut for an external keyboard",
    )
    parser.add_argument(
        "--microphone-gain",
        type=float,
        choices=(1.0, 2.0, 4.0, 8.0),
        default=None,
        help="Digital microphone sensitivity multiplier (default: 2.0)",
    )
    parser.add_argument(
        "--input-device",
        type=int,
        help="Core Audio input device ID (physical microphone preferred by default)",
    )
    args = parser.parse_args()

    if args.list_devices:
        print(sd.query_devices())
        return

    input_device_was_explicit = args.input_device is not None
    settings_store = SettingsStore()
    saved_settings = settings_store.load()
    saved_input_device_name = saved_settings.input_device_name
    args.engine = args.engine or saved_settings.engine
    args.language = args.language or saved_settings.language
    if args.maximum_accuracy is None:
        args.maximum_accuracy = saved_settings.maximum_accuracy
    if args.replacements is None and saved_settings.replacements:
        args.replacements = Path(saved_settings.replacements)
    if args.post_process_local is None:
        args.post_process_local = saved_settings.post_process_local
    if args.post_process_openai is None:
        args.post_process_openai = saved_settings.post_process_openai
    if args.post_process_openrouter is None:
        args.post_process_openrouter = saved_settings.post_process_openrouter
    if args.post_process_chatgpt is None:
        args.post_process_chatgpt = saved_settings.post_process_chatgpt
    if args.chatgpt_rewrite_model is None:
        args.chatgpt_rewrite_model = saved_settings.chatgpt_rewrite_model
    args.hotkey = args.hotkey or saved_settings.hotkey_preset
    args.secondary_hotkey = (
        args.secondary_hotkey or saved_settings.secondary_hotkey_preset
    )
    args.microphone_gain = args.microphone_gain or saved_settings.microphone_gain
    if args.input_device is None:
        args.input_device = saved_settings.input_device
    args.openai_model = args.openai_model or saved_settings.openai_model
    args.elevenlabs_model = (
        args.elevenlabs_model or saved_settings.elevenlabs_model
    )
    args.openai_rewrite_model = (
        args.openai_rewrite_model
        or saved_settings.openai_rewrite_model
        or DEFAULT_OPENAI_REWRITE_MODEL
    )
    args.openrouter_model = (
        args.openrouter_model
        or saved_settings.openrouter_model
        or DEFAULT_OPENROUTER_MODEL
    )
    if sum(
        (
            args.post_process_local,
            args.post_process_openai,
            args.post_process_openrouter,
            args.post_process_chatgpt,
        )
    ) > 1:
        parser.error("Select only one restructuring engine")

    input_devices = input_devices_from_sounddevice(sd.query_devices())
    default_device = sd.default.device
    try:
        default_input_id = int(default_device[0])
    except (TypeError, IndexError):
        default_input_id = int(default_device)
    try:
        if args.input_device is None:
            selected_input = preferred_input_device(input_devices, default_input_id)
        else:
            selected_input = device_by_id(input_devices, args.input_device)
            if (
                not input_device_was_explicit
                and saved_input_device_name
                and selected_input.name != saved_input_device_name
            ):
                selected_input = device_by_name(
                    input_devices, saved_input_device_name
                )
    except ValueError as error:
        if input_device_was_explicit:
            parser.error(str(error))
        try:
            selected_input = device_by_name(
                input_devices, saved_input_device_name
            )
        except (TypeError, ValueError):
            selected_input = preferred_input_device(input_devices, default_input_id)

    if not args.no_launch_gui:
        from launch_gui import configure_in_browser

        selection = configure_in_browser(
            engine=args.engine,
            language=args.language,
            maximum_accuracy=args.maximum_accuracy,
            replacements=str(args.replacements) if args.replacements else None,
            post_process_local=args.post_process_local,
            post_process_openai=args.post_process_openai,
            post_process_openrouter=args.post_process_openrouter,
            post_process_chatgpt=args.post_process_chatgpt,
            chatgpt_rewrite_model=args.chatgpt_rewrite_model,
            hotkey_preset=args.hotkey,
            secondary_hotkey_preset=args.secondary_hotkey,
            microphone_gain=args.microphone_gain,
            input_devices=input_devices,
            input_device=selected_input.id,
            openai_model=args.openai_model,
            elevenlabs_model=args.elevenlabs_model,
            openai_rewrite_model=args.openai_rewrite_model,
            openrouter_model=args.openrouter_model,
        )
        args.engine = selection.engine
        args.language = selection.language
        args.maximum_accuracy = selection.maximum_accuracy
        args.replacements = Path(selection.replacements) if selection.replacements else None
        args.post_process_local = selection.post_process_local
        args.post_process_openai = selection.post_process_openai
        args.post_process_openrouter = selection.post_process_openrouter
        args.post_process_chatgpt = selection.post_process_chatgpt
        args.chatgpt_rewrite_model = selection.chatgpt_rewrite_model
        args.hotkey = selection.hotkey_preset
        args.secondary_hotkey = selection.secondary_hotkey_preset
        args.microphone_gain = selection.microphone_gain
        if selection.input_device is not None:
            try:
                selected_input = device_by_id(input_devices, selection.input_device)
            except ValueError as error:
                parser.error(str(error))
        args.openai_model = selection.openai_model
        args.elevenlabs_model = selection.elevenlabs_model
        args.openai_rewrite_model = selection.openai_rewrite_model
        args.openrouter_model = selection.openrouter_model
        try:
            settings_store.save(
                AppSettings(
                    engine=args.engine,
                    language=args.language,
                    maximum_accuracy=args.maximum_accuracy,
                    replacements=str(args.replacements) if args.replacements else None,
                    post_process_local=args.post_process_local,
                    post_process_openai=args.post_process_openai,
                    post_process_openrouter=args.post_process_openrouter,
                    post_process_chatgpt=args.post_process_chatgpt,
                    chatgpt_rewrite_model=args.chatgpt_rewrite_model,
                    hotkey_preset=args.hotkey,
                    secondary_hotkey_preset=args.secondary_hotkey,
                    microphone_gain=args.microphone_gain,
                    input_device=selected_input.id,
                    input_device_name=selected_input.name,
                    openai_model=args.openai_model,
                    elevenlabs_model=args.elevenlabs_model,
                    openai_rewrite_model=args.openai_rewrite_model,
                    openrouter_model=args.openrouter_model,
                    openrouter_models=tuple(
                        dict.fromkeys(
                            (*saved_settings.openrouter_models, args.openrouter_model)
                        )
                    ),
                )
            )
        except (OSError, ValueError) as error:
            parser.error(f"Could not save settings: {error}")

    current_settings = AppSettings(
        engine=args.engine,
        language=args.language,
        maximum_accuracy=args.maximum_accuracy,
        replacements=str(args.replacements) if args.replacements else None,
        post_process_local=args.post_process_local,
        post_process_openai=args.post_process_openai,
        post_process_openrouter=args.post_process_openrouter,
        post_process_chatgpt=args.post_process_chatgpt,
        chatgpt_rewrite_model=args.chatgpt_rewrite_model,
        hotkey_preset=args.hotkey,
        secondary_hotkey_preset=args.secondary_hotkey,
        microphone_gain=args.microphone_gain,
        input_device=selected_input.id,
        input_device_name=selected_input.name,
        openai_model=args.openai_model,
        elevenlabs_model=args.elevenlabs_model,
        openai_rewrite_model=args.openai_rewrite_model,
        openrouter_model=args.openrouter_model,
        openrouter_models=tuple(
            dict.fromkeys((*saved_settings.openrouter_models, args.openrouter_model))
        ),
    )
    hotkey = HotkeyConfig(args.hotkey)
    secondary_hotkey = (
        HotkeyConfig(args.secondary_hotkey) if args.secondary_hotkey else None
    )

    try:
        language = None if args.language == "auto" else args.language
        local_transcriber = None
        if args.engine == "local":
            local_transcriber = MLXWhisperTranscriber(
                LocalWhisperOptions(
                    model=args.model,
                    language=language,
                    maximum_accuracy=args.maximum_accuracy,
                    initial_prompt=args.initial_prompt,
                )
            )
            transcriber = local_transcriber
            engine_label = args.model.split("/")[-1]
        elif args.engine == "openai":
            transcriber = OpenAITranscriber(
                OpenAITranscriptionOptions(
                    api_key=openai_key_from_environment(),
                    model=args.openai_model,
                    language=language,
                    prompt=args.initial_prompt,
                )
            )
            engine_label = args.openai_model
        else:
            transcriber = ElevenLabsTranscriber(
                ElevenLabsTranscriptionOptions(
                    api_key=elevenlabs_key_from_environment(),
                    model=args.elevenlabs_model,
                    language=language,
                )
            )
            engine_label = f"ElevenLabs {args.elevenlabs_model}"

        replacements = (
            TermReplacer.from_json(args.replacements) if args.replacements else None
        )
        local_post_processor = None
        if args.post_process_local:
            local_post_processor = LocalLLMPostProcessor(args.local_llm_model)
            post_processor = local_post_processor
        elif args.post_process_chatgpt:
            post_processor = ChatGPTTextPostProcessor(args.chatgpt_rewrite_model)
        elif args.post_process_openai:
            post_processor = OpenAITextPostProcessor(
                OpenAIRewriteOptions(
                    api_key=openai_key_from_environment(),
                    model=args.openai_rewrite_model,
                )
            )
        elif args.post_process_openrouter:
            post_processor = OpenRouterTextPostProcessor(
                OpenRouterRewriteOptions(
                    api_key=openrouter_key_from_environment(),
                    model=args.openrouter_model,
                )
            )
        else:
            post_processor = None
        pipeline = TranscriptPipeline(transcriber, replacements, post_processor)
    except ConfigurationError as error:
        parser.error(str(error))

    print("┌─────────────────────────────────────────┐")
    print("│  🎙️  RedWhisper — voice-to-text         │")
    print("│  Hotkey  : {:<29s}│".format(hotkey.label))
    if secondary_hotkey:
        print("│  Hotkey 2: {:<29s}│".format(secondary_hotkey.label))
    print("│  Engine  : {:<29s}│".format(engine_label[:29]))
    print(
        "│  Rewrite : {:<29s}│".format(
            restructuring_label(current_settings)[:29]
        )
    )
    print("│  Language : {:<28s}│".format(args.language))
    print("│  Mic      : {:<29s}│".format(selected_input.name[:29]))
    print("└─────────────────────────────────────────┘")
    print()

    app = MLXWhisperApp(
        pipeline=pipeline,
        engine_label=engine_label,
        language=args.language,
        hotkey=hotkey,
        secondary_hotkey=secondary_hotkey,
        microphone_gain=args.microphone_gain,
        input_device=selected_input.id,
        input_device_name=selected_input.name,
        local_transcriber=local_transcriber,
        local_post_processor=local_post_processor,
        settings_store=settings_store,
        current_settings=current_settings,
        input_devices=input_devices,
    )
    app.run()


if __name__ == "__main__":
    main()
