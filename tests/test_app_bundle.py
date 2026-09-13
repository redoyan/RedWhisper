import plistlib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "RedWhisper.app"


class AppBundleTests(unittest.TestCase):
    def test_bundle_owns_a_regular_dock_application(self) -> None:
        with (APP / "Contents" / "Info.plist").open("rb") as file:
            info = plistlib.load(file)

        self.assertEqual(info["CFBundleExecutable"], "RedWhisperLauncher")
        self.assertEqual(info["CFBundleIconFile"], "RedWhisper.icns")
        self.assertEqual(info["CFBundleIdentifier"], "com.redwhisper.app")
        self.assertEqual(info["CFBundlePackageType"], "APPL")
        self.assertNotIn("LSUIElement", info)
        self.assertIn("NSMicrophoneUsageDescription", info)
        self.assertTrue((APP / "Contents" / "Resources" / "RedWhisper.icns").exists())
        self.assertTrue(
            (APP / "Contents" / "Resources" / "RedWhisper-menu.png").exists()
        )

if __name__ == "__main__":
    unittest.main()
