from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "windows_usb_recovery.ps1"
TASK_NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"


def task_xml() -> ET.Element:
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    match = re.search(r'\$taskXml = @"\r?\n(.*?)\r?\n"@', script, re.DOTALL)
    if match is None:
        raise AssertionError("Task XML here-string not found")
    text = (
        match.group(1)
        .replace("$action", "powershell.exe")
        .replace("$arguments", "-File &quot;C:\\Example\\repair.ps1&quot;")
    )
    return ET.fromstring(text.encode("utf-16"))


class WindowsUsbRecoveryTaskTests(unittest.TestCase):
    def test_task_xml_uses_task_scheduler_namespace(self) -> None:
        # Task Scheduler rejects any other namespace (HRESULT 0x80041317).
        self.assertEqual(task_xml().tag, f"{{{TASK_NAMESPACE}}}Task")

    def test_task_runs_as_system_on_boot_device_failure_and_resume(self) -> None:
        ns = {"t": TASK_NAMESPACE}
        root = task_xml()

        self.assertEqual(root.findtext("t:Principals/t:Principal/t:UserId", namespaces=ns), "S-1-5-18")
        self.assertIsNotNone(root.find("t:Triggers/t:BootTrigger", ns))
        subscriptions = [
            element.text or ""
            for element in root.findall("t:Triggers/t:EventTrigger/t:Subscription", ns)
        ]
        self.assertTrue(any("EventID=411" in text for text in subscriptions))
        self.assertTrue(any("Power-Troubleshooter" in text for text in subscriptions))
        self.assertEqual(
            root.findtext("t:Settings/t:MultipleInstancesPolicy", namespaces=ns),
            "IgnoreNew",
        )


if __name__ == "__main__":
    unittest.main()
