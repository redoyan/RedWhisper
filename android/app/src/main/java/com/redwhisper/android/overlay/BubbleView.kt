package com.redwhisper.android.overlay

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.os.SystemClock
import android.view.View
import com.redwhisper.android.R
import com.redwhisper.android.dictation.Dictation
import kotlin.math.sin

/** The floating Red Whisper logo: translucent when idle, ringed in red while listening. */
@SuppressLint("ViewConstructor")
class BubbleView(context: Context, private val level: () -> Float) : View(context) {
    private val density = resources.displayMetrics.density
    private val logo: Bitmap = BitmapFactory.decodeResource(resources, R.drawable.bubble_logo)
    private val diameter = 46 * density
    private val shadowPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0x40000000 }
    private val logoPaint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
    private val ringPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        color = 0xFFFF3B3B.toInt()
        strokeCap = Paint.Cap.ROUND
    }
    private val arcPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        color = 0xFFFFFFFF.toInt()
        strokeWidth = 3 * density
        strokeCap = Paint.Cap.ROUND
    }
    private val clip = Path()
    private val logoBounds = RectF()
    private val arcBounds = RectF()

    var phase: Dictation.Phase = Dictation.Phase.IDLE
        set(value) {
            field = value
            alpha = if (value == Dictation.Phase.IDLE) IDLE_ALPHA else 1f
            contentDescription = when (value) {
                Dictation.Phase.IDLE -> "Start Red Whisper dictation"
                Dictation.Phase.RECORDING -> "Stop recording"
                else -> "Transcribing"
            }
            invalidate()
        }

    init {
        alpha = IDLE_ALPHA
        contentDescription = "Start Red Whisper dictation"
        isClickable = true
    }

    /** Room for the ring and shadow around the logo. */
    val sizePx: Int get() = (diameter + 16 * density).toInt()

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        setMeasuredDimension(sizePx, sizePx)
    }

    override fun onDraw(canvas: Canvas) {
        val cx = width / 2f
        val cy = height / 2f
        val radius = diameter / 2
        canvas.drawCircle(cx, cy + 1.5f * density, radius + density, shadowPaint)

        clip.reset()
        clip.addCircle(cx, cy, radius, Path.Direction.CW)
        canvas.save()
        canvas.clipPath(clip)
        // The app icon has a rounded-square frame; zoom slightly so the circle shows only the mark.
        val zoom = radius * 1.18f
        logoBounds.set(cx - zoom, cy - zoom, cx + zoom, cy + zoom)
        canvas.drawBitmap(logo, null, logoBounds, logoPaint)
        canvas.restore()

        val time = SystemClock.uptimeMillis()
        when (phase) {
            Dictation.Phase.RECORDING -> {
                val pulse = 0.5f + 0.5f * sin(time / 260.0).toFloat()
                ringPaint.strokeWidth = (2.5f + 4f * level().coerceIn(0f, 1f) * 6f).coerceAtMost(6.5f) * density
                ringPaint.alpha = (170 + 85 * pulse).toInt()
                canvas.drawCircle(cx, cy, radius + 3 * density, ringPaint)
                postInvalidateOnAnimation()
            }
            Dictation.Phase.STARTING, Dictation.Phase.PROCESSING -> {
                arcBounds.set(cx - radius + 5 * density, cy - radius + 5 * density, cx + radius - 5 * density, cy + radius - 5 * density)
                canvas.drawArc(arcBounds, (time % 1000) * 0.36f, 100f, false, arcPaint)
                postInvalidateOnAnimation()
            }
            Dictation.Phase.IDLE -> Unit
        }
    }

    private companion object {
        const val IDLE_ALPHA = 0.72f
    }
}
