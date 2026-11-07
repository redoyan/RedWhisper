package com.redwhisper.android.core

import java.math.BigInteger
import java.security.KeyPairGenerator
import java.security.Signature
import java.security.interfaces.RSAPublicKey
import java.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class ChatGptSubscriptionTest {
    private val now = 1_800_000_000_000L

    private class MemoryStore(var json: String? = null) : AccountStateStore {
        override fun load(): String? = json
        override fun save(json: String) {
            this.json = json
        }
    }

    private fun connectedStore(expiresAt: Double, syncedAt: Double): MemoryStore {
        val profile = JSONObject()
            .put("access_token", "access-1")
            .put("refresh_token", "refresh-1")
            .put("scopes", JSONArray(listOf("openid", ChatGPT.DIRECT_SCOPE)))
            .put("expires_at", expiresAt)
            .put("email", "test@example.com")
            .put("models", JSONArray().put(JSONObject().put("slug", "first").put("display_name", "First")))
            .put("models_synced_at", syncedAt)
        val state = JSONObject()
            .put("host_id", "urn:uuid:test")
            .put("profiles", JSONObject().put("client-123456", profile))
            .put("active", "client-123456")
        return MemoryStore(state.toString())
    }

    private fun subscription(store: MemoryStore, http: FakeTransport) =
        ChatGPTSubscription(store, http, openBrowser = { false }, clock = { now })

    private val completedStream = listOf(
        "event: response.output_text.delta",
        """data: {"type": "response.output_text.delta", "delta": "Hi "}""",
        "",
        """data: {"type": "response.output_text.delta", "delta": "there."}""",
        "",
        """data: {"type": "response.completed", "response": {"output": []}}""",
        "",
    )

    @Test
    fun rewriteUsesFirstAccountModelAndStreamsText() {
        val store = connectedStore(expiresAt = now / 1000.0 + 3600, syncedAt = now / 1000.0)
        val http = FakeTransport().replyStream(200, completedStream)

        val result = subscription(store, http).rewrite("<transcript>\nhi there\n</transcript>", "edit", "")

        assertEquals("Hi there.", result)
        val request = http.requests.single()
        assertEquals("${ChatGPT.RESOURCE}/responses", request.url)
        assertEquals("Bearer access-1", request.headers["Authorization"])
        assertEquals("text/event-stream", request.headers["Accept"])
        val payload = JSONObject(http.lastBody())
        assertEquals("first", payload.getString("model"))
        assertFalse(payload.getBoolean("store"))
        assertTrue(payload.getBoolean("stream"))
    }

    @Test
    fun chatGptRephrasingUsesTheChosenTone() {
        val store = connectedStore(expiresAt = now / 1000.0 + 3600, syncedAt = now / 1000.0)
        val http = FakeTransport().replyStream(200, completedStream)
        ChatGPTTextPostProcessor("", subscription(store, http), RewriteTone.PROFESSIONAL).process("hi there")

        val payload = JSONObject(http.lastBody())
        assertEquals(RewriteRules.instructionFor(RewriteTone.PROFESSIONAL), payload.getString("instructions"))
        assertEquals(
            "<transcript>\nhi there\n</transcript>",
            payload.getJSONArray("input").getJSONObject(0).getString("content"),
        )
    }

    @Test
    fun staleCatalogIsSynchronizedBeforeRewriting() {
        val store = connectedStore(expiresAt = now / 1000.0 + 3600, syncedAt = 0.0)
        val http = FakeTransport()
            .reply(
                200,
                """{"models": [
                    {"slug": "new", "display_name": "New", "visibility": "list"},
                    {"slug": "hidden", "visibility": "hide"},
                    {"slug": "new", "display_name": "Duplicate", "visibility": "list"}
                ]}""",
            )
            .replyStream(200, completedStream)

        subscription(store, http).rewrite("text", "edit", "new")

        assertEquals("GET", http.requests.first().method)
        assertEquals("${ChatGPT.RESOURCE}/models", http.requests.first().url)
        val models = JSONObject(store.json!!).getJSONObject("profiles").getJSONObject("client-123456")
            .getJSONArray("models")
        assertEquals(1, models.length())
        assertEquals("new", models.getJSONObject(0).getString("slug"))
    }

    @Test
    fun removedModelIsReportedInsteadOfReplaced() {
        val store = connectedStore(expiresAt = now / 1000.0 + 3600, syncedAt = now / 1000.0)
        val error = assertThrows(ChatGPTError::class.java) {
            subscription(store, FakeTransport()).rewrite("text", "edit", "gone")
        }
        assertTrue(error.message!!.contains("no longer available"))
    }

    @Test
    fun expiredTokenIsRefreshedAndInvalidRefreshClearsCredentials() {
        val store = connectedStore(expiresAt = 0.0, syncedAt = now / 1000.0)
        val http = FakeTransport()
            .reply(
                200,
                """{"access_token": "access-2", "token_type": "Bearer", "expires_in": 3600,
                    "scope": "openid ${ChatGPT.DIRECT_SCOPE}"}""",
            )
            .replyStream(200, completedStream)
        subscription(store, http).rewrite("text", "edit", "")
        assertTrue(http.lastBody().contains("model"))
        assertTrue(http.requests.first().body!!.toString(Charsets.UTF_8).contains("grant_type=refresh_token"))
        assertEquals("Bearer access-2", http.requests.last().headers["Authorization"])

        val expired = connectedStore(expiresAt = 0.0, syncedAt = now / 1000.0)
        val rejecting = FakeTransport().reply(400, """{"error": {"code": "refresh_token_expired"}}""")
        val error = assertThrows(ChatGPTError::class.java) { subscription(expired, rejecting).rewrite("t", "e", "") }
        assertEquals("Your ChatGPT connection expired. Please reconnect.", error.message)
        assertFalse(subscription(expired, FakeTransport()).snapshot().connected)
    }

    @Test
    fun snapshotListsAccountsWithShortIds() {
        val store = connectedStore(expiresAt = now / 1000.0 + 3600, syncedAt = now / 1000.0)
        val snapshot = subscription(store, FakeTransport()).snapshot()
        assertTrue(snapshot.connected)
        assertEquals("test@example.com · 123456", snapshot.accounts.single().label)
        assertEquals(listOf(ChatGPTModel("first", "First")), snapshot.models)
    }

    @Test
    fun streamErrorsMapToActionableMessages() {
        val limited = listOf(
            """data: {"type": "response.failed", "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded"}}}""",
            "",
        )
        val error = assertThrows(ChatGPTError::class.java) { completedText(limited.asSequence()) }
        assertTrue(error.message!!.startsWith("ChatGPT plan or app usage limit reached"))

        assertThrows(ChatGPTError::class.java) { completedText(sequenceOf("data: {not json", "")) }
        val unfinished = assertThrows(ChatGPTError::class.java) {
            completedText(sequenceOf("""data: {"type": "response.output_text.delta", "delta": "x"}""", ""))
        }
        assertTrue(unfinished.message!!.contains("did not complete"))
    }

    @Test
    fun callbackQueryParsingMatchesPythonParseQs() {
        val query = ChatGPTSubscription.parseQuery("code=abc&state=xyz&empty=&state=again&plus=a+b")
        assertEquals(listOf("abc"), query["code"])
        assertEquals(listOf("xyz", "again"), query["state"])
        assertFalse(query.containsKey("empty"))
        assertEquals(listOf("a b"), query["plus"])
    }

    @Test
    fun idTokenSignatureAndClaimsAreVerified() {
        val keys = KeyPairGenerator.getInstance("RSA").apply { initialize(2048) }.generateKeyPair()
        val publicKey = keys.public as RSAPublicKey
        fun b64(bytes: ByteArray) = Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
        fun unsigned(value: BigInteger) = value.toByteArray().let { if (it[0].toInt() == 0) it.copyOfRange(1, it.size) else it }
        val jwks = JSONObject().put(
            "keys",
            JSONArray().put(
                JSONObject().put("kty", "RSA").put("kid", "k1").put("use", "sig")
                    .put("n", b64(unsigned(publicKey.modulus))).put("e", b64(unsigned(publicKey.publicExponent))),
            ),
        ).toString()

        fun token(nonce: String, audience: String, expiresIn: Long = 600): String {
            val header = b64("""{"alg":"RS256","kid":"k1"}""".toByteArray())
            val claims = JSONObject()
                .put("iss", ChatGPT.ISSUER).put("aud", JSONArray().put(audience)).put("sub", "user-1")
                .put("exp", now / 1000 + expiresIn).put("iat", now / 1000).put("nonce", nonce)
                .put("email", "test@example.com")
            val payload = b64(claims.toString().toByteArray())
            val signer = Signature.getInstance("SHA256withRSA").apply {
                initSign(keys.private)
                update("$header.$payload".toByteArray())
            }
            return "$header.$payload.${b64(signer.sign())}"
        }

        fun verifier() = IdTokenVerifier(FakeTransport().reply(200, jwks), clock = { now })
        val claims = verifier().verify(token("n-1", "client-1"), "client-1", "n-1")
        assertEquals("user-1", claims.getString("sub"))

        assertThrows(ChatGPTError::class.java) { verifier().verify(token("other", "client-1"), "client-1", "n-1") }
        assertThrows(ChatGPTError::class.java) { verifier().verify(token("n-1", "client-2"), "client-1", "n-1") }
        assertThrows(ChatGPTError::class.java) { verifier().verify(token("n-1", "client-1", -60), "client-1", "n-1") }
        val tampered = token("n-1", "client-1").let { it.substring(0, it.length - 4) + "AAAA" }
        assertThrows(ChatGPTError::class.java) { verifier().verify(tampered, "client-1", "n-1") }
    }
}
