package com.redwhisper.android.dictation

import com.redwhisper.android.RedWhisperApp
import com.redwhisper.android.core.ChatGPTTextPostProcessor
import com.redwhisper.android.core.ElevenLabsTranscriber
import com.redwhisper.android.core.ElevenLabsTranscriptionOptions
import com.redwhisper.android.core.OpenAIRewriteOptions
import com.redwhisper.android.core.OpenAITextPostProcessor
import com.redwhisper.android.core.OpenAITranscriber
import com.redwhisper.android.core.OpenAITranscriptionOptions
import com.redwhisper.android.core.OpenRouterRewriteOptions
import com.redwhisper.android.core.OpenRouterTextPostProcessor
import com.redwhisper.android.core.PostProcessor
import com.redwhisper.android.core.RewriteTone
import com.redwhisper.android.core.TermReplacer
import com.redwhisper.android.core.TranscriptPipeline
import com.redwhisper.android.data.AppSettings
import com.redwhisper.android.data.SecretStore

/** Builds the same pipeline `voxtape.main` builds from saved settings, minus local models. */
object PipelineFactory {
    /** The Mac app's default `--initial-prompt`. */
    const val INITIAL_PROMPT = "Voice dictation. Use accurate punctuation and paragraph breaks."

    fun create(app: RedWhisperApp, settings: AppSettings): TranscriptPipeline {
        val secrets = app.secrets
        val language = settings.language.takeUnless { it == "auto" }
        val transcriber = when (settings.engine) {
            "elevenlabs" -> ElevenLabsTranscriber(
                ElevenLabsTranscriptionOptions(
                    apiKey = secrets.get(SecretStore.ELEVENLABS_API_KEY).orEmpty(),
                    model = settings.elevenlabsModel,
                    language = language,
                ),
                app.http,
            )
            else -> OpenAITranscriber(
                OpenAITranscriptionOptions(
                    apiKey = secrets.get(SecretStore.OPENAI_API_KEY).orEmpty(),
                    model = settings.openaiModel,
                    language = language,
                    prompt = INITIAL_PROMPT,
                ),
                app.http,
            )
        }
        val replacements = app.settingsStore.replacementsJson?.let(TermReplacer::fromJson)
        val tone = RewriteTone.from(settings.rewriteTone)
        val postProcessor: PostProcessor? = when (settings.rewriteEngine) {
            "openai" -> OpenAITextPostProcessor(
                OpenAIRewriteOptions(
                    secrets.get(SecretStore.OPENAI_API_KEY).orEmpty(),
                    settings.openaiRewriteModel,
                    tone,
                ),
                app.http,
            )
            "openrouter" -> OpenRouterTextPostProcessor(
                OpenRouterRewriteOptions(
                    secrets.get(SecretStore.OPENROUTER_API_KEY).orEmpty(),
                    settings.openrouterModel,
                    tone,
                ),
                app.http,
            )
            "chatgpt" -> ChatGPTTextPostProcessor(settings.chatgptRewriteModel, app.chatGpt, tone)
            else -> null
        }
        return TranscriptPipeline(transcriber, replacements, postProcessor)
    }
}
