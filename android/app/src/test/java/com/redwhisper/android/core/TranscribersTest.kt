package com.redwhisper.android.core

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class TranscribersTest {
    private val clip = AudioClip(ByteArray(32_000) { (it % 7).toByte() })

    @Test
    fun gptTranscribeUsesPluralLanguageHint() {
        val http = FakeTransport().reply(200, """{"text": " Hello there. "}""")
        val text = OpenAITranscriber(
            OpenAITranscriptionOptions("test-key", model = "gpt-transcribe", language = "en", prompt = "Voice dictation."),
            http,
        ).transcribe(clip)

        assertEquals("Hello there.", text)
        val body = http.lastBody()
        assertTrue(body.contains("name=\"languages[]\"\r\n\r\nen"))
        assertTrue(body.contains("name=\"prompt\"\r\n\r\nVoice dictation."))
        assertTrue(body.contains("name=\"response_format\"\r\n\r\njson"))
        assertTrue(body.contains("filename=\"dictation.wav\""))
        assertEquals("Bearer test-key", http.requests.last().headers["Authorization"])
    }

    @Test
    fun diarizationModelRequestsCompatibleResponse() {
        val http = FakeTransport().reply(200, """{"text": "Speaker text"}""")
        OpenAITranscriber(
            OpenAITranscriptionOptions("test-key", model = "gpt-4o-transcribe-diarize", language = "en", prompt = "hint"),
            http,
        ).transcribe(clip)

        val body = http.lastBody()
        assertTrue(body.contains("diarized_json"))
        assertTrue(body.contains("name=\"chunking_strategy\"\r\n\r\nauto"))
        assertFalse(body.contains("name=\"language\""))
        assertFalse(body.contains("name=\"prompt\""))
    }

    @Test
    fun httpErrorDoesNotIncludeApiKey() {
        val http = FakeTransport().reply(401, """{"error": {"message": "Incorrect API key provided"}}""")
        val error = assertThrows(TranscriptionError::class.java) {
            OpenAITranscriber(OpenAITranscriptionOptions("test-key"), http).transcribe(clip)
        }
        assertEquals("OpenAI transcription failed (HTTP 401): Incorrect API key provided", error.message)
        assertFalse(error.message!!.contains("test-key"))
    }

    @Test
    fun apiKeyAndSupportedModelAreRequired() {
        assertThrows(ConfigurationError::class.java) {
            OpenAITranscriber(OpenAITranscriptionOptions(" "), FakeTransport())
        }
        assertThrows(ConfigurationError::class.java) {
            OpenAITranscriber(OpenAITranscriptionOptions("test-key", model = "eleven_v3"), FakeTransport())
        }
        assertThrows(ConfigurationError::class.java) {
            ElevenLabsTranscriber(ElevenLabsTranscriptionOptions("test-key", model = "eleven_v3"), FakeTransport())
        }
    }

    @Test
    fun elevenLabsRequestUsesScribeKeyAndCleanDictationFields() {
        val http = FakeTransport().reply(200, """{"text": "Scribe output"}""")
        val text = ElevenLabsTranscriber(
            ElevenLabsTranscriptionOptions("test-key", model = "scribe_v2", language = "en"),
            http,
        ).transcribe(clip)

        assertEquals("Scribe output", text)
        val request = http.requests.last()
        assertEquals(Providers.ELEVENLABS_TRANSCRIPTION_URL, request.url)
        assertEquals("test-key", request.headers["xi-api-key"])
        val body = http.lastBody()
        assertTrue(body.contains("name=\"model_id\"\r\n\r\nscribe_v2"))
        assertTrue(body.contains("name=\"tag_audio_events\"\r\n\r\nfalse"))
        assertTrue(body.contains("name=\"language_code\"\r\n\r\neng"))
    }

    @Test
    fun realtimeModelStreamsPcmAndReturnsCommittedText() {
        val http = FakeTransport()
        val socket = FakeSocket(
            mutableListOf(
                """{"message_type": "session_started"}""",
                """{"message_type": "partial_transcript", "text": "Hel"}""",
                """{"message_type": "committed_transcript", "text": "Hello from realtime."}""",
            ),
        )
        http.socket = socket
        val text = ElevenLabsTranscriber(
            ElevenLabsTranscriptionOptions("test-key", model = "scribe_v2_realtime", language = "en"),
            http,
        ).transcribe(clip)

        assertEquals("Hello from realtime.", text)
        assertTrue(http.socketUrl!!.startsWith(Providers.ELEVENLABS_REALTIME_URL + "?model_id=scribe_v2_realtime"))
        assertTrue(http.socketUrl!!.contains("audio_format=pcm_16000"))
        assertTrue(http.socketUrl!!.contains("commit_strategy=manual"))
        assertTrue(http.socketUrl!!.contains("language_code=en"))
        assertEquals("test-key", http.socketHeaders["xi-api-key"])
        // One second of audio arrives as two half-second chunks, and only the last commits.
        assertEquals(2, socket.sent.size)
        assertFalse(JSONObject(socket.sent.first()).has("commit"))
        assertTrue(JSONObject(socket.sent.last()).getBoolean("commit"))
        assertEquals(16_000, JSONObject(socket.sent.first()).getInt("sample_rate"))
        assertTrue(socket.closed)
    }

    @Test
    fun realtimeErrorsAreReportedWithoutTheKey() {
        val http = FakeTransport()
        http.socket = FakeSocket(mutableListOf("""{"message_type": "auth_error", "error": "invalid key test-key"}"""))
        val error = assertThrows(TranscriptionError::class.java) {
            ElevenLabsTranscriber(ElevenLabsTranscriptionOptions("test-key", model = "scribe_v2_realtime"), http)
                .transcribe(clip)
        }
        assertTrue(error.message!!.startsWith("ElevenLabs Realtime transcription failed"))
    }

    @Test
    fun wavHeaderDescribesMono16BitAudio() {
        val wav = AudioClip(ByteArray(8)).toWav()
        assertEquals(52, wav.size)
        assertEquals("RIFF", String(wav, 0, 4))
        assertEquals("WAVE", String(wav, 8, 4))
        assertEquals(1, wav[22].toInt()) // channels
        assertEquals(16, wav[34].toInt()) // bits per sample
    }

    @Test
    fun gainClipsInsteadOfWrapping() {
        val boosted = AudioLevels.applyGain(shortArrayOf(20_000, -20_000, 100), 2.0f)
        assertEquals(Short.MAX_VALUE, boosted[0])
        assertEquals(Short.MIN_VALUE, boosted[1])
        assertEquals(200.toShort(), boosted[2])
        assertThrows(IllegalArgumentException::class.java) { AudioLevels.applyGain(shortArrayOf(1), 3.0f) }
    }
}
