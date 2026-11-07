package com.redwhisper.android.core

import java.io.BufferedReader
import java.io.IOException
import java.io.InputStreamReader
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketTimeoutException
import java.net.URI
import java.net.URLDecoder
import java.net.URLEncoder
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64
import java.util.UUID
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock
import org.json.JSONArray
import org.json.JSONObject

/**
 * ChatGPT plan usage via the documented public Sign in with ChatGPT flow, ported from the Mac
 * app's `chatgpt_subscription.py`. These credentials are deliberately separate from API keys,
 * and only text rewrites use them; audio never reaches this path.
 */
object ChatGPT {
    const val ISSUER = "https://auth.openai.com"
    const val AUTHORIZE_URL = "$ISSUER/api/accounts/authorize"
    const val TOKEN_URL = "$ISSUER/api/accounts/oauth/token"
    const val RESOURCE = "https://api.openai.com/v1"
    const val USAGE_URL = "https://chatgpt.com/settings/usage"
    const val DIRECT_SCOPE = "chatgpt.tokens.use.direct"
    const val SCOPES = "openid profile email offline_access resource.invoke $DIRECT_SCOPE"
}

class ChatGPTError(message: String, val code: String = "") : TranscriptionError(message)

/** Persists the account blob. On Android it is encrypted with the Keystore. */
interface AccountStateStore {
    fun load(): String?
    fun save(json: String)
}

data class ChatGPTModel(val slug: String, val displayName: String)

data class ChatGPTAccount(val id: String, val label: String)

data class ChatGPTSnapshot(
    val connected: Boolean = false,
    val email: String = "",
    val models: List<ChatGPTModel> = emptyList(),
    val active: String = "",
    val accounts: List<ChatGPTAccount> = emptyList(),
)

internal fun chatGptFailure(code: String = "", status: Int? = null): ChatGPTError {
    val messages = mapOf(
        "subscription_sharing_usage_limit_exceeded" to
            "ChatGPT plan or app usage limit reached. Open Manage usage; your original transcript is preserved.",
        "subscription_sharing_user_not_eligible" to
            "This account or workspace is not eligible for ChatGPT plan usage.",
        "subscription_sharing_unsupported_capability" to
            "This model or request is not supported by ChatGPT plan usage. Refresh models and select another model.",
        "subscription_sharing_route_not_supported" to
            "ChatGPT plan usage is not available on this route.",
    )
    val fallback = "ChatGPT request failed${status?.let { " (HTTP $it)" } ?: ""}. " +
        "Reconnect or try again later; no API rewrite was charged."
    return ChatGPTError(messages[code] ?: fallback, code)
}

/** Reads SSE lines from the Responses API until the rewrite completes. */
internal fun completedText(lines: Sequence<String>, clock: () -> Long = System::currentTimeMillis): String {
    val data = mutableListOf<String>()
    val parts = StringBuilder()
    val deadline = clock() + 180_000
    for (raw in lines) {
        if (clock() > deadline) {
            throw ChatGPTError("ChatGPT rewriting timed out; your original transcript is preserved.")
        }
        try {
            val line = raw.trimEnd('\r', '\n')
            if (line.startsWith("data:")) {
                data.add(line.substring(5).trimStart())
            } else if (line.isEmpty() && data.isNotEmpty()) {
                val value = data.joinToString("\n")
                data.clear()
                if (value == "[DONE]") break
                val event = JSONObject(value)
                val kind = event.optString("type")
                if (kind in setOf("error", "response.failed", "response.incomplete")) {
                    val error = event.optJSONObject("response")?.optJSONObject("error")
                        ?: event.optJSONObject("error")
                    throw chatGptFailure(error?.opt("code") as? String ?: "")
                }
                if (kind == "response.output_text.delta") parts.append(event.get("delta") as String)
                if (kind == "response.completed") {
                    val result = RewriteRules.outputText(event.optJSONObject("response") ?: JSONObject())
                        .ifEmpty { parts.toString() }
                    if (result.isNotBlank()) return result.trim()
                    throw ChatGPTError("ChatGPT returned an empty rewrite.")
                }
            }
        } catch (error: ChatGPTError) {
            throw error
        } catch (_: Exception) {
            throw ChatGPTError("ChatGPT returned an invalid response stream.")
        }
    }
    throw ChatGPTError("ChatGPT did not complete the rewrite; your original transcript is preserved.")
}

/** One active account, with registrations retained for account switching. */
class ChatGPTSubscription(
    private val store: AccountStateStore,
    private val http: HttpTransport,
    private val openBrowser: (String) -> Boolean,
    private val clock: () -> Long = System::currentTimeMillis,
    private val identity: IdTokenVerifier = IdTokenVerifier(http, clock),
) {
    private val random = SecureRandom()

    private fun now(): Double = clock() / 1000.0

    private fun load(): JSONObject {
        val raw = try {
            store.load()
        } catch (_: Exception) {
            throw ChatGPTError("Could not read the ChatGPT connection from secure storage.")
        }
        if (raw.isNullOrEmpty()) {
            return JSONObject()
                .put("host_id", "urn:uuid:${UUID.randomUUID()}")
                .put("profiles", JSONObject())
                .put("active", "")
        }
        return parseJsonObject(raw) ?: throw ChatGPTError("Could not read the ChatGPT connection from secure storage.")
    }

    private fun save(state: JSONObject) {
        try {
            store.save(state.toString())
        } catch (_: Exception) {
            throw ChatGPTError("Could not save the ChatGPT connection in secure storage.")
        }
    }

    private fun profiles(state: JSONObject): JSONObject = state.getJSONObject("profiles")

    /** The active profile, attached to [state] so updates persist, or a detached empty one. */
    private fun profile(state: JSONObject): JSONObject =
        profiles(state).optJSONObject(state.optString("active")) ?: JSONObject()

    private fun scopes(profile: JSONObject): List<String> =
        profile.optJSONArray("scopes")?.let { array -> (0 until array.length()).map { array.optString(it) } }
            ?: emptyList()

    private fun models(profile: JSONObject): List<ChatGPTModel> {
        val array = profile.optJSONArray("models") ?: return emptyList()
        return (0 until array.length()).mapNotNull { array.optJSONObject(it) }.map {
            ChatGPTModel(it.getString("slug"), it.optString("display_name", it.getString("slug")))
        }
    }

    fun snapshot(): ChatGPTSnapshot = locked {
        val state = load()
        val profile = profile(state)
        val profiles = profiles(state)
        ChatGPTSnapshot(
            connected = profile.optString("access_token").isNotEmpty() && ChatGPT.DIRECT_SCOPE in scopes(profile),
            email = profile.optString("email", ""),
            models = models(profile),
            active = state.optString("active"),
            accounts = profiles.keys().asSequence().map { key ->
                val email = profiles.getJSONObject(key).optString("email", "ChatGPT")
                ChatGPTAccount(key, "$email · ${key.takeLast(6)}")
            }.toList(),
        )
    }

    fun selectAccount(clientId: String): List<ChatGPTModel> {
        locked {
            val state = load()
            if (!profiles(state).has(clientId)) throw ChatGPTError("Select a saved ChatGPT account.")
            state.put("active", clientId)
            save(state)
        }
        return syncModels()
    }

    fun signIn(newAccount: Boolean = false, cancel: AtomicBoolean = AtomicBoolean(false)): List<ChatGPTModel> {
        val (hostId, previous, clientId) = locked {
            val state = load()
            save(state) // Persist the host ID before opening the browser.
            Triple(
                state.getString("host_id"),
                if (newAccount) JSONObject() else JSONObject(profile(state).toString()),
                if (newAccount) "" else state.optString("active"),
            )
        }
        val verifier = tokenUrlSafe()
        val nonce = tokenUrlSafe()
        val oauthState = tokenUrlSafe()
        val challenge = Base64.getUrlEncoder().withoutPadding()
            .encodeToString(MessageDigest.getInstance("SHA-256").digest(verifier.toByteArray(Charsets.US_ASCII)))

        var callback: Map<String, String>? = null
        val redirectUri: String
        ServerSocket(0, 8, InetAddress.getByName("127.0.0.1")).use { server ->
            server.soTimeout = 250
            redirectUri = "http://127.0.0.1:${server.localPort}/auth/callback"
            val params = linkedMapOf(
                "client_id" to clientId.ifEmpty { "dynamic_agent_client" },
                "ext_agent_host_id" to hostId,
                "response_type" to "code",
                "redirect_uri" to redirectUri,
                "scope" to ChatGPT.SCOPES,
                "resource" to ChatGPT.RESOURCE,
                "state" to oauthState,
                "nonce" to nonce,
                "code_challenge_method" to "S256",
                "code_challenge" to challenge,
            )
            if (clientId.isEmpty()) {
                params["agent_name_hint"] = "RedWhisper"
            } else if (ChatGPT.DIRECT_SCOPE !in scopes(previous)) {
                params["prompt"] = "consent"
            }
            if (!openBrowser(ChatGPT.AUTHORIZE_URL + "?" + formEncode(params))) {
                throw ChatGPTError("Could not open your browser for ChatGPT sign-in.")
            }
            val deadline = clock() + 300_000
            while (callback == null && !cancel.get() && clock() < deadline) {
                val socket = try {
                    server.accept()
                } catch (_: SocketTimeoutException) {
                    continue
                }
                socket.use { callback = handleCallback(it, oauthState) }
            }
        }
        val result = callback
        if (cancel.get() || result == null) throw ChatGPTError("ChatGPT sign-in cancelled or timed out.")
        val code = result["code"]
        if (result["error"] != null || code.isNullOrEmpty()) throw ChatGPTError("ChatGPT sign-in was not approved.")
        val issued = result["client_id"] ?: clientId
        if (issued.isEmpty() || issued == "dynamic_agent_client" || (clientId.isNotEmpty() && issued != clientId)) {
            throw ChatGPTError("ChatGPT returned an unexpected client registration.")
        }
        locked {
            // Retain the registration even if the code expires. Do not change the active,
            // validated account until the new identity has been checked.
            val state = load()
            if (!profiles(state).has(issued)) profiles(state).put(issued, JSONObject().put("email", "Sign-in incomplete"))
            save(state)
        }
        val tokens = jsonRequest(
            ChatGPT.TOKEN_URL,
            form = linkedMapOf(
                "grant_type" to "authorization_code",
                "client_id" to issued,
                "code" to code,
                "code_verifier" to verifier,
                "redirect_uri" to redirectUri,
                "resource" to ChatGPT.RESOURCE,
            ),
        )
        val claims = identity.verify(tokens.optString("id_token", ""), issued, nonce)
        if (cancel.get()) throw ChatGPTError("ChatGPT sign-in cancelled.")
        val previousSubject = previous.optString("subject")
        if (previousSubject.isNotEmpty() && previousSubject != claims.getString("sub")) {
            throw ChatGPTError("The signed-in account does not match the selected account.")
        }
        val profile = tokenRecord(tokens)
            .put("subject", claims.getString("sub"))
            .put("email", claims.optString("email", "ChatGPT"))
            .put("models", JSONArray())
        locked {
            val state = load()
            profiles(state).put(issued, profile)
            state.put("active", issued)
            save(state)
        }
        return syncModels()
    }

    fun syncModels(): List<ChatGPTModel> = locked {
        val state = load()
        syncModels(state, accessToken(state))
    }

    /** Clears local credentials; returns false when remote revocation was not confirmed. */
    fun disconnect(): Boolean = locked {
        var confirmed = true
        val state = load()
        val profile = profile(state)
        val refreshToken = profile.optString("refresh_token")
        if (refreshToken.isNotEmpty()) {
            try {
                val endpoint = jsonRequest("${ChatGPT.ISSUER}/.well-known/openid-configuration")
                    .optString("revocation_endpoint", "")
                if (!endpoint.startsWith("${ChatGPT.ISSUER}/")) throw ChatGPTError("Invalid revocation endpoint")
                open(
                    HttpRequest(
                        endpoint,
                        headers = emptyMap(),
                        body = formEncode(
                            linkedMapOf(
                                "token" to refreshToken,
                                "token_type_hint" to "refresh_token",
                                "client_id" to state.optString("active"),
                            ),
                        ).toByteArray(),
                        contentType = "application/x-www-form-urlencoded",
                        timeoutSeconds = 30,
                    ),
                )
            } catch (_: ChatGPTError) {
                confirmed = false
            }
        }
        listOf("access_token", "refresh_token", "id_token").forEach { profile.remove(it) }
        profile.put("models", JSONArray())
        save(state)
        confirmed
    }

    /** Holds the lock through inference so account switching cannot mix tokens with a request. */
    fun rewrite(text: String, instructions: String, model: String): String = locked {
        val state = load()
        val token = accessToken(state)
        val profile = profile(state)
        if (now() - profile.optDouble("models_synced_at", 0.0) > 3600) syncModels(state, token)
        val models = models(profile)
        if (models.isEmpty()) {
            throw ChatGPTError("No models are available for this ChatGPT account. Refresh models in settings.")
        }
        val selected = model.ifEmpty { models.first().slug }
        if (models.none { it.slug == selected }) {
            throw ChatGPTError("The selected ChatGPT model is no longer available. Refresh models and choose another.")
        }
        val payload = JSONObject()
            .put("model", selected)
            .put("instructions", instructions)
            .put("input", JSONArray().put(JSONObject().put("role", "user").put("content", text)))
            .put("store", false)
            .put("stream", true)
        try {
            http.stream(
                HttpRequest(
                    "${ChatGPT.RESOURCE}/responses",
                    headers = mapOf("Authorization" to "Bearer $token", "Accept" to "text/event-stream"),
                    body = payload.toString().toByteArray(),
                    contentType = "application/json",
                    timeoutSeconds = 120,
                ),
            ) { response ->
                if (!response.isSuccessful) throw failureFrom(response.code, response.errorBody)
                completedText(response.lines, clock)
            }
        } catch (_: IOException) {
            throw ChatGPTError("Could not reach ChatGPT. Check your connection and try again.")
        }
    }

    private fun accessToken(state: JSONObject): String {
        val profile = profile(state)
        if (profile.optString("access_token").isEmpty() || ChatGPT.DIRECT_SCOPE !in scopes(profile)) {
            throw ChatGPTError("Connect ChatGPT and allow Use your ChatGPT plan in account settings.")
        }
        if (profile.optDouble("expires_at", 0.0) <= now() + 60) {
            val refreshToken = profile.optString("refresh_token")
            if (refreshToken.isEmpty()) throw ChatGPTError("Your ChatGPT connection expired. Please reconnect.")
            val tokens = try {
                jsonRequest(
                    ChatGPT.TOKEN_URL,
                    form = linkedMapOf(
                        "grant_type" to "refresh_token",
                        "client_id" to state.optString("active"),
                        "refresh_token" to refreshToken,
                        "resource" to ChatGPT.RESOURCE,
                    ),
                )
            } catch (error: ChatGPTError) {
                if (error.code in INVALID_REFRESH_CODES) {
                    listOf("access_token", "refresh_token", "id_token").forEach { profile.remove(it) }
                    profile.put("models", JSONArray())
                    save(state)
                    throw ChatGPTError("Your ChatGPT connection expired. Please reconnect.")
                }
                throw error
            }
            val updated = tokenRecord(tokens, profile)
            updated.keys().forEach { key -> profile.put(key, updated.get(key)) }
            save(state)
        }
        if (ChatGPT.DIRECT_SCOPE !in scopes(profile)) {
            throw ChatGPTError("ChatGPT plan permission was removed. Please reconnect.")
        }
        return profile.getString("access_token")
    }

    private fun syncModels(state: JSONObject, token: String): List<ChatGPTModel> {
        val payload = jsonRequest("${ChatGPT.RESOURCE}/models", token = token)
        val entries = payload.opt("models") as? JSONArray
            ?: throw ChatGPTError("ChatGPT returned an invalid model catalog.")
        val models = mutableListOf<ChatGPTModel>()
        val seen = mutableSetOf<String>()
        for (index in 0 until entries.length()) {
            val model = entries.optJSONObject(index) ?: continue
            if (model.optString("visibility") != "list") continue
            val slug = model.opt("slug") as? String ?: continue
            if (slug.isEmpty() || !seen.add(slug)) continue
            val label = (model.opt("display_name") as? String)?.takeIf { it.isNotEmpty() } ?: slug
            models.add(ChatGPTModel(slug, label))
        }
        val profile = profile(state)
        profile.put(
            "models",
            JSONArray(models.map { JSONObject().put("slug", it.slug).put("display_name", it.displayName) }),
        )
        profile.put("models_synced_at", now())
        save(state)
        return models
    }

    private fun tokenRecord(tokens: JSONObject, previous: JSONObject? = null): JSONObject {
        val record = JSONObject(previous?.toString() ?: "{}")
        val accessToken = tokens.opt("access_token")
        if (accessToken !is String || accessToken.isEmpty()) throw ChatGPTError("ChatGPT did not return an access token.")
        if (tokens.optString("token_type", "").lowercase() != "bearer") {
            throw ChatGPTError("ChatGPT returned an unsupported credential type.")
        }
        listOf("access_token", "refresh_token", "id_token").forEach { field ->
            if (tokens.has(field)) record.put(field, tokens.get(field))
        }
        val scopeText = tokens.opt("scope") as? String ?: scopes(record).joinToString(" ")
        record.put("scopes", JSONArray(scopeText.split(Regex("\\s+")).filter { it.isNotEmpty() }))
        val lifetime = when (val value = tokens.opt("expires_in")) {
            is Number -> value.toDouble()
            is String -> value.toDoubleOrNull()
            else -> null
        }
        if (lifetime == null || lifetime <= 0 || lifetime >= 365 * 86400) {
            throw ChatGPTError("ChatGPT returned an invalid token expiry.")
        }
        record.put("expires_at", now() + lifetime)
        return record
    }

    private fun jsonRequest(url: String, form: Map<String, String>? = null, token: String? = null): JSONObject {
        val headers = mutableMapOf("Accept" to "application/json")
        token?.let { headers["Authorization"] = "Bearer $it" }
        val request = if (form != null) {
            HttpRequest(url, "POST", headers, formEncode(form).toByteArray(), "application/x-www-form-urlencoded", 30)
        } else {
            HttpRequest(url, "GET", headers, timeoutSeconds = 30)
        }
        return parseJsonObject(open(request).body) ?: throw ChatGPTError("ChatGPT returned an invalid response.")
    }

    private fun open(request: HttpRequest): HttpResponse {
        val response = try {
            http.execute(request)
        } catch (_: IOException) {
            throw ChatGPTError("Could not reach ChatGPT. Check your connection and try again.")
        }
        if (!response.isSuccessful) throw failureFrom(response.code, response.body)
        return response
    }

    private fun failureFrom(status: Int, body: ByteArray): ChatGPTError {
        val code = when (val detail = parseJsonObject(body)?.opt("error")) {
            is JSONObject -> detail.opt("code") as? String ?: ""
            is String -> detail
            else -> ""
        }
        return chatGptFailure(code, status)
    }

    /** Accepts exactly one well-formed callback; codes never reach any log. */
    private fun handleCallback(socket: Socket, oauthState: String): Map<String, String>? {
        socket.soTimeout = 2000
        val reader = BufferedReader(InputStreamReader(socket.getInputStream(), Charsets.ISO_8859_1))
        val requestLine = reader.readLine() ?: return null
        while (true) {
            val header = reader.readLine() ?: break
            if (header.isEmpty()) break
        }
        val output = socket.getOutputStream()
        fun respond(status: String, body: String, extraHeaders: String = "") {
            val bytes = body.toByteArray(Charsets.UTF_8)
            output.write(
                ("HTTP/1.1 $status\r\nContent-Type: text/plain; charset=utf-8\r\n$extraHeaders" +
                    "Content-Length: ${bytes.size}\r\nConnection: close\r\n\r\n").toByteArray(Charsets.ISO_8859_1),
            )
            output.write(bytes)
            output.flush()
        }

        val target = requestLine.split(" ").getOrNull(1)
        val uri = target?.let { runCatching { URI(it) }.getOrNull() }
        val query = parseQuery(uri?.rawQuery)
        val valid = requestLine.startsWith("GET ") && uri?.path == "/auth/callback" &&
            query["state"] == listOf(oauthState) && query.values.all { it.size == 1 }
        if (!valid) {
            respond("400 Bad Request", "Invalid sign-in callback")
            return null
        }
        respond(
            "200 OK",
            "Return to Red Whisper to finish connecting. You can close this tab.",
            "Cache-Control: no-store\r\nReferrer-Policy: no-referrer\r\n",
        )
        return query.mapValues { it.value.first() }
    }

    private fun tokenUrlSafe(): String {
        val bytes = ByteArray(48).also(random::nextBytes)
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
    }

    private inline fun <T> locked(block: () -> T): T = LOCK.withLock(block)

    companion object {
        private val LOCK = ReentrantLock()
        private val INVALID_REFRESH_CODES = setOf(
            "invalid_grant",
            "invalid_refresh_token",
            "token_expired",
            "refresh_token_expired",
            "refresh_token_invalidated",
            "refresh_token_reused",
        )

        internal fun formEncode(values: Map<String, String>): String = values.entries.joinToString("&") { (key, value) ->
            "${URLEncoder.encode(key, "UTF-8")}=${URLEncoder.encode(value, "UTF-8")}"
        }

        /** Like Python's `parse_qs`: repeated keys keep every value and blank values are dropped. */
        internal fun parseQuery(rawQuery: String?): Map<String, List<String>> {
            if (rawQuery.isNullOrEmpty()) return emptyMap()
            val result = linkedMapOf<String, MutableList<String>>()
            for (pair in rawQuery.split("&")) {
                if (pair.isEmpty()) continue
                val separator = pair.indexOf('=')
                if (separator < 0) continue
                val key = URLDecoder.decode(pair.substring(0, separator), "UTF-8")
                val value = URLDecoder.decode(pair.substring(separator + 1), "UTF-8")
                if (value.isEmpty()) continue
                result.getOrPut(key) { mutableListOf() }.add(value)
            }
            return result
        }
    }
}

/** Rewrites through the connected ChatGPT plan; it never falls back to paid API rephrasing. */
class ChatGPTTextPostProcessor(
    private val model: String,
    private val client: ChatGPTSubscription,
    private val tone: RewriteTone = RewriteTone.CASUAL,
) : PostProcessor {
    override fun process(text: String): String {
        val (source, messageTone) = RewriteRules.sourceAndTone(text, tone)
        if (source.isEmpty()) return ""
        val result = client.rewrite(
            "<transcript>\n$source\n</transcript>",
            RewriteRules.instructionFor(messageTone),
            model,
        )
        return RewriteRules.safeRewrite(source, result, messageTone)
    }
}
