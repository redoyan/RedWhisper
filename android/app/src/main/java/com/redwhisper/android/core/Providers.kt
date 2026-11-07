package com.redwhisper.android.core

/** Provider endpoints and model catalogs, kept identical to the Mac app's `mlx_whisper_core.py`. */
object Providers {
    const val OPENAI_TRANSCRIPTION_URL = "https://api.openai.com/v1/audio/transcriptions"
    const val ELEVENLABS_TRANSCRIPTION_URL = "https://api.elevenlabs.io/v1/speech-to-text"
    const val ELEVENLABS_REALTIME_URL = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
    const val OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
    const val OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"

    val OPENAI_TRANSCRIPTION_MODELS = listOf(
        "gpt-transcribe",
        "gpt-4o-transcribe",
        "gpt-4o-mini-transcribe",
        "gpt-4o-transcribe-diarize",
        "whisper-1",
    )
    val ELEVENLABS_TRANSCRIPTION_MODELS = listOf("scribe_v2", "scribe_v2_realtime", "scribe_v1")
    val OPENAI_REWRITE_MODELS = listOf(
        "gpt-5-nano",
        "gpt-5-mini",
        "gpt-5.6-luna",
        "gpt-5.6-terra",
        "gpt-5.6-sol",
    )
    val DEFAULT_OPENAI_REWRITE_MODEL = OPENAI_REWRITE_MODELS.first()
    const val DEFAULT_OPENROUTER_MODEL = "openrouter/free"
}

fun validateOpenRouterModel(model: String): String {
    val value = model.trim()
    if (value.isEmpty() || value.length > 200 || value.any { it.isWhitespace() }) {
        throw ConfigurationError("Enter a valid OpenRouter model ID")
    }
    return value
}
