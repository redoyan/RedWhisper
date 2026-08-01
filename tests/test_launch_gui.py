import re
import threading
import time
import unittest
from unittest.mock import patch
import urllib.parse
import urllib.request

from audio_devices import InputDevice
from launch_gui import (
    LaunchSelection,
    MacResources,
    _api_key_control,
    _elevenlabs_api_key_control,
    _openrouter_api_key_control,
    _settings_page,
    _started_page,
    configure_in_browser,
)


class LaunchGUIPageTests(unittest.TestCase):
    def test_saved_api_key_is_not_requested_again(self) -> None:
        control = _api_key_control(has_openai_key=True)
        self.assertIn("Saved securely in macOS Keychain", control)
        self.assertIn("Replace saved key", control)
        self.assertNotIn("Enter key to save", control)

    def test_saved_openrouter_key_is_not_requested_again(self) -> None:
        control = _openrouter_api_key_control(has_openrouter_key=True)
        self.assertIn("Saved securely in macOS Keychain", control)
        self.assertIn("Replace saved key", control)
        self.assertNotIn("Enter key to save", control)

    def test_saved_elevenlabs_key_is_not_requested_again(self) -> None:
        control = _elevenlabs_api_key_control(has_elevenlabs_key=True)
        self.assertIn("Saved securely in macOS Keychain", control)
        self.assertIn("Replace saved key", control)
        self.assertNotIn("Enter key to save", control)

    def test_page_explains_local_cloud_and_compute_tradeoffs(self) -> None:
        page = _settings_page(
            MacResources(total_memory_gb=16, chip="Apple M4 Pro"),
            LaunchSelection("local", "auto", False, None, False, input_device=1),
            "csrf-token",
            has_openai_key=False,
            has_local_llm=False,
            input_devices=[
                InputDevice(1, "MacBook Pro Microphone", 1),
                InputDevice(3, "Virtual Desktop Mic", 2),
            ],
        )

        self.assertIn("Whisper Large v3 Turbo", page)
        self.assertIn("GPT-4o Transcribe", page)
        self.assertIn("GPT-4o Mini Transcribe", page)
        self.assertIn("GPT Transcribe", page)
        self.assertIn("GPT-4o Transcribe Diarize", page)
        self.assertIn("Whisper-1", page)
        self.assertIn("ElevenLabs Scribe", page)
        self.assertIn("Scribe v2 Realtime", page)
        self.assertIn("Scribe v1", page)
        self.assertNotIn("Eleven v3", page)
        self.assertIn('name="elevenlabs_model"', page)
        self.assertIn('name="openai_model"', page)
        self.assertIn("16 GB unified memory", page)
        self.assertIn("Audio:</b> never leaves Mac", page)
        self.assertIn('name="api_key" type="password"', page)
        self.assertNotIn('value="openai"  disabled', page)
        self.assertIn("--with-local-llm", page)
        self.assertIn('name="hotkey_preset"', page)
        self.assertIn('name="secondary_hotkey_preset"', page)
        self.assertIn("Fn / Globe — single key", page)
        self.assertIn('name="microphone_gain"', page)
        self.assertIn("MacBook Pro Microphone", page)
        self.assertIn('name="input_device"', page)
        self.assertIn("Settings are saved on this Mac", page)
        self.assertIn("Automatic restructuring", page)
        self.assertIn('name="rewrite_engine"', page)
        self.assertIn('name="openai_rewrite_model"', page)
        self.assertIn('value="openrouter"', page)
        self.assertIn('name="openrouter_model"', page)
        self.assertIn('name="openrouter_api_key"', page)
        self.assertIn('name="elevenlabs_api_key"', page)
        self.assertIn("openrouter/free", page)
        self.assertIn("Casual by default", page)

    def test_runtime_settings_page_explains_automatic_restart(self) -> None:
        page = _settings_page(
            MacResources(total_memory_gb=16, chip="Apple M4 Pro"),
            LaunchSelection("local", "en", False, None, True, input_device=1),
            "csrf-token",
            has_openai_key=True,
            has_local_llm=True,
            input_devices=[InputDevice(1, "MacBook Pro Microphone", 1)],
            runtime=True,
        )
        completed = _started_page(
            "local", "Fn / Globe", "gpt-4o-transcribe", runtime=True
        )

        self.assertIn("Save Settings", page)
        self.assertIn("restarts automatically", page)
        self.assertNotIn("Save &amp; Start RedWhisper", page)
        self.assertIn("restarting in the background", completed)

    def test_local_form_submission_returns_selection(self) -> None:
        request_errors = []

        def submit_form(url: str) -> bool:
            def worker() -> None:
                try:
                    for _ in range(50):
                        try:
                            page = urllib.request.urlopen(url, timeout=1).read().decode()
                            break
                        except OSError:
                            time.sleep(0.01)
                    else:
                        raise RuntimeError("settings server did not start")
                    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
                    body = urllib.parse.urlencode(
                        {
                            "csrf": csrf,
                            "engine": "local",
                            "language": "en",
                            "maximum_accuracy": "on",
                            "replacements": "/tmp/replacements.json",
                            "hotkey_preset": "control_space",
                            "secondary_hotkey_preset": "option_space",
                            "microphone_gain": "4.0",
                            "input_device": "1",
                        }
                    ).encode()
                    urllib.request.urlopen(
                        urllib.request.Request(url + "start", data=body, method="POST"),
                        timeout=2,
                    ).read()
                except Exception as error:
                    request_errors.append(error)

            threading.Thread(target=worker, daemon=True).start()
            return True

        with patch("webbrowser.open", side_effect=submit_form), patch.dict(
            "os.environ", {}, clear=True
        ):
            selection = configure_in_browser(
                engine="local",
                language="auto",
                maximum_accuracy=False,
                replacements=None,
                post_process_local=False,
                input_devices=[
                    InputDevice(1, "MacBook Pro Microphone", 1),
                    InputDevice(3, "Virtual Desktop Mic", 2),
                ],
                input_device=1,
            )

        self.assertEqual(request_errors, [])
        self.assertEqual(selection.engine, "local")
        self.assertEqual(selection.language, "en")
        self.assertTrue(selection.maximum_accuracy)
        self.assertEqual(selection.replacements, "/tmp/replacements.json")
        self.assertEqual(selection.hotkey_preset, "control_space")
        self.assertEqual(selection.secondary_hotkey_preset, "option_space")
        self.assertEqual(selection.microphone_gain, 4.0)
        self.assertEqual(selection.input_device, 1)

    def test_api_key_can_be_saved_while_selecting_openai(self) -> None:
        saved_keys = []
        request_errors = []

        def submit_form(url: str) -> bool:
            def worker() -> None:
                try:
                    for _ in range(50):
                        try:
                            page = urllib.request.urlopen(url, timeout=1).read().decode()
                            break
                        except OSError:
                            time.sleep(0.01)
                    else:
                        raise RuntimeError("settings server did not start")
                    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
                    body = urllib.parse.urlencode(
                        {
                            "csrf": csrf,
                            "engine": "openai",
                            "language": "en",
                            "api_key": "test-openai-key",
                            "openai_model": "gpt-4o-mini-transcribe",
                            "rewrite_engine": "openai",
                            "openai_rewrite_model": "gpt-5-nano",
                        }
                    ).encode()
                    urllib.request.urlopen(
                        urllib.request.Request(url + "start", data=body, method="POST"),
                        timeout=2,
                    ).read()
                except Exception as error:
                    request_errors.append(error)

            threading.Thread(target=worker, daemon=True).start()
            return True

        with patch("webbrowser.open", side_effect=submit_form), patch(
            "launch_gui.openai_key_from_environment", return_value=""
        ), patch("launch_gui.save_openai_key", side_effect=saved_keys.append), patch.dict(
            "os.environ", {}, clear=True
        ):
            selection = configure_in_browser(
                engine="local",
                language="auto",
                maximum_accuracy=False,
                replacements=None,
                post_process_local=False,
            )

        self.assertEqual(request_errors, [])
        self.assertEqual(saved_keys, ["test-openai-key"])
        self.assertEqual(selection.engine, "openai")
        self.assertEqual(selection.openai_model, "gpt-4o-mini-transcribe")
        self.assertTrue(selection.post_process_openai)
        self.assertEqual(selection.openai_rewrite_model, "gpt-5-nano")

    def test_elevenlabs_key_can_be_saved_while_selecting_scribe(self) -> None:
        saved_keys = []
        request_errors = []

        def submit_form(url: str) -> bool:
            def worker() -> None:
                try:
                    for _ in range(50):
                        try:
                            page = urllib.request.urlopen(url, timeout=1).read().decode()
                            break
                        except OSError:
                            time.sleep(0.01)
                    else:
                        raise RuntimeError("settings server did not start")
                    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
                    body = urllib.parse.urlencode(
                        {
                            "csrf": csrf,
                            "engine": "elevenlabs",
                            "language": "en",
                            "elevenlabs_api_key": "test-eleven-key",
                            "elevenlabs_model": "scribe_v2",
                        }
                    ).encode()
                    urllib.request.urlopen(
                        urllib.request.Request(url + "start", data=body, method="POST"),
                        timeout=2,
                    ).read()
                except Exception as error:
                    request_errors.append(error)

            threading.Thread(target=worker, daemon=True).start()
            return True

        with patch("webbrowser.open", side_effect=submit_form), patch(
            "launch_gui.openai_key_from_environment", return_value=""
        ), patch(
            "launch_gui.openrouter_key_from_environment", return_value=""
        ), patch(
            "launch_gui.elevenlabs_key_from_environment", return_value=""
        ), patch(
            "launch_gui.save_elevenlabs_key", side_effect=saved_keys.append
        ):
            selection = configure_in_browser(
                engine="local",
                language="auto",
                maximum_accuracy=False,
                replacements=None,
                post_process_local=False,
            )

        self.assertEqual(request_errors, [])
        self.assertEqual(saved_keys, ["test-eleven-key"])
        self.assertEqual(selection.engine, "elevenlabs")
        self.assertEqual(selection.elevenlabs_model, "scribe_v2")

    def test_openrouter_key_and_model_can_be_saved_and_selected(self) -> None:
        saved_keys = []
        request_errors = []

        def submit_form(url: str) -> bool:
            def worker() -> None:
                try:
                    for _ in range(50):
                        try:
                            page = urllib.request.urlopen(url, timeout=1).read().decode()
                            break
                        except OSError:
                            time.sleep(0.01)
                    else:
                        raise RuntimeError("settings server did not start")
                    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
                    body = urllib.parse.urlencode(
                        {
                            "csrf": csrf,
                            "engine": "local",
                            "language": "en",
                            "openrouter_api_key": "test-router-key",
                            "rewrite_engine": "openrouter",
                            "openrouter_model": "openrouter/free",
                        }
                    ).encode()
                    urllib.request.urlopen(
                        urllib.request.Request(url + "start", data=body, method="POST"),
                        timeout=2,
                    ).read()
                except Exception as error:
                    request_errors.append(error)

            threading.Thread(target=worker, daemon=True).start()
            return True

        with patch("webbrowser.open", side_effect=submit_form), patch(
            "launch_gui.openrouter_key_from_environment", return_value=""
        ), patch(
            "launch_gui.save_openrouter_key", side_effect=saved_keys.append
        ), patch.dict("os.environ", {}, clear=True):
            selection = configure_in_browser(
                engine="local",
                language="auto",
                maximum_accuracy=False,
                replacements=None,
                post_process_local=False,
            )

        self.assertEqual(request_errors, [])
        self.assertEqual(saved_keys, ["test-router-key"])
        self.assertTrue(selection.post_process_openrouter)
        self.assertFalse(selection.post_process_openai)
        self.assertEqual(selection.openrouter_model, "openrouter/free")


if __name__ == "__main__":
    unittest.main()
