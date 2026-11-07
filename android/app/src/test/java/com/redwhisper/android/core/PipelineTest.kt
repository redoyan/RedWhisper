package com.redwhisper.android.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class PipelineTest {
    private val clip = AudioClip(ByteArray(4))

    private fun transcriber(text: String) = object : Transcriber {
        override fun transcribe(audio: AudioClip) = text
    }

    @Test
    fun replacementThenExplicitPostProcessing() {
        val seen = mutableListOf<String>()
        val pipeline = TranscriptPipeline(
            transcriber("ship red whispr today"),
            TermReplacer(mapOf("red whispr" to "Red Whisper")),
            object : PostProcessor {
                override fun process(text: String): String = text.also { seen.add(it) } + "."
            },
        )
        assertEquals("ship Red Whisper today.", pipeline.process(clip))
        assertEquals(listOf("ship Red Whisper today"), seen)
    }

    @Test
    fun knownHallucinationOnlyOutputIsDropped() {
        assertEquals("", TranscriptPipeline(transcriber(" Thanks for watching! ")).process(clip))
        assertEquals("", TranscriptPipeline(transcriber("♪ Music ♪")).process(clip))
    }

    @Test
    fun validSentenceContainingMusicIsNotDropped() {
        assertEquals("Turn the music down.", TranscriptPipeline(transcriber("Turn the music down.")).process(clip))
    }

    @Test
    fun transcriptWrapperTagsAreRemoved() {
        assertEquals(
            "hello there",
            TranscriptPipeline(transcriber("<transcript>hello there</transcript>")).process(clip),
        )
    }

    @Test
    fun postProcessingFailurePreservesTheOriginalTranscript() {
        val pipeline = TranscriptPipeline(
            transcriber("keep this exactly"),
            postProcessor = object : PostProcessor {
                override fun process(text: String): String = throw TranscriptionError("rewrite down")
            },
        )
        assertEquals("keep this exactly", pipeline.process(clip))
        assertEquals("TranscriptionError: rewrite down", pipeline.consumePostProcessWarning())
        assertNull(pipeline.consumePostProcessWarning())
    }

    @Test
    fun longestTermsAreReplacedFirstCaseInsensitively() {
        val replacer = TermReplacer(mapOf("open ai" to "OpenAI", "open ai whisper" to "OpenAI Whisper", "mlx" to "MLX"))
        assertEquals("OpenAI Whisper runs on MLX, not OpenAI", replacer.process("Open AI whisper runs on mlx, not open ai"))
    }

    @Test
    fun replacementJsonMustBeAnObjectOfStrings() {
        assertEquals(1, TermReplacer.fromJson("""{"mlx": "MLX"}""").size)
        assertThrows(ConfigurationError::class.java) { TermReplacer.fromJson("[1, 2]") }
        assertThrows(ConfigurationError::class.java) { TermReplacer.fromJson("""{"mlx": 3}""") }
        assertThrows(ConfigurationError::class.java) { TermReplacer.fromJson("""{"": "x"}""") }
        assertTrue(TermReplacer.fromJson("{}").process("unchanged") == "unchanged")
    }
}
