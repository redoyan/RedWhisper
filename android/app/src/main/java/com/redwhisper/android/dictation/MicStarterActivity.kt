package com.redwhisper.android.dictation

import android.app.Activity
import android.os.Bundle
import androidx.core.content.ContextCompat

/**
 * An invisible, instantly finished activity. Starting the recorder from here counts as
 * foreground use, which Android requires before an app may record from the background.
 */
class MicStarterActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        try {
            ContextCompat.startForegroundService(this, RecordingService.startIntent(this, fromActivity = true))
        } catch (_: Exception) {
            Dictation.fail("Android blocked the microphone. Open Red Whisper once, then try the bubble again.")
        }
        finish()
    }
}
