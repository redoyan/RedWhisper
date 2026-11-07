package com.redwhisper.android.core

import java.io.IOException
import java.net.URLEncoder
import java.util.Base64
import java.util.UUID
import org.json.JSONObject

interface Transcriber {
    fun transcribe(audio: AudioClip): String
}

data class OpenAITranscriptionOptions(
    val apiKey: String,
    val model: String = "gpt-transcribe",
    val language: String? = null,
    val prompt: String? = null,
    val timeoutSeconds: Long = 120,
)

/** Explicit cloud transcription through OpenAI's Audio API. */
class OpenAITranscriber(
    private val options: OpenAITranscriptionOptions,
    private val http: HttpTransport,
) : Transcriber {
    init {
        if (options.apiKey.isBlank()) {
            throw ConfigurationError("Add an OpenAI API key under API Keys to use OpenAI transcription")
        }
        if (options.model !in Providers.OPENAI_TRANSCRIPTION_MODELS) {
            throw ConfigurationError("Unsupported OpenAI transcription model: ${options.model}")
        }
    }

    override fun transcribe(audio: AudioClip): String {
        val fields = linkedMapOf("model" to options.model)
        if (options.model == "gpt-4o-transcribe-diarize") {
            fields["response_format"] = "diarized_json"
            fields["chunking_strategy"] = "auto"
        } else {
            fields["response_format"] = "json"
            options.language?.takeIf { it.isNotEmpty() }?.let { language ->
                val field = if (options.model == "gpt-transcribe") "languages[]" else "language"
                fields[field] = language
            }
            options.prompt?.takeIf { it.isNotEmpty() }?.let { fields["prompt"] = it }
        }
        val boundary = "----redwhisper-${UUID.randomUUID().toString().replace("-", "")}"
        val response = try {
            http.execute(
                HttpRequest(
                    url = Providers.OPENAI_TRANSCRIPTION_URL,
                    headers = mapOf("Authorization" to "Bearer ${options.apiKey}"),
                    body = multipartBody(boundary, fields, audio.toWav(), "dictation.wav"),
                    contentType = "multipart/form-data; boundary=$boundary",
                    timeoutSeconds = options.timeoutSeconds,
                ),
            )
        } catch (error: IOException) {
            throw TranscriptionError("OpenAI transcription unavailable: ${error.message}", error)
        }
        if (!response.isSuccessful) {
            throw TranscriptionError(
                "OpenAI transcription failed (HTTP ${response.code}): ${apiErrorMessage(response.body)}",
            )
        }
        val payload = parseJsonObject(response.body)
            ?: throw TranscriptionError("OpenAI returned an invalid transcription response")
        val text = payload.opt("text") as? String
            ?: throw TranscriptionError("OpenAI transcription response did not contain text")
        return text.trim()
    }
}

data class ElevenLabsTranscriptionOptions(
    val apiKey: String,
    val model: String = "scribe_v2",
    val language: String? = null,
    val timeoutSeconds: Long = 120,
)

/** Explicit cloud transcription through ElevenLabs Scribe. */
class ElevenLabsTranscriber(
    private val options: ElevenLabsTranscriptionOptions,
    private val http: HttpTransport,
    private val clock: () -> Long = System::currentTimeMillis,
) : Transcriber {
    init {
        if (options.apiKey.isBlank()) {
            throw ConfigurationError("Add an ElevenLabs API key under API Keys to use ElevenLabs transcription")
        }
        if (options.model !in Providers.ELEVENLABS_TRANSCRIPTION_MODELS) {
            throw ConfigurationError("Unsupported ElevenLabs transcription model: ${options.model}")
        }
    }

    override fun transcribe(audio: AudioClip): String {
        if (options.model == "scribe_v2_realtime") return transcribeRealtime(audio)

        val fields = linkedMapOf("model_id" to options.model, "tag_audio_events" to "false")
        options.language?.takeIf { it.isNotEmpty() }?.let { fields["language_code"] = languageCode(it) }
        val boundary = "----redwhisper-${UUID.randomUUID().toString().replace("-", "")}"
        val response = try {
            http.execute(
                HttpRequest(
                    url = Providers.ELEVENLABS_TRANSCRIPTION_URL,
                    headers = mapOf("xi-api-key" to options.apiKey),
                    body = multipartBody(boundary, fields, audio.toWav(), "dictation.wav"),
                    contentType = "multipart/form-data; boundary=$boundary",
                    timeoutSeconds = options.timeoutSeconds,
                ),
            )
        } catch (error: IOException) {
            throw TranscriptionError("ElevenLabs transcription unavailable: ${error.message}", error)
        }
        if (!response.isSuccessful) {
            throw TranscriptionError(
                "ElevenLabs transcription failed (HTTP ${response.code}): ${apiErrorMessage(response.body)}",
            )
        }
        val payload = parseJsonObject(response.body)
            ?: throw TranscriptionError("ElevenLabs returned an invalid transcription response")
        val text = payload.opt("text") as? String
            ?: throw TranscriptionError("ElevenLabs transcription response did not contain text")
        return text.trim()
    }

    private fun transcribeRealtime(audio: AudioClip): String {
        if (audio.sampleRate != AudioClip.SAMPLE_RATE) {
            throw TranscriptionError("ElevenLabs Realtime requires mono 16-bit PCM audio at 16 kHz")
        }
        if (audio.pcm16.isEmpty()) return ""

        val query = linkedMapOf(
            "model_id" to options.model,
            "audio_format" to "pcm_16000",
            "commit_strategy" to "manual",
        )
        options.language?.takeIf { it.isNotEmpty() }?.let { query["language_code"] = it }
        val url = Providers.ELEVENLABS_REALTIME_URL + "?" + query.entries.joinToString("&") { (key, value) ->
            "${URLEncoder.encode(key, "UTF-8")}=${URLEncoder.encode(value, "UTF-8")}"
        }
        // Half a second of 16-bit audio per message, as on the Mac.
        val chunkSize = audio.sampleRate * 2 / 2
        val committed = mutableListOf<String>()
        val deadline = clock() + options.timeoutSeconds * 1000

        try {
            http.openWebSocket(
                url,
                mapOf("xi-api-key" to options.apiKey),
                openTimeoutMillis = minOf(15_000L, options.timeoutSeconds * 1000),
            ).use { socket ->
                val session = realtimeMessage(
                    socket.receive(15_000) ?: throw IOException("timed out waiting for the session"),
                )
                raiseRealtimeError(session)
                if (session.optString("message_type") != "session_started") {
                    throw TranscriptionError("ElevenLabs Realtime did not start a transcription session")
                }

                val chunks = (audio.pcm16.indices step chunkSize).map { offset ->
                    audio.pcm16.copyOfRange(offset, minOf(offset + chunkSize, audio.pcm16.size))
                }
                chunks.forEachIndexed { index, chunk ->
                    val message = JSONObject()
                        .put("message_type", "input_audio_chunk")
                        .put("audio_base_64", Base64.getEncoder().encodeToString(chunk))
                        .put("sample_rate", audio.sampleRate)
                    if (index == chunks.lastIndex) message.put("commit", true)
                    socket.send(message.toString())
                }

                while (clock() < deadline) {
                    val waitMillis = minOf(5_000L, deadline - clock())
                    val raw = socket.receive(waitMillis)
                    if (raw == null) {
                        if (committed.isNotEmpty()) break
                        continue
                    }
                    val message = realtimeMessage(raw)
                    raiseRealtimeError(message)
                    if (message.optString("message_type") == "committed_transcript") {
                        val text = message.opt("text")
                        if (text is String && text.isNotBlank()) committed.add(text.trim())
                    }
                }
            }
        } catch (error: TranscriptionError) {
            throw error
        } catch (error: Exception) {
            val detail = (error.message ?: error.javaClass.simpleName).replace(options.apiKey, "[redacted]")
            throw TranscriptionError("ElevenLabs Realtime transcription unavailable: $detail", error)
        }

        if (committed.isEmpty()) {
            throw TranscriptionError("ElevenLabs Realtime response did not contain committed text")
        }
        return committed.joinToString(" ")
    }

    private fun realtimeMessage(message: String): JSONObject =
        parseJsonObject(message) ?: throw TranscriptionError("ElevenLabs Realtime returned an invalid response")

    private fun raiseRealtimeError(message: JSONObject) {
        if (message.optString("message_type") !in setOf("error", "auth_error", "quota_error")) return
        val detail = message.opt("error")?.takeIf { it != JSONObject.NULL && it.toString().isNotEmpty() }
            ?: message.opt("message")?.takeIf { it != JSONObject.NULL && it.toString().isNotEmpty() }
            ?: "request rejected"
        throw TranscriptionError("ElevenLabs Realtime transcription failed: ${detail.toString().take(300)}")
    }

    companion object {
        /** RedWhisper stores English as ISO-639-1, while Scribe's batch API documents ISO-639-3. */
        fun languageCode(language: String): String = if (language.lowercase() == "en") "eng" else language
    }
}
