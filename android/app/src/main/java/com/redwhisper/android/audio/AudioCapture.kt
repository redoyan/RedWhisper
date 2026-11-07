package com.redwhisper.android.audio

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import com.redwhisper.android.core.AudioClip
import com.redwhisper.android.core.AudioLevels
import java.io.ByteArrayOutputStream

/**
 * Records 16 kHz mono PCM in memory, like the Mac app's `sounddevice` stream. Bluetooth
 * headsets need a communication route, which is opened only while recording.
 */
class AudioCapture(
    private val context: Context,
    private val device: InputDeviceOption?,
    private val gain: Float,
) {
    private val audioManager = context.getSystemService(AudioManager::class.java)
    private val pcm = ByteArrayOutputStream()
    private var record: AudioRecord? = null
    private var reader: Thread? = null
    private var previousMode: Int? = null
    @Volatile private var running = false

    /** The latest input level on the Mac app's [0, 1] RMS scale, after gain. */
    @Volatile var level: Float = 0f
        private set

    @SuppressLint("MissingPermission") // Callers check RECORD_AUDIO before recording.
    fun start() {
        val bluetooth = device?.isBluetooth == true
        if (bluetooth) routeToBluetooth(device!!.device)
        val minimum = AudioRecord.getMinBufferSize(
            AudioClip.SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        val audioRecord = AudioRecord(
            if (bluetooth) MediaRecorder.AudioSource.VOICE_COMMUNICATION else MediaRecorder.AudioSource.VOICE_RECOGNITION,
            AudioClip.SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            maxOf(minimum, AudioClip.SAMPLE_RATE / 2),
        )
        if (audioRecord.state != AudioRecord.STATE_INITIALIZED) {
            audioRecord.release()
            restoreRoute()
            throw IllegalStateException("The microphone could not be opened")
        }
        device?.let { audioRecord.setPreferredDevice(it.device) }
        audioRecord.startRecording()
        if (audioRecord.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
            audioRecord.release()
            restoreRoute()
            throw IllegalStateException("The microphone is in use by another app")
        }
        record = audioRecord
        running = true
        reader = Thread({ readLoop(audioRecord) }, "redwhisper-microphone").apply { start() }
    }

    private fun readLoop(audioRecord: AudioRecord) {
        val chunk = ShortArray(1024)
        while (running) {
            val count = audioRecord.read(chunk, 0, chunk.size)
            if (count < 0) break
            if (count == 0) continue
            val bytes = AudioLevels.toPcmBytes(chunk.copyOf(count))
            synchronized(pcm) { pcm.write(bytes) }
            level = (AudioLevels.rootMeanSquare(chunk, count) * gain).coerceAtMost(1f)
        }
    }

    /** True when Android is feeding this app silence because it may not use the microphone now. */
    fun isSilencedBySystem(): Boolean = record?.activeRecordingConfiguration?.isClientSilenced == true

    /** Stops recording and returns the raw clip; gain and silence checks happen afterwards. */
    fun stop(): AudioClip {
        running = false
        reader?.join(2_000)
        record?.let { audioRecord ->
            runCatching { audioRecord.stop() }
            audioRecord.release()
        }
        record = null
        restoreRoute()
        return AudioClip(synchronized(pcm) { pcm.toByteArray() })
    }

    private fun routeToBluetooth(input: AudioDeviceInfo) {
        previousMode = audioManager.mode
        audioManager.mode = AudioManager.MODE_IN_COMMUNICATION
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val target = audioManager.availableCommunicationDevices.firstOrNull {
                it.type == input.type && it.address == input.address
            } ?: audioManager.availableCommunicationDevices.firstOrNull { it.isBluetoothInput() }
            target?.let(audioManager::setCommunicationDevice)
        } else {
            @Suppress("DEPRECATION")
            audioManager.startBluetoothSco()
            @Suppress("DEPRECATION")
            audioManager.isBluetoothScoOn = true
        }
    }

    private fun restoreRoute() {
        val mode = previousMode ?: return
        previousMode = null
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            audioManager.clearCommunicationDevice()
        } else {
            @Suppress("DEPRECATION")
            audioManager.isBluetoothScoOn = false
            @Suppress("DEPRECATION")
            audioManager.stopBluetoothSco()
        }
        audioManager.mode = mode
    }
}
