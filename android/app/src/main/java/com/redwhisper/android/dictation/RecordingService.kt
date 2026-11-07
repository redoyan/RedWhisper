package com.redwhisper.android.dictation

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import com.redwhisper.android.R
import com.redwhisper.android.RedWhisperApp
import com.redwhisper.android.audio.AudioCapture
import com.redwhisper.android.audio.InputDevices
import com.redwhisper.android.core.AudioClip
import com.redwhisper.android.core.AudioLevels
import com.redwhisper.android.redWhisper
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.Executors

/**
 * Records while the bubble is active, then transcribes off the main thread. A microphone
 * foreground service is how Android allows recording while another app is on screen.
 */
class RecordingService : Service() {
    private val main = Handler(Looper.getMainLooper())
    private val worker = Executors.newSingleThreadExecutor()
    private var capture: AudioCapture? = null
    private var startedAt = 0L
    private var fromActivity = false

    /** Live input level for the bubble's ring. */
    val level: Float get() = capture?.level ?: 0f

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        Dictation.attach(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> finish()
            ACTION_START -> begin(intent.getBooleanExtra(EXTRA_FROM_ACTIVITY, false))
        }
        return START_NOT_STICKY
    }

    private fun begin(fromActivity: Boolean) {
        if (capture != null) return
        this.fromActivity = fromActivity
        try {
            startForeground(
                NOTIFICATION_ID,
                notification("Listening… tap the bubble to finish"),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE,
            )
        } catch (_: RuntimeException) {
            // Android 14+ refuses microphone access for a service started from the background.
            satisfyForegroundContract()
            retryOrFail()
            return
        }
        instance = this
        val settings = redWhisper.settingsStore.load()
        val audio = AudioCapture(this, InputDevices.find(this, settings.inputDevice), settings.microphoneGain)
        try {
            audio.start()
        } catch (error: RuntimeException) {
            shutDown()
            Dictation.fail("Microphone error: ${error.message}")
            return
        }
        capture = audio
        startedAt = System.currentTimeMillis()
        Dictation.setPhase(Dictation.Phase.RECORDING)
        main.postDelayed(::checkNotSilenced, SILENCE_CHECK_DELAY_MS)
        main.postDelayed(::finish, MAX_RECORDING_MS)
    }

    /** Older Android versions silently zero the microphone instead of throwing. */
    private fun checkNotSilenced() {
        val audio = capture ?: return
        if (!audio.isSilencedBySystem()) return
        capture = null
        main.removeCallbacksAndMessages(null)
        worker.execute { audio.stop() }
        shutDown()
        retryOrFail()
    }

    private fun retryOrFail() {
        stopSelf()
        if (fromActivity) {
            Dictation.fail("Android blocked the microphone. Open Red Whisper once, then try the bubble again.")
        } else {
            Dictation.startFromActivity(applicationContext)
        }
    }

    /** Ends recording and processes it; safe to call more than once. */
    fun finish() {
        val audio = capture ?: return
        capture = null
        main.removeCallbacksAndMessages(null)
        Dictation.setPhase(Dictation.Phase.PROCESSING)
        NotificationManagerCompat.from(this).runCatching { notify(NOTIFICATION_ID, notification("Transcribing…")) }
        worker.execute {
            try {
                process(audio.stop())
            } finally {
                main.post { shutDown() }
            }
        }
    }

    private fun process(raw: AudioClip) {
        val app = applicationContext as RedWhisperApp
        val settings = app.settingsStore.load()
        val minimumBytes = (AudioClip.SAMPLE_RATE * MIN_DURATION_S).toInt() * 2
        if (raw.pcm16.size < minimumBytes) {
            Dictation.fail("Recording too short")
            return
        }
        val samples = ShortArray(raw.pcm16.size / 2)
        ByteBuffer.wrap(raw.pcm16).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().get(samples)
        val boosted = AudioLevels.applyGain(samples, settings.microphoneGain)
        val energy = AudioLevels.rootMeanSquare(boosted)
        if (energy < MIN_AUDIO_ENERGY) {
            Dictation.fail("No audio was captured. Check the microphone in Red Whisper's settings.")
            return
        }
        val clip = AudioClip(AudioLevels.toPcmBytes(boosted))
        try {
            val started = System.currentTimeMillis()
            val pipeline = PipelineFactory.create(app, settings)
            val text = pipeline.process(clip)
            val warning = pipeline.consumePostProcessWarning()
            // Timing only: dictated text never reaches the log.
            Log.i(TAG, "%.1fs audio → %.1fs processing".format(clip.durationSeconds, (System.currentTimeMillis() - started) / 1000.0))
            if (text.isEmpty()) {
                Dictation.fail("No speech detected")
                return
            }
            Dictation.setPhase(Dictation.Phase.IDLE)
            Dictation.deliver(text)
            if (warning != null) {
                Log.w(TAG, "Restructuring failed; inserted the original transcript: $warning")
                Dictation.message("Restructuring unavailable. The original transcript was inserted instead.")
            }
        } catch (error: Exception) {
            Log.w(TAG, "Transcription error: ${error.javaClass.simpleName}")
            Dictation.fail("Transcription error: ${error.message?.take(200)}")
        }
    }

    private fun satisfyForegroundContract() {
        // startForegroundService() obliges a startForeground() call, even when recording can't start.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            runCatching {
                startForeground(NOTIFICATION_ID, notification("Starting…"), ServiceInfo.FOREGROUND_SERVICE_TYPE_SHORT_SERVICE)
            }
        }
    }

    private fun shutDown() {
        if (instance === this) instance = null
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        capture?.let { audio -> worker.execute { audio.stop() } }
        capture = null
        if (instance === this) instance = null
        if (Dictation.phase != Dictation.Phase.IDLE && Dictation.phase != Dictation.Phase.STARTING) {
            Dictation.setPhase(Dictation.Phase.IDLE)
        }
        worker.shutdown()
        super.onDestroy()
    }

    private fun notification(text: String): Notification {
        val stop = PendingIntent.getService(
            this,
            1,
            Intent(this, RecordingService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return NotificationCompat.Builder(this, RedWhisperApp.DICTATION_CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_mic)
            .setContentTitle("Red Whisper")
            .setContentText(text)
            .setOngoing(true)
            .setSilent(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .addAction(0, "Finish", stop)
            .build()
    }

    companion object {
        private const val TAG = "RedWhisper"
        private const val ACTION_START = "com.redwhisper.android.START"
        private const val ACTION_STOP = "com.redwhisper.android.STOP"
        private const val EXTRA_FROM_ACTIVITY = "from_activity"
        private const val NOTIFICATION_ID = 7
        private const val SILENCE_CHECK_DELAY_MS = 500L
        private const val MAX_RECORDING_MS = 10 * 60 * 1000L

        /** The Mac app's thresholds: ignore clips under 0.8 s and near-total silence. */
        const val MIN_DURATION_S = 0.8
        const val MIN_AUDIO_ENERGY = 0.0002f

        @Volatile var instance: RecordingService? = null
            private set

        fun startIntent(context: Context, fromActivity: Boolean): Intent =
            Intent(context, RecordingService::class.java)
                .setAction(ACTION_START)
                .putExtra(EXTRA_FROM_ACTIVITY, fromActivity)
    }
}
