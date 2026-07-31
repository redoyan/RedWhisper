import plistlib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "RedWhisper.app"


class AppBundleTests(unittest.TestCase):
    def test_bundle_is_background_menu_bar_application(self) -> None:
        with (APP / "Contents" / "Info.plist").open("rb") as file:
            info = plistlib.load(file)

        self.assertEqual(info["CFBundleExecutable"], "RedWhisper")
        self.assertEqual(info["CFBundleIconFile"], "RedWhisper.icns")
        self.assertEqual(info["CFBundleIdentifier"], "com.redwhisper.app")
        self.assertEqual(info["CFBundlePackageType"], "APPL")
        self.assertTrue(info["LSUIElement"])
        self.assertIn("NSMicrophoneUsageDescription", info)
        self.assertTrue((APP / "Contents" / "Resources" / "RedWhisper.icns").exists())
        self.assertTrue(
            (APP / "Contents" / "Resources" / "RedWhisper-menu.png").exists()
        )

    def test_launcher_hides_model_output_in_log(self) -> None:
        launcher = APP / "Contents" / "MacOS" / "RedWhisper"
        source = launcher.read_text(encoding="utf-8")

        self.assertTrue(launcher.stat().st_mode & 0o100)
        self.assertIn("Library/Logs/RedWhisper", source)
        self.assertIn('"$PYTHON" "$SCRIPT"', source)
        self.assertIn("--no-launch-gui", source)
        self.assertIn('wait "$CHILD_PID"', source)
        self.assertIn('if [[ "$STATUS" -eq 75 ]]', source)
        self.assertNotIn('exec "$PYTHON"', source)
        self.assertNotIn("Terminal", source)


if __name__ == "__main__":
    unittest.main()
