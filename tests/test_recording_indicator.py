import unittest

from voxtape import (
    RECORDING_GLASS_ALPHA,
    WAVEFORM_BAR_RGBA,
    recording_indicator_origin,
    recording_status_text,
)


class RecordingIndicatorTests(unittest.TestCase):
    def test_glass_background_is_translucent(self) -> None:
        self.assertEqual(RECORDING_GLASS_ALPHA, 0.72)

    def test_waveform_bars_are_bright_enough_to_remain_visible(self) -> None:
        self.assertEqual(WAVEFORM_BAR_RGBA, (0.56, 0.37, 0.22, 0.78))

    def test_origin_is_bottom_center_of_visible_screen(self) -> None:
        self.assertEqual(
            recording_indicator_origin(-1920, 25, 1920, 344),
            (-1132.0, 49),
        )

    def test_status_contains_only_elapsed_time(self) -> None:
        self.assertEqual(
            recording_status_text(65.9),
            "01:05",
        )

    def test_negative_elapsed_time_is_clamped(self) -> None:
        self.assertEqual(
            recording_status_text(-2),
            "00:00",
        )


if __name__ == "__main__":
    unittest.main()
