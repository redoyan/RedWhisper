package com.redwhisper.android.core

import java.math.BigInteger
import java.security.KeyFactory
import java.security.Signature
import java.security.spec.RSAPublicKeySpec
import java.util.Base64
import org.json.JSONArray
import org.json.JSONObject

/**
 * Verifies a Sign in with ChatGPT ID token (RS256 against the issuer's JWKS), checking the
 * same claims the Mac app checks with PyJWT: issuer, audience, expiry, issue time, and nonce.
 */
class IdTokenVerifier(
    private val http: HttpTransport,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    fun verify(idToken: String, audience: String, nonce: String): JSONObject {
        try {
            val parts = idToken.split(".")
            require(parts.size == 3)
            val header = JSONObject(String(decode(parts[0]), Charsets.UTF_8))
            require(header.optString("alg") == "RS256")

            val signature = Signature.getInstance("SHA256withRSA")
            signature.initVerify(signingKey(header.optString("kid", "")))
            signature.update("${parts[0]}.${parts[1]}".toByteArray(Charsets.US_ASCII))
            require(signature.verify(decode(parts[2])))

            val claims = JSONObject(String(decode(parts[1]), Charsets.UTF_8))
            val now = clock() / 1000.0
            require(claims.optString("iss") == ChatGPT.ISSUER)
            require(audienceMatches(claims.opt("aud"), audience))
            require(listOf("sub", "exp", "iat", "nonce").all { claims.has(it) })
            require(claims.getDouble("exp") > now - LEEWAY_SECONDS)
            require(claims.getDouble("iat") <= now + LEEWAY_SECONDS)
            if (claims.has("nbf")) require(claims.getDouble("nbf") <= now + LEEWAY_SECONDS)
            val subject = claims.opt("sub")
            require(subject is String && subject.isNotEmpty())
            require(claims.opt("nonce") == nonce)
            return claims
        } catch (_: Exception) {
            throw ChatGPTError("ChatGPT sign-in identity could not be verified. Please sign in again.")
        }
    }

    private fun signingKey(kid: String): java.security.PublicKey {
        val response = http.execute(
            HttpRequest("${ChatGPT.ISSUER}/.well-known/jwks.json", method = "GET", timeoutSeconds = 30),
        )
        require(response.isSuccessful)
        val keys = parseJsonObject(response.body)?.optJSONArray("keys") ?: throw IllegalStateException()
        val candidates = (0 until keys.length()).mapNotNull { keys.optJSONObject(it) }.filter { key ->
            key.optString("kty") == "RSA" && key.optString("use", "sig") == "sig"
        }
        val jwk = if (kid.isNotEmpty()) {
            candidates.first { it.optString("kid") == kid }
        } else {
            candidates.single()
        }
        val spec = RSAPublicKeySpec(
            BigInteger(1, decode(jwk.getString("n"))),
            BigInteger(1, decode(jwk.getString("e"))),
        )
        return KeyFactory.getInstance("RSA").generatePublic(spec)
    }

    private fun audienceMatches(value: Any?, audience: String): Boolean = when (value) {
        is String -> value == audience
        is JSONArray -> (0 until value.length()).any { value.opt(it) == audience }
        else -> false
    }

    private fun decode(segment: String): ByteArray = Base64.getUrlDecoder().decode(segment)

    private companion object {
        const val LEEWAY_SECONDS = 5
    }
}
