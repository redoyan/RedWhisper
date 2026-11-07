package com.redwhisper.android.data

import com.redwhisper.android.core.AudioLevels
import com.redwhisper.android.core.Providers
import com.redwhisper.android.core.RewriteTone
import com.redwhisper.android.core.validateOpenRouterModel
import org.json.JSONArray
import org.json.JSONObject

/**
 * Non-secret settings, the Android counterpart of the Mac app's `AppSettings`. Keyboard
 * shortcuts become bubble settings, and transcription is cloud-only in this version.
 */
data class AppSettings(
    val engine: String = "openai",
    val language: String = "en",
    val openaiModel: String = "gpt-transcribe",
    val elevenlabsModel: String = "scribe_v2",
    val rewriteEngine: String = "off",
    val rewriteTone: String = RewriteTone.CASUAL.value,
    val openaiRewriteModel: String = Providers.DEFAULT_OPENAI_REWRITE_MODEL,
    val openrouterModel: String = Providers.DEFAULT_OPENROUTER_MODEL,
    val openrouterModels: List<String> = listOf(Providers.DEFAULT_OPENROUTER_MODEL),
    val chatgptRewriteModel: String = "",
    val microphoneGain: Float = 2.0f,
    val inputDevice: String? = null,
    val replacementsName: String? = null,
    val bubbleEnabled: Boolean = true,
    val bubbleInAllApps: Boolean = true,
    val chatApps: List<String> = ChatApps.DEFAULTS,
) {
    fun validate() {
        require(engine in ENGINES) { "Invalid transcription engine" }
        require(language.isNotEmpty() && language.length <= 20) { "Enter a language code such as en, or auto" }
        require(rewriteEngine in REWRITE_ENGINES) { "Invalid restructuring engine" }
        require(RewriteTone.entries.any { it.value == rewriteTone }) { "Choose a casual or professional tone" }
        require(openaiModel in Providers.OPENAI_TRANSCRIPTION_MODELS) { "Invalid OpenAI model" }
        require(elevenlabsModel in Providers.ELEVENLABS_TRANSCRIPTION_MODELS) { "Invalid ElevenLabs model" }
        require(openaiRewriteModel in Providers.OPENAI_REWRITE_MODELS) { "Invalid OpenAI restructuring model" }
        require(chatgptRewriteModel.length <= 200 && chatgptRewriteModel.none { it.isWhitespace() }) {
            "Invalid ChatGPT restructuring model"
        }
        validateOpenRouterModel(openrouterModel)
        require(openrouterModels.size <= 50) { "Invalid OpenRouter model list" }
        openrouterModels.forEach(::validateOpenRouterModel)
        require(microphoneGain in AudioLevels.GAINS) { "Invalid microphone sensitivity" }
        require(chatApps.size <= 500) { "Too many chat apps" }
    }

    fun toJson(): String = JSONObject()
        .put("engine", engine)
        .put("language", language)
        .put("openai_model", openaiModel)
        .put("elevenlabs_model", elevenlabsModel)
        .put("rewrite_engine", rewriteEngine)
        .put("rewrite_tone", rewriteTone)
        .put("openai_rewrite_model", openaiRewriteModel)
        .put("openrouter_model", openrouterModel)
        .put("openrouter_models", JSONArray(openrouterModels))
        .put("chatgpt_rewrite_model", chatgptRewriteModel)
        .put("microphone_gain", microphoneGain.toDouble())
        .put("input_device", inputDevice ?: JSONObject.NULL)
        .put("replacements_name", replacementsName ?: JSONObject.NULL)
        .put("bubble_enabled", bubbleEnabled)
        .put("bubble_all_apps", bubbleInAllApps)
        .put("chat_apps", JSONArray(chatApps))
        .toString(2)

    companion object {
        val ENGINES = listOf("openai", "elevenlabs")
        val REWRITE_ENGINES = listOf("off", "openai", "openrouter", "chatgpt")

        fun fromJson(json: String): AppSettings {
            val payload = JSONObject(json)
            val defaults = AppSettings()
            fun string(key: String, default: String) = if (payload.has(key)) payload.getString(key) else default
            fun optionalString(key: String, default: String?) =
                if (!payload.has(key) || payload.isNull(key)) default else payload.getString(key)
            fun boolean(key: String, default: Boolean) = if (payload.has(key)) payload.getBoolean(key) else default
            fun list(key: String, default: List<String>) = payload.optJSONArray(key)
                ?.let { array -> (0 until array.length()).map { array.getString(it) } } ?: default

            val settings = AppSettings(
                engine = string("engine", defaults.engine),
                language = string("language", defaults.language),
                openaiModel = string("openai_model", defaults.openaiModel),
                elevenlabsModel = string("elevenlabs_model", defaults.elevenlabsModel),
                rewriteEngine = string("rewrite_engine", defaults.rewriteEngine),
                rewriteTone = string("rewrite_tone", defaults.rewriteTone),
                openaiRewriteModel = string("openai_rewrite_model", defaults.openaiRewriteModel),
                openrouterModel = string("openrouter_model", defaults.openrouterModel),
                openrouterModels = list("openrouter_models", defaults.openrouterModels),
                chatgptRewriteModel = string("chatgpt_rewrite_model", defaults.chatgptRewriteModel),
                microphoneGain = if (payload.has("microphone_gain")) {
                    payload.getDouble("microphone_gain").toFloat()
                } else {
                    defaults.microphoneGain
                },
                inputDevice = optionalString("input_device", defaults.inputDevice),
                replacementsName = optionalString("replacements_name", defaults.replacementsName),
                bubbleEnabled = boolean("bubble_enabled", defaults.bubbleEnabled),
                bubbleInAllApps = boolean("bubble_all_apps", defaults.bubbleInAllApps),
                chatApps = list("chat_apps", defaults.chatApps),
            )
            settings.validate()
            return settings
        }
    }
}
