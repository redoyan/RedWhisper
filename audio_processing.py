"""Small, testable audio-level helpers used before transcription."""

import numpy as np


def apply_microphone_gain(audio: np.ndarray, gain: float) -> np.ndarray:
    if gain not in {1.0, 2.0, 4.0, 8.0}:
        raise ValueError("Microphone gain must be 1×, 2×, 4×, or 8×")
    return np.clip(audio * gain, -1.0, 1.0)


def root_mean_square(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(audio**2))) if audio.size else 0.0


def resample_audio(
    audio: np.ndarray, source_rate: int, target_rate: int
) -> np.ndarray:
    """Resample mono audio without adding a heavyweight runtime dependency."""
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("Audio sample rates must be positive")
    flattened = np.asarray(audio, dtype=np.float32).reshape(-1)
    if flattened.size == 0 or source_rate == target_rate:
        return flattened.copy()
    target_length = max(1, round(flattened.size * target_rate / source_rate))
    source_positions = np.arange(flattened.size, dtype=np.float64)
    target_positions = np.linspace(
        0, flattened.size - 1, target_length, dtype=np.float64
    )
    return np.interp(target_positions, source_positions, flattened).astype(
        np.float32
    )
