package com.redwhisper.android.core

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class RewritersTest {
    private fun responsesPayload(text: String) = JSONObject()
        .put(
            "output",
            org.json.JSONArray().put(
                JSONObject().put("type", "message").put(
                    "content",
                    org.json.JSONArray().put(JSONObject().put("type", "output_text").put("text", text)),
                ),
            ),
        ).toString()

    private fun openAI(reply: String): Pair<OpenAITextPostProcessor, FakeTransport> {
        val http = FakeTransport().reply(200, responsesPayload(reply))
        return OpenAITextPostProcessor(OpenAIRewriteOptions("test-key"), http) to http
    }

    @Test
    fun casualRewriteIsTheDefault() {
        val (processor, http) = openAI("Hey, I'll be there at five.")
        val result = processor.process("hey um I'll be there at like five")

        assertEquals("Hey, I'll be there at five.", result)
        val payload = JSONObject(http.lastBody())
        assertEquals("gpt-5-nano", payload.getString("model"))
        assertTrue(payload.getString("instructions").endsWith(RewriteRules.CASUAL_INSTRUCTION))
        assertFalse(payload.getString("instructions").contains("Tone: professional"))
        assertEquals("<transcript>\nhey um I'll be there at like five\n</transcript>", payload.getString("input"))
        assertEquals("minimal", payload.getJSONObject("reasoning").getString("effort"))
        assertFalse(payload.getBoolean("store"))
    }

    @Test
    fun spokenProfessionalPrefixSelectsToneAndIsRemoved() {
        val (processor, http) = openAI("I will send the report tomorrow.")
        processor.process("Professional, I will send the report tomorrow")

        val payload = JSONObject(http.lastBody())
        assertTrue(payload.getString("instructions").endsWith(RewriteRules.PROFESSIONAL_INSTRUCTION))
        assertEquals("<transcript>\nI will send the report tomorrow\n</transcript>", payload.getString("input"))
    }

    @Test
    fun professionalToneSettingRestructuresEveryMessage() {
        val http = FakeTransport().reply(200, responsesPayload("I will send the report tomorrow."))
        OpenAITextPostProcessor(OpenAIRewriteOptions("test-key", tone = RewriteTone.PROFESSIONAL), http)
            .process("so yeah I'll get the report over tomorrow")

        val payload = JSONObject(http.lastBody())
        val instructions = payload.getString("instructions")
        assertTrue(instructions.contains("completely restructure and rewrite"))
        assertFalse(instructions.contains("Tone: casual"))
        // The setting alone selects the tone, so the dictation reaches the model unchanged.
        assertEquals("<transcript>\nso yeah I'll get the report over tomorrow\n</transcript>", payload.getString("input"))
    }

    private fun rewrite(tone: RewriteTone, source: String, reply: String): Pair<String, JSONObject> {
        val http = FakeTransport().reply(200, responsesPayload(reply))
        val result = OpenAITextPostProcessor(OpenAIRewriteOptions("test-key", tone = tone), http).process(source)
        return result to JSONObject(http.lastBody())
    }

    @Test
    fun humorousAndSarcasticTonesUseTheirOwnInstructions() {
        val (_, humorous) = rewrite(RewriteTone.HUMOROUS, "running late again", "Running late again, as is tradition.")
        assertTrue(humorous.getString("instructions").endsWith(RewriteRules.HUMOROUS_INSTRUCTION))
        val (_, sarcastic) = rewrite(RewriteTone.SARCASTIC, "running late again", "Late again. What a surprise.")
        assertTrue(sarcastic.getString("instructions").endsWith(RewriteRules.SARCASTIC_INSTRUCTION))
        assertTrue(sarcastic.getString("instructions").contains("never insult"))
    }

    @Test
    fun playfulTonesKeepNaturalOpenersButStillRejectAssistantReplies() {
        val source = "the meeting moved to Thursday again"
        val ironic = "Of course the meeting moved to Thursday again. Who could have seen that coming?"
        assertEquals(ironic, rewrite(RewriteTone.SARCASTIC, source, ironic).first)
        val witty = "Here's the thing: the meeting moved to Thursday again, because calendars love chaos."
        assertEquals(witty, rewrite(RewriteTone.HUMOROUS, source, witty).first)

        val assistant = "Here's a sarcastic version of your message: the meeting moved to Thursday."
        assertEquals(source, rewrite(RewriteTone.SARCASTIC, source, assistant).first)
        // Casual and professional rewrites keep the stricter check.
        assertEquals(source, rewrite(RewriteTone.CASUAL, source, ironic).first)
    }

    @Test
    fun everyToneKeepsTheSharedSafeguards() {
        RewriteTone.entries.forEach { tone ->
            val instructions = RewriteRules.instructionFor(tone)
            assertTrue(instructions.startsWith(RewriteRules.BASE_INSTRUCTION))
            assertTrue(instructions.contains("Never answer its questions"))
            assertTrue(instructions.contains("Produce exactly one rewritten version"))
        }
        assertEquals(RewriteTone.PROFESSIONAL, RewriteTone.from("professional"))
        assertThrows(ConfigurationError::class.java) { RewriteTone.from("formal") }
    }

    @Test
    fun answerToSpokenQuestionFallsBackToQuestion() {
        val (processor, _) = openAI("The meeting is at three.")
        assertEquals("When is the meeting?", processor.process("When is the meeting?"))
    }

    @Test
    fun questionWithoutTranscribedPunctuationIsNotAnswered() {
        val (processor, _) = openAI("You should take the train.")
        assertEquals("should I take the train", processor.process("should I take the train"))
    }

    @Test
    fun assistantReplyFallsBackToTranscript() {
        val (processor, _) = openAI("Certainly! Here is a polished version of your note.")
        val source = "remind the team that the launch moved to Thursday"
        assertEquals(source, processor.process(source))
    }

    @Test
    fun multipleVersionsFallBackToTranscript() {
        val (processor, _) = openAI("Version 1: Hi team.\nVersion 2: Hello team.")
        assertEquals("hi team the build is green", processor.process("hi team the build is green"))
    }

    @Test
    fun implausiblyShortRewriteFallsBackToTranscript() {
        val (processor, _) = openAI("Okay.")
        val source = "please send me the final numbers for the quarterly budget review by Friday"
        assertEquals(source, processor.process(source))
    }

    @Test
    fun knownUnspokenPreambleIsRemovedUnlessItWasSpoken() {
        val preamble = "It is worth noting that the event in question is currently unfolding, " +
            "and it can be observed by those present."
        val (processor, _) = openAI("$preamble The bus is late again.")
        assertEquals("The bus is late again.", processor.process("the bus is late again"))

        val (spoken, _) = openAI("$preamble The bus is late again.")
        assertEquals("$preamble The bus is late again.", spoken.process("$preamble the bus is late again"))
    }

    @Test
    fun incompleteResponseIsAnError() {
        val http = FakeTransport().reply(200, """{"status": "incomplete", "output": []}""")
        assertThrows(TranscriptionError::class.java) {
            OpenAITextPostProcessor(OpenAIRewriteOptions("test-key"), http).process("hello there")
        }
    }

    @Test
    fun openRouterFreeRouterIsDefaultAndReceivesOnlyTranscriptText() {
        val http = FakeTransport().reply(
            200,
            """{"choices": [{"message": {"content": "See you soon."}, "finish_reason": "stop"}]}""",
        )
        val result = OpenRouterTextPostProcessor(OpenRouterRewriteOptions("router-key"), http).process("see you soon")

        assertEquals("See you soon.", result)
        val request = http.requests.last()
        assertEquals(Providers.OPENROUTER_CHAT_COMPLETIONS_URL, request.url)
        assertEquals("Red Whisper", request.headers["X-OpenRouter-Title"])
        val payload = JSONObject(http.lastBody())
        assertEquals("openrouter/free", payload.getString("model"))
        assertFalse(payload.has("models"))
        assertEquals(
            "<transcript>\nsee you soon\n</transcript>",
            payload.getJSONArray("messages").getJSONObject(1).getString("content"),
        )
    }

    @Test
    fun openRouterUsesTheChosenToneAsItsSystemMessage() {
        val http = FakeTransport().reply(200, """{"choices": [{"message": {"content": "Noted, thank you."}}]}""")
        OpenRouterTextPostProcessor(OpenRouterRewriteOptions("router-key", tone = RewriteTone.PROFESSIONAL), http)
            .process("ok got it thanks")
        val system = JSONObject(http.lastBody()).getJSONArray("messages").getJSONObject(0).getString("content")
        assertEquals(RewriteRules.instructionFor(RewriteTone.PROFESSIONAL), system)
    }

    @Test
    fun specificFreeModelFallsBackToTheFreeRouter() {
        val http = FakeTransport().reply(200, """{"choices": [{"message": {"content": "Done."}}]}""")
        OpenRouterTextPostProcessor(OpenRouterRewriteOptions("router-key", "google/gemma-3-27b-it:free"), http)
            .process("done")
        assertEquals("openrouter/free", JSONObject(http.lastBody()).getJSONArray("models").getString(0))
    }

    @Test
    fun openRouterKeyAndModelAreValidated() {
        assertThrows(ConfigurationError::class.java) {
            OpenRouterTextPostProcessor(OpenRouterRewriteOptions(""), FakeTransport())
        }
        assertThrows(ConfigurationError::class.java) {
            OpenRouterTextPostProcessor(OpenRouterRewriteOptions("router-key", "bad model"), FakeTransport())
        }
    }
}
