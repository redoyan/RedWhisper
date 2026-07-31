import unittest

from audio_devices import (
    device_by_id,
    device_by_name,
    input_devices_from_sounddevice,
    preferred_input_device,
    refresh_input_devices,
)


SOUNDDEVICES = (
    {"name": "Display", "max_input_channels": 0},
    {"name": "MacBook Pro Microphone", "max_input_channels": 1},
    {"name": "Speakers", "max_input_channels": 0},
    {"name": "Virtual Desktop Mic", "max_input_channels": 2},
)


class AudioDeviceTests(unittest.TestCase):
    def test_output_only_devices_are_excluded(self) -> None:
        devices = input_devices_from_sounddevice(SOUNDDEVICES)
        self.assertEqual([device.id for device in devices], [1, 3])

    def test_physical_microphone_wins_over_virtual_system_default(self) -> None:
        devices = input_devices_from_sounddevice(SOUNDDEVICES)
        selected = preferred_input_device(devices, system_default_id=3)
        self.assertEqual(selected.id, 1)
        self.assertEqual(selected.name, "MacBook Pro Microphone")

    def test_selected_device_must_still_exist(self) -> None:
        devices = input_devices_from_sounddevice(SOUNDDEVICES)
        with self.assertRaises(ValueError):
            device_by_id(devices, 99)

    def test_saved_bluetooth_microphone_can_be_resolved_by_name(self) -> None:
        devices = input_devices_from_sounddevice(
            (*SOUNDDEVICES, {"name": "AirPods Microphone", "max_input_channels": 1})
        )

        selected = device_by_name(devices, "AirPods Microphone")

        self.assertEqual(selected.name, "AirPods Microphone")

    def test_refresh_reinitializes_portaudio_before_detecting_bluetooth(self) -> None:
        class StaleSoundDevice:
            def __init__(self) -> None:
                self.calls = []
                self.devices = list(SOUNDDEVICES)

            def _terminate(self) -> None:
                self.calls.append("terminate")

            def _initialize(self) -> None:
                self.calls.append("initialize")
                self.devices.append(
                    {"name": "Bose QC Ultra Headphones", "max_input_channels": 1}
                )

            def query_devices(self):
                self.calls.append("query")
                return self.devices

        sounddevice = StaleSoundDevice()

        devices = refresh_input_devices(sounddevice)

        self.assertEqual(sounddevice.calls, ["terminate", "initialize", "query"])
        self.assertEqual(devices[-1].name, "Bose QC Ultra Headphones")
        self.assertEqual(devices[-1].input_channels, 1)


if __name__ == "__main__":
    unittest.main()
