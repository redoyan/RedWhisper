"""Input-device discovery and safe default selection."""

from dataclasses import dataclass


VIRTUAL_DEVICE_TERMS = ("virtual", "blackhole", "loopback", "aggregate")
PHYSICAL_MICROPHONE_TERMS = ("macbook", "built-in", "microphone", "external")


@dataclass(frozen=True)
class InputDevice:
    id: int
    name: str
    input_channels: int


def input_devices_from_sounddevice(devices: list | tuple) -> list[InputDevice]:
    return [
        InputDevice(index, str(device["name"]), int(device["max_input_channels"]))
        for index, device in enumerate(devices)
        if int(device["max_input_channels"]) > 0
    ]


def refresh_input_devices(sounddevice_module) -> list[InputDevice]:
    """Refresh PortAudio's macOS device cache before enumerating inputs."""
    terminate = getattr(sounddevice_module, "_terminate", None)
    initialize = getattr(sounddevice_module, "_initialize", None)
    if callable(terminate) and callable(initialize):
        terminate()
        initialize()
    return input_devices_from_sounddevice(sounddevice_module.query_devices())


def preferred_input_device(
    devices: list[InputDevice], system_default_id: int | None
) -> InputDevice:
    if not devices:
        raise ValueError("No audio input devices are available")

    def is_virtual(device: InputDevice) -> bool:
        name = device.name.casefold()
        return any(term in name for term in VIRTUAL_DEVICE_TERMS)

    physical = [
        device
        for device in devices
        if not is_virtual(device)
        and any(term in device.name.casefold() for term in PHYSICAL_MICROPHONE_TERMS)
    ]
    if physical:
        return physical[0]

    system_default = next(
        (device for device in devices if device.id == system_default_id), None
    )
    if system_default and not is_virtual(system_default):
        return system_default

    non_virtual = [device for device in devices if not is_virtual(device)]
    return non_virtual[0] if non_virtual else devices[0]


def device_by_id(devices: list[InputDevice], device_id: int) -> InputDevice:
    try:
        return next(device for device in devices if device.id == device_id)
    except StopIteration as error:
        raise ValueError(f"Audio input device {device_id} is not available") from error


def device_by_name(devices: list[InputDevice], device_name: str) -> InputDevice:
    try:
        return next(device for device in devices if device.name == device_name)
    except StopIteration as error:
        raise ValueError(f'Audio input device "{device_name}" is not available') from error
