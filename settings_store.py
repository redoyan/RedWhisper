"""Persistent non-secret application settings."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import tempfile

from hotkey_config import HotkeyConfig
from mlx_whisper_core import (
    DEFAULT_OPENROUTER_MODEL,
    DEFAULT_OPENAI_REWRITE_MODEL,
    ELEVENLABS_TRANSCRIPTION_MODELS,
    OPENAI_REWRITE_MODELS,
    OPENAI_TRANSCRIPTION_MODELS,
    validate_openrouter_model,
)


DEFAULT_SETTINGS_PATH = (
    Path.home() / "Library" / "Application Support" / "RedWhisper" / "settings.json"
)
LEGACY_SETTINGS_PATH = (
    Path.home() / "Library" / "Application Support" / "MLX Whisper" / "settings.json"
)


@dataclass(frozen=True)
class AppSettings:
    engine: str = "local"
    language: str = "en"
    maximum_accuracy: bool = False
    replacements: str | None = None
    post_process_local: bool = False
    post_process_openai: bool = False
    post_process_openrouter: bool = False
    post_process_chatgpt: bool = False
    chatgpt_rewrite_model: str = ""
    hotkey_preset: str = "fn"
    secondary_hotkey_preset: str | None = None
    microphone_gain: float = 2.0
    input_device: int | None = None
    input_device_name: str | None = None
    openai_model: str = "gpt-4o-transcribe"
    elevenlabs_model: str = "scribe_v2"
    openai_rewrite_model: str = DEFAULT_OPENAI_REWRITE_MODEL
    openrouter_model: str = DEFAULT_OPENROUTER_MODEL
    openrouter_models: tuple[str, ...] = (DEFAULT_OPENROUTER_MODEL,)

    @classmethod
    def from_dict(cls, payload: dict) -> "AppSettings":
        values = {
            key: payload[key]
            for key in cls.__dataclass_fields__
            if key in payload
        }
        if isinstance(values.get("openrouter_models"), list):
            values["openrouter_models"] = tuple(values["openrouter_models"])
        settings = cls(**values)
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.engine not in {"local", "openai", "elevenlabs"}:
            raise ValueError("Invalid saved transcription engine")
        if not isinstance(self.language, str) or not self.language or len(self.language) > 20:
            raise ValueError("Invalid saved language")
        if any(
            not isinstance(value, bool)
            for value in (
                self.maximum_accuracy,
                self.post_process_local,
                self.post_process_openai,
                self.post_process_openrouter,
                self.post_process_chatgpt,
            )
        ):
            raise ValueError("Invalid saved boolean setting")
        if sum(
            (
                self.post_process_local,
                self.post_process_openai,
                self.post_process_openrouter,
                self.post_process_chatgpt,
            )
        ) > 1:
            raise ValueError("Select only one restructuring engine")
        if self.replacements is not None and not isinstance(self.replacements, str):
            raise ValueError("Invalid saved replacement path")
        HotkeyConfig(self.hotkey_preset)
        if self.secondary_hotkey_preset is not None:
            HotkeyConfig(self.secondary_hotkey_preset)
            if self.secondary_hotkey_preset == self.hotkey_preset:
                raise ValueError("Primary and secondary hotkeys must be different")
        if self.microphone_gain not in {1.0, 2.0, 4.0, 8.0}:
            raise ValueError("Invalid saved microphone sensitivity")
        if self.input_device is not None and not isinstance(self.input_device, int):
            raise ValueError("Invalid saved microphone device")
        if self.input_device_name is not None and (
            not isinstance(self.input_device_name, str) or not self.input_device_name
        ):
            raise ValueError("Invalid saved microphone name")
        if self.openai_model not in OPENAI_TRANSCRIPTION_MODELS:
            raise ValueError("Invalid saved OpenAI model")
        if self.elevenlabs_model not in ELEVENLABS_TRANSCRIPTION_MODELS:
            raise ValueError("Invalid saved ElevenLabs model")
        if self.openai_rewrite_model not in OPENAI_REWRITE_MODELS:
            raise ValueError("Invalid saved OpenAI restructuring model")
        if not isinstance(self.chatgpt_rewrite_model, str) or len(self.chatgpt_rewrite_model) > 200 or any(c.isspace() for c in self.chatgpt_rewrite_model):
            raise ValueError("Invalid saved ChatGPT restructuring model")
        validate_openrouter_model(self.openrouter_model)
        if not isinstance(self.openrouter_models, tuple) or len(self.openrouter_models) > 50:
            raise ValueError("Invalid saved OpenRouter model list")
        for model in self.openrouter_models:
            if not isinstance(model, str):
                raise ValueError("Invalid saved OpenRouter model list")
            validate_openrouter_model(model)


class SettingsStore:
    def __init__(
        self,
        path: Path = DEFAULT_SETTINGS_PATH,
        legacy_path: Path | None = None,
    ) -> None:
        self.path = path
        self.legacy_path = (
            LEGACY_SETTINGS_PATH
            if path == DEFAULT_SETTINGS_PATH and legacy_path is None
            else legacy_path
        )

    def load(self) -> AppSettings:
        source = self.path
        if not source.exists() and self.legacy_path and self.legacy_path.exists():
            source = self.legacy_path
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Settings must be a JSON object")
            settings = AppSettings.from_dict(payload)
            if source != self.path:
                self.save(settings)
            return settings
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return AppSettings()

    def save(self, settings: AppSettings) -> None:
        settings.validate()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix="settings-",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(asdict(settings), temporary, indent=2, sort_keys=True)
                temporary.write("\n")
            temporary_path.chmod(0o600)
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
