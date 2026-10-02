import copy
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.parse
import urllib.request

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from chatgpt_subscription import (
    ChatGPTError, ChatGPTSubscription, ChatGPTTextPostProcessor, DIRECT_SCOPE,
    ISSUER, RESOURCE, TOKEN_URL, _completed_text, _verify_identity,
)
from mlx_whisper_core import TranscriptPipeline


def event_stream(*events):
    return io.BytesIO(b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events))


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        patcher = patch("chatgpt_subscription.LOCK_PATH", Path(temporary.name) / "account.lock")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.state = {"host_id": "urn:uuid:test-host", "active": "oaiapp_test", "profiles": {
            "oaiapp_test": {"access_token": "test-access", "refresh_token": "test-refresh",
                "subject": "test-user", "email": "test@example.com", "scopes": [DIRECT_SCOPE],
                "expires_at": time.time() + 3600, "models_synced_at": time.time(),
                "models": [{"slug": "test-model", "display_name": "Test Model"}]}}}
        self.client = ChatGPTSubscription()
        self.client._load = lambda: copy.deepcopy(self.state)
        self.client._save = self.save

    def save(self, value):
        self.state = copy.deepcopy(value)

    def test_catalog_uses_subscription_token_and_preserves_all_visible_models(self):
        catalog = {"models": [
            {"slug": "future-model", "display_name": "Future Model", "visibility": "list"},
            {"slug": "hidden", "visibility": "hidden"},
            {"slug": "test-model", "visibility": "list"},
            {"slug": "future-model", "visibility": "list"}]}
        with patch("chatgpt_subscription._json_request", return_value=catalog) as request:
            models = self.client.sync_models()
        request.assert_called_once_with(RESOURCE + "/models", token="test-access")
        self.assertEqual([m["slug"] for m in models], ["future-model", "test-model"])
        self.assertEqual(self.client.snapshot()["models"], models)
        self.assertNotIn("access_token", json.dumps(self.client.snapshot()))

    def test_refresh_rotates_tokens_before_sync(self):
        self.state["profiles"]["oaiapp_test"]["expires_at"] = 0
        token = {"access_token": "test-new", "refresh_token": "test-rotated", "token_type": "Bearer", "expires_in": 3600}
        with patch("chatgpt_subscription._json_request", side_effect=[token, {"models": []}]) as request:
            self.client.sync_models()
        self.assertEqual(request.call_args_list[0].args[0], TOKEN_URL)
        self.assertNotIn("scope", request.call_args_list[0].kwargs["form"])
        self.assertEqual(request.call_args_list[1].kwargs["token"], "test-new")
        self.assertEqual(self.state["profiles"]["oaiapp_test"]["refresh_token"], "test-rotated")

    def test_refresh_terminal_error_clears_only_credentials(self):
        self.state["profiles"]["oaiapp_test"]["expires_at"] = 0
        error = ChatGPTError("Refresh failed")
        error.code = "invalid_grant"
        with patch("chatgpt_subscription._json_request", side_effect=error):
            with self.assertRaisesRegex(ChatGPTError, "reconnect"):
                self.client.sync_models()
        self.assertNotIn("access_token", self.state["profiles"]["oaiapp_test"])
        self.assertEqual(self.state["active"], "oaiapp_test")

    def test_network_failure_preserves_refresh_credentials(self):
        self.state["profiles"]["oaiapp_test"]["expires_at"] = 0
        with patch("chatgpt_subscription._json_request", side_effect=ChatGPTError("offline")):
            with self.assertRaises(ChatGPTError):
                self.client.sync_models()
        self.assertEqual(self.state["profiles"]["oaiapp_test"]["refresh_token"], "test-refresh")

    def test_missing_permission_never_calls_inference(self):
        self.state["profiles"]["oaiapp_test"]["scopes"] = []
        with patch("chatgpt_subscription._open") as request:
            with self.assertRaises(ChatGPTError):
                self.client.rewrite("text", "instruction", "")
        request.assert_not_called()

    def test_rewrite_request_is_streamed_and_does_not_use_api_settings(self):
        stream = event_stream({"type": "response.output_text.delta", "delta": "Rephrased."}, {"type": "response.completed"})
        with patch("chatgpt_subscription._open", return_value=stream) as request:
            self.assertEqual(self.client.rewrite("Dictation", "Rewrite only", "test-model"), "Rephrased.")
        outgoing = request.call_args.args[0]
        self.assertEqual(outgoing.full_url, RESOURCE + "/responses")
        self.assertEqual(outgoing.get_header("Authorization"), "Bearer test-access")
        body = json.loads(outgoing.data)
        self.assertEqual(body, {"model": "test-model", "instructions": "Rewrite only", "input": [{"role": "user", "content": "Dictation"}], "store": False, "stream": True})

    def test_removed_selected_model_is_not_silently_replaced(self):
        with patch("chatgpt_subscription._open") as request:
            with self.assertRaisesRegex(ChatGPTError, "no longer available"):
                self.client.rewrite("Text", "Rewrite", "removed-model")
        request.assert_not_called()

    def test_stale_catalog_is_refreshed_before_rewrite(self):
        self.state["profiles"]["oaiapp_test"]["models_synced_at"] = 0
        stream = event_stream({"type": "response.completed", "response": {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Done"}]}]}})
        with patch("chatgpt_subscription._json_request", return_value={"models": [{"slug": "new-model", "visibility": "list"}]}), patch("chatgpt_subscription._open", return_value=stream) as request:
            self.client.rewrite("Text", "Rewrite", "")
        self.assertEqual(json.loads(request.call_args.args[0].data)["model"], "new-model")

    def test_account_switch_refreshes_catalog_with_that_accounts_token(self):
        self.state["profiles"]["oaiapp_other"] = dict(self.state["profiles"]["oaiapp_test"], access_token="test-other")
        with patch("chatgpt_subscription._json_request", return_value={"models": []}) as request:
            self.client.select_account("oaiapp_other")
        self.assertEqual(request.call_args.kwargs["token"], "test-other")

    def test_disconnect_clears_tokens_and_models_but_retains_registration(self):
        with patch("chatgpt_subscription._json_request", return_value={"revocation_endpoint": ISSUER + "/revoke"}), patch("chatgpt_subscription._open", return_value=io.BytesIO()) as request:
            self.assertTrue(self.client.disconnect())
        body = urllib.parse.parse_qs(request.call_args.args[0].data.decode())
        self.assertEqual(body["token"], ["test-refresh"])
        self.assertEqual(self.state["active"], "oaiapp_test")
        self.assertFalse(self.client.snapshot()["connected"])
        self.assertEqual(self.client.snapshot()["models"], [])

    def test_disconnect_reports_unconfirmed_remote_revocation(self):
        with patch("chatgpt_subscription._json_request", side_effect=ChatGPTError("offline")):
            self.assertFalse(self.client.disconnect())
        self.assertFalse(self.client.snapshot()["connected"])

    def test_browser_sign_in_validates_state_and_uses_issued_client(self):
        self.state["profiles"] = {}
        self.state["active"] = ""
        captured = {}
        errors = []

        def browser(url):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            captured.update(query)
            def callback():
                try:
                    base = query["redirect_uri"][0]
                    with self.assertRaises(urllib.error.HTTPError):
                        urllib.request.urlopen(base + "?state=invalid&code=bad", timeout=2)
                    params = urllib.parse.urlencode({"state": query["state"][0], "code": "test-code", "client_id": "oaiapp_issued"})
                    urllib.request.urlopen(base + "?" + params, timeout=2).close()
                except Exception as error:
                    errors.append(error)
            threading.Thread(target=callback, daemon=True).start()
            return True

        tokens = {"access_token": "test-access", "refresh_token": "test-refresh", "id_token": "test-id", "scope": DIRECT_SCOPE, "token_type": "Bearer", "expires_in": 3600}
        with patch("chatgpt_subscription.webbrowser.open", side_effect=browser), patch("chatgpt_subscription._json_request", side_effect=[tokens, {"models": []}]) as request, patch("chatgpt_subscription._verify_identity", return_value={"sub": "new-user", "email": "new@example.com"}) as verify:
            self.client.sign_in()
        self.assertEqual(errors, [])
        self.assertEqual(captured["client_id"], ["dynamic_agent_client"])
        self.assertEqual(captured["agent_name_hint"], ["RedWhisper"])
        self.assertEqual(captured["code_challenge_method"], ["S256"])
        exchange = request.call_args_list[0].kwargs["form"]
        self.assertEqual(exchange["client_id"], "oaiapp_issued")
        self.assertEqual(exchange["redirect_uri"], captured["redirect_uri"][0])
        verify.assert_called_once_with("test-id", "oaiapp_issued", captured["nonce"][0])
        self.assertEqual(self.state["active"], "oaiapp_issued")

    def test_cancel_sign_in_keeps_existing_account(self):
        cancel = threading.Event()
        cancel.set()
        with patch("chatgpt_subscription.webbrowser.open", return_value=True), patch("chatgpt_subscription._json_request") as request:
            with self.assertRaisesRegex(ChatGPTError, "cancelled"):
                self.client.sign_in(cancel=cancel)
        request.assert_not_called()
        self.assertTrue(self.client.snapshot()["connected"])


class StreamAndIdentityTests(unittest.TestCase):
    def test_partial_stream_is_never_success(self):
        with self.assertRaisesRegex(ChatGPTError, "did not complete"):
            _completed_text(event_stream({"type": "response.output_text.delta", "delta": "Partial"}))

    def test_late_usage_failure_discards_partial_text(self):
        stream = event_stream({"type": "response.output_text.delta", "delta": "Partial"}, {"type": "response.failed", "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded"}}})
        with self.assertRaisesRegex(ChatGPTError, "usage limit"):
            _completed_text(stream)

    def test_pipeline_preserves_transcript_when_subscription_fails(self):
        client = Mock()
        client.rewrite.side_effect = ChatGPTError("Plan limit reached")
        transcriber = Mock()
        transcriber.transcribe.return_value = "Please send the meeting notes."
        pipeline = TranscriptPipeline(transcriber, post_processor=ChatGPTTextPostProcessor(client=client))
        self.assertEqual(pipeline.process(Path("test.wav")), "Please send the meeting notes.")
        self.assertIn("Plan limit", pipeline.consume_post_process_warning())

    def test_rewrite_reuses_tone_and_intent_safeguards(self):
        client = Mock()
        client.rewrite.return_value = "Yes, I can help with that."
        processor = ChatGPTTextPostProcessor("test-model", client)
        self.assertEqual(processor.process("professional Can you send the notes?"), "Can you send the notes?")
        self.assertIn("professional written prose", client.rewrite.call_args.args[1])

    def test_id_token_signature_issuer_audience_expiry_and_nonce(self):
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        claims = {"iss": ISSUER, "aud": "oaiapp_test", "sub": "test-user", "iat": int(time.time()), "exp": int(time.time()) + 300, "nonce": "test-nonce"}
        with patch("jwt.PyJWKClient") as jwks:
            jwks.return_value.get_signing_key_from_jwt.return_value.key = private.public_key()
            good = jwt.encode(claims, private, algorithm="RS256")
            self.assertEqual(_verify_identity(good, "oaiapp_test", "test-nonce")["sub"], "test-user")
            for changes in ({"iss": "https://invalid.example"}, {"aud": "other"}, {"exp": 1}, {"nonce": "wrong"}, {"sub": ""}):
                with self.subTest(changes=changes), self.assertRaises(ChatGPTError):
                    _verify_identity(jwt.encode(dict(claims, **changes), private, algorithm="RS256"), "oaiapp_test", "test-nonce")
            wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            with self.assertRaises(ChatGPTError):
                _verify_identity(jwt.encode(claims, wrong_key, algorithm="RS256"), "oaiapp_test", "test-nonce")


if __name__ == "__main__":
    unittest.main()
