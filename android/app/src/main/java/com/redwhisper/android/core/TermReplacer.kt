package com.redwhisper.android.core

/** Deterministic, local, longest-match-first term replacement. */
class TermReplacer(replacements: Map<String, String>) {
    private val folded = replacements.entries.associate { (key, value) -> key.lowercase() to value }
    private val pattern: Regex?

    init {
        if (replacements.keys.any { it.isEmpty() }) {
            throw ConfigurationError("Replacement keys must be non-empty and values must be strings")
        }
        val ordered = replacements.keys.sortedByDescending { it.length }
        pattern = if (ordered.isEmpty()) {
            null
        } else {
            Regex(ordered.joinToString("|") { Regex.escape(it) }, RegexOption.IGNORE_CASE)
        }
    }

    val size: Int get() = folded.size

    fun process(text: String): String =
        pattern?.replace(text) { match -> folded[match.value.lowercase()] ?: match.value } ?: text

    companion object {
        fun fromJson(json: String): TermReplacer {
            val payload = parseJsonObject(json)
                ?: throw ConfigurationError("Replacements file must contain a JSON object")
            val replacements = linkedMapOf<String, String>()
            for (key in payload.keys()) {
                val value = payload.get(key)
                if (value !is String) {
                    throw ConfigurationError("Replacement keys must be non-empty and values must be strings")
                }
                replacements[key] = value
            }
            return TermReplacer(replacements)
        }
    }
}
