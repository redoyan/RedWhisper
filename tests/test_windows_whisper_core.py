from pathlib import Path
from types import SimpleNamespace
import unittest

from windows_whisper_core import FasterWhisperOptions, FasterWhisperTranscriber


class FakeModel:
    def __init__(self) -> None:
        self.calls = []

    def transcribe(self, path, **options):
        self.calls.append((path, options))
        return iter(
            [
                SimpleNamespace(text=" Hello "),
                SimpleNamespace(text=""),
                SimpleNamespace(text="Windows."),
            ]
        ), object()


class FasterWhisperTranscriberTests(unittest.TestCase):
    def test_cpu_runtime_uses_int8_and_joins_segments(self) -> None:
        factory_calls = []
        model = FakeModel()

        def factory(name, **options):
            factory_calls.append((name, options))
            return model

        transcriber = FasterWhisperTranscriber(
            FasterWhisperOptions(model="tiny", language="en"),
            model_factory=factory,
            cuda_device_count=lambda: 0,
        )

        text = transcriber.transcribe(Path("recording.wav"))

        self.assertEqual(text, "Hello Windows.")
        self.assertEqual(
            factory_calls,
            [("tiny", {"device": "cpu", "compute_type": "int8"})],
        )
        self.assertEqual(model.calls[0][1]["beam_size"], 1)
        self.assertTrue(model.calls[0][1]["vad_filter"])

    def test_cuda_maximum_accuracy_uses_float16_and_beam_five(self) -> None:
        model = FakeModel()
        transcriber = FasterWhisperTranscriber(
            FasterWhisperOptions(model="large-v3-turbo", maximum_accuracy=True),
            model_factory=lambda *_args, **_kwargs: model,
            cuda_device_count=lambda: 1,
        )

        transcriber.transcribe(Path("recording.wav"))

        self.assertEqual(transcriber.device, "cuda")
        self.assertEqual(transcriber.compute_type, "float16")
        self.assertEqual(model.calls[0][1]["beam_size"], 5)
        self.assertTrue(model.calls[0][1]["condition_on_previous_text"])

    def test_warmup_loads_the_model_only_once(self) -> None:
        calls = []
        transcriber = FasterWhisperTranscriber(
            FasterWhisperOptions(model="tiny"),
            model_factory=lambda *_args, **_kwargs: calls.append(True) or FakeModel(),
            cuda_device_count=lambda: 0,
        )

        transcriber.warmup()
        transcriber.warmup()

        self.assertEqual(calls, [True])


if __name__ == "__main__":
    unittest.main()
