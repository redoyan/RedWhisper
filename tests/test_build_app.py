import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BuildAppTests(unittest.TestCase):
    def test_launcher_uses_bundle_owned_runtime(self) -> None:
        launcher = (ROOT / "redwhisper_launcher.m").read_text(encoding="utf-8")

        self.assertIn('URLByAppendingPathComponent:@"runtime"', launcher)
        self.assertIn('environment[@"REDWHISPER_BUNDLED"] = @"1"', launcher)
        self.assertIn('environment[@"REDWHISPER_LAUNCHER_PID"]', launcher)
        self.assertIn("AXIsProcessTrustedWithOptions", launcher)
        self.assertIn("requestAccessForMediaType:AVMediaTypeAudio", launcher)
        self.assertIn("RedWhisperRestartExitCode = 75", launcher)
        self.assertIn("Library/Logs/RedWhisper", launcher)

    def test_builder_copies_required_application_modules(self) -> None:
        builder = (ROOT / "build_app.sh").read_text(encoding="utf-8")

        self.assertIn("voxtape.py", builder)
        self.assertIn("mlx_whisper_core.py", builder)
        self.assertIn("launch_gui.py", builder)
        self.assertIn("native_settings.py", builder)
        self.assertIn("chatgpt_subscription.py", builder)
        self.assertIn("RedWhisper-icon.png", builder)
        self.assertIn("RedWhisper-menu.png", builder)
        self.assertIn("RedWhisper.icns", builder)
        self.assertIn('cp -cR "$ROOT/.venv"', builder)
        self.assertIn("redwhisper_launcher.m", builder)
        self.assertIn("-framework Cocoa", builder)
        self.assertIn("-framework AVFoundation", builder)
        self.assertIn("-framework ApplicationServices", builder)


if __name__ == "__main__":
    unittest.main()
