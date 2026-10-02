import json
from pathlib import Path
import tempfile
import unittest

from settings_store import AppSettings, SettingsStore


class SettingsStoreTests(unittest.TestCase):
    def test_round_trip_preserves_launch_choices(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            store = SettingsStore(path)
            expected = AppSettings(
                engine="elevenlabs",
                language="en",
                maximum_accuracy=True,
                replacements="/tmp/replacements.json",
                post_process_openai=True,
                hotkey_preset="option_space",
                secondary_hotkey_preset="control_space",
                microphone_gain=4.0,
                input_device=1,
                input_device_name="MacBook Pro Microphone",
                openai_model="gpt-4o-mini-transcribe",
                elevenlabs_model="scribe_v2",
                openai_rewrite_model="gpt-5-nano",
                openrouter_model="openrouter/free",
                openrouter_models=(
                    "openrouter/free",
                    "meta-llama/llama-3.3-70b-instruct:free",
                ),
            )
            store.save(expected)
            loaded = store.load()

            self.assertEqual(loaded, expected)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["openrouter_models"],
                list(expected.openrouter_models),
            )
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_missing_or_invalid_settings_fall_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            store = SettingsStore(path)
            self.assertEqual(store.load(), AppSettings())
            path.write_text(json.dumps({"engine": "invalid"}), encoding="utf-8")
            self.assertEqual(store.load(), AppSettings())

    def test_api_key_is_not_part_of_persisted_settings(self) -> None:
        self.assertNotIn("api_key", AppSettings.__dataclass_fields__)

    def test_subscription_round_trip_accepts_new_models_without_api_allowlist(self):
        settings = AppSettings.from_dict({"engine": "openai", "post_process_chatgpt": True, "chatgpt_rewrite_model": "future-account-model"})
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")
            store.save(settings)
            self.assertEqual(store.load(), settings)
        with self.assertRaises(ValueError):
            AppSettings(post_process_chatgpt=True, post_process_openai=True).validate()
        for field in ("access_token", "refresh_token", "id_token"):
            self.assertNotIn(field, AppSettings.__dataclass_fields__)

    def test_all_documented_cloud_transcription_models_are_valid(self) -> None:
        for openai_model in (
            "gpt-transcribe",
            "gpt-4o-transcribe",
            "gpt-4o-mini-transcribe",
            "gpt-4o-transcribe-diarize",
            "whisper-1",
        ):
            AppSettings(openai_model=openai_model).validate()
        for elevenlabs_model in ("scribe_v2", "scribe_v2_realtime", "scribe_v1"):
            AppSettings(elevenlabs_model=elevenlabs_model).validate()

    def test_primary_and_secondary_shortcuts_must_differ(self) -> None:
        with self.assertRaises(ValueError):
            AppSettings(
                hotkey_preset="fn", secondary_hotkey_preset="fn"
            ).validate()

    def test_only_one_restructuring_engine_can_be_enabled(self) -> None:
        with self.assertRaises(ValueError):
            AppSettings(
                post_process_local=True,
                post_process_openrouter=True,
            ).validate()

    def test_legacy_mlx_whisper_settings_are_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = root / "RedWhisper" / "settings.json"
            legacy = root / "MLX Whisper" / "settings.json"
            legacy.parent.mkdir(parents=True)
            legacy.write_text(
                json.dumps({"engine": "openai", "language": "en"}),
                encoding="utf-8",
            )

            loaded = SettingsStore(current, legacy_path=legacy).load()

            self.assertEqual(loaded.engine, "openai")
            self.assertTrue(current.exists())


if __name__ == "__main__":
    unittest.main()
