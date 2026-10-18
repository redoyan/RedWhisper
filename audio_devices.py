"""Input-device discovery and safe default selection."""

from dataclasses import dataclass


VIRTUAL_DEVICE_TERMS = (
    "virtual",
    "blackhole",
    "loopback",
    "aggregate",
    "stereo mix",
    "voicemeeter",
)
PHYSICAL_MICROPHONE_TERMS = (
    "macbook",
    "built-in",
    "microphone",
    "external",
    "mic array",
    "headset",
)


@dataclass(frozen=True)
class InputDevice:
    id: int
    name: str
    input_channels: int
    host_api: int | None = None
    host_api_name: str | None = None
    is_host_default: bool = False


def input_devices_from_sounddevice(
    devices: list | tuple,
    host_apis: list | tuple | None = None,
) -> list[InputDevice]:
    host_api_names: dict[int, str] = {}
    host_api_defaults: dict[int, int] = {}
    if host_apis is not None:
        for index, host_api in enumerate(host_apis):
            host_api_names[index] = str(host_api.get("name", ""))
            try:
                host_api_defaults[index] = int(host_api["default_input_device"])
            except (KeyError, TypeError, ValueError):
                pass

    inputs: list[InputDevice] = []
    for index, device in enumerate(devices):
        input_channels = int(device["max_input_channels"])
        if input_channels <= 0:
            continue
        try:
            host_api = int(device["hostapi"])
        except (KeyError, TypeError, ValueError):
            host_api = None
        inputs.append(
            InputDevice(
                index,
                str(device["name"]),
                input_channels,
                host_api=host_api,
                host_api_name=host_api_names.get(host_api),
                is_host_default=(
                    host_api is not None
                    and host_api_defaults.get(host_api) == index
                ),
            )
        )
    return inputs


def refresh_input_devices(sounddevice_module) -> list[InputDevice]:
    """Refresh PortAudio's device cache before enumerating inputs."""
    terminate = getattr(sounddevice_module, "_terminate", None)
    initialize = getattr(sounddevice_module, "_initialize", None)
    if callable(terminate) and callable(initialize):
        terminate()
        initialize()
    query_host_apis = getattr(sounddevice_module, "query_hostapis", None)
    host_apis = query_host_apis() if callable(query_host_apis) else None
    return input_devices_from_sounddevice(
        sounddevice_module.query_devices(),
        host_apis,
    )


def is_virtual_input_device(device: InputDevice) -> bool:
    name = device.name.casefold()
    return any(term in name for term in VIRTUAL_DEVICE_TERMS)


def windows_input_devices(devices: list[InputDevice]) -> list[InputDevice]:
    """Return one clean Windows endpoint per microphone, preferring WASAPI."""
    if not devices:
        return []

    generic_names = {
        "microsoft sound mapper - input",
        "primary sound capture driver",
    }
    real_devices = [
        device
        for device in devices
        if device.name.casefold() not in generic_names
    ]
    candidates = real_devices or devices
    wasapi = [
        device
        for device in candidates
        if "wasapi" in (device.host_api_name or "").casefold()
    ]
    if wasapi:
        candidates = wasapi
    else:
        host_priority = {
            "windows directsound": 0,
            "mme": 1,
            "windows wdm-ks": 2,
        }
        candidates = sorted(
            candidates,
            key=lambda device: (
                host_priority.get((device.host_api_name or "").casefold(), 3),
                device.id,
            ),
        )

    unique: list[InputDevice] = []
    seen_names: set[str] = set()
    for device in candidates:
        key = " ".join(device.name.casefold().split())
        if key not in seen_names:
            seen_names.add(key)
            unique.append(device)
    return unique


def preferred_input_device(
    devices: list[InputDevice], system_default_id: int | None
) -> InputDevice:
    if not devices:
        raise ValueError("No audio input devices are available")

    physical = [
        device
        for device in devices
        if not is_virtual_input_device(device)
        and any(term in device.name.casefold() for term in PHYSICAL_MICROPHONE_TERMS)
    ]
    if physical:
        return physical[0]

    system_default = next(
        (device for device in devices if device.id == system_default_id), None
    )
    if system_default and not is_virtual_input_device(system_default):
        return system_default

    non_virtual = [
        device for device in devices if not is_virtual_input_device(device)
    ]
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
