"""Windows-compatible local Whisper backend powered by faster-whisper."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Callable

from mlx_whisper_core import TranscriptionError


DEFAULT_WINDOWS_MODEL = "large-v3-turbo"


@dataclass(frozen=True)
class FasterWhisperOptions:
    model: str = DEFAULT_WINDOWS_MODEL
    language: str | None = None
    maximum_accuracy: bool = False
    initial_prompt: str = "Voice dictation. Use accurate punctuation and paragraph breaks."
    device: str = "auto"
    compute_type: str = "auto"


class FasterWhisperTranscriber:
    """Lazy, thread-safe faster-whisper transcriber for CPU and NVIDIA CUDA."""

    def __init__(
        self,
        options: FasterWhisperOptions,
        model_factory: Callable[..., object] | None = None,
        cuda_device_count: Callable[[], int] | None = None,
    ) -> None:
        self.options = options
        self._model_factory = model_factory
        self._cuda_device_count = cuda_device_count
        self._model = None
        self._lock = threading.Lock()
        self.device = ""
        self.compute_type = ""

    def _runtime(self) -> tuple[str, str]:
        if self.options.device != "auto":
            device = self.options.device
        else:
            count_fn = self._cuda_device_count
            if count_fn is None:
                try:
                    import ctranslate2

                    count_fn = ctranslate2.get_cuda_device_count
                except (ImportError, AttributeError):
                    count_fn = lambda: 0
            try:
                device = "cuda" if count_fn() > 0 else "cpu"
            except RuntimeError:
                device = "cpu"

        compute_type = self.options.compute_type
        if compute_type == "auto":
            compute_type = "float16" if device == "cuda" else "int8"
        return device, compute_type

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        model_factory = self._model_factory
        if model_factory is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as error:
                raise TranscriptionError(
                    "Local transcription is unavailable. Reinstall RedWhisper "
                    "with the Windows local-model components."
                ) from error
            model_factory = WhisperModel

        self.device, self.compute_type = self._runtime()
        try:
            self._model = model_factory(
                self.options.model,
                device=self.device,
                compute_type=self.compute_type,
            )
        except Exception as error:
            raise TranscriptionError(
                f"Could not load local Whisper on {self.device}: {error}"
            ) from error
        return self._model

    def warmup(self) -> None:
        """Download and load the selected model without transcribing dummy audio."""
        with self._lock:
            self._ensure_model()

    def transcribe(self, audio_path: Path) -> str:
        with self._lock:
            model = self._ensure_model()
            try:
                segments, _info = model.transcribe(
                    str(audio_path),
                    language=self.options.language,
                    beam_size=5 if self.options.maximum_accuracy else 1,
                    best_of=5 if self.options.maximum_accuracy else 1,
                    temperature=0.0,
                    condition_on_previous_text=self.options.maximum_accuracy,
                    initial_prompt=self.options.initial_prompt,
                    word_timestamps=False,
                    vad_filter=True,
                    vad_parameters={"min_silence_duration_ms": 300},
                )
                return " ".join(
                    str(segment.text).strip()
                    for segment in segments
                    if str(segment.text).strip()
                ).strip()
            except Exception as error:
                raise TranscriptionError(
                    f"Local Whisper transcription failed: {error}"
                ) from error
