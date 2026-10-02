import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import urllib.error
import sys
import threading
import wave

from mlx_whisper_core import (
    ConfigurationError,
    ELEVENLABS_KEYCHAIN_ACCOUNT,
    ELEVENLABS_KEYCHAIN_SERVICE,
    ElevenLabsTranscriber,
    ElevenLabsTranscriptionOptions,
    LocalWhisperOptions,
    LocalLLMPostProcessor,
    MLXWhisperTranscriber,
    OpenAITranscriber,
    OpenAITranscriptionOptions,
    OpenAITextPostProcessor,
    OpenAIRewriteOptions,
    OpenRouterTextPostProcessor,
    OpenRouterRewriteOptions,
    OPENAI_KEYCHAIN_ACCOUNT,
    OPENAI_KEYCHAIN_SERVICE,
    OPENROUTER_KEYCHAIN_ACCOUNT,
    OPENROUTER_KEYCHAIN_SERVICE,
    TermReplacer,
    TranscriptionError,
    TranscriptPipeline,
    elevenlabs_key_from_environment,
    openai_key_from_environment,
    openrouter_key_from_environment,
    save_openai_key,
    save_openrouter_key,
    save_elevenlabs_key,
)


class FakeTranscriber:
    def __init__(self, text: str) -> None:
        self.text = text

    def transcribe(self, _audio_path: Path) -> str:
        return self.text


class FakePostProcessor:
    def process(self, text: str) -> str:
        return f"rewritten: {text}"


class MLXWhisperTranscriberTests(unittest.TestCase):
    def test_default_mode_uses_turbo_fallback_decoding(self) -> None:
        captured = {}

        def fake_transcribe(path, **kwargs):
            captured.update(path=path, **kwargs)
            return {"text": " hello "}

        transcriber = MLXWhisperTranscriber(
            LocalWhisperOptions(language="en"), transcribe_fn=fake_transcribe
        )
        result = transcriber.transcribe(Path("speech.wav"))

        self.assertEqual(result, "hello")
        self.assertEqual(captured["language"], "en")
        self.assertEqual(captured["condition_on_previous_text"], False)
        self.assertNotIn("beam_size", captured)

    def test_maximum_accuracy_mode_uses_best_of_five(self) -> None:
        captured = {}

        def fake_transcribe(_path, **kwargs):
            captured.update(kwargs)
            return {"text": "hello"}

        transcriber = MLXWhisperTranscriber(
            LocalWhisperOptions(maximum_accuracy=True), transcribe_fn=fake_transcribe
        )
        transcriber.transcribe(Path("speech.wav"))

        self.assertEqual(captured["temperature"], 0.2)
        self.assertEqual(captured["best_of"], 5)
        self.assertEqual(captured["condition_on_previous_text"], True)


class TermReplacerTests(unittest.TestCase):
    def test_longest_terms_are_replaced_first_case_insensitively(self) -> None:
        replacer = TermReplacer({"mlx": "MLX", "mlx whisper": "MLX Whisper"})
        self.assertEqual(
            replacer.process("mlx whisper runs on mlx"),
            "MLX Whisper runs on MLX",
        )

    def test_json_file_must_be_an_object(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "replacements.json"
            path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                TermReplacer.from_json(path)


class LocalLLMPostProcessorTests(unittest.TestCase):
    def test_model_loading_and_generation_use_same_dedicated_thread(self) -> None:
        thread_ids = []
        generation_limits = []
        mlx_lm_module = types.ModuleType("mlx_lm")

        class FakeTokenizer:
            def apply_chat_template(self, _messages, **_kwargs):
                thread_ids.append(("template", threading.get_ident()))
                return "prompt"

        def fake_load(_model_name):
            thread_ids.append(("load", threading.get_ident()))
            return object(), FakeTokenizer()

        def fake_generate(_model, _tokenizer, **_kwargs):
            thread_ids.append(("generate", threading.get_ident()))
            generation_limits.append(_kwargs["max_tokens"])
            return "Professionally rewritten text."

        mlx_lm_module.load = fake_load
        mlx_lm_module.generate = fake_generate
        main_thread_id = threading.get_ident()

        with patch.dict(sys.modules, {"mlx_lm": mlx_lm_module}):
            processor = LocalLLMPostProcessor("test-model")
            processor.warmup()
            result = processor.process("spoken text")
            second_result = processor.process("more spoken text")

        worker_thread_ids = {thread_id for _, thread_id in thread_ids}
        self.assertEqual(result, "Professionally rewritten text.")
        self.assertEqual(second_result, "Professionally rewritten text.")
        self.assertEqual(len(worker_thread_ids), 1)
        self.assertNotIn(main_thread_id, worker_thread_ids)
        self.assertEqual([event for event, _ in thread_ids].count("load"), 1)
        self.assertEqual(generation_limits, [1, 768, 768])

    def test_implausibly_short_rewrite_falls_back_to_full_transcript(self) -> None:
        mlx_lm_module = types.ModuleType("mlx_lm")

        class FakeTokenizer:
            def apply_chat_template(self, _messages, **_kwargs):
                return "prompt"

        mlx_lm_module.load = lambda _model_name: (object(), FakeTokenizer())
        mlx_lm_module.generate = lambda _model, _tokenizer, **_kwargs: "Okay."
        transcript = "This complete spoken transcript contains several important words and details."

        with patch.dict(sys.modules, {"mlx_lm": mlx_lm_module}):
            processor = LocalLLMPostProcessor("test-model")
            result = processor.process(transcript)

        self.assertEqual(result, transcript)

    def test_local_rewrite_uses_casual_default_and_spoken_professional_tone(self) -> None:
        mlx_lm_module = types.ModuleType("mlx_lm")

        class FakeTokenizer:
            def __init__(self):
                self.system_messages = []
                self.user_messages = []

            def apply_chat_template(self, messages, **_kwargs):
                self.system_messages.append(messages[0]["content"])
                self.user_messages.append(messages[1]["content"])
                return "prompt"

        tokenizer = FakeTokenizer()
        mlx_lm_module.load = lambda _model_name: (object(), tokenizer)
        mlx_lm_module.generate = lambda _model, _tokenizer, **_kwargs: (
            "The release is ready for review."
        )

        with patch.dict(sys.modules, {"mlx_lm": mlx_lm_module}):
            processor = LocalLLMPostProcessor("test-model")
            processor.process("The release is ready for review.")
            result = processor.process(
                "Professional, the release is ready for review."
            )

        self.assertEqual(result, "The release is ready for review.")
        self.assertIn("casual written language", tokenizer.system_messages[0])
        self.assertIn("professional written prose", tokenizer.system_messages[1])
        self.assertNotIn("Professional,", tokenizer.user_messages[1])

    def test_assistant_reply_falls_back_to_full_transcript(self) -> None:
        mlx_lm_module = types.ModuleType("mlx_lm")

        class FakeTokenizer:
            def apply_chat_template(self, messages, **_kwargs):
                self.messages = messages
                return "prompt"

        tokenizer = FakeTokenizer()
        mlx_lm_module.load = lambda _model_name: (object(), tokenizer)
        mlx_lm_module.generate = lambda _model, _tokenizer, **_kwargs: (
            "Certainly! I can help you prepare that detailed report tomorrow morning."
        )
        transcript = (
            "The detailed project report needs to be prepared and sent tomorrow morning."
        )

        with patch.dict(sys.modules, {"mlx_lm": mlx_lm_module}):
            processor = LocalLLMPostProcessor("test-model")
            result = processor.process(transcript)

        self.assertEqual(result, transcript)
        self.assertIn("<transcript>", tokenizer.messages[1]["content"])
        self.assertIn("never answer", tokenizer.messages[0]["content"])

    def test_known_unspoken_preamble_is_removed_from_rewrite(self) -> None:
        mlx_lm_module = types.ModuleType("mlx_lm")

        class FakeTokenizer:
            def apply_chat_template(self, _messages, **_kwargs):
                return "prompt"

        mlx_lm_module.load = lambda _model_name: (object(), FakeTokenizer())
        mlx_lm_module.generate = lambda _model, _tokenizer, **_kwargs: (
            "It is worth noting that the event in question is currently unfolding, "
            "and it can be observed by those present.\n\n"
            "The release remains on schedule, and the final review begins tomorrow."
        )
        transcript = (
            "The release is still on schedule and the final review starts tomorrow."
        )

        with patch.dict(sys.modules, {"mlx_lm": mlx_lm_module}):
            processor = LocalLLMPostProcessor("test-model")
            result = processor.process(transcript)

        self.assertEqual(
            result,
            "The release remains on schedule, and the final review begins tomorrow.",
        )

    def test_known_preamble_is_preserved_when_it_was_spoken(self) -> None:
        mlx_lm_module = types.ModuleType("mlx_lm")

        class FakeTokenizer:
            def apply_chat_template(self, _messages, **_kwargs):
                return "prompt"

        transcript = (
            "It is worth noting that the event in question is currently unfolding, "
            "and it can be observed by those present."
        )
        mlx_lm_module.load = lambda _model_name: (object(), FakeTokenizer())
        mlx_lm_module.generate = lambda _model, _tokenizer, **_kwargs: transcript

        with patch.dict(sys.modules, {"mlx_lm": mlx_lm_module}):
            processor = LocalLLMPostProcessor("test-model")
            result = processor.process(transcript)

        self.assertEqual(result, transcript)

    def test_spoken_request_bypasses_generative_rewrite(self) -> None:
        mlx_lm_module = types.ModuleType("mlx_lm")
        generated = []
        mlx_lm_module.load = lambda _model_name: self.fail("request loaded model")
        mlx_lm_module.generate = lambda *_args, **_kwargs: generated.append(True)
        transcript = "Please prepare the project report and send it tomorrow morning."

        with patch.dict(sys.modules, {"mlx_lm": mlx_lm_module}):
            processor = LocalLLMPostProcessor("test-model")
            result = processor.process(transcript)

        self.assertEqual(result, transcript)
        self.assertEqual(generated, [])

    def test_question_changed_into_advice_falls_back_to_transcript(self) -> None:
        mlx_lm_module = types.ModuleType("mlx_lm")

        class FakeTokenizer:
            def apply_chat_template(self, _messages, **_kwargs):
                return "prompt"

        mlx_lm_module.load = lambda _model_name: (object(), FakeTokenizer())
        mlx_lm_module.generate = lambda _model, _tokenizer, **_kwargs: (
            "It would be prudent to notify the customer about the delay today."
        )
        transcript = "What should we do about the delay, and should we tell the customer?"

        with patch.dict(sys.modules, {"mlx_lm": mlx_lm_module}):
            processor = LocalLLMPostProcessor("test-model")
            result = processor.process(transcript)

        self.assertEqual(result, transcript)


class OpenAITranscriberTests(unittest.TestCase):
    def test_api_key_is_required_for_explicit_cloud_mode(self) -> None:
        with self.assertRaises(ConfigurationError):
            OpenAITranscriber(OpenAITranscriptionOptions(api_key=""))

    def test_only_supported_openai_transcription_models_are_allowed(self) -> None:
        for model in (
            "gpt-transcribe",
            "gpt-4o-transcribe",
            "gpt-4o-mini-transcribe",
            "gpt-4o-transcribe-diarize",
            "whisper-1",
        ):
            OpenAITranscriber(
                OpenAITranscriptionOptions(api_key="test-key", model=model)
            )
        with self.assertRaises(ConfigurationError):
            OpenAITranscriber(
                OpenAITranscriptionOptions(api_key="test-key", model="unknown")
            )

    def test_gpt_transcribe_uses_plural_language_hint(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"text":"Hello from GPT Transcribe."}'

        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "speech.wav"
            audio_path.write_bytes(b"RIFF-test-audio")
            transcriber = OpenAITranscriber(
                OpenAITranscriptionOptions(
                    api_key="test-key",
                    model="gpt-transcribe",
                    language="en",
                    prompt="Technical vocabulary",
                )
            )
            with patch(
                "urllib.request.urlopen", return_value=FakeResponse()
            ) as urlopen:
                text = transcriber.transcribe(audio_path)

        body = urlopen.call_args.args[0].data
        self.assertIn(b'name="languages[]"', body)
        self.assertNotIn(b'name="language"\r\n', body)
        self.assertIn(b"Technical vocabulary", body)
        self.assertEqual(text, "Hello from GPT Transcribe.")

    def test_diarization_model_requests_compatible_response(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"text":"A: Hello. B: Hi."}'

        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "speech.wav"
            audio_path.write_bytes(b"RIFF-test-audio")
            transcriber = OpenAITranscriber(
                OpenAITranscriptionOptions(
                    api_key="test-key",
                    model="gpt-4o-transcribe-diarize",
                    language="en",
                    prompt="This prompt is unsupported for diarization",
                )
            )
            with patch(
                "urllib.request.urlopen", return_value=FakeResponse()
            ) as urlopen:
                transcriber.transcribe(audio_path)

        body = urlopen.call_args.args[0].data
        self.assertIn(b"diarized_json", body)
        self.assertIn(b"chunking_strategy", body)
        self.assertIn(b"auto", body)
        self.assertNotIn(b"This prompt is unsupported", body)

    def test_multipart_request_contains_model_audio_and_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "speech.wav"
            audio_path.write_bytes(b"RIFF-test-audio")
            transcriber = OpenAITranscriber(
                OpenAITranscriptionOptions(
                    api_key="test-key",
                    language="en",
                    prompt="Technical vocabulary",
                )
            )
            body = transcriber._multipart_body(
                "test-boundary",
                {
                    "model": "gpt-4o-transcribe",
                    "language": "en",
                    "prompt": "Technical vocabulary",
                },
                audio_path,
            )

        self.assertIn(b"gpt-4o-transcribe", body)
        self.assertIn(b"Technical vocabulary", body)
        self.assertIn(b"RIFF-test-audio", body)

    def test_http_error_does_not_include_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "speech.wav"
            audio_path.write_bytes(b"RIFF")
            transcriber = OpenAITranscriber(
                OpenAITranscriptionOptions(api_key="test-key")
            )
            error = urllib.error.HTTPError(
                "https://api.openai.com/v1/audio/transcriptions",
                401,
                "Unauthorized",
                {},
                None,
            )
            with patch("urllib.request.urlopen", side_effect=error):
                with self.assertRaises(TranscriptionError) as raised:
                    transcriber.transcribe(audio_path)

        self.assertNotIn("test-key", str(raised.exception))

    def test_keychain_storage_is_used_when_environment_key_is_absent(self) -> None:
        stored = {}
        keyring_module = types.ModuleType("keyring")
        errors_module = types.ModuleType("keyring.errors")

        class FakeKeyringError(Exception):
            pass

        errors_module.KeyringError = FakeKeyringError
        keyring_module.set_password = lambda service, account, value: stored.__setitem__(
            (service, account), value
        )
        keyring_module.get_password = lambda service, account: stored.get(
            (service, account)
        )

        with patch.dict(
            sys.modules,
            {"keyring": keyring_module, "keyring.errors": errors_module},
        ), patch.dict("os.environ", {}, clear=True):
            save_openai_key(" secret-key ")
            loaded = openai_key_from_environment()

        self.assertEqual(
            stored[(OPENAI_KEYCHAIN_SERVICE, OPENAI_KEYCHAIN_ACCOUNT)],
            "secret-key",
        )
        self.assertEqual(loaded, "secret-key")


class ElevenLabsTranscriberTests(unittest.TestCase):
    def test_api_key_and_supported_model_are_required(self) -> None:
        with self.assertRaises(ConfigurationError):
            ElevenLabsTranscriber(ElevenLabsTranscriptionOptions(api_key=""))
        with self.assertRaises(ConfigurationError):
            ElevenLabsTranscriber(
                ElevenLabsTranscriptionOptions(api_key="test-key", model="eleven_v3")
            )
        ElevenLabsTranscriber(
            ElevenLabsTranscriptionOptions(api_key="test-key", model="scribe_v1")
        )
        ElevenLabsTranscriber(
            ElevenLabsTranscriptionOptions(
                api_key="test-key", model="scribe_v2_realtime"
            )
        )

    def test_request_uses_scribe_endpoint_key_and_clean_dictation_fields(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"text":"Hello from Scribe."}'

        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "speech.wav"
            audio_path.write_bytes(b"RIFF-test-audio")
            transcriber = ElevenLabsTranscriber(
                ElevenLabsTranscriptionOptions(
                    api_key="eleven-key", language="en"
                )
            )
            with patch(
                "urllib.request.urlopen", return_value=FakeResponse()
            ) as urlopen:
                text = transcriber.transcribe(audio_path)

        request = urlopen.call_args.args[0]
        body = request.data
        self.assertEqual(request.full_url, "https://api.elevenlabs.io/v1/speech-to-text")
        self.assertEqual(request.get_header("Xi-api-key"), "eleven-key")
        self.assertIn(b"scribe_v2", body)
        self.assertIn(b"language_code", body)
        self.assertIn(b"eng", body)
        self.assertIn(b"tag_audio_events", body)
        self.assertIn(b"false", body)
        self.assertIn(b"RIFF-test-audio", body)
        self.assertEqual(text, "Hello from Scribe.")

    def test_realtime_model_streams_pcm_and_returns_committed_text(self) -> None:
        class FakeWebSocket:
            def __init__(self):
                self.sent = []
                self.responses = [
                    json.dumps({"message_type": "session_started"}),
                    json.dumps(
                        {"message_type": "partial_transcript", "text": "Hello"}
                    ),
                    json.dumps(
                        {
                            "message_type": "committed_transcript",
                            "text": "Hello from realtime Scribe.",
                        }
                    ),
                ]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def send(self, message):
                self.sent.append(json.loads(message))

            def recv(self, timeout=None):
                if self.responses:
                    return self.responses.pop(0)
                raise TimeoutError

        websocket = FakeWebSocket()
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "speech.wav"
            with wave.open(str(audio_path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16_000)
                audio.writeframes(b"\x00\x01" * 16_000)
            transcriber = ElevenLabsTranscriber(
                ElevenLabsTranscriptionOptions(
                    api_key="eleven-key",
                    model="scribe_v2_realtime",
                    language="en",
                )
            )
            with patch(
                "websockets.sync.client.connect", return_value=websocket
            ) as connect:
                text = transcriber.transcribe(audio_path)

        url = connect.call_args.args[0]
        self.assertIn("model_id=scribe_v2_realtime", url)
        self.assertIn("audio_format=pcm_16000", url)
        self.assertIn("language_code=en", url)
        self.assertEqual(
            connect.call_args.kwargs["additional_headers"],
            {"xi-api-key": "eleven-key"},
        )
        self.assertEqual(websocket.sent[-1]["commit"], True)
        self.assertEqual(websocket.sent[-1]["message_type"], "input_audio_chunk")
        self.assertEqual(text, "Hello from realtime Scribe.")

    def test_keychain_storage_is_used_when_environment_key_is_absent(self) -> None:
        stored = {}
        keyring_module = types.ModuleType("keyring")
        errors_module = types.ModuleType("keyring.errors")

        class FakeKeyringError(Exception):
            pass

        errors_module.KeyringError = FakeKeyringError
        keyring_module.set_password = lambda service, account, value: stored.__setitem__(
            (service, account), value
        )
        keyring_module.get_password = lambda service, account: stored.get(
            (service, account)
        )
        with patch.dict(
            sys.modules,
            {"keyring": keyring_module, "keyring.errors": errors_module},
        ), patch.dict("os.environ", {}, clear=True):
            save_elevenlabs_key(" eleven-secret ")
            loaded = elevenlabs_key_from_environment()

        self.assertEqual(
            stored[(ELEVENLABS_KEYCHAIN_SERVICE, ELEVENLABS_KEYCHAIN_ACCOUNT)],
            "eleven-secret",
        )
        self.assertEqual(loaded, "eleven-secret")


class OpenAITextPostProcessorTests(unittest.TestCase):
    @staticmethod
    def _response(text: str):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(
                    {
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": text}],
                            }
                        ]
                    }
                ).encode()

        return FakeResponse()

    def test_casual_rewrite_is_the_default(self) -> None:
        processor = OpenAITextPostProcessor(OpenAIRewriteOptions(api_key="test-key"))

        with patch(
            "urllib.request.urlopen",
            return_value=self._response("Hey, the release is ready for review."),
        ) as urlopen:
            result = processor.process(
                "Hey so the release is ready and you can review it now."
            )

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(result, "Hey, the release is ready for review.")
        self.assertIn("casual written language", payload["instructions"])
        self.assertIn("exactly one rewritten version", payload["instructions"])
        self.assertIn(
            "group related ideas into coherent paragraphs",
            payload["instructions"],
        )
        self.assertIn("Hey so the release", payload["input"])
        self.assertFalse(payload["store"])
        self.assertEqual(payload["model"], "gpt-5-nano")
        self.assertEqual(payload["reasoning"]["effort"], "minimal")

    def test_spoken_professional_prefix_selects_tone_and_is_removed(self) -> None:
        processor = OpenAITextPostProcessor(
            OpenAIRewriteOptions(api_key="test-key", model="gpt-5-mini")
        )

        with patch(
            "urllib.request.urlopen",
            return_value=self._response("The release is ready for review and approval."),
        ) as urlopen:
            result = processor.process(
                "Professional, the release is ready for review and approval."
            )

        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(result, "The release is ready for review and approval.")
        self.assertIn("professional written prose", payload["instructions"])
        self.assertNotIn("Professional,", payload["input"])
        self.assertEqual(payload["model"], "gpt-5-mini")

    def test_answer_to_spoken_question_falls_back_to_question(self) -> None:
        processor = OpenAITextPostProcessor(OpenAIRewriteOptions(api_key="test-key"))
        source = "Should we send the customer the revised release date?"

        with patch(
            "urllib.request.urlopen",
            return_value=self._response(
                "Certainly, send the customer the revised release date today."
            ),
        ):
            result = processor.process(source)

        self.assertEqual(result, source)

    def test_question_without_transcribed_punctuation_is_not_answered(self) -> None:
        processor = OpenAITextPostProcessor(OpenAIRewriteOptions(api_key="test-key"))
        source = "How should we explain the revised release date to the customer"

        with patch(
            "urllib.request.urlopen",
            return_value=self._response(
                "Explain the revised release date clearly and apologize for the delay."
            ),
        ):
            result = processor.process(source)

        self.assertEqual(result, source)

    def test_api_key_and_supported_model_are_required(self) -> None:
        with self.assertRaises(ConfigurationError):
            OpenAITextPostProcessor(OpenAIRewriteOptions(api_key=""))
        with self.assertRaises(ConfigurationError):
            OpenAITextPostProcessor(
                OpenAIRewriteOptions(api_key="test-key", model="unknown")
            )


class OpenRouterTextPostProcessorTests(unittest.TestCase):
    @staticmethod
    def _response(text: str, finish_reason: str = "stop"):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(
                    {
                        "model": "meta-llama/llama-3.3-70b-instruct:free",
                        "choices": [
                            {
                                "finish_reason": finish_reason,
                                "message": {"content": text},
                            }
                        ],
                    }
                ).encode()

        return FakeResponse()

    def test_free_router_is_default_and_receives_only_transcript_text(self) -> None:
        processor = OpenRouterTextPostProcessor(
            OpenRouterRewriteOptions(api_key="router-key")
        )

        with patch(
            "urllib.request.urlopen",
            return_value=self._response("Hey, the release is ready for review."),
        ) as urlopen:
            result = processor.process(
                "Hey so the release is ready and you can review it now."
            )

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(result, "Hey, the release is ready for review.")
        self.assertEqual(payload["model"], "openrouter/free")
        self.assertNotIn("models", payload)
        self.assertIn("casual written language", payload["messages"][0]["content"])
        self.assertIn("Hey so the release", payload["messages"][1]["content"])
        self.assertEqual(request.headers["Authorization"], "Bearer router-key")

    def test_custom_free_model_and_professional_switch_are_supported(self) -> None:
        processor = OpenRouterTextPostProcessor(
            OpenRouterRewriteOptions(
                api_key="router-key",
                model="meta-llama/llama-3.3-70b-instruct:free",
            )
        )

        with patch(
            "urllib.request.urlopen",
            return_value=self._response("The release is ready for formal review."),
        ) as urlopen:
            result = processor.process(
                "Professional, the release is ready for formal review."
            )

        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(result, "The release is ready for formal review.")
        self.assertIn("professional written prose", payload["messages"][0]["content"])
        self.assertEqual(payload["models"], ["openrouter/free"])
        self.assertNotIn("Professional,", payload["messages"][1]["content"])

    def test_multiple_versions_fall_back_to_the_original_transcript(self) -> None:
        processor = OpenRouterTextPostProcessor(
            OpenRouterRewriteOptions(api_key="router-key")
        )
        source = "Hey so the release is ready and you can review it now."
        multiple_versions = (
            "Version 1: Hey, the release is ready for review.\n\n"
            "Version 2: The release is ready, so please review it."
        )

        with patch(
            "urllib.request.urlopen",
            return_value=self._response(multiple_versions),
        ) as urlopen:
            result = processor.process(source)

        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(result, source)
        self.assertIn(
            "exactly one rewritten version", payload["messages"][0]["content"]
        )

    def test_api_key_and_model_are_validated(self) -> None:
        with self.assertRaises(ConfigurationError):
            OpenRouterTextPostProcessor(OpenRouterRewriteOptions(api_key=""))
        with self.assertRaises(ConfigurationError):
            OpenRouterTextPostProcessor(
                OpenRouterRewriteOptions(api_key="router-key", model="bad model")
            )

    def test_openrouter_key_uses_separate_keychain_entry(self) -> None:
        stored = {}
        keyring_module = types.ModuleType("keyring")
        errors_module = types.ModuleType("keyring.errors")

        class FakeKeyringError(Exception):
            pass

        errors_module.KeyringError = FakeKeyringError
        keyring_module.set_password = lambda service, account, value: stored.__setitem__(
            (service, account), value
        )
        keyring_module.get_password = lambda service, account: stored.get(
            (service, account)
        )

        with patch.dict(
            sys.modules,
            {"keyring": keyring_module, "keyring.errors": errors_module},
        ), patch.dict("os.environ", {}, clear=True):
            save_openrouter_key(" router-secret ")
            loaded = openrouter_key_from_environment()

        self.assertEqual(
            stored[(OPENROUTER_KEYCHAIN_SERVICE, OPENROUTER_KEYCHAIN_ACCOUNT)],
            "router-secret",
        )
        self.assertEqual(loaded, "router-secret")


class TranscriptPipelineTests(unittest.TestCase):
    def test_replacement_then_explicit_post_processing(self) -> None:
        pipeline = TranscriptPipeline(
            FakeTranscriber("use mlx whispr"),
            replacements=TermReplacer({"mlx whispr": "MLX Whisper"}),
            post_processor=FakePostProcessor(),
        )
        self.assertEqual(
            pipeline.process(Path("unused.wav")),
            "rewritten: use MLX Whisper",
        )

    def test_known_hallucination_only_output_is_dropped(self) -> None:
        pipeline = TranscriptPipeline(FakeTranscriber("Thanks for watching!"))
        self.assertEqual(pipeline.process(Path("unused.wav")), "")

    def test_valid_sentence_containing_music_is_not_dropped(self) -> None:
        pipeline = TranscriptPipeline(FakeTranscriber("I work in the music industry."))
        self.assertEqual(
            pipeline.process(Path("unused.wav")),
            "I work in the music industry.",
        )

    def test_transcript_wrapper_tags_are_removed_from_transcriber_output(self) -> None:
        pipeline = TranscriptPipeline(
            FakeTranscriber("<transcript>\nKeep this exact sentence.\n</transcript>")
        )

        self.assertEqual(
            pipeline.process(Path("unused.wav")),
            "Keep this exact sentence.",
        )

    def test_transcript_wrapper_tags_are_removed_after_post_processing(self) -> None:
        class WrappedPostProcessor:
            def process(self, text: str) -> str:
                return f"<TRANSCRIPT>{text}</ TRANSCRIPT >"

        pipeline = TranscriptPipeline(
            FakeTranscriber("Keep this exact sentence."),
            post_processor=WrappedPostProcessor(),
        )

        self.assertEqual(
            pipeline.process(Path("unused.wav")),
            "Keep this exact sentence.",
        )

    def test_post_processing_failure_preserves_the_original_transcript(self) -> None:
        class FailingPostProcessor:
            def process(self, _text: str) -> str:
                raise TranscriptionError(
                    "OpenRouter restructuring failed (HTTP 429): rate limited"
                )

        pipeline = TranscriptPipeline(
            FakeTranscriber("Keep this usable transcript."),
            post_processor=FailingPostProcessor(),
        )

        self.assertEqual(
            pipeline.process(Path("unused.wav")),
            "Keep this usable transcript.",
        )
        self.assertIn(
            "HTTP 429",
            pipeline.consume_post_process_warning(),
        )
        self.assertIsNone(pipeline.consume_post_process_warning())


if __name__ == "__main__":
    unittest.main()
