"""Local launch-time settings GUI for the RedWhisper menu-bar app."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
import importlib.util
import secrets
import subprocess
import threading
from urllib.parse import parse_qs
import webbrowser

from mlx_whisper_core import (
    ConfigurationError,
    DEFAULT_OPENROUTER_MODEL,
    DEFAULT_OPENAI_REWRITE_MODEL,
    ELEVENLABS_TRANSCRIPTION_MODELS,
    OPENAI_REWRITE_MODELS,
    OPENAI_TRANSCRIPTION_MODELS,
    elevenlabs_key_from_environment,
    openai_key_from_environment,
    openrouter_key_from_environment,
    save_elevenlabs_key,
    save_openai_key,
    save_openrouter_key,
    validate_openrouter_model,
)
from hotkey_config import HOTKEY_PRESET_LABELS, HOTKEY_PRESETS
from audio_devices import InputDevice
from chatgpt_subscription import ChatGPTSubscription, ChatGPTError


LOCAL_MODEL_ESTIMATED_GB = 3.0
LOCAL_LLM_ADDITIONAL_GB = 2.5


@dataclass(frozen=True)
class LaunchSelection:
    engine: str
    language: str
    maximum_accuracy: bool
    replacements: str | None
    post_process_local: bool
    hotkey_preset: str = "fn"
    secondary_hotkey_preset: str | None = None
    microphone_gain: float = 2.0
    input_device: int | None = None
    openai_model: str = "gpt-4o-transcribe"
    elevenlabs_model: str = "scribe_v2"
    post_process_openai: bool = False
    openai_rewrite_model: str = DEFAULT_OPENAI_REWRITE_MODEL
    post_process_openrouter: bool = False
    openrouter_model: str = DEFAULT_OPENROUTER_MODEL
    post_process_chatgpt: bool = False
    chatgpt_rewrite_model: str = ""
    chatgpt_models: tuple[dict, ...] = ()


@dataclass(frozen=True)
class MacResources:
    total_memory_gb: float
    chip: str

    @property
    def local_recommendation(self) -> str:
        if self.total_memory_gb < 12:
            return "Cloud transcription is recommended when other large apps are open."
        if self.total_memory_gb < 24:
            return "Local transcription is suitable; avoid local LLM rewriting under heavy load."
        return "Local transcription and optional rewriting should fit comfortably."


def detect_mac_resources() -> MacResources:
    def sysctl(name: str, fallback: str) -> str:
        try:
            return subprocess.check_output(
                ["sysctl", "-n", name], text=True, stderr=subprocess.DEVNULL
            ).strip()
        except (OSError, subprocess.SubprocessError):
            return fallback

    memory_bytes = int(sysctl("hw.memsize", "0"))
    chip = sysctl("machdep.cpu.brand_string", "Apple Silicon")
    return MacResources(memory_bytes / (1024**3), chip)


def configure_in_browser(
    *,
    engine: str,
    language: str,
    maximum_accuracy: bool,
    replacements: str | None,
    post_process_local: bool,
    hotkey_preset: str = "fn",
    secondary_hotkey_preset: str | None = None,
    microphone_gain: float = 2.0,
    input_devices: list[InputDevice] | None = None,
    input_device: int | None = None,
    openai_model: str = "gpt-4o-transcribe",
    elevenlabs_model: str = "scribe_v2",
    post_process_openai: bool = False,
    openai_rewrite_model: str = DEFAULT_OPENAI_REWRITE_MODEL,
    post_process_openrouter: bool = False,
    openrouter_model: str = DEFAULT_OPENROUTER_MODEL,
    runtime: bool = False,
    post_process_chatgpt: bool = False,
    chatgpt_rewrite_model: str = "",
) -> LaunchSelection:
    """Open a one-use localhost form and block until settings are submitted."""

    resources = detect_mac_resources()
    csrf_token = secrets.token_urlsafe(32)
    has_openai_key = bool(openai_key_from_environment())
    has_openrouter_key = bool(openrouter_key_from_environment())
    has_elevenlabs_key = bool(elevenlabs_key_from_environment())
    has_local_llm = importlib.util.find_spec("mlx_lm") is not None
    available_input_devices = input_devices or []
    chatgpt_models = ()
    try:
        subscription = ChatGPTSubscription()
        if subscription.snapshot()["connected"]:
            chatgpt_models = tuple(subscription.sync_models())
    except ChatGPTError:
        pass  # Native account settings provides connection/error recovery.
    initial = LaunchSelection(
        engine=engine,
        language=language,
        maximum_accuracy=maximum_accuracy,
        replacements=replacements,
        post_process_local=post_process_local,
        hotkey_preset=hotkey_preset,
        secondary_hotkey_preset=secondary_hotkey_preset,
        microphone_gain=microphone_gain,
        input_device=input_device,
        openai_model=openai_model,
        elevenlabs_model=elevenlabs_model,
        post_process_openai=post_process_openai,
        openai_rewrite_model=openai_rewrite_model,
        post_process_openrouter=post_process_openrouter,
        openrouter_model=openrouter_model,
        post_process_chatgpt=post_process_chatgpt,
        chatgpt_rewrite_model=chatgpt_rewrite_model,
        chatgpt_models=chatgpt_models,
    )

    class SettingsServer(ThreadingHTTPServer):
        selection: LaunchSelection | None = None

        def server_bind(self):
            TCPServer.server_bind(self)
            self.server_name, self.server_port = self.server_address

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            if self.path != "/":
                self.send_error(404)
                return
            self._send_html(
                _settings_page(
                    resources,
                    initial,
                    csrf_token,
                    has_openai_key,
                    has_local_llm,
                    available_input_devices,
                    runtime=runtime,
                    has_openrouter_key=has_openrouter_key,
                    has_elevenlabs_key=has_elevenlabs_key,
                )
            )

        def do_POST(self) -> None:
            nonlocal has_openai_key, has_openrouter_key, has_elevenlabs_key
            if self.path != "/start":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_error(400, "Invalid content length")
                return
            if length > 16_384:
                self.send_error(413, "Settings request is too large")
                return
            form = parse_qs(self.rfile.read(length).decode("utf-8"))
            if form.get("csrf", [""])[0] != csrf_token:
                self.send_error(403, "Invalid settings token")
                return

            selected_engine = form.get("engine", ["local"])[0]
            if selected_engine not in {"local", "openai", "elevenlabs"}:
                self.send_error(400, "Invalid transcription engine")
                return
            provided_api_key = form.get("api_key", [""])[0].strip()
            if provided_api_key:
                try:
                    save_openai_key(provided_api_key)
                    has_openai_key = True
                except ConfigurationError as error:
                    self._send_html(
                        _settings_page(
                            resources,
                            initial,
                            csrf_token,
                            has_openai_key,
                            has_local_llm,
                            available_input_devices,
                            runtime=runtime,
                            error_message=str(error),
                            has_openrouter_key=has_openrouter_key,
                        )
                    )
                    return
            provided_openrouter_key = form.get("openrouter_api_key", [""])[0].strip()
            if provided_openrouter_key:
                try:
                    save_openrouter_key(provided_openrouter_key)
                    has_openrouter_key = True
                except ConfigurationError as error:
                    self._send_html(
                        _settings_page(
                            resources,
                            initial,
                            csrf_token,
                            has_openai_key,
                            has_local_llm,
                            available_input_devices,
                            runtime=runtime,
                            error_message=str(error),
                            has_openrouter_key=has_openrouter_key,
                        )
                    )
                    return
            provided_elevenlabs_key = form.get(
                "elevenlabs_api_key", [""]
            )[0].strip()
            if provided_elevenlabs_key:
                try:
                    save_elevenlabs_key(provided_elevenlabs_key)
                    has_elevenlabs_key = True
                except ConfigurationError as error:
                    self._send_html(
                        _settings_page(
                            resources,
                            initial,
                            csrf_token,
                            has_openai_key,
                            has_local_llm,
                            available_input_devices,
                            runtime=runtime,
                            error_message=str(error),
                            has_openrouter_key=has_openrouter_key,
                            has_elevenlabs_key=has_elevenlabs_key,
                        )
                    )
                    return
            selected_rewrite_engine = form.get("rewrite_engine", ["off"])[0]
            if selected_rewrite_engine not in {"off", "local", "openai", "openrouter", "chatgpt"}:
                self.send_error(400, "Invalid restructuring engine")
                return
            selected_chatgpt_model = form.get("chatgpt_rewrite_model", [chatgpt_rewrite_model])[0]
            if selected_rewrite_engine == "chatgpt" and (
                not chatgpt_models or (selected_chatgpt_model and selected_chatgpt_model not in {model["slug"] for model in chatgpt_models})
            ):
                self.send_error(400, "Connect ChatGPT and select an available model in the app's native Settings first.")
                return
            if selected_engine == "elevenlabs" and not has_elevenlabs_key:
                self._send_html(
                    _settings_page(
                        resources,
                        initial,
                        csrf_token,
                        has_openai_key,
                        has_local_llm,
                        available_input_devices,
                        runtime=runtime,
                        error_message="Enter an ElevenLabs API key before selecting ElevenLabs.",
                        has_openrouter_key=has_openrouter_key,
                        has_elevenlabs_key=has_elevenlabs_key,
                    )
                )
                return
            if selected_rewrite_engine == "openrouter" and not has_openrouter_key:
                self._send_html(
                    _settings_page(
                        resources,
                        initial,
                        csrf_token,
                        has_openai_key,
                        has_local_llm,
                        available_input_devices,
                        runtime=runtime,
                        error_message="Enter an OpenRouter API key before selecting OpenRouter.",
                        has_openrouter_key=has_openrouter_key,
                    )
                )
                return
            if (
                selected_engine == "openai" or selected_rewrite_engine == "openai"
            ) and not has_openai_key:
                self._send_html(
                    _settings_page(
                        resources,
                        initial,
                        csrf_token,
                        has_openai_key,
                        has_local_llm,
                        available_input_devices,
                        runtime=runtime,
                        error_message="Enter an OpenAI API key before selecting an OpenAI feature.",
                        has_openrouter_key=has_openrouter_key,
                    )
                )
                return
            if selected_rewrite_engine == "local" and not has_local_llm:
                self.send_error(400, "Optional local LLM support is not installed")
                return

            selected_language = form.get("language", ["auto"])[0].strip() or "auto"
            if len(selected_language) > 20:
                self.send_error(400, "Invalid language")
                return
            selected_openai_model = form.get(
                "openai_model", ["gpt-4o-transcribe"]
            )[0]
            if selected_openai_model not in OPENAI_TRANSCRIPTION_MODELS:
                self.send_error(400, "Invalid OpenAI transcription model")
                return
            selected_elevenlabs_model = form.get(
                "elevenlabs_model", ["scribe_v2"]
            )[0]
            if selected_elevenlabs_model not in ELEVENLABS_TRANSCRIPTION_MODELS:
                self.send_error(400, "Invalid ElevenLabs transcription model")
                return
            selected_openai_rewrite_model = form.get(
                "openai_rewrite_model", [DEFAULT_OPENAI_REWRITE_MODEL]
            )[0]
            if selected_openai_rewrite_model not in OPENAI_REWRITE_MODELS:
                self.send_error(400, "Invalid OpenAI restructuring model")
                return
            selected_openrouter_model = form.get(
                "openrouter_model", [DEFAULT_OPENROUTER_MODEL]
            )[0]
            try:
                selected_openrouter_model = validate_openrouter_model(
                    selected_openrouter_model
                )
            except ConfigurationError as error:
                self.send_error(400, str(error))
                return
            replacement_value = form.get("replacements", [""])[0].strip()
            selected_hotkey = form.get("hotkey_preset", ["fn"])[0]
            if selected_hotkey not in HOTKEY_PRESETS:
                self.send_error(400, "Invalid recording shortcut")
                return
            selected_secondary = form.get("secondary_hotkey_preset", ["none"])[0]
            if selected_secondary == "none":
                selected_secondary = None
            elif selected_secondary not in HOTKEY_PRESETS:
                self.send_error(400, "Invalid secondary recording shortcut")
                return
            if selected_secondary == selected_hotkey:
                self.send_error(400, "Primary and secondary shortcuts must differ")
                return
            try:
                microphone_gain = float(form.get("microphone_gain", ["2.0"])[0])
            except ValueError:
                self.send_error(400, "Invalid microphone sensitivity")
                return
            if microphone_gain not in {1.0, 2.0, 4.0, 8.0}:
                self.send_error(400, "Invalid microphone sensitivity")
                return
            selected_device_id = input_device
            if available_input_devices:
                try:
                    selected_device_id = int(
                        form.get("input_device", [str(input_device)])[0]
                    )
                except (TypeError, ValueError):
                    self.send_error(400, "Invalid microphone device")
                    return
                if selected_device_id not in {
                    device.id for device in available_input_devices
                }:
                    self.send_error(400, "Selected microphone is unavailable")
                    return
            self.server.selection = LaunchSelection(
                engine=selected_engine,
                language=selected_language,
                maximum_accuracy="maximum_accuracy" in form,
                replacements=replacement_value or None,
                post_process_local=selected_rewrite_engine == "local",
                post_process_chatgpt=selected_rewrite_engine == "chatgpt",
                chatgpt_rewrite_model=selected_chatgpt_model,
                hotkey_preset=selected_hotkey,
                secondary_hotkey_preset=selected_secondary,
                microphone_gain=microphone_gain,
                input_device=selected_device_id,
                openai_model=selected_openai_model,
                elevenlabs_model=selected_elevenlabs_model,
                post_process_openai=selected_rewrite_engine == "openai",
                openai_rewrite_model=selected_openai_rewrite_model,
                post_process_openrouter=selected_rewrite_engine == "openrouter",
                openrouter_model=selected_openrouter_model,
            )
            self._send_html(
                _started_page(
                    selected_engine,
                    HOTKEY_PRESET_LABELS[selected_hotkey],
                    selected_openai_model,
                    selected_elevenlabs_model,
                    runtime=runtime,
                )
            )
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def _send_html(self, content: str) -> None:
            payload = content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'none'; frame-ancestors 'none'; form-action 'self'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = SettingsServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Opening launch settings: {url}")
    webbrowser.open(url)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    if server.selection is None:
        raise RuntimeError("Launch settings closed without a selection")
    return server.selection


def _settings_page(
    resources: MacResources,
    current: LaunchSelection,
    csrf_token: str,
    has_openai_key: bool,
    has_local_llm: bool,
    input_devices: list[InputDevice] | None = None,
    runtime: bool = False,
    error_message: str | None = None,
    has_openrouter_key: bool = False,
    has_elevenlabs_key: bool = False,
) -> str:
    local_checked = "checked" if current.engine == "local" else ""
    openai_checked = "checked" if current.engine == "openai" else ""
    elevenlabs_checked = (
        "checked" if current.engine == "elevenlabs" else ""
    )
    accuracy_checked = "checked" if current.maximum_accuracy else ""
    rewrite_engine = (
        "chatgpt"
        if current.post_process_chatgpt
        else "openrouter"
        if current.post_process_openrouter
        else "openai"
        if current.post_process_openai
        else "local"
        if current.post_process_local and has_local_llm
        else "off"
    )
    openai_state = (
        "API key available in the environment or macOS Keychain."
        if has_openai_key
        else "Enter an API key below to enable this option."
    )
    elevenlabs_state = (
        "API key available in the environment or macOS Keychain."
        if has_elevenlabs_key
        else "Enter an API key below to enable this option."
    )
    llm_warning = (
        "Not recommended on this Mac while other memory-heavy apps are open."
        if resources.total_memory_gb < 24
        else "This Mac has enough memory for typical short rewrites."
    )
    if not has_local_llm:
        llm_warning = "Install explicitly with ./install.sh --with-local-llm to enable."
    replacements = escape(current.replacements or "")
    hotkey_options = "".join(
        f'<option value="{value}" {"selected" if current.hotkey_preset == value else ""}>{escape(label)}</option>'
        for value, label in HOTKEY_PRESET_LABELS.items()
    )
    secondary_hotkey_options = (
        '<option value="none">Disabled — primary shortcut only</option>'
        + "".join(
            f'<option value="{value}" {"selected" if current.secondary_hotkey_preset == value else ""}>{escape(label)}</option>'
            for value, label in HOTKEY_PRESET_LABELS.items()
        )
    )
    gain_options = "".join(
        f'<option value="{value:.1f}" {"selected" if current.microphone_gain == value else ""}>{label}</option>'
        for value, label in (
            (1.0, "Standard — 1×"),
            (2.0, "Sensitive — 2× (Default)"),
            (4.0, "High — 4×"),
            (8.0, "Very high — 8×"),
        )
    )
    device_options = "".join(
        f'<option value="{device.id}" {"selected" if current.input_device == device.id else ""}>{escape(device.name)}</option>'
        for device in (input_devices or [])
    )
    openai_model_labels = {
        "gpt-transcribe": "GPT Transcribe — recommended for recorded audio",
        "gpt-4o-transcribe": "GPT-4o Transcribe — highest accuracy",
        "gpt-4o-mini-transcribe": "GPT-4o Mini Transcribe — faster / lower cost",
        "gpt-4o-transcribe-diarize": "GPT-4o Transcribe Diarize — speaker labels",
        "whisper-1": "Whisper-1 — legacy / timestamps",
    }
    openai_model_options = "".join(
        f'<option value="{model}" {"selected" if current.openai_model == model else ""}>{escape(openai_model_labels[model])}</option>'
        for model in OPENAI_TRANSCRIPTION_MODELS
    )
    elevenlabs_model_labels = {
        "scribe_v2": "Scribe v2 — recorded audio / maximum accuracy",
        "scribe_v2_realtime": "Scribe v2 Realtime — WebSocket streaming",
        "scribe_v1": "Scribe v1 — legacy",
    }
    elevenlabs_model_options = "".join(
        f'<option value="{model}" {"selected" if current.elevenlabs_model == model else ""}>{escape(elevenlabs_model_labels[model])}</option>'
        for model in ELEVENLABS_TRANSCRIPTION_MODELS
    )
    rewrite_engine_options = "".join(
        f'<option value="{value}" {"selected" if rewrite_engine == value else ""} {"disabled" if value == "local" and not has_local_llm else ""}>{escape(label)}</option>'
        for value, label in (
            ("off", "Off — transcription only"),
            ("local", "Local Llama 3B — free & private"),
            ("openai", "OpenAI API — automatic tone"),
            ("openrouter", "OpenRouter — free models"),
            ("chatgpt", "ChatGPT subscription — rephrasing only"),
        )
    )
    openai_rewrite_labels = {
        "gpt-5-nano": "GPT-5 Nano — cheapest",
        "gpt-5-mini": "GPT-5 Mini — balanced value",
        "gpt-5.6-luna": "GPT-5.6 Luna — fast",
        "gpt-5.6-terra": "GPT-5.6 Terra — balanced",
        "gpt-5.6-sol": "GPT-5.6 Sol — maximum quality",
    }
    chatgpt_options = '<option value="">Automatic — account default</option>' + "".join(
        f'<option value="{escape(model["slug"], quote=True)}" {"selected" if current.chatgpt_rewrite_model == model["slug"] else ""}>{escape(model["display_name"])}</option>'
        for model in current.chatgpt_models
    )
    if current.chatgpt_rewrite_model and current.chatgpt_rewrite_model not in {model["slug"] for model in current.chatgpt_models}:
        chatgpt_options += f'<option selected value="{escape(current.chatgpt_rewrite_model, quote=True)}">{escape(current.chatgpt_rewrite_model)} — unavailable</option>'
    openai_rewrite_options = "".join(
        f'<option value="{model}" {"selected" if current.openai_rewrite_model == model else ""}>{escape(openai_rewrite_labels[model])}</option>'
        for model in OPENAI_REWRITE_MODELS
    )
    error_banner = (
        f'<div class="error" role="alert">{escape(error_message)}</div>'
        if error_message
        else ""
    )
    page_title = "Settings" if runtime else "Launch Settings"
    heading = "Update how your voice is transcribed." if runtime else "Choose how your voice is transcribed."
    action_note = (
        "Changes are saved securely and RedWhisper restarts automatically."
        if runtime
        else "Settings are saved on this Mac and restored at the next launch. Secrets remain in Keychain."
    )
    action_label = "Save Settings" if runtime else "Save &amp; Start RedWhisper"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RedWhisper · {page_title}</title>
<style>
:root {{ color-scheme: dark; --bg:#0a0d12; --panel:#121821; --line:#273241; --text:#f4f7fb; --muted:#9dacbe; --accent:#77e1b5; --blue:#79a9ff; --warn:#f5c26b; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; min-height:100vh; background:radial-gradient(circle at 15% 0%, #182639 0, transparent 38%), var(--bg); color:var(--text); font:15px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text",sans-serif; }}
main {{ width:min(920px, calc(100% - 32px)); margin:0 auto; padding:52px 0 64px; }}
.eyebrow {{ color:var(--accent); font-size:12px; font-weight:700; letter-spacing:.16em; text-transform:uppercase; }}
h1 {{ margin:8px 0 6px; font-size:clamp(32px,6vw,54px); letter-spacing:-.045em; line-height:1.02; }}
.intro {{ color:var(--muted); font-size:17px; max-width:680px; margin:0 0 30px; }}
.machine {{ display:flex; gap:16px; flex-wrap:wrap; background:#0f151e; border:1px solid var(--line); border-radius:16px; padding:16px 18px; margin-bottom:24px; }}
.machine strong {{ color:var(--text); }} .machine span {{ color:var(--muted); }}
fieldset {{ border:0; padding:0; margin:0 0 26px; }} legend {{ font-size:13px; font-weight:700; color:var(--muted); text-transform:uppercase; letter-spacing:.09em; margin-bottom:12px; }}
.models {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; }}
.card {{ display:block; position:relative; background:linear-gradient(145deg,#151d28,#10161f); border:1px solid var(--line); border-radius:18px; padding:20px; cursor:pointer; min-height:206px; transition:.18s ease; }}
.card:hover {{ border-color:#42546a; transform:translateY(-1px); }}
.card:has(input:checked) {{ border-color:var(--accent); box-shadow:0 0 0 1px var(--accent), 0 14px 42px #0007; }}
.card:has(input:disabled) {{ opacity:.55; cursor:not-allowed; transform:none; }}
.card input[type=radio] {{ position:absolute; top:19px; right:18px; width:19px; height:19px; accent-color:var(--accent); }}
.badge {{ display:inline-block; border:1px solid #3a4b60; border-radius:999px; padding:3px 9px; color:var(--muted); font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.06em; }}
.badge.local {{ color:var(--accent); border-color:#39745e; }} .badge.cloud {{ color:var(--blue); border-color:#3d5f92; }}
.card h2 {{ font-size:21px; letter-spacing:-.025em; margin:14px 34px 5px 0; }} .card p {{ color:var(--muted); margin:0 0 16px; }}
.facts {{ display:grid; gap:6px; font-size:13px; }} .facts b {{ color:var(--text); }}
.options {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; }}
.option {{ background:var(--panel); border:1px solid var(--line); border-radius:15px; padding:16px; }}
.option label {{ display:flex; gap:10px; align-items:flex-start; font-weight:650; }} .option input {{ margin-top:4px; accent-color:var(--accent); }}
.option small {{ display:block; color:var(--muted); margin:7px 0 0 26px; }} .option .warning {{ color:var(--warn); }}
.option select {{ width:100%; margin-top:10px; border:1px solid #354357; border-radius:10px; background:#0b1017; color:var(--text); padding:10px 12px; font:inherit; }}
.fields {{ display:grid; grid-template-columns:160px 1fr; gap:12px 16px; align-items:center; background:var(--panel); border:1px solid var(--line); border-radius:15px; padding:18px; margin-top:14px; }}
.fields label {{ font-weight:650; }} input[type=text] {{ width:100%; border:1px solid #354357; border-radius:10px; background:#0b1017; color:var(--text); padding:11px 12px; font:inherit; }} input[type=text]:focus {{ outline:2px solid #477a69; border-color:var(--accent); }}
.fields input[type=password] {{ width:100%; border:1px solid #354357; border-radius:10px; background:#0b1017; color:var(--text); padding:11px 12px; font:inherit; }} .error {{ border:1px solid #934c51; background:#31191d; color:#ffb8bd; border-radius:12px; padding:12px 14px; margin:0 0 18px; }}
.fields select {{ width:100%; border:1px solid #354357; border-radius:10px; background:#0b1017; color:var(--text); padding:11px 12px; font:inherit; }}
.actions {{ display:flex; justify-content:flex-end; align-items:center; gap:18px; margin-top:24px; }} .privacy {{ color:var(--muted); font-size:13px; }}
button {{ appearance:none; border:0; border-radius:12px; background:var(--accent); color:#07110d; font:700 15px/1 -apple-system,sans-serif; padding:15px 22px; cursor:pointer; box-shadow:0 10px 30px #49b88b33; }} button:hover {{ filter:brightness(1.06); }}
@media (max-width:720px) {{ .models,.options {{ grid-template-columns:1fr; }} .fields {{ grid-template-columns:1fr; gap:7px; }} main {{ padding-top:30px; }} .actions {{ align-items:flex-end; flex-direction:column; }} }}
</style>
</head>
<body><main>
<div class="eyebrow">{page_title}</div>
<h1>{heading}</h1>
<p class="intro">Local MLX is the private default. OpenAI and ElevenLabs reduce local compute, but send each selected recording to the chosen cloud provider.</p>
<div class="machine"><strong>{escape(resources.chip)}</strong><span>{resources.total_memory_gb:.0f} GB unified memory</span><span>{escape(resources.local_recommendation)}</span></div>
{error_banner}
<form method="post" action="/start">
<input type="hidden" name="csrf" value="{escape(csrf_token)}">
<fieldset><legend>Transcription engine</legend><div class="models">
<label class="card"><input type="radio" name="engine" value="local" {local_checked}>
<span class="badge local">Local · Default</span><h2>Whisper Large v3 Turbo</h2>
<p>Runs privately on this Mac through Apple MLX.</p>
<div class="facts"><span><b>Audio:</b> never leaves Mac</span><span><b>Memory:</b> about {LOCAL_MODEL_ESTIMATED_GB:.0f} GB peak for short dictation</span><span><b>Compute:</b> Apple GPU + unified memory</span></div></label>
<label class="card"><input type="radio" name="engine" value="openai" {openai_checked}>
<span class="badge cloud">Cloud</span><h2>OpenAI Transcription</h2>
<p>High-quality cloud transcription with low local inference load.</p>
<div class="facts"><span><b>Audio:</b> sent to OpenAI</span><span><b>Memory:</b> minimal local use</span><span><b>Status:</b> {escape(openai_state)}</span></div></label>
<label class="card"><input type="radio" name="engine" value="elevenlabs" {elevenlabs_checked}>
<span class="badge cloud">Cloud</span><h2>ElevenLabs Scribe</h2>
<p>High-accuracy multilingual transcription with low local inference load.</p>
<div class="facts"><span><b>Audio:</b> sent to ElevenLabs</span><span><b>Memory:</b> minimal local use</span><span><b>Status:</b> {escape(elevenlabs_state)}</span></div></label>
</div></fieldset>
<fieldset><legend>Accuracy and rewriting</legend><div class="options">
<div class="option"><label><input type="checkbox" name="maximum_accuracy" {accuracy_checked}>Maximum local accuracy</label><small>Evaluates five candidates. Similar memory use, higher GPU work and latency. Applies only to local Whisper.</small></div>
<div class="option"><label for="rewrite-engine">Automatic restructuring</label><select id="rewrite-engine" name="rewrite_engine">{rewrite_engine_options}</select><select name="openai_rewrite_model" aria-label="OpenAI restructuring model">{openai_rewrite_options}</select><input name="openrouter_model" type="text" value="{escape(current.openrouter_model)}" aria-label="OpenRouter restructuring model" placeholder="openrouter/free"><small>Casual by default. Begin a recording with “professional” to use professional prose. OpenAI and OpenRouter send only the transcript. <code>openrouter/free</code> chooses an available free model; a specific free model can end in <code>:free</code>.</small><small class="warning">Local rewriting adds about {LOCAL_LLM_ADDITIONAL_GB:.1f} GB. {escape(llm_warning)}</small></div>
</div></fieldset>
<fieldset><legend>ChatGPT subscription rephrasing</legend><div class="fields">
<label for="chatgpt-model">Account models</label><select id="chatgpt-model" name="chatgpt_rewrite_model">{chatgpt_options}</select>
<span></span><span>Connect or switch accounts in RedWhisper → Settings → ChatGPT account. Models synchronize from your account. Transcription still uses its selected provider and API billing.</span>
</div></fieldset>
<fieldset><legend>Language and terminology</legend><div class="fields">
<label for="language">Language</label><input id="language" name="language" type="text" value="{escape(current.language)}" placeholder="auto or en">
<label for="openai-model">OpenAI model</label><select id="openai-model" name="openai_model">{openai_model_options}</select>
<label for="elevenlabs-model">ElevenLabs model</label><select id="elevenlabs-model" name="elevenlabs_model">{elevenlabs_model_options}</select>
<label for="replacements">Replacement file</label><input id="replacements" name="replacements" type="text" value="{replacements}" placeholder="Optional path to replacements.json">
{_api_key_control(has_openai_key)}
{_openrouter_api_key_control(has_openrouter_key)}
{_elevenlabs_api_key_control(has_elevenlabs_key)}
</div></fieldset>
<fieldset><legend>Recording controls</legend><div class="fields">
<label for="hotkey-preset">Push-to-talk key</label><select id="hotkey-preset" name="hotkey_preset">{hotkey_options}</select>
<span></span><span class="privacy">Hold to record. Release to stop, transcribe, and insert the text. Fn / Globe is the default single key.</span>
<label for="secondary-hotkey-preset">Secondary key</label><select id="secondary-hotkey-preset" name="secondary_hotkey_preset">{secondary_hotkey_options}</select>
<span></span><span class="privacy">Optional second hold-to-record shortcut for an external keyboard or docking setup.</span>
<label for="microphone-gain">Microphone sensitivity</label><select id="microphone-gain" name="microphone_gain">{gain_options}</select>
<span></span><span class="privacy">Digital gain is safely limited before transcription. Increase it if recordings are reported as too quiet.</span>
<label for="input-device">Microphone</label><select id="input-device" name="input_device">{device_options}</select>
<span></span><span class="privacy">Physical microphones are preferred over virtual or loopback audio devices.</span>
</div></fieldset>
<div class="actions"><span class="privacy">{action_note}</span><button type="submit">{action_label}</button></div>
</form></main></body></html>"""


def _started_page(
    engine: str,
    hotkey_label: str,
    openai_model: str,
    elevenlabs_model: str = "scribe_v2",
    runtime: bool = False,
) -> str:
    openai_labels = {
        "gpt-transcribe": "GPT Transcribe",
        "gpt-4o-transcribe": "GPT-4o Transcribe",
        "gpt-4o-mini-transcribe": "GPT-4o Mini Transcribe",
        "gpt-4o-transcribe-diarize": "GPT-4o Transcribe Diarize",
        "whisper-1": "Whisper-1",
    }
    elevenlabs_labels = {
        "scribe_v2": "ElevenLabs Scribe v2",
        "scribe_v2_realtime": "ElevenLabs Scribe v2 Realtime",
        "scribe_v1": "ElevenLabs Scribe v1",
    }
    if engine == "local":
        label = "Whisper Large v3 Turbo"
    elif engine == "openai":
        label = openai_labels[openai_model]
    else:
        label = elevenlabs_labels[elevenlabs_model]
    heading = "Settings saved." if runtime else "RedWhisper is ready."
    detail = (
        "RedWhisper is restarting in the background. You can close this tab."
        if runtime
        else f"You can close this tab and use <b>{escape(hotkey_label)}</b> to dictate."
    )
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>RedWhisper</title><style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0a0d12;color:#f4f7fb;font:16px -apple-system,sans-serif}}main{{text-align:center;padding:30px}}b{{color:#77e1b5}}p{{color:#9dacbe}}</style><main><h1>{heading}</h1><p><b>{escape(label)}</b> selected. {detail}</p></main></html>"""


def _api_key_control(has_openai_key: bool) -> str:
    if has_openai_key:
        return """<label>OpenAI API key</label><div><strong>Saved securely in macOS Keychain</strong><details><summary>Replace saved key</summary><input id="api-key" name="api_key" type="password" value="" autocomplete="off" placeholder="Enter a replacement key"></details></div>"""
    return """<label for="api-key">OpenAI API key</label><input id="api-key" name="api_key" type="password" value="" autocomplete="off" placeholder="Enter key to save in macOS Keychain">"""


def _openrouter_api_key_control(has_openrouter_key: bool) -> str:
    if has_openrouter_key:
        return """<label>OpenRouter API key</label><div><strong>Saved securely in macOS Keychain</strong><details><summary>Replace saved key</summary><input id="openrouter-api-key" name="openrouter_api_key" type="password" value="" autocomplete="off" placeholder="Enter a replacement key"></details></div>"""
    return """<label for="openrouter-api-key">OpenRouter API key</label><input id="openrouter-api-key" name="openrouter_api_key" type="password" value="" autocomplete="off" placeholder="Enter key to save in macOS Keychain">"""


def _elevenlabs_api_key_control(has_elevenlabs_key: bool) -> str:
    if has_elevenlabs_key:
        return """<label>ElevenLabs API key</label><div><strong>Saved securely in macOS Keychain</strong><details><summary>Replace saved key</summary><input id="elevenlabs-api-key" name="elevenlabs_api_key" type="password" value="" autocomplete="off" placeholder="Enter a replacement key"></details></div>"""
    return """<label for="elevenlabs-api-key">ElevenLabs API key</label><input id="elevenlabs-api-key" name="elevenlabs_api_key" type="password" value="" autocomplete="off" placeholder="Enter key to save in macOS Keychain">"""
    elevenlabs_key_from_environment,
    save_elevenlabs_key,
