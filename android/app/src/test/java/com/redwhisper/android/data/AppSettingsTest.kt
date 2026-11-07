package com.redwhisper.android.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class AppSettingsTest {
    @Test
    fun settingsRoundTripThroughJson() {
        val settings = AppSettings(
            engine = "elevenlabs",
            language = "auto",
            elevenlabsModel = "scribe_v2_realtime",
            rewriteEngine = "openrouter",
            rewriteTone = "sarcastic",
            openrouterModel = "google/gemma-3-27b-it:free",
            openrouterModels = listOf("openrouter/free", "google/gemma-3-27b-it:free"),
            microphoneGain = 4.0f,
            inputDevice = "bluetooth:AirPods Pro",
            replacementsName = "replacements.json",
            bubbleInAllApps = false,
            chatApps = listOf("com.whatsapp"),
        )
        assertEquals(settings, AppSettings.fromJson(settings.toJson()))
    }

    @Test
    fun missingKeysUseDefaults() {
        assertEquals(AppSettings(language = "fr"), AppSettings.fromJson("""{"language": "fr"}"""))
        // Settings saved before the tone setting existed keep the original casual behavior.
        assertEquals("casual", AppSettings.fromJson("""{"rewrite_engine": "openai"}""").rewriteTone)
        // The bubble now follows the keyboard into every app, including for earlier "chat apps" settings.
        assertEquals(true, AppSettings.fromJson("""{"bubble_everywhere": false}""").bubbleInAllApps)
    }

    @Test
    fun invalidValuesAreRejected() {
        listOf(
            AppSettings(engine = "local"),
            AppSettings(language = ""),
            AppSettings(rewriteEngine = "local"),
            AppSettings(rewriteTone = "formal"),
            AppSettings(openaiModel = "eleven_v3"),
            AppSettings(microphoneGain = 3.0f),
            AppSettings(openrouterModel = "two words"),
            AppSettings(chatgptRewriteModel = "has space"),
        ).forEach { settings ->
            assertThrows(IllegalArgumentException::class.java) { settings.validate() }
        }
    }
}
