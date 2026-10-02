"""ChatGPT plan usage via the documented public Sign in with ChatGPT flow.

This credential store is deliberately separate from OpenAI API keys. Only text
rewrites use it; audio transcription never receives a subscription credential.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import TCPServer
import json
from pathlib import Path
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser

from mlx_whisper_core import OpenAITextPostProcessor, TranscriptionError


ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = ISSUER + "/api/accounts/authorize"
TOKEN_URL = ISSUER + "/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
USAGE_URL = "https://chatgpt.com/settings/usage"
DIRECT_SCOPE = "chatgpt.tokens.use.direct"
SCOPES = "openid profile email offline_access resource.invoke " + DIRECT_SCOPE
SERVICE = "com.redwhisper.chatgpt-subscription"
LOCK_PATH = Path.home() / "Library/Application Support/RedWhisper/chatgpt.lock"
_THREAD_LOCK = threading.RLock()


class LoopbackServer(HTTPServer):
    def server_bind(self):
        # A numeric loopback address needs no potentially blocking reverse DNS.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(2)
        return connection, address


class ChatGPTError(TranscriptionError):
    pass


def _failure(code: str = "", status: int | None = None) -> ChatGPTError:
    if not isinstance(code, str):
        code = ""
    messages = {
        "subscription_sharing_usage_limit_exceeded": "ChatGPT plan or app usage limit reached. Open Manage usage; your original transcript is preserved.",
        "subscription_sharing_user_not_eligible": "This account or workspace is not eligible for ChatGPT plan usage.",
        "subscription_sharing_unsupported_capability": "This model or request is not supported by ChatGPT plan usage. Refresh models and select another model.",
        "subscription_sharing_route_not_supported": "ChatGPT plan usage is not available on this route.",
    }
    return ChatGPTError(messages.get(code, f"ChatGPT request failed{f' (HTTP {status})' if status else ''}. Reconnect or try again later; no API rewrite was charged."))


def _open(request, timeout=30):
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        try:
            payload = json.loads(error.read())
            detail = payload.get("error", {})
            code = detail.get("code", "") if isinstance(detail, dict) else detail
        except (ValueError, AttributeError):
            code = ""
        failure = _failure(code, error.code)
        failure.code = code
        raise failure from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ChatGPTError("Could not reach ChatGPT. Check your connection and try again.") from None


def _json_request(url, *, form=None, token=None):
    headers = {"Accept": "application/json"}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with _open(urllib.request.Request(url, data=data, headers=headers)) as response:
        try:
            value = json.loads(response.read())
        except (ValueError, UnicodeError):
            raise ChatGPTError("ChatGPT returned an invalid response.") from None
    if not isinstance(value, dict):
        raise ChatGPTError("ChatGPT returned an invalid response.")
    return value


def _verify_identity(id_token, client_id, nonce):
    import jwt

    try:
        key = jwt.PyJWKClient(ISSUER + "/.well-known/jwks.json", timeout=30).get_signing_key_from_jwt(id_token)
        identity = jwt.decode(
            id_token, key.key, algorithms=["RS256"], audience=client_id,
            issuer=ISSUER, leeway=5,
            options={"require": ["sub", "exp", "iat", "nonce"]},
        )
        if not isinstance(identity["sub"], str) or not identity["sub"] or identity["nonce"] != nonce:
            raise ValueError("Invalid identity")
        return identity
    except (jwt.PyJWTError, ValueError, OSError):
        raise ChatGPTError("ChatGPT sign-in identity could not be verified. Please sign in again.") from None


def _token_record(tokens, previous=None):
    record = dict(previous or {})
    if not isinstance(tokens.get("access_token"), str) or not tokens["access_token"]:
        raise ChatGPTError("ChatGPT did not return an access token.")
    if tokens.get("token_type", "").lower() != "bearer":
        raise ChatGPTError("ChatGPT returned an unsupported credential type.")
    for field in ("access_token", "refresh_token", "id_token"):
        if field in tokens:
            record[field] = tokens[field]
    record["scopes"] = tokens.get("scope", " ".join(record.get("scopes", []))).split()
    try:
        lifetime = float(tokens["expires_in"])
        if not 0 < lifetime < 365 * 86400:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ChatGPTError("ChatGPT returned an invalid token expiry.") from None
    record["expires_at"] = time.time() + lifetime
    return record


class ChatGPTSubscription:
    """One active account, with registrations retained for account switching."""

    @contextmanager
    def _locked(self):
        # Both the settings process and app runtime may renew the same token.
        with _THREAD_LOCK:
            LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
            with LOCK_PATH.open("a") as lock:
                LOCK_PATH.chmod(0o600)
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)

    def _load(self):
        import keyring
        try:
            raw = keyring.get_password(SERVICE, "accounts")
            return json.loads(raw) if raw else {"host_id": "urn:uuid:" + str(uuid.uuid4()), "profiles": {}, "active": ""}
        except (keyring.errors.KeyringError, ValueError):
            raise ChatGPTError("Could not read the ChatGPT connection from macOS Keychain.") from None

    def _save(self, state):
        import keyring
        try:
            keyring.set_password(SERVICE, "accounts", json.dumps(state))
        except keyring.errors.KeyringError:
            raise ChatGPTError("Could not save the ChatGPT connection in macOS Keychain.") from None

    def _profile(self, state):
        return state["profiles"].get(state["active"], {})

    def snapshot(self):
        with self._locked():
            state = self._load()
            profile = self._profile(state)
            return {
                "connected": bool(profile.get("access_token") and DIRECT_SCOPE in profile.get("scopes", [])),
                "email": profile.get("email", ""),
                "models": profile.get("models", []),
                "active": state["active"],
                "accounts": [{"id": key, "label": value.get("email", "ChatGPT") + " · " + key[-6:]} for key, value in state["profiles"].items()],
            }

    def select_account(self, client_id):
        with self._locked():
            state = self._load()
            if client_id not in state["profiles"]:
                raise ChatGPTError("Select a saved ChatGPT account.")
            state["active"] = client_id
            self._save(state)
        return self.sync_models()

    def sign_in(self, new_account=False, cancel=None):
        cancel = cancel or threading.Event()
        with self._locked():
            state = self._load()
            self._save(state)  # Persist host ID before opening the browser.
            previous = {} if new_account else dict(self._profile(state))
            client_id = "" if new_account else state["active"]
        verifier, nonce, oauth_state = (secrets.token_urlsafe(48) for _ in range(3))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        callback = {}

        class CallbackHandler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass  # Authorization codes must not enter logs.

            def do_GET(self):
                parsed = urllib.parse.urlsplit(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                valid = parsed.path == "/auth/callback" and query.get("state") == [oauth_state]
                if not valid:
                    self.send_error(400, "Invalid sign-in callback")
                    return
                if any(len(values) != 1 for values in query.values()):
                    self.send_error(400, "Invalid sign-in callback")
                    return
                callback.update({key: values[0] for key, values in query.items()})
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(b"Return to RedWhisper to finish connecting. You can close this tab.")

        with LoopbackServer(("127.0.0.1", 0), CallbackHandler) as server:
            server.timeout = 0.25
            redirect_uri = f"http://127.0.0.1:{server.server_port}/auth/callback"
            params = dict(client_id=client_id or "dynamic_agent_client", ext_agent_host_id=state["host_id"],
                          response_type="code", redirect_uri=redirect_uri, scope=SCOPES, resource=RESOURCE,
                          state=oauth_state, nonce=nonce, code_challenge_method="S256", code_challenge=challenge)
            if not client_id:
                params["agent_name_hint"] = "RedWhisper"
            elif DIRECT_SCOPE not in previous.get("scopes", []):
                params["prompt"] = "consent"
            if not webbrowser.open(AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)):
                raise ChatGPTError("Could not open your browser for ChatGPT sign-in.")
            deadline = time.monotonic() + 300
            while not callback and not cancel.is_set() and time.monotonic() < deadline:
                server.handle_request()
        if cancel.is_set() or not callback:
            raise ChatGPTError("ChatGPT sign-in cancelled or timed out.")
        if callback.get("error") or not callback.get("code"):
            raise ChatGPTError("ChatGPT sign-in was not approved.")
        issued = callback.get("client_id", client_id)
        if not issued or issued == "dynamic_agent_client" or (client_id and issued != client_id):
            raise ChatGPTError("ChatGPT returned an unexpected client registration.")
        with self._locked():
            state = self._load()
            # Retain registration even if the code expires. Do not change the
            # active, validated account until the new identity has been checked.
            state["profiles"].setdefault(issued, {"email": "Sign-in incomplete"})
            self._save(state)
        tokens = _json_request(TOKEN_URL, form=dict(grant_type="authorization_code", client_id=issued,
                               code=callback["code"], code_verifier=verifier, redirect_uri=redirect_uri, resource=RESOURCE))
        identity = _verify_identity(tokens.get("id_token", ""), issued, nonce)
        if cancel.is_set():
            raise ChatGPTError("ChatGPT sign-in cancelled.")
        if previous.get("subject") and previous["subject"] != identity["sub"]:
            raise ChatGPTError("The signed-in account does not match the selected account.")
        profile = _token_record(tokens)
        profile.update(subject=identity["sub"], email=identity.get("email", "ChatGPT"), models=[])
        with self._locked():
            state = self._load()
            state["profiles"][issued] = profile
            state["active"] = issued
            self._save(state)
        return self.sync_models()

    def _access_token(self, state):
        profile = self._profile(state)
        if not profile.get("access_token") or DIRECT_SCOPE not in profile.get("scopes", []):
            raise ChatGPTError("Connect ChatGPT and allow Use your ChatGPT plan in account settings.")
        if profile.get("expires_at", 0) <= time.time() + 60:
            if not profile.get("refresh_token"):
                raise ChatGPTError("Your ChatGPT connection expired. Please reconnect.")
            try:
                tokens = _json_request(TOKEN_URL, form=dict(grant_type="refresh_token", client_id=state["active"],
                                       refresh_token=profile["refresh_token"], resource=RESOURCE))
            except ChatGPTError as error:
                if getattr(error, "code", "") in {"invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired", "refresh_token_invalidated", "refresh_token_reused"}:
                    for field in ("access_token", "refresh_token", "id_token"):
                        profile.pop(field, None)
                    profile["models"] = []
                    self._save(state)
                    raise ChatGPTError("Your ChatGPT connection expired. Please reconnect.") from None
                raise
            profile.update(_token_record(tokens, profile))
            self._save(state)
        if DIRECT_SCOPE not in profile.get("scopes", []):
            raise ChatGPTError("ChatGPT plan permission was removed. Please reconnect.")
        return profile["access_token"]

    def _sync_models(self, state, token):
        payload = _json_request(RESOURCE + "/models", token=token)
        if not isinstance(payload.get("models"), list):
            raise ChatGPTError("ChatGPT returned an invalid model catalog.")
        models = []
        seen = set()
        for model in payload["models"]:
            if not isinstance(model, dict) or model.get("visibility") != "list":
                continue
            slug = model.get("slug")
            if not isinstance(slug, str) or not slug or slug in seen:
                continue
            seen.add(slug)
            label = model.get("display_name")
            models.append({"slug": slug, "display_name": label if isinstance(label, str) and label else slug})
        profile = self._profile(state)
        profile.update(models=models, models_synced_at=time.time())
        self._save(state)
        return models

    def sync_models(self):
        with self._locked():
            state = self._load()
            return self._sync_models(state, self._access_token(state))

    def disconnect(self):
        confirmed = True
        with self._locked():
            state = self._load()
            profile = self._profile(state)
            if profile.get("refresh_token"):
                try:
                    discovery = _json_request(ISSUER + "/.well-known/openid-configuration")
                    endpoint = discovery.get("revocation_endpoint", "")
                    if not endpoint.startswith(ISSUER + "/"):
                        raise ChatGPTError("Invalid revocation endpoint")
                    request = urllib.request.Request(endpoint, data=urllib.parse.urlencode(dict(
                        token=profile["refresh_token"], token_type_hint="refresh_token", client_id=state["active"])).encode(),
                        headers={"Content-Type": "application/x-www-form-urlencoded"})
                    with _open(request):
                        pass
                except ChatGPTError:
                    confirmed = False
            for field in ("access_token", "refresh_token", "id_token"):
                profile.pop(field, None)
            profile["models"] = []
            self._save(state)
        return confirmed

    def rewrite(self, text, instructions, model):
        # Hold the credential lock through inference so account switching and
        # logout cannot mix tokens, models or identity with an in-flight request.
        with self._locked():
            state = self._load()
            token = self._access_token(state)
            profile = self._profile(state)
            if time.time() - profile.get("models_synced_at", 0) > 3600:
                self._sync_models(state, token)
            models = profile.get("models", [])
            if not models:
                raise ChatGPTError("No models are available for this ChatGPT account. Refresh models in settings.")
            selected = model or models[0]["slug"]
            if selected not in {item["slug"] for item in models}:
                raise ChatGPTError("The selected ChatGPT model is no longer available. Refresh models and choose another.")
            payload = dict(model=selected, instructions=instructions,
                           input=[{"role": "user", "content": text}], store=False, stream=True)
            request = urllib.request.Request(RESOURCE + "/responses", data=json.dumps(payload).encode(),
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "text/event-stream"})
            with _open(request, timeout=120) as response:
                return _completed_text(response)


def _completed_text(response):
    data, parts = [], []
    deadline = time.monotonic() + 180
    for raw in response:
        if time.monotonic() > deadline:
            raise ChatGPTError("ChatGPT rewriting timed out; your original transcript is preserved.")
        try:
            line = raw.decode("utf-8").rstrip("\r\n")
            if line.startswith("data:"):
                data.append(line[5:].lstrip())
            elif not line and data:
                value = "\n".join(data)
                data = []
                if value == "[DONE]":
                    break
                event = json.loads(value)
                kind = event.get("type")
                if kind in {"error", "response.failed", "response.incomplete"}:
                    error = event.get("response", {}).get("error") or event.get("error") or {}
                    raise _failure(error.get("code", "") if isinstance(error, dict) else "")
                if kind == "response.output_text.delta":
                    parts.append(event["delta"])
                if kind == "response.completed":
                    result = OpenAITextPostProcessor._output_text(event.get("response", {})) or "".join(parts)
                    if result.strip():
                        return result.strip()
                    raise ChatGPTError("ChatGPT returned an empty rewrite.")
        except (ValueError, UnicodeError, KeyError, TypeError, AttributeError):
            raise ChatGPTError("ChatGPT returned an invalid response stream.") from None
    raise ChatGPTError("ChatGPT did not complete the rewrite; your original transcript is preserved.")


class ChatGPTTextPostProcessor(OpenAITextPostProcessor):
    def __init__(self, model="", client=None):
        self.model = model
        self.client = client or ChatGPTSubscription()

    def process(self, text):
        source, professional = self._source_and_tone(text)
        if not source:
            return ""
        result = self.client.rewrite(f"<transcript>\n{source}\n</transcript>", self._instruction_for(professional), self.model)
        return self._safe_rewrite(source, result)
