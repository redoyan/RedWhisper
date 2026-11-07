package com.redwhisper.android.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import com.redwhisper.android.core.AccountStateStore
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * API keys and ChatGPT tokens, encrypted with a non-exportable Android Keystore key. This plays
 * the role macOS Keychain plays in the Mac app; nothing secret goes into settings.
 */
class SecretStore(context: Context) {
    private val prefs = context.getSharedPreferences("secrets", Context.MODE_PRIVATE)

    fun get(name: String): String? {
        val stored = prefs.getString(name, null) ?: return null
        return try {
            val (iv, ciphertext) = stored.split(":").map { Base64.decode(it, Base64.NO_WRAP) }
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, iv))
            String(cipher.doFinal(ciphertext), Charsets.UTF_8)
        } catch (_: Exception) {
            // A key lost with a restore or a cleared credential store reads as "not saved".
            null
        }
    }

    fun put(name: String, value: String?) {
        if (value == null) {
            check(prefs.edit().remove(name).commit()) { "Could not update secure storage" }
            return
        }
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val ciphertext = cipher.doFinal(value.toByteArray(Charsets.UTF_8))
        val encoded = Base64.encodeToString(cipher.iv, Base64.NO_WRAP) + ":" +
            Base64.encodeToString(ciphertext, Base64.NO_WRAP)
        check(prefs.edit().putString(name, encoded).commit()) { "Could not update secure storage" }
    }

    fun has(name: String): Boolean = !get(name).isNullOrEmpty()

    /** ChatGPT account state for the shared subscription client. */
    val chatGptAccounts: AccountStateStore = object : AccountStateStore {
        override fun load(): String? = get(CHATGPT_ACCOUNTS)
        override fun save(json: String) = put(CHATGPT_ACCOUNTS, json)
    }

    private fun key(): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build(),
        )
        return generator.generateKey()
    }

    companion object {
        const val OPENAI_API_KEY = "openai_api_key"
        const val OPENROUTER_API_KEY = "openrouter_api_key"
        const val ELEVENLABS_API_KEY = "elevenlabs_api_key"
        private const val CHATGPT_ACCOUNTS = "chatgpt_accounts"
        private const val ANDROID_KEYSTORE = "AndroidKeyStore"
        private const val KEY_ALIAS = "redwhisper_secrets"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
    }
}
