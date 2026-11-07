package com.redwhisper.android.core

/** Whisper-style models occasionally emit these for silence; the Mac app drops them too. */
val HALLUCINATION_ONLY = Regex(
    """^(?:\s|[.♪🎵])*""" +
        """(?:sous[- ]?titr(?:age)?|subtitles?|music|merci d'avoir regard[ée]|""" +
        """thanks for watching|subscribe|abonne[zr][- ]?vous|like and share|merci)""" +
        """(?:\s|[.!?♪🎵])*?$""",
    RegexOption.IGNORE_CASE,
)

/** Transcribe, drop hallucinations, replace terms, optionally rewrite, and strip wrapper tags. */
class TranscriptPipeline(
    private val transcriber: Transcriber,
    private val replacements: TermReplacer? = null,
    private val postProcessor: PostProcessor? = null,
) {
    private var postProcessWarning: String? = null

    fun process(audio: AudioClip): String {
        postProcessWarning = null
        var text = transcriber.transcribe(audio).trim()
        if (text.isEmpty() || HALLUCINATION_ONLY.matches(text)) return ""
        replacements?.let { text = it.process(text) }
        postProcessor?.let { processor ->
            val original = text
            text = try {
                processor.process(text)
            } catch (error: Exception) {
                // A failed rewrite never loses dictation: paste the original transcript instead.
                postProcessWarning = "${error.javaClass.simpleName}: ${error.message}"
                original
            }
        }
        return RewriteRules.TRANSCRIPT_WRAPPER_TAG.replace(text, "").trim()
    }

    fun consumePostProcessWarning(): String? = postProcessWarning.also { postProcessWarning = null }
}
