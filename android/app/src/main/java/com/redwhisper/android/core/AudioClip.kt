package com.redwhisper.android.core

import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.sqrt

/**
 * Mono 16-bit little-endian PCM held in memory. Recordings never touch disk on Android,
 * which is stricter than the Mac app's private temporary WAV files.
 */
class AudioClip(val pcm16: ByteArray, val sampleRate: Int = SAMPLE_RATE) {
    val durationSeconds: Double get() = pcm16.size / 2.0 / sampleRate

    fun toWav(): ByteArray {
        val header = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN)
        header.put("RIFF".toByteArray()).putInt(36 + pcm16.size).put("WAVE".toByteArray())
        header.put("fmt ".toByteArray()).putInt(16).putShort(1).putShort(1)
        header.putInt(sampleRate).putInt(sampleRate * 2).putShort(2).putShort(16)
        header.put("data".toByteArray()).putInt(pcm16.size)
        return header.array() + pcm16
    }

    companion object {
        const val SAMPLE_RATE = 16_000
    }
}

/** Matches `audio_processing.py`: digital gain with clipping protection. */
object AudioLevels {
    val GAINS = listOf(1.0f, 2.0f, 4.0f, 8.0f)

    fun applyGain(samples: ShortArray, gain: Float): ShortArray {
        require(gain in GAINS) { "Microphone gain must be 1×, 2×, 4×, or 8×" }
        return ShortArray(samples.size) { index ->
            (samples[index] * gain).coerceIn(Short.MIN_VALUE.toFloat(), Short.MAX_VALUE.toFloat())
                .toInt().toShort()
        }
    }

    /** Root mean square on the [-1, 1] scale the Mac app's thresholds use. */
    fun rootMeanSquare(samples: ShortArray, count: Int = samples.size): Float {
        if (count == 0) return 0f
        var sum = 0.0
        for (index in 0 until count) {
            val value = samples[index] / 32768.0
            sum += value * value
        }
        return sqrt(sum / count).toFloat()
    }

    fun toPcmBytes(samples: ShortArray): ByteArray {
        val buffer = ByteBuffer.allocate(samples.size * 2).order(ByteOrder.LITTLE_ENDIAN)
        samples.forEach { buffer.putShort(it) }
        return buffer.array()
    }
}
