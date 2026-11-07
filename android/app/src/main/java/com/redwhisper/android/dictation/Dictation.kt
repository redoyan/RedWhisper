package com.redwhisper.android.dictation

import android.Manifest
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Handler
import android.os.Looper
import android.widget.Toast
import androidx.core.content.ContextCompat
import com.redwhisper.android.redWhisper
import com.redwhisper.android.ui.MainActivity

/**
 * Coordinates one dictation at a time between the bubble (in the accessibility service) and
 * [RecordingService], the way `MLXWhisperApp` coordinates its hotkeys and worker thread.
 */
object Dictation {
    enum class Phase { IDLE, STARTING, RECORDING, PROCESSING }

    interface Listener {
        fun onPhaseChanged(phase: Phase)
        fun onTranscript(text: String)
    }

    private val main = Handler(Looper.getMainLooper())
    private lateinit var appContext: Context

    @Volatile var phase: Phase = Phase.IDLE
        private set

    /** The bubble's host. Accessed on the main thread only. */
    var listener: Listener? = null

    fun toggle(context: Context) {
        appContext = context.applicationContext
        when (phase) {
            Phase.IDLE -> start(context)
            Phase.RECORDING -> RecordingService.instance?.finish() ?: setPhase(Phase.IDLE)
            Phase.STARTING, Phase.PROCESSING -> Unit
        }
    }

    private fun start(context: Context) {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            message("Allow microphone access in Red Whisper to dictate.")
            openSettings(context)
            return
        }
        try {
            // Fail before recording when a provider key or setting is missing, like the Mac app.
            PipelineFactory.create(context.redWhisper, context.redWhisper.settingsStore.load())
        } catch (error: IllegalArgumentException) {
            message(error.message ?: "Check Red Whisper's settings.")
            return
        }
        setPhase(Phase.STARTING)
        try {
            ContextCompat.startForegroundService(context, RecordingService.startIntent(context, fromActivity = false))
        } catch (_: Exception) {
            // Android may refuse a background start; a momentary activity makes the app eligible.
            startFromActivity(context)
        }
    }

    /** Re-runs the start through [MicStarterActivity], which Android treats as foreground use. */
    internal fun startFromActivity(context: Context) {
        try {
            context.startActivity(
                Intent(context, MicStarterActivity::class.java).addFlags(
                    Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NO_ANIMATION or Intent.FLAG_ACTIVITY_MULTIPLE_TASK,
                ),
            )
        } catch (_: Exception) {
            fail("Android blocked the microphone. Open Red Whisper once, then try the bubble again.")
        }
    }

    internal fun setPhase(phase: Phase) {
        this.phase = phase
        main.post { listener?.onPhaseChanged(phase) }
    }

    internal fun fail(text: String) {
        setPhase(Phase.IDLE)
        message(text)
    }

    /** Hands text to the bubble for insertion; without one, the text goes to the clipboard. */
    internal fun deliver(text: String) {
        main.post {
            val host = listener
            if (host != null) {
                host.onTranscript(text)
            } else {
                copyToClipboard(text)
                toast("Dictation copied. Paste it where you want it.")
            }
        }
    }

    internal fun copyToClipboard(text: String) {
        appContext.getSystemService(ClipboardManager::class.java)
            .setPrimaryClip(ClipData.newPlainText("Red Whisper", text))
    }

    internal fun message(text: String) = main.post { toast(text) }

    private fun toast(text: String) {
        if (::appContext.isInitialized) Toast.makeText(appContext, text, Toast.LENGTH_LONG).show()
    }

    private fun openSettings(context: Context) {
        runCatching {
            context.startActivity(Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }
    }

    internal fun attach(context: Context) {
        appContext = context.applicationContext
    }
}
