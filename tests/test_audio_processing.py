import unittest

import numpy as np

from audio_processing import apply_microphone_gain, root_mean_square


class AudioProcessingTests(unittest.TestCase):
    def test_default_gain_doubles_quiet_audio(self) -> None:
        audio = np.array([0.0001, -0.0001], dtype=np.float32)
        amplified = apply_microphone_gain(audio, 2.0)
        np.testing.assert_allclose(amplified, [0.0002, -0.0002])

    def test_gain_is_safely_clipped(self) -> None:
        audio = np.array([0.5, -0.5], dtype=np.float32)
        amplified = apply_microphone_gain(audio, 4.0)
        np.testing.assert_allclose(amplified, [1.0, -1.0])

    def test_rms_handles_empty_audio(self) -> None:
        self.assertEqual(root_mean_square(np.array([], dtype=np.float32)), 0.0)


if __name__ == "__main__":
    unittest.main()
