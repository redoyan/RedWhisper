"""Small, testable audio-level helpers used before transcription."""

import numpy as np


def apply_microphone_gain(audio: np.ndarray, gain: float) -> np.ndarray:
    if gain not in {1.0, 2.0, 4.0, 8.0}:
        raise ValueError("Microphone gain must be 1×, 2×, 4×, or 8×")
    return np.clip(audio * gain, -1.0, 1.0)


def root_mean_square(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(audio**2))) if audio.size else 0.0
