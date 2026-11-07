package com.redwhisper.android.core

/** A transcription or rewrite backend failed without exposing secrets. */
open class TranscriptionError(message: String, cause: Throwable? = null) :
    RuntimeException(message, cause)

/** The requested pipeline configuration is invalid. */
class ConfigurationError(message: String) : IllegalArgumentException(message)
