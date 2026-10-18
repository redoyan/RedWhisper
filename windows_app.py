"""RedWhisper Windows system-tray application."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from functools import lru_cache
import io
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import wave

import numpy as np
import sounddevice as sd
import soundfile as sf

from audio_devices import (
    InputDevice,
    device_by_id,
    is_virtual_input_device,
    preferred_input_device,
    refresh_input_devices,
    windows_input_devices,
)
from audio_processing import apply_microphone_gain, resample_audio, root_mean_square
from hotkey_config import ActiveHotkeys, HotkeyConfig
from mlx_whisper_core import (
    ConfigurationError,
    ElevenLabsTranscriber,
    ElevenLabsTranscriptionOptions,
    OpenAIRewriteOptions,
    OpenAITextPostProcessor,
    OpenAITranscriber,
    OpenAITranscriptionOptions,
    OpenRouterRewriteOptions,
    OpenRouterTextPostProcessor,
    OpenRouterTranscriber,
    OpenRouterTranscriptionOptions,
    TermReplacer,
    TranscriptPipeline,
    elevenlabs_key_from_environment,
    openai_key_from_environment,
    openrouter_key_from_environment,
)
from settings_store import AppSettings, SettingsStore
from windows_settings import configure_windows_settings, windows_default_settings
from windows_whisper_core import (
    DEFAULT_WINDOWS_MODEL,
    FasterWhisperOptions,
    FasterWhisperTranscriber,
)


SAMPLE_RATE = 16_000
MIN_DURATION_SECONDS = 0.25
MIN_AUDIO_ENERGY = 0.0005
INDICATOR_WIDTH = 76
INDICATOR_HEIGHT = 76
INDICATOR_BOTTOM_MARGIN = 24
RADIAL_LEVEL_COUNT = 64
WINDOWS_PUSH_TO_TALK_KEYS = {
    "right_control": 0xA3,  # VK_RCONTROL
    "right_alt": 0xA5,  # VK_RMENU
    "f8": 0x77,  # VK_F8
}
def enable_native_dpi_awareness() -> None:
    """Keep the compact recording indicator at an exact physical pixel size."""
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        pass


def resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def configure_logging() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    log_path = base / "RedWhisper" / "RedWhisper.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        encoding="utf-8",
    )
    logging.info("RedWhisper starting (frozen=%s)", getattr(sys, "frozen", False))
    return log_path


def copy_text_to_clipboard(text: str) -> None:
    """Place Unicode text on the Windows clipboard using the Win32 API."""
    if os.name != "nt":
        raise OSError("The Windows clipboard is only available on Windows")
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = (wintypes.HGLOBAL,)
    kernel32.GlobalFree.restype = wintypes.HGLOBAL
    user32.OpenClipboard.argtypes = (wintypes.HWND,)
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes = ()
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes = ()
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
    user32.SetClipboardData.restype = wintypes.HANDLE

    encoded = text.encode("utf-16-le") + b"\x00\x00"
    handle = kernel32.GlobalAlloc(0x0002, len(encoded))
    if not handle:
        raise ctypes.WinError()
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        kernel32.GlobalFree(handle)
        raise ctypes.WinError()
    try:
        ctypes.memmove(pointer, encoded, len(encoded))
    finally:
        kernel32.GlobalUnlock(handle)

    opened = False
    for _attempt in range(10):
        if user32.OpenClipboard(None):
            opened = True
            break
        time.sleep(0.02)
    if not opened:
        kernel32.GlobalFree(handle)
        raise ctypes.WinError()
    try:
        if not user32.EmptyClipboard():
            raise ctypes.WinError()
        if not user32.SetClipboardData(13, handle):  # CF_UNICODETEXT
            raise ctypes.WinError()
        handle = None  # Clipboard now owns the memory.
    finally:
        user32.CloseClipboard()
        if handle:
            kernel32.GlobalFree(handle)


def paste_at_cursor(text: str) -> None:
    copy_text_to_clipboard(text)
    time.sleep(0.06)
    from pynput.keyboard import Controller, Key

    keyboard = Controller()
    with keyboard.pressed(Key.ctrl):
        keyboard.press("v")
        keyboard.release("v")


def windows_hotkey_label(config: HotkeyConfig) -> str:
    single_key_names = {
        "right_control": "Right Ctrl",
        "right_alt": "Right Alt",
        "f8": "F8",
    }
    if config.preset in single_key_names:
        return single_key_names[config.preset]
    names = {"control": "Ctrl", "option": "Alt", "shift": "Shift", "command": "Win"}
    parts = [names[name] for name in config.modifiers]
    if config.key:
        parts.append(config.key)
    return " + ".join(parts)


def windows_hotkey_virtual_key(config: HotkeyConfig) -> int | None:
    if config.preset in WINDOWS_PUSH_TO_TALK_KEYS:
        return WINDOWS_PUSH_TO_TALK_KEYS[config.preset]
    if config.preset.startswith("custom:") and not config.modifiers:
        return config.keycode
    return None


def recording_status_text(elapsed_seconds: float, hotkey_label: str) -> str:
    elapsed = max(0, int(elapsed_seconds))
    minutes, seconds = divmod(elapsed, 60)
    return f"{minutes:02d}:{seconds:02d}"


@lru_cache(maxsize=2)
def soft_chime_wave(starting: bool) -> bytes:
    """Create a clear original cue with Wispr Flow-like attack and timing."""
    sample_rate = 44_100
    duration = 0.18 if starting else 0.22
    onset = 0.018 if starting else 0.035
    attack = 0.008
    sample_count = round(sample_rate * duration)
    timeline = np.arange(sample_count, dtype=np.float64) / sample_rate
    local_time = np.maximum(0.0, timeline - onset)
    start_frequency, end_frequency = (
        (480.0, 430.0) if starting else (325.0, 280.0)
    )
    active_duration = duration - onset
    sweep = (end_frequency - start_frequency) / active_duration
    phase = 2.0 * np.pi * (
        start_frequency * local_time + 0.5 * sweep * local_time**2
    )
    envelope = np.where(
        timeline < onset,
        0.0,
        np.where(
            local_time < attack,
            np.sin((np.pi / 2.0) * local_time / attack) ** 2,
            np.exp(-(local_time - attack) * (38.0 if starting else 30.0)),
        ),
    )
    tone = np.sin(phase) + (0.14 * np.sin((2.0 * phase) + 0.35))
    samples = tone * envelope
    peak = float(np.max(np.abs(samples)))
    samples = samples * ((0.30 if starting else 0.28) / max(peak, 1e-12))
    pcm_mono = (samples * np.iinfo(np.int16).max).astype("<i2")
    pcm = np.repeat(pcm_mono[:, None], 2, axis=1).tobytes()
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(2)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(pcm)
    return output.getvalue()


def play_soft_chime(starting: bool) -> None:
    """Play a clear cue without blocking microphone or UI handling."""

    def play() -> None:
        try:
            import winsound

            winsound.PlaySound(
                soft_chime_wave(starting),
                winsound.SND_MEMORY | winsound.SND_NODEFAULT,
            )
        except (RuntimeError, OSError):
            logging.exception("Could not play recording chime")

    threading.Thread(target=play, daemon=True).start()


def radial_levels_from_audio(
    audio: np.ndarray,
    sample_rate: int,
    count: int = RADIAL_LEVEL_COUNT,
) -> np.ndarray:
    """Map live microphone frequencies to mirrored radial animation levels."""
    values = np.asarray(audio, dtype=np.float64)
    if values.ndim > 1:
        values = values.mean(axis=1)
    values = values.reshape(-1)
    half = max(1, count // 2)
    if values.size < 8 or not np.any(values):
        return np.zeros(count, dtype=np.float32)

    values = values - np.mean(values)
    window = np.hanning(values.size)
    spectrum = (
        2.0
        * np.abs(np.fft.rfft(values * window))
        / max(float(np.sum(window)), 1.0)
    )
    frequencies = np.fft.rfftfreq(values.size, 1.0 / sample_rate)
    high_frequency = min(5_000.0, sample_rate / 2.0)
    if high_frequency <= 90.0:
        return np.zeros(count, dtype=np.float32)
    edges = np.geomspace(90.0, high_frequency, half + 1)
    bands = np.zeros(half, dtype=np.float64)
    for index in range(half):
        selected = spectrum[
            (frequencies >= edges[index])
            & (frequencies < edges[index + 1])
        ]
        if selected.size:
            bands[index] = float(np.max(selected))
    decibels = 20.0 * np.log10(np.maximum(bands, 1e-8))
    bands = np.clip((decibels + 68.0) / 48.0, 0.0, 1.0) ** 0.72
    mirrored = np.concatenate((bands, bands[::-1]))
    if mirrored.size < count:
        mirrored = np.pad(mirrored, (0, count - mirrored.size))
    return mirrored[:count].astype(np.float32)


class WindowsHotkeyListener:
    """Capture a push-to-talk key and prevent it reaching the foreground app."""

    def __init__(
        self,
        hotkey: HotkeyConfig,
        on_press,
        on_release,
        get_async_key_state=None,
    ) -> None:
        self.hotkey = hotkey
        self.on_press = on_press
        self.on_release = on_release
        self._pressed_modifiers: set[str] = set()
        self._active = False
        self._enabled = True
        self._listener = None
        self._get_async_key_state = get_async_key_state

    @staticmethod
    def _modifier(key) -> str | None:
        from pynput.keyboard import Key

        groups = {
            "control": {Key.ctrl, Key.ctrl_l, Key.ctrl_r},
            "option": {Key.alt, Key.alt_l, Key.alt_r, Key.alt_gr},
            "shift": {Key.shift, Key.shift_l, Key.shift_r},
            "command": {Key.cmd, Key.cmd_l, Key.cmd_r},
        }
        return next((name for name, keys in groups.items() if key in keys), None)

    def _matches_key(self, key) -> bool:
        from pynput.keyboard import Key

        expected = self.hotkey.key
        special_keys = {
            "Right Ctrl": Key.ctrl_r,
            "Right Alt": Key.alt_r,
            "F8": Key.f8,
        }
        if expected in special_keys:
            return key == special_keys[expected]
        if expected == "Space":
            return key == Key.space
        character = getattr(key, "char", None)
        return bool(character and expected and character.casefold() == expected.casefold())

    def _on_press(self, key) -> None:
        modifier = self._modifier(key)
        if modifier:
            self._pressed_modifiers.add(modifier)
            return
        if (
            self._enabled
            and not self._active
            and self._matches_key(key)
            and self._pressed_modifiers == set(self.hotkey.modifiers)
        ):
            self._active = True
            self.on_press()

    def _on_release(self, key) -> None:
        modifier = self._modifier(key)
        should_release = self._active and (
            self._matches_key(key)
            or (modifier is not None and modifier in self.hotkey.modifiers)
        )
        if modifier:
            self._pressed_modifiers.discard(modifier)
        if should_release:
            self._active = False
            self.on_release()

    def _win32_event_filter(self, message, data) -> bool:
        """Handle and consume the selected key in the low-level keyboard hook."""
        if not self._enabled:
            return True
        target = windows_hotkey_virtual_key(self.hotkey)
        if target is None or int(data.vkCode) != target:
            return True
        if message in (0x0100, 0x0104):  # WM_KEYDOWN / WM_SYSKEYDOWN
            if not self._active:
                self._active = True
                self.on_press()
        elif message in (0x0101, 0x0105):  # WM_KEYUP / WM_SYSKEYUP
            if self._active:
                self._active = False
                self.on_release()
        return False

    def start(self) -> bool:
        from pynput.keyboard import Listener

        self._listener = Listener(
            on_press=self._on_press,
            on_release=self._on_release,
            win32_event_filter=self._win32_event_filter,
        )
        self._listener.start()
        return True

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if not enabled and self._active:
            self._active = False
            self.on_release()

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()

    def reset(self) -> None:
        self._active = False
        self._pressed_modifiers.clear()

    def is_physically_pressed(self) -> bool:
        """Check the real keyboard state so a swallowed key-up cannot latch recording."""
        get_state = self._get_async_key_state
        if get_state is None:
            user32 = ctypes.windll.user32
            user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
            user32.GetAsyncKeyState.restype = ctypes.c_short
            get_state = user32.GetAsyncKeyState

        def down(virtual_key: int) -> bool:
            return bool(get_state(virtual_key) & 0x8000)

        modifier_keys = {
            "control": (0x11,),
            "option": (0x12,),
            "shift": (0x10,),
            "command": (0x5B, 0x5C),
        }
        for modifier in self.hotkey.modifiers:
            if not any(down(key) for key in modifier_keys[modifier]):
                return False
        target_key = windows_hotkey_virtual_key(self.hotkey)
        if target_key is not None:
            return down(target_key)
        key = self.hotkey.key
        target_key = 0x20 if key == "Space" else ord(key.upper()) if key else None
        return target_key is not None and down(target_key)


def _island_font(name: str, size: int):
    from PIL import ImageFont

    fonts = {
        "status": ("seguisb.ttf", "segoeui.ttf"),
    }
    windows_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for filename in fonts[name]:
        try:
            return ImageFont.truetype(str(windows_fonts / filename), size)
        except OSError:
            continue
    return ImageFont.load_default()


@lru_cache(maxsize=1)
def _recording_ring():
    from PIL import Image

    with Image.open(
        resource_path("assets/sound-levels-circle-transparent.png")
    ) as source:
        return source.convert("RGBA")


@lru_cache(maxsize=1)
def _recording_ring_geometry():
    """Precompute polar coordinates for animating the supplied ring image."""
    from PIL import Image

    scale = 4
    width, height = INDICATOR_WIDTH * scale, INDICATOR_HEIGHT * scale
    base = np.asarray(
        _recording_ring().resize(
            (width, height),
            resample=Image.Resampling.LANCZOS,
        ),
        dtype=np.uint8,
    ).copy()
    y_coordinates, x_coordinates = np.indices((height, width))
    center_x = (width - 1) / 2.0
    center_y = (height - 1) / 2.0
    radius = np.hypot(
        x_coordinates - center_x,
        y_coordinates - center_y,
    )
    angle = np.mod(
        np.arctan2(y_coordinates - center_y, x_coordinates - center_x)
        + (2.0 * np.pi),
        2.0 * np.pi,
    )
    sectors = np.floor(
        angle * RADIAL_LEVEL_COUNT / (2.0 * np.pi)
    ).astype(np.int16)
    visible = base[:, :, 3] > 4
    inner_radius = float(np.min(radius[visible]))
    maximum_radius = np.full(
        RADIAL_LEVEL_COUNT,
        inner_radius,
        dtype=np.float32,
    )
    for index in range(RADIAL_LEVEL_COUNT):
        sector_pixels = visible & (sectors == index)
        if np.any(sector_pixels):
            maximum_radius[index] = float(np.max(radius[sector_pixels]))
    return base, radius, sectors, inner_radius, maximum_radius


def render_recording_island(
    status_text: str,
    radial_levels: np.ndarray | list[float] | tuple[float, ...] | None = None,
):
    """Render a bold, translucent live radial microphone visualizer."""
    from PIL import Image, ImageDraw, ImageFilter

    scale = 4
    width, height = INDICATOR_WIDTH * scale, INDICATOR_HEIGHT * scale
    base, radius, sectors, inner_radius, maximum_radius = (
        _recording_ring_geometry()
    )
    if radial_levels is None:
        levels = np.zeros(RADIAL_LEVEL_COUNT, dtype=np.float32)
    else:
        levels = np.asarray(radial_levels, dtype=np.float32).reshape(-1)
        if levels.size != RADIAL_LEVEL_COUNT:
            source = np.linspace(0.0, 1.0, max(levels.size, 1))
            target = np.linspace(0.0, 1.0, RADIAL_LEVEL_COUNT)
            levels = np.interp(
                target,
                source,
                levels if levels.size else np.zeros(1),
            ).astype(np.float32)
        levels = np.clip(levels, 0.0, 1.0)
    reach = inner_radius + (
        (maximum_radius - inner_radius) * (0.24 + (0.76 * levels))
    )
    edge_softness = 2.5 * scale
    opacity = np.clip(
        (reach[sectors] - radius + edge_softness) / edge_softness,
        0.0,
        1.0,
    )
    dynamic_alpha = (
        base[:, :, 3].astype(np.float32) * opacity
    ).astype(np.uint8)
    wave_mask = Image.fromarray(dynamic_alpha).filter(
        ImageFilter.MaxFilter(5)
    )
    wave_alpha = wave_mask.point(
        [round(value * 0.62) for value in range(256)]
    )
    shadow_alpha = wave_mask.filter(
        ImageFilter.GaussianBlur(radius=1.35 * scale)
    ).point([round(value * 0.30) for value in range(256)])
    shadow_offset = Image.new("L", (width, height))
    shadow_offset.paste(shadow_alpha, (0, scale))

    shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    shadow.putalpha(shadow_offset)
    waves = Image.new("RGBA", (width, height), (16, 16, 18, 0))
    waves.putalpha(wave_alpha)
    image = Image.alpha_composite(shadow, waves)
    draw = ImageDraw.Draw(image)
    status_font = _island_font("status", 11 * scale)
    draw.text(
        (width // 2, height // 2),
        status_text,
        font=status_font,
        fill=(15, 15, 16, 255),
        anchor="mm",
        stroke_width=3,
        stroke_fill=(255, 255, 255, 235),
    )
    return image.resize(
        (INDICATOR_WIDTH, INDICATOR_HEIGHT),
        resample=Image.Resampling.LANCZOS,
    )


class RecordingIndicator:
    """A click-through circular timer on a per-pixel-transparent window."""

    def __init__(self, root, hotkey_label: str) -> None:
        import tkinter as tk

        self.width, self.height = INDICATOR_WIDTH, INDICATOR_HEIGHT
        self.root = root
        self.hotkey_label = hotkey_label
        self.started_at = 0.0
        self.levels = np.zeros(RADIAL_LEVEL_COUNT, dtype=np.float32)
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg="#000000")
        self.window.geometry(f"{self.width}x{self.height}")
        self.window.update_idletasks()
        self.hwnd = self._top_level_window_handle()
        self._apply_native_style()

    def _top_level_window_handle(self):
        """Return Tk's native wrapper HWND, not its clipped client child."""
        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user32.GetAncestor.restype = wintypes.HWND
        client = wintypes.HWND(self.window.winfo_id())
        wrapper = user32.GetAncestor(client, 2)  # GA_ROOT
        return wintypes.HWND(wrapper or client.value)

    def _apply_native_style(self) -> None:
        user32 = ctypes.windll.user32
        get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        get_style.argtypes = (wintypes.HWND, ctypes.c_int)
        get_style.restype = ctypes.c_ssize_t
        set_style.argtypes = (
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_ssize_t,
        )
        set_style.restype = ctypes.c_ssize_t
        required_style = (
            0x00080000  # WS_EX_LAYERED
            | 0x00000080  # WS_EX_TOOLWINDOW
            | 0x08000000  # WS_EX_NOACTIVATE
            | 0x00000020  # WS_EX_TRANSPARENT (click-through)
        )
        extended_style = get_style(self.hwnd, -20)
        set_style(self.hwnd, -20, extended_style | required_style)
        applied_style = get_style(self.hwnd, -20)
        if applied_style & required_style != required_style:
            raise OSError(
                "Windows did not apply the recording indicator transparency style"
            )

    def _position_at_bottom_center(self) -> tuple[int, int]:
        x = (self.window.winfo_screenwidth() - self.width) // 2
        y = self.window.winfo_screenheight() - self.height - INDICATOR_BOTTOM_MARGIN
        try:
            user32 = ctypes.windll.user32

            class Point(ctypes.Structure):
                _fields_ = (("x", wintypes.LONG), ("y", wintypes.LONG))

            class MonitorInfo(ctypes.Structure):
                _fields_ = (
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD),
                )

            point = Point()
            user32.GetCursorPos.argtypes = (ctypes.POINTER(Point),)
            user32.GetCursorPos.restype = wintypes.BOOL
            user32.GetCursorPos(ctypes.byref(point))
            user32.MonitorFromPoint.argtypes = (Point, wintypes.DWORD)
            user32.MonitorFromPoint.restype = wintypes.HMONITOR
            monitor = user32.MonitorFromPoint(point, 2)
            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(info)
            user32.GetMonitorInfoW.argtypes = (
                wintypes.HMONITOR,
                ctypes.POINTER(MonitorInfo),
            )
            user32.GetMonitorInfoW.restype = wintypes.BOOL
            if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                work = info.rcWork
                x = work.left + ((work.right - work.left) - self.width) // 2
                y = work.bottom - self.height - INDICATOR_BOTTOM_MARGIN
        except (AttributeError, OSError):
            pass
        return x, y

    def _draw_layered_window(self) -> None:
        class BitmapInfoHeader(ctypes.Structure):
            _fields_ = (
                ("biSize", wintypes.DWORD),
                ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            )

        class BitmapInfo(ctypes.Structure):
            _fields_ = (
                ("bmiHeader", BitmapInfoHeader),
                ("bmiColors", wintypes.DWORD * 3),
            )

        class BlendFunction(ctypes.Structure):
            _fields_ = (
                ("BlendOp", wintypes.BYTE),
                ("BlendFlags", wintypes.BYTE),
                ("SourceConstantAlpha", wintypes.BYTE),
                ("AlphaFormat", wintypes.BYTE),
            )

        x, y = self._position_at_bottom_center()
        elapsed = time.perf_counter() - self.started_at
        status = recording_status_text(elapsed, self.hotkey_label)
        rgba = np.asarray(
            render_recording_island(status, self.levels),
            dtype=np.uint8,
        )
        premultiplied = rgba.copy()
        alpha = premultiplied[:, :, 3:4].astype(np.uint16)
        premultiplied[:, :, :3] = (
            premultiplied[:, :, :3].astype(np.uint16) * alpha // 255
        ).astype(np.uint8)
        bgra = premultiplied[:, :, [2, 1, 0, 3]].tobytes()

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        user32.GetDC.argtypes = (wintypes.HWND,)
        user32.GetDC.restype = wintypes.HDC
        user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
        user32.ReleaseDC.restype = ctypes.c_int
        gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.CreateDIBSection.argtypes = (
            wintypes.HDC,
            ctypes.POINTER(BitmapInfo),
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p),
            wintypes.HANDLE,
            wintypes.DWORD,
        )
        gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HANDLE)
        gdi32.SelectObject.restype = wintypes.HANDLE
        gdi32.DeleteObject.argtypes = (wintypes.HANDLE,)
        gdi32.DeleteObject.restype = wintypes.BOOL
        gdi32.DeleteDC.argtypes = (wintypes.HDC,)
        gdi32.DeleteDC.restype = wintypes.BOOL
        user32.UpdateLayeredWindow.argtypes = (
            wintypes.HWND,
            wintypes.HDC,
            ctypes.POINTER(wintypes.POINT),
            ctypes.POINTER(wintypes.SIZE),
            wintypes.HDC,
            ctypes.POINTER(wintypes.POINT),
            wintypes.DWORD,
            ctypes.POINTER(BlendFunction),
            wintypes.DWORD,
        )
        user32.UpdateLayeredWindow.restype = wintypes.BOOL
        screen_dc = user32.GetDC(None)
        memory_dc = gdi32.CreateCompatibleDC(screen_dc)
        bits = ctypes.c_void_p()
        bitmap_info = BitmapInfo()
        bitmap_info.bmiHeader.biSize = ctypes.sizeof(BitmapInfoHeader)
        bitmap_info.bmiHeader.biWidth = self.width
        bitmap_info.bmiHeader.biHeight = -self.height
        bitmap_info.bmiHeader.biPlanes = 1
        bitmap_info.bmiHeader.biBitCount = 32
        bitmap = gdi32.CreateDIBSection(
            screen_dc,
            ctypes.byref(bitmap_info),
            0,
            ctypes.byref(bits),
            None,
            0,
        )
        previous = gdi32.SelectObject(memory_dc, bitmap)
        try:
            ctypes.memmove(bits, bgra, len(bgra))
            destination = wintypes.POINT(x, y)
            size = wintypes.SIZE(self.width, self.height)
            source = wintypes.POINT(0, 0)
            blend = BlendFunction(0, 0, 255, 1)  # AC_SRC_OVER / AC_SRC_ALPHA
            updated = user32.UpdateLayeredWindow(
                self.hwnd,
                screen_dc,
                ctypes.byref(destination),
                ctypes.byref(size),
                memory_dc,
                ctypes.byref(source),
                0,
                ctypes.byref(blend),
                2,
            )
            if not updated:
                kernel32 = ctypes.windll.kernel32
                kernel32.GetLastError.argtypes = ()
                kernel32.GetLastError.restype = wintypes.DWORD
                error_code = kernel32.GetLastError()
                raise ctypes.WinError(error_code)
            user32.SetWindowPos.argtypes = (
                wintypes.HWND,
                wintypes.HWND,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.UINT,
            )
            user32.SetWindowPos.restype = wintypes.BOOL
            topmost = wintypes.HWND(-1)
            positioned = user32.SetWindowPos(
                self.hwnd,
                topmost,
                x,
                y,
                self.width,
                self.height,
                0x0010 | 0x0040,  # SWP_NOACTIVATE | SWP_SHOWWINDOW
            )
            if not positioned:
                kernel32 = ctypes.windll.kernel32
                error_code = kernel32.GetLastError()
                raise ctypes.WinError(error_code)
        finally:
            gdi32.SelectObject(memory_dc, previous)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(memory_dc)
            user32.ReleaseDC(None, screen_dc)

    def show(self, hotkey_label: str) -> None:
        self.hotkey_label = hotkey_label
        self.started_at = time.perf_counter()
        self.levels = np.zeros(RADIAL_LEVEL_COUNT, dtype=np.float32)
        self.window.deiconify()
        self.window.update_idletasks()
        # Tk restores its own extended styles whenever a withdrawn Toplevel is
        # mapped. Reapply the layered/click-through styles after every show.
        self.hwnd = self._top_level_window_handle()
        self._apply_native_style()
        self.update(0.0)

    def update(self, levels) -> None:
        values = np.asarray(levels, dtype=np.float32).reshape(-1)
        if values.size == 1:
            values = np.full(
                RADIAL_LEVEL_COUNT,
                float(values[0]),
                dtype=np.float32,
            )
        self.levels = np.clip(values, 0.0, 1.0)
        self._draw_layered_window()

    def hide(self) -> None:
        self.window.withdraw()


class RedWhisperWindowsApp:
    def __init__(
        self,
        root,
        pipeline: TranscriptPipeline,
        settings: AppSettings,
        settings_store: SettingsStore,
        input_device: InputDevice,
        engine_label: str,
        local_transcriber: FasterWhisperTranscriber | None,
        log_path: Path,
    ) -> None:
        import pystray
        from PIL import Image

        self.root = root
        self.pipeline = pipeline
        self.settings = settings
        self.settings_store = settings_store
        self.input_device = input_device
        self.engine_label = engine_label
        self.local_transcriber = local_transcriber
        self.log_path = log_path
        self.hotkey = HotkeyConfig(settings.hotkey_preset)
        self.hotkey_label = windows_hotkey_label(self.hotkey)
        self.active_hotkeys = ActiveHotkeys()
        self.listener = WindowsHotkeyListener(
            self.hotkey,
            lambda: self._enqueue(self._hotkey_pressed),
            lambda: self._enqueue(self._hotkey_released),
        )
        self.listener.start()

        self.ready = local_transcriber is None
        self.is_recording = False
        self.is_transcribing = False
        self.audio_chunks: list[np.ndarray] = []
        self.stream: sd.InputStream | None = None
        self.audio_stream_recovery_attempts = 0
        self.latest_level = 0.0
        self.radial_levels = np.zeros(
            RADIAL_LEVEL_COUNT,
            dtype=np.float32,
        )
        self.recording_requires_hold = False
        self.recording_started_at = 0.0
        device_info = sd.query_devices(input_device.id)
        self.capture_rate = int(float(device_info["default_samplerate"]))
        self.indicator = RecordingIndicator(root, self.hotkey_label)
        self.indicator_enabled = True

        image = Image.open(resource_path("assets/RedWhisper-icon.png")).convert("RGBA")
        self.icon = pystray.Icon(
            "RedWhisper",
            image,
            "RedWhisper",
            menu=pystray.Menu(
                pystray.MenuItem(
                    lambda _item: (
                        "Stop Recording"
                        if self.is_recording
                        else f"Start Recording ({self.hotkey_label})"
                    ),
                    lambda _icon, _item: self._enqueue(self.toggle),
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(f"Engine: {engine_label}", None, enabled=False),
                pystray.MenuItem(
                    lambda _item: (
                        "Microphone: "
                        + (
                            f"{self.settings.input_device_name} → {self.input_device.name}"
                            if self.settings.input_device_name
                            else f"Auto-detect → {self.input_device.name}"
                        )
                    ),
                    None,
                    enabled=False,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    "Settings...",
                    lambda _icon, _item: self._enqueue(self.open_settings),
                ),
                pystray.MenuItem(
                    "Open diagnostic log",
                    lambda _icon, _item: os.startfile(self.log_path),
                ),
                pystray.MenuItem(
                    "Quit RedWhisper",
                    lambda _icon, _item: self._enqueue(self.shutdown),
                ),
            ),
        )
        threading.Thread(target=self.icon.run, daemon=True).start()
        if self.local_transcriber:
            threading.Thread(target=self._warmup, daemon=True).start()
        else:
            self._notify("RedWhisper is ready", f"Hold {self.hotkey_label} to dictate.")

    def _enqueue(self, callback) -> None:
        try:
            self.root.after(0, callback)
        except RuntimeError:
            pass

    def _notify(self, title: str, message: str) -> None:
        try:
            self.icon.notify(message, title)
        except Exception:
            logging.exception("Could not display notification")

    def _warmup(self) -> None:
        try:
            logging.info("Loading local model %s", self.engine_label)
            self.local_transcriber.warmup()
            self.ready = True
            logging.info(
                "Local model ready on %s (%s)",
                self.local_transcriber.device,
                self.local_transcriber.compute_type,
            )
            self._notify(
                "RedWhisper is ready",
                f"Hold {self.hotkey_label} to dictate.",
            )
        except Exception as error:
            logging.exception("Local model warmup failed")
            self._notify("Local model failed to load", str(error)[:180])

    def _hotkey_pressed(self) -> None:
        if self.active_hotkeys.press(self.hotkey.preset):
            self.start_recording(require_hold=True)

    def _hotkey_released(self) -> None:
        if self.active_hotkeys.release(self.hotkey.preset):
            self.stop_recording()

    def toggle(self) -> None:
        if self.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self, require_hold: bool = False) -> None:
        if self.is_recording or self.is_transcribing:
            return
        if not self.ready:
            self._notify("Model is still loading", "Please try again shortly.")
            return
        try:
            self._refresh_microphone()
            self.audio_chunks = []
            self.radial_levels = np.zeros(
                RADIAL_LEVEL_COUNT,
                dtype=np.float32,
            )
            self.is_recording = True
            self.audio_stream_recovery_attempts = 0
            self._open_microphone_stream()
            self.recording_requires_hold = require_hold
            self.recording_started_at = time.perf_counter()
        except Exception as error:
            logging.exception("Could not start microphone")
            self.is_recording = False
            self._close_microphone_stream()
            self._notify("Microphone error", str(error)[:180])
            return
        logging.info(
            "Recording started (%s, shortcut=%s, microphone=%s -> %s)",
            "push-to-talk" if require_hold else "tray menu",
            self.hotkey_label,
            self.settings.input_device_name or "Auto-detect",
            self.input_device.name,
        )
        if self.indicator_enabled:
            try:
                self.indicator.show(self.hotkey_label)
            except Exception:
                self.indicator_enabled = False
                logging.exception("Recording indicator could not be displayed")
                self._notify(
                    "Recording started",
                    "The microphone is recording, but the status island could not be displayed.",
                )
        play_soft_chime(starting=True)
        self.root.after(900, self._verify_audio_stream)
        self._refresh_indicator()

    def _open_microphone_stream(self) -> None:
        self.stream = sd.InputStream(
            device=self.input_device.id,
            samplerate=self.capture_rate,
            channels=1,
            dtype="float32",
            blocksize=1024,
            callback=self._audio_callback,
        )
        self.stream.start()

    def _close_microphone_stream(self) -> None:
        stream, self.stream = self.stream, None
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception:
            logging.exception("Could not close microphone stream")

    def _verify_audio_stream(self) -> None:
        """Recover a stream that opened but never delivered an audio callback."""
        if not self.is_recording or self.audio_chunks:
            return
        if self.audio_stream_recovery_attempts < 1:
            self.audio_stream_recovery_attempts += 1
            logging.warning(
                "Microphone stream produced no frames; refreshing audio devices and retrying"
            )
            try:
                self._close_microphone_stream()
                self._refresh_microphone()
                self._open_microphone_stream()
            except Exception as error:
                logging.exception("Could not recover unresponsive microphone stream")
                self._stop_unresponsive_microphone(str(error))
                return
            self.root.after(900, self._verify_audio_stream)
            return
        self._stop_unresponsive_microphone(
            f'Windows is not sending audio from "{self.input_device.name}".'
        )

    def _stop_unresponsive_microphone(self, detail: str) -> None:
        logging.error("Microphone stream remained unresponsive: %s", detail)
        self.is_recording = False
        self.recording_requires_hold = False
        self._close_microphone_stream()
        if self.indicator_enabled:
            self.indicator.hide()
        play_soft_chime(starting=False)
        self._notify(
            "Microphone is not responding",
            f"{detail[:120]} RedWhisper refreshed it automatically, but Windows still returned no audio.",
        )

    def _refresh_microphone(self) -> None:
        devices = refresh_input_devices(sd)
        try:
            self.input_device = resolve_input_device(devices, self.settings)
        except NoPhysicalMicrophoneError as error:
            # A webcam that fails USB enumeration (Code 43) is invisible to
            # PortAudio; name the real cause instead of a generic message.
            failed = failed_usb_device_names()
            if not failed:
                raise
            logging.warning("USB devices Windows could not start: %s", failed)
            if request_usb_recovery():
                logging.info("Started USB recovery task %s", USB_RECOVERY_TASK)
                raise NoPhysicalMicrophoneError(USB_MICROPHONE_RECOVERING_MESSAGE) from error
            raise NoPhysicalMicrophoneError(USB_MICROPHONE_FAILED_MESSAGE) from error
        device_info = sd.query_devices(self.input_device.id)
        self.capture_rate = int(float(device_info["default_samplerate"]))

    def _audio_callback(self, indata, _frames, _time_info, status) -> None:
        if status:
            logging.warning("Audio status: %s", status)
        if self.is_recording:
            self.audio_chunks.append(indata.copy())
            self.latest_level = root_mean_square(indata) * self.settings.microphone_gain
            target_levels = radial_levels_from_audio(
                indata * self.settings.microphone_gain,
                self.capture_rate,
            )
            self.radial_levels = np.maximum(
                target_levels,
                self.radial_levels * 0.72,
            )

    def _refresh_indicator(self) -> None:
        if self.is_recording:
            if (
                self.recording_requires_hold
                and time.perf_counter() - self.recording_started_at > 0.35
                and not self.listener.is_physically_pressed()
            ):
                logging.warning(
                    "Hotkey release event was missed; stopping recording via keyboard-state watchdog"
                )
                self.listener.reset()
                self.active_hotkeys.release(self.hotkey.preset)
                self.stop_recording()
                return
            if self.indicator_enabled:
                try:
                    self.indicator.update(self.radial_levels)
                except Exception:
                    self.indicator_enabled = False
                    logging.exception("Recording indicator update failed")
            self.root.after(60, self._refresh_indicator)

    def stop_recording(self) -> None:
        if not self.is_recording:
            return
        self.is_recording = False
        self.recording_requires_hold = False
        logging.info("Recording stopped")
        if self.indicator_enabled:
            self.indicator.hide()
        self._close_microphone_stream()
        play_soft_chime(starting=False)
        self.is_transcribing = True
        threading.Thread(target=self._transcribe, daemon=True).start()

    def _transcribe(self) -> None:
        temp_path: Path | None = None
        try:
            if not self.audio_chunks:
                logging.warning("Recording contained no microphone frames")
                self._notify(
                    "No microphone frames",
                    f'Windows returned no audio from "{self.input_device.name}". '
                    "RedWhisper will refresh the device on the next recording.",
                )
                return
            audio = np.concatenate(self.audio_chunks, axis=0).flatten()
            if len(audio) < self.capture_rate * MIN_DURATION_SECONDS:
                logging.info("Recording was too short; skipped")
                return
            audio = apply_microphone_gain(audio, self.settings.microphone_gain)
            audio = resample_audio(audio, self.capture_rate, SAMPLE_RATE)
            energy = root_mean_square(audio)
            if energy < MIN_AUDIO_ENERGY:
                logging.info("Recording was too quiet (energy=%0.5f); skipped", energy)
                self._notify(
                    "No microphone signal",
                    f'No usable audio came from "{self.input_device.name}". '
                    "Choose the physical headset or microphone in Settings.",
                )
                return
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temporary:
                temp_path = Path(temporary.name)
            sf.write(temp_path, audio, SAMPLE_RATE)
            started = time.perf_counter()
            text = self.pipeline.process(temp_path)
            if text:
                paste_at_cursor(text)
                logging.info(
                    "Transcribed %.1fs of audio in %.1fs",
                    len(audio) / SAMPLE_RATE,
                    time.perf_counter() - started,
                )
            else:
                logging.info("No speech detected")
        except Exception as error:
            logging.exception("Transcription failed")
            self._notify("Transcription error", str(error)[:180])
        finally:
            if temp_path:
                temp_path.unlink(missing_ok=True)
            self.is_transcribing = False
            self.audio_chunks = []

    def open_settings(self) -> None:
        if self.is_recording or self.is_transcribing:
            self._notify("Settings unavailable", "Finish the current dictation first.")
            return
        self.listener.set_enabled(False)
        try:
            devices = refresh_input_devices(sd)
            updated = configure_windows_settings(
                self.settings,
                devices,
                first_run=False,
                parent=self.root,
            )
            if updated:
                self.settings_store.save(updated)
                self.restart()
            else:
                self.listener.set_enabled(True)
        except Exception:
            logging.exception("Settings dialog failed")
            self.listener.set_enabled(True)
            self._notify("Settings error", "Could not open settings. See the log.")

    def restart(self) -> None:
        command = [sys.executable]
        if not getattr(sys, "frozen", False):
            command.append(str(Path(__file__).resolve()))
        subprocess.Popen(command, close_fds=True)
        self.shutdown()

    def shutdown(self) -> None:
        try:
            self.listener.stop()
            if self.stream:
                self.stream.stop()
                self.stream.close()
            self.indicator.hide()
            self.icon.stop()
        finally:
            self.root.quit()
        self.root.destroy()


def _default_input_id() -> int | None:
    default = sd.default.device
    try:
        return int(default[0])
    except (TypeError, IndexError):
        try:
            return int(default)
        except (TypeError, ValueError):
            return None


USB_RECOVERY_TASK = r"\RedWhisper\Repair USB devices"
USB_MICROPHONE_FAILED_MESSAGE = (
    "Your USB webcam or microphone is plugged in, but Windows could not start "
    "it. Unplug it and plug it into a different USB port."
)
USB_MICROPHONE_RECOVERING_MESSAGE = (
    "Your USB webcam or microphone failed to start. RedWhisper is resetting "
    "it now; try again in about 20 seconds."
)


class NoPhysicalMicrophoneError(ValueError):
    """Only virtual microphones, or none, are available to Windows."""


def resolve_input_device(
    devices: list[InputDevice], settings: AppSettings
) -> InputDevice:
    """Resolve a saved endpoint or rank a usable live Windows microphone."""
    choices = windows_input_devices(devices)
    if not choices:
        raise ValueError("No audio input devices are available")

    if settings.input_device_name:
        saved_by_name = next(
            (
                device
                for device in choices
                if device.name.casefold()
                == settings.input_device_name.casefold()
            ),
            None,
        )
        if saved_by_name:
            return saved_by_name
    # PortAudio indices are reassigned when devices connect/disconnect. Never
    # use a missing named endpoint's old index to identify a different device.
    elif settings.input_device is not None:
        try:
            saved = device_by_id(choices, settings.input_device)
            if not is_virtual_input_device(saved):
                return saved
        except ValueError:
            pass

    physical_choices = [
        device for device in choices if not is_virtual_input_device(device)
    ]
    # In automatic mode prefer a connected headset, then a webcam microphone.
    # Re-evaluate the live list for every recording; do not persist a fallback
    # over the preference, so reconnecting the headset restores it automatically.
    for terms in (("headset", "hands-free", "hands free", "bluetooth"), ("webcam",)):
        preferred = next(
            (device for device in physical_choices
             if any(term in device.name.casefold() for term in terms)),
            None,
        )
        if preferred:
            return preferred

    non_virtual_host_default = next(
        (
            device
            for device in choices
            if device.is_host_default and not is_virtual_input_device(device)
        ),
        None,
    )
    if non_virtual_host_default:
        return non_virtual_host_default

    physical = preferred_input_device(choices, None)
    if not is_virtual_input_device(physical):
        return physical

    raise NoPhysicalMicrophoneError(
        "No usable physical microphone is connected. Connect or enable a "
        "microphone in Windows Sound settings."
    )


def failed_usb_device_names() -> list[str]:
    """Return connected USB devices that Windows failed to start (e.g. Code 43)."""
    # Codes 10 and 43 match windows_usb_recovery.ps1: failures a port reset fixes.
    command = (
        "Get-CimInstance Win32_PnPEntity -Filter "
        "'ConfigManagerErrorCode = 10 OR ConfigManagerErrorCode = 43' | "
        "Where-Object { $_.PNPDeviceID -like 'USB\\*' } | "
        "ForEach-Object { $_.Name }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def request_usb_recovery() -> bool:
    """Start the SYSTEM repair task installed by windows_usb_recovery.ps1."""
    try:
        result = subprocess.run(
            ["schtasks", "/Run", "/TN", USB_RECOVERY_TASK],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def create_pipeline(
    settings: AppSettings,
    model_name: str,
) -> tuple[TranscriptPipeline, FasterWhisperTranscriber | None, str]:
    language = None if settings.language == "auto" else settings.language
    local_transcriber = None
    if settings.engine == "local":
        local_transcriber = FasterWhisperTranscriber(
            FasterWhisperOptions(
                model=model_name,
                language=language,
                maximum_accuracy=settings.maximum_accuracy,
            )
        )
        transcriber = local_transcriber
        engine_label = f"Local {model_name}"
    elif settings.engine == "openai":
        transcriber = OpenAITranscriber(
            OpenAITranscriptionOptions(
                api_key=openai_key_from_environment(),
                model=settings.openai_model,
                language=language,
                prompt="Voice dictation. Use accurate punctuation and paragraph breaks.",
            )
        )
        engine_label = settings.openai_model
    elif settings.engine == "openrouter":
        transcriber = OpenRouterTranscriber(
            OpenRouterTranscriptionOptions(
                api_key=openrouter_key_from_environment(),
                model=settings.openrouter_transcription_model,
                language=language,
            )
        )
        engine_label = f"OpenRouter · {settings.openrouter_transcription_model}"
    else:
        transcriber = ElevenLabsTranscriber(
            ElevenLabsTranscriptionOptions(
                api_key=elevenlabs_key_from_environment(),
                model=settings.elevenlabs_model,
                language=language,
            )
        )
        engine_label = f"ElevenLabs · {settings.elevenlabs_model}"
    replacements = (
        TermReplacer.from_json(Path(settings.replacements))
        if settings.replacements
        else None
    )
    if settings.post_process_local:
        raise ConfigurationError("Local MLX restructuring is not available on Windows")
    if settings.post_process_openai:
        post_processor = OpenAITextPostProcessor(
            OpenAIRewriteOptions(
                api_key=openai_key_from_environment(),
                model=settings.openai_rewrite_model,
            )
        )
    elif settings.post_process_openrouter:
        post_processor = OpenRouterTextPostProcessor(
            OpenRouterRewriteOptions(
                api_key=openrouter_key_from_environment(),
                model=settings.openrouter_model,
            )
        )
    else:
        post_processor = None
    return (
        TranscriptPipeline(
            transcriber,
            replacements=replacements,
            post_processor=post_processor,
        ),
        local_transcriber,
        engine_label,
    )


def main() -> int:
    if os.name != "nt":
        print("windows_app.py can only run on Windows.", file=sys.stderr)
        return 1
    enable_native_dpi_awareness()
    parser = argparse.ArgumentParser(description="RedWhisper for Windows")
    parser.add_argument("--settings", action="store_true", help="Open settings before starting")
    parser.add_argument("--list-devices", action="store_true", help="List microphone devices")
    parser.add_argument("--model", default=DEFAULT_WINDOWS_MODEL, help="faster-whisper model name")
    args = parser.parse_args()

    log_path = configure_logging()
    try:
        devices = refresh_input_devices(sd)
        if args.list_devices:
            for device in devices:
                print(f"{device.id}: {device.name} ({device.input_channels} channels)")
            return 0
        if not devices:
            raise ConfigurationError(
                "No microphone was found. Connect or enable a microphone and restart RedWhisper."
            )

        store = SettingsStore()
        first_run = not store.path.exists()
        loaded_settings = store.load()
        settings = windows_default_settings(loaded_settings)
        if settings != loaded_settings:
            store.save(settings)
        try:
            input_device = resolve_input_device(devices, settings)
        except ValueError:
            # Keep the tray app alive so a newly connected microphone can be
            # discovered by _refresh_microphone on the next recording attempt.
            input_device = preferred_input_device(devices, _default_input_id())
        if first_run or args.settings:
            configured = configure_windows_settings(
                settings,
                devices,
                first_run=first_run,
            )
            if configured is None:
                return 0
            store.save(configured)
            settings = configured
            input_device = resolve_input_device(devices, settings)

        pipeline, local_transcriber, engine_label = create_pipeline(
            settings, args.model
        )

        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        app = RedWhisperWindowsApp(
            root,
            pipeline,
            settings,
            store,
            input_device,
            engine_label,
            local_transcriber,
            log_path,
        )
        root.mainloop()
        return 0
    except Exception as error:
        logging.error("Fatal startup error:\n%s", traceback.format_exc())
        try:
            import tkinter.messagebox

            tkinter.messagebox.showerror(
                "RedWhisper could not start",
                f"{error}\n\nDiagnostic log:\n{log_path}",
            )
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
