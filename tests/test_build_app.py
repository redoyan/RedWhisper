import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BuildAppTests(unittest.TestCase):
    def test_launcher_uses_bundle_owned_runtime(self) -> None:
        launcher = (
            ROOT / "RedWhisper.app" / "Contents" / "MacOS" / "RedWhisper"
        ).read_text(encoding="utf-8")

        self.assertIn("../Resources/runtime", launcher)
        self.assertNotIn('PROJECT_DIR=', launcher)

    def test_builder_copies_required_application_modules(self) -> None:
        builder = (ROOT / "build_app.sh").read_text(encoding="utf-8")

        self.assertIn("voxtape.py", builder)
        self.assertIn("mlx_whisper_core.py", builder)
        self.assertIn("launch_gui.py", builder)
        self.assertIn("native_settings.py", builder)
        self.assertIn("RedWhisper-icon.png", builder)
        self.assertIn("RedWhisper-menu.png", builder)
        self.assertIn("RedWhisper.icns", builder)
        self.assertIn('cp -cR "$ROOT/.venv"', builder)


if __name__ == "__main__":
    unittest.main()
