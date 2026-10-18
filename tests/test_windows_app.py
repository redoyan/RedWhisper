from array import array
import io
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
import wave

import numpy as np

from hotkey_config import HotkeyConfig
from audio_devices import InputDevice
from settings_store import AppSettings
from mlx_whisper_core import OpenAITextPostProcessor
from windows_app import (
    INDICATOR_BOTTOM_MARGIN,
    INDICATOR_HEIGHT,
    INDICATOR_WIDTH,
    NoPhysicalMicrophoneError,
    WindowsHotkeyListener,
    RedWhisperWindowsApp,
    create_pipeline,
    failed_usb_device_names,
    render_recording_island,
    radial_levels_from_audio,
    recording_status_text,
    request_usb_recovery,
    resolve_input_device,
    soft_chime_wave,
    windows_hotkey_virtual_key,
)
from windows_settings import windows_custom_hotkey_value


class WindowsIndicatorTests(unittest.TestCase):
    def test_indicator_is_a_compact_circle_above_the_taskbar(self) -> None:
        self.assertEqual((INDICATOR_WIDTH, INDICATOR_HEIGHT), (76, 76))
        self.assertEqual(INDICATOR_BOTTOM_MARGIN, 24)

    def test_status_contains_only_the_centered_timer(self) -> None:
        self.assertEqual(
            recording_status_text(65.8, "Right Ctrl"),
            "01:05",
        )
        self.assertEqual(
            recording_status_text(-1, "Ctrl + Space"),
            "00:00",
        )

    def test_rendered_indicator_has_a_transparent_background(self) -> None:
        image = render_recording_island("00:00")
        alpha = image.getchannel("A")
        transparent_pixels = alpha.histogram()[0]

        self.assertEqual(image.size, (76, 76))
        self.assertEqual(image.getpixel((0, 0))[3], 0)
        self.assertGreater(image.getpixel((38, 38))[3], 0)
        self.assertGreater(transparent_pixels, image.width * image.height // 2)

    def test_live_audio_expands_the_radial_ring(self) -> None:
        idle = render_recording_island(
            "00:00",
            np.zeros(64, dtype=np.float32),
        )
        active = render_recording_island(
            "00:00",
            np.ones(64, dtype=np.float32),
        )

        idle_alpha = sum(idle.getchannel("A").histogram()[1:])
        active_alpha = sum(active.getchannel("A").histogram()[1:])
        self.assertGreater(active_alpha, idle_alpha + 200)
        self.assertNotEqual(idle.tobytes(), active.tobytes())

    def test_live_waves_remain_translucent_after_boldening(self) -> None:
        active = render_recording_island(
            "00:00",
            np.ones(64, dtype=np.float32),
        )
        alpha = np.asarray(active.getchannel("A"))
        y_coordinates, x_coordinates = np.indices(alpha.shape)
        outer_ring = alpha[
            np.hypot(x_coordinates - 37.5, y_coordinates - 37.5) > 28
        ]

        self.assertGreater(np.count_nonzero(outer_ring), 1_000)
        self.assertLessEqual(int(np.max(outer_ring)), 210)
        self.assertGreater(np.count_nonzero((outer_ring > 0) & (outer_ring < 80)), 200)

    def test_microphone_spectrum_drives_mirrored_radial_levels(self) -> None:
        sample_rate = 16_000
        timeline = np.arange(1_024, dtype=np.float64) / sample_rate
        tone = 0.04 * np.sin(2.0 * np.pi * 440.0 * timeline)

        silence = radial_levels_from_audio(
            np.zeros((1_024, 1), dtype=np.float32),
            sample_rate,
        )
        levels = radial_levels_from_audio(tone[:, None], sample_rate)

        self.assertEqual(levels.shape, (64,))
        self.assertTrue(np.all(silence == 0))
        self.assertGreater(float(np.max(levels)), 0.5)
        np.testing.assert_allclose(levels[:32], levels[32:][::-1])

    def test_start_and_finish_chimes_are_clear_and_distinct(self) -> None:
        start = soft_chime_wave(True)
        finish = soft_chime_wave(False)

        self.assertNotEqual(start, finish)
        with wave.open(io.BytesIO(start), "rb") as audio:
            self.assertEqual(audio.getnchannels(), 2)
            self.assertEqual(audio.getsampwidth(), 2)
            self.assertEqual(audio.getframerate(), 44_100)
            self.assertEqual(audio.getnframes(), 7_938)
            samples = array("h", audio.readframes(audio.getnframes()))
        peak = max(abs(sample) for sample in samples)
        self.assertGreater(peak, 9_000)
        self.assertLessEqual(peak, 9_900)


class WindowsHotkeyWatchdogTests(unittest.TestCase):
    def test_physical_state_tracks_the_single_push_to_talk_key(self) -> None:
        pressed = {0xA3}
        listener = WindowsHotkeyListener(
            HotkeyConfig("right_control"),
            Mock(),
            Mock(),
            get_async_key_state=lambda virtual_key: (
                0x8000 if virtual_key in pressed else 0
            ),
        )

        self.assertTrue(listener.is_physically_pressed())
        pressed.remove(0xA3)
        self.assertFalse(listener.is_physically_pressed())

    def test_selected_key_is_consumed_and_drives_press_release(self) -> None:
        on_press = Mock()
        on_release = Mock()
        listener = WindowsHotkeyListener(
            HotkeyConfig("right_control"),
            on_press,
            on_release,
        )
        event = SimpleNamespace(vkCode=0xA3)

        self.assertFalse(listener._win32_event_filter(0x0100, event))
        self.assertFalse(listener._win32_event_filter(0x0100, event))
        self.assertFalse(listener._win32_event_filter(0x0101, event))
        on_press.assert_called_once_with()
        on_release.assert_called_once_with()

    def test_unrelated_keys_are_not_consumed(self) -> None:
        listener = WindowsHotkeyListener(
            HotkeyConfig("f8"),
            Mock(),
            Mock(),
        )

        self.assertTrue(
            listener._win32_event_filter(0x0100, SimpleNamespace(vkCode=0x20))
        )

    def test_custom_single_key_is_consumed(self) -> None:
        config = HotkeyConfig(windows_custom_hotkey_value(0x7B))
        listener = WindowsHotkeyListener(config, Mock(), Mock())

        self.assertEqual(windows_hotkey_virtual_key(config), 0x7B)
        self.assertFalse(
            listener._win32_event_filter(0x0100, SimpleNamespace(vkCode=0x7B))
        )


class WindowsMicrophoneSelectionTests(unittest.TestCase):
    def test_saved_bose_endpoint_wins_over_generic_mapper(self) -> None:
        devices = [
            InputDevice(0, "Microsoft Sound Mapper - Input", 2),
            InputDevice(
                20,
                "Microphone (Virtual Desktop Audio)",
                1,
                host_api=2,
                host_api_name="Windows WASAPI",
                is_host_default=True,
            ),
            InputDevice(
                21,
                "Headset (Bose QC Ultra Headphones)",
                1,
                host_api=2,
                host_api_name="Windows WASAPI",
            ),
        ]
        settings = AppSettings(
            input_device=21,
            input_device_name="Headset (Bose QC Ultra Headphones)",
        )

        selected = resolve_input_device(devices, settings)

        self.assertEqual(selected.id, 21)

    def test_auto_detect_skips_silent_virtual_default_for_physical_headset(
        self,
    ) -> None:
        devices = [
            InputDevice(
                20,
                "Microphone (Virtual Desktop Audio)",
                1,
                host_api_name="Windows WASAPI",
                is_host_default=True,
            ),
            InputDevice(
                21,
                "Headset (Bose QC Ultra Headphones)",
                1,
                host_api_name="Windows WASAPI",
            ),
        ]

        selected = resolve_input_device(devices, AppSettings())

        self.assertEqual(selected.id, 21)

    def test_auto_detect_prefers_headset_over_physical_wasapi_default(self) -> None:
        devices = [
            InputDevice(
                20,
                "Studio Microphone",
                1,
                host_api_name="Windows WASAPI",
                is_host_default=True,
            ),
            InputDevice(
                21,
                "Headset (Bose QC Ultra Headphones)",
                1,
                host_api_name="Windows WASAPI",
            ),
        ]

        selected = resolve_input_device(devices, AppSettings())

        self.assertEqual(selected.id, 21)

    def test_auto_detect_switches_headset_webcam_headset_after_reindex(self) -> None:
        webcam = InputDevice(18, "Microphone (HP 4K Streaming Webcam)", 1)
        headset = InputDevice(21, "Headset (LE-Bose QC Ultra Headphones)", 1)
        virtual = InputDevice(20, "Microphone (Virtual Desktop Audio)", 1,
                              is_host_default=True)
        settings = AppSettings()
        self.assertEqual(resolve_input_device([virtual, webcam, headset], settings), headset)
        reindexed_webcam = InputDevice(21, webcam.name, 1)
        self.assertEqual(resolve_input_device([virtual, reindexed_webcam], settings),
                         reindexed_webcam)
        reindexed_headset = InputDevice(23, headset.name, 1)
        self.assertEqual(resolve_input_device([virtual, reindexed_webcam, reindexed_headset],
                                              settings), reindexed_headset)
        self.assertIsNone(settings.input_device_name)

    def test_missing_saved_headset_does_not_select_reused_virtual_index(self) -> None:
        settings = AppSettings(input_device=21, input_device_name="Headset (Bose)")
        webcam = InputDevice(22, "Microphone (HP 4K Streaming Webcam)", 1)
        virtual = InputDevice(21, "Microphone (Virtual Desktop Audio)", 1)
        self.assertEqual(resolve_input_device([virtual, webcam], settings), webcam)

    def test_explicit_webcam_preference_is_respected(self) -> None:
        webcam = InputDevice(18, "Microphone (HP 4K Streaming Webcam)", 1)
        headset = InputDevice(21, "Headset (Bose)", 1)
        settings = AppSettings(input_device=18, input_device_name=webcam.name)
        self.assertEqual(resolve_input_device([headset, webcam], settings), webcam)

    def test_webcam_precedes_other_physical_default_when_headset_absent(self) -> None:
        webcam = InputDevice(18, "Microphone (HP 4K Streaming Webcam)", 1)
        studio = InputDevice(21, "Studio Microphone", 1, is_host_default=True)
        self.assertEqual(resolve_input_device([studio, webcam], AppSettings()), webcam)

    def test_missing_saved_endpoint_rejects_reused_virtual_only_index(self) -> None:
        settings = AppSettings(input_device=21, input_device_name="Headset (Bose)")
        virtual = InputDevice(21, "Microphone (Virtual Desktop Audio)", 1)
        with self.assertRaisesRegex(ValueError, "physical microphone"):
            resolve_input_device([virtual], settings)

    def test_auto_detect_rejects_only_virtual_microphones(self) -> None:
        devices = [
            InputDevice(
                20,
                "Microphone (Virtual Desktop Audio)",
                1,
                host_api_name="Windows WASAPI",
                is_host_default=True,
            ),
        ]

        with self.assertRaisesRegex(ValueError, "physical microphone"):
            resolve_input_device(devices, AppSettings())


class WindowsAudioRecoveryTests(unittest.TestCase):
    @patch("windows_app.sd.query_devices", return_value={"default_samplerate": 48000})
    @patch("windows_app.refresh_input_devices")
    def test_refresh_reselects_live_input_without_pinning_fallback(self, refresh, query) -> None:
        app = RedWhisperWindowsApp.__new__(RedWhisperWindowsApp)
        app.settings = AppSettings()
        webcam = InputDevice(18, "Microphone (HP 4K Streaming Webcam)", 1)
        headset = InputDevice(21, "Headset (LE-Bose QC Ultra Headphones)", 1)
        refresh.side_effect = [[webcam, headset], [webcam], [webcam, headset]]
        for expected in (headset, webcam, headset):
            app._refresh_microphone()
            self.assertEqual(app.input_device, expected)
            self.assertEqual(app.capture_rate, 48000)
        self.assertIsNone(app.settings.input_device_name)
        self.assertEqual(refresh.call_count, 3)

    @patch("windows_app.request_usb_recovery", return_value=False)
    @patch("windows_app.failed_usb_device_names",
           return_value=["Unknown USB Device (Device Descriptor Request Failed)"])
    @patch("windows_app.refresh_input_devices")
    def test_failed_usb_webcam_is_named_when_only_virtual_mic_remains(
        self, refresh, failed, recovery
    ) -> None:
        app = RedWhisperWindowsApp.__new__(RedWhisperWindowsApp)
        app.settings = AppSettings()
        refresh.return_value = [
            InputDevice(15, "Microphone (Virtual Desktop Audio)", 1,
                        host_api_name="Windows WASAPI", is_host_default=True),
        ]

        with self.assertRaisesRegex(NoPhysicalMicrophoneError, "different USB port") as caught:
            app._refresh_microphone()

        self.assertLessEqual(len(str(caught.exception)), 180)
        failed.assert_called_once_with()
        recovery.assert_called_once_with()

    @patch("windows_app.request_usb_recovery", return_value=True)
    @patch("windows_app.failed_usb_device_names",
           return_value=["Unknown USB Device (Device Descriptor Request Failed)"])
    @patch("windows_app.refresh_input_devices")
    def test_failed_usb_webcam_triggers_installed_recovery_task(
        self, refresh, _failed, recovery
    ) -> None:
        app = RedWhisperWindowsApp.__new__(RedWhisperWindowsApp)
        app.settings = AppSettings()
        refresh.return_value = [InputDevice(15, "Microphone (Virtual Desktop Audio)", 1)]

        with self.assertRaisesRegex(NoPhysicalMicrophoneError, "resetting") as caught:
            app._refresh_microphone()

        self.assertLessEqual(len(str(caught.exception)), 180)
        recovery.assert_called_once_with()

    @patch("windows_app.subprocess.run")
    def test_request_usb_recovery_runs_scheduled_task(self, run) -> None:
        run.return_value = SimpleNamespace(returncode=0)
        self.assertTrue(request_usb_recovery())
        self.assertEqual(run.call_args.args[0],
                         ["schtasks", "/Run", "/TN", r"\RedWhisper\Repair USB devices"])
        run.return_value = SimpleNamespace(returncode=1)
        self.assertFalse(request_usb_recovery())
        run.side_effect = OSError("schtasks missing")
        self.assertFalse(request_usb_recovery())

    @patch("windows_app.failed_usb_device_names", return_value=[])
    @patch("windows_app.refresh_input_devices")
    def test_generic_message_kept_when_no_usb_device_failed(self, refresh, failed) -> None:
        app = RedWhisperWindowsApp.__new__(RedWhisperWindowsApp)
        app.settings = AppSettings()
        refresh.return_value = [InputDevice(15, "Microphone (Virtual Desktop Audio)", 1)]

        with self.assertRaisesRegex(NoPhysicalMicrophoneError, "Windows Sound settings"):
            app._refresh_microphone()

    @patch("windows_app.subprocess.run")
    def test_failed_usb_device_names_parses_powershell_output(self, run) -> None:
        run.return_value = SimpleNamespace(
            stdout="Unknown USB Device (Device Descriptor Request Failed)\r\n\r\n"
        )
        self.assertEqual(failed_usb_device_names(),
                         ["Unknown USB Device (Device Descriptor Request Failed)"])
        run.side_effect = OSError("powershell missing")
        self.assertEqual(failed_usb_device_names(), [])

    def make_app(self) -> RedWhisperWindowsApp:
        app = RedWhisperWindowsApp.__new__(RedWhisperWindowsApp)
        app.root = Mock()
        app.is_recording = True
        app.recording_requires_hold = True
        app.audio_chunks = []
        app.audio_stream_recovery_attempts = 0
        app.input_device = InputDevice(15, "HP 4K Streaming Webcam", 1)
        app.stream = Mock()
        app.indicator_enabled = True
        app.indicator = Mock()
        app._notify = Mock()
        return app

    def test_silent_stream_is_refreshed_and_reopened_once(self) -> None:
        app = self.make_app()
        app._close_microphone_stream = Mock()
        app._refresh_microphone = Mock()
        app._open_microphone_stream = Mock()

        app._verify_audio_stream()

        app._close_microphone_stream.assert_called_once_with()
        app._refresh_microphone.assert_called_once_with()
        app._open_microphone_stream.assert_called_once_with()
        app.root.after.assert_called_once_with(900, app._verify_audio_stream)
        self.assertEqual(app.audio_stream_recovery_attempts, 1)
        self.assertTrue(app.is_recording)

    def test_callbacks_prevent_unnecessary_stream_recovery(self) -> None:
        app = self.make_app()
        app.audio_chunks = [np.zeros((1_024, 1), dtype=np.float32)]
        app._refresh_microphone = Mock()

        app._verify_audio_stream()

        app._refresh_microphone.assert_not_called()
        app.root.after.assert_not_called()

    @patch("windows_app.play_soft_chime")
    def test_second_silent_stream_stops_with_a_clear_notification(
        self,
        play_soft_chime_mock,
    ) -> None:
        app = self.make_app()
        app.audio_stream_recovery_attempts = 1
        app._close_microphone_stream = Mock()

        app._verify_audio_stream()

        self.assertFalse(app.is_recording)
        self.assertFalse(app.recording_requires_hold)
        app._close_microphone_stream.assert_called_once_with()
        app.indicator.hide.assert_called_once_with()
        play_soft_chime_mock.assert_called_once_with(starting=False)
        app._notify.assert_called_once()
        self.assertIn("not responding", app._notify.call_args.args[0].lower())


class WindowsPipelineTests(unittest.TestCase):
    def test_local_transcription_can_feed_an_openai_rewrite_model(self) -> None:
        settings = AppSettings(
            engine="local",
            post_process_openai=True,
            openai_rewrite_model="gpt-5-mini",
        )

        with patch("windows_app.openai_key_from_environment", return_value="test-key"):
            pipeline, local_transcriber, _label = create_pipeline(
                settings,
                "large-v3-turbo",
            )

        self.assertIsNotNone(local_transcriber)
        self.assertIsInstance(pipeline.post_processor, OpenAITextPostProcessor)
        self.assertEqual(pipeline.post_processor.options.model, "gpt-5-mini")


if __name__ == "__main__":
    unittest.main()
