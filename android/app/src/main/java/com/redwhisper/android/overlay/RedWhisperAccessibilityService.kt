package com.redwhisper.android.overlay

import android.accessibilityservice.AccessibilityService
import android.annotation.SuppressLint
import android.content.Context
import android.graphics.PixelFormat
import android.graphics.Rect
import android.graphics.Region
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.HapticFeedbackConstants
import android.view.MotionEvent
import android.view.ViewConfiguration
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import android.widget.Toast
import com.redwhisper.android.data.AppSettings
import com.redwhisper.android.dictation.Dictation
import com.redwhisper.android.dictation.RecordingService
import com.redwhisper.android.redWhisper
import kotlin.math.abs

/**
 * Shows the bubble when a chat box is being typed in and inserts finished dictation there.
 * It only inspects which field has focus; it never stores or sends anything on screen.
 */
class RedWhisperAccessibilityService : AccessibilityService(), Dictation.Listener {
    private val handler = Handler(Looper.getMainLooper())
    private val refreshTask = Runnable { refresh() }
    // Keyboards and windows animate after the events that announce them, so check again once
    // they settle, and keep checking while the bubble is up so it never outlives the chat box.
    private val settleTask = Runnable { refresh() }
    private val watchTask = Runnable { refresh() }
    private lateinit var windowManager: WindowManager
    private var bubble: BubbleView? = null
    private var layout: WindowManager.LayoutParams? = null
    private var shown = false
    private var target: AccessibilityNodeInfo? = null
    private var settings = AppSettings()
    private var settingsLoadedAt = 0L
    private var offsetY = 0
    private var anchorY = 0

    override fun onServiceConnected() {
        windowManager = getSystemService(WindowManager::class.java)
        offsetY = getSharedPreferences(PREFS, Context.MODE_PRIVATE).getInt(KEY_OFFSET, 0)
        Dictation.attach(this)
        Dictation.listener = this
        createBubble()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent) = scheduleRefresh()

    private fun scheduleRefresh() {
        handler.removeCallbacks(refreshTask)
        handler.removeCallbacks(settleTask)
        handler.postDelayed(refreshTask, REFRESH_DELAY_MS)
        handler.postDelayed(settleTask, SETTLE_DELAY_MS)
    }

    override fun onInterrupt() = Unit

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        if (Dictation.listener === this) Dictation.listener = null
        bubble?.let { view -> if (shown) runCatching { windowManager.removeView(view) } }
        shown = false
        super.onDestroy()
    }

    private fun currentSettings(): AppSettings {
        val now = System.currentTimeMillis()
        if (now - settingsLoadedAt > SETTINGS_TTL_MS) {
            settings = redWhisper.settingsStore.load()
            settingsLoadedAt = now
        }
        return settings
    }

    private fun refresh() {
        handler.removeCallbacks(watchTask)
        // While listening or transcribing, the bubble stays put and keeps its target field.
        if (Dictation.phase != Dictation.Phase.IDLE) return
        val settings = currentSettings()
        if (!settings.bubbleEnabled) return hide()

        val chatBox = runCatching { findChatBox(settings) }.getOrNull() ?: return hide()
        target = chatBox.node
        show(chatBox.field, chatBox.keyboard)
        handler.postDelayed(watchTask, WATCH_INTERVAL_MS)
    }

    /** Where to put the bubble: the box being typed in when the app reports one, or the keyboard. */
    private class ChatBox(val node: AccessibilityNodeInfo?, val field: Rect?, val keyboard: Rect)

    /**
     * The text box being typed in, when the keyboard is on screen in the app in front and that
     * app is allowed. Anything stale from an app that is closing is ignored.
     */
    private fun findChatBox(settings: AppSettings): ChatBox? {
        val windows = windows
        val keyboardWindow = windows.firstOrNull { it.type == AccessibilityWindowInfo.TYPE_INPUT_METHOD } ?: return null
        val keyboard = Rect().also(keyboardWindow::getBoundsInScreen)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            // Some phones keep a hidden keyboard's window listed; only its visible area counts.
            val visible = Region().also(keyboardWindow::getRegionInScreen)
            if (visible.isEmpty) return null
            keyboard.set(visible.bounds)
        }
        val minimumKeyboard = (120 * resources.displayMetrics.density).toInt()
        if (keyboard.height() < minimumKeyboard || keyboard.top >= screenSize().second) return null

        val appWindow = windows.firstOrNull { it.type == AccessibilityWindowInfo.TYPE_APPLICATION && it.isFocused }
        val root = appWindow?.root ?: rootInActiveWindow ?: return null
        val app = root.packageName?.toString() ?: return null
        if (app == packageName || !(settings.bubbleInAllApps || app in settings.chatApps)) return null

        val focused = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT) ?: return null
        if (focused.isPassword || !focused.isVisibleToUser || focused.packageName?.toString() != app) return null
        val field = Rect().also(focused::getBoundsInScreen)
        val typeable = focused.isEditable && !field.isEmpty && field.bottom <= keyboard.bottom
        return when {
            typeable -> ChatBox(focused, field, keyboard)
            // Browsers, games, and custom editors don't always expose an editable box. With the
            // keyboard up the bubble still appears, and the text goes to the clipboard if needed.
            settings.bubbleInAllApps -> ChatBox(null, null, keyboard)
            else -> null
        }
    }

    private fun show(field: Rect?, keyboard: Rect) {
        val view = bubble ?: return
        val params = layout ?: return
        val size = view.sizePx
        val screen = screenSize()
        val margin = (6 * resources.displayMetrics.density).toInt()
        // Sit just above the text box at the right edge, clear of the send button.
        anchorY = minOf(field?.top ?: keyboard.top, keyboard.top) - size - margin
        params.x = screen.first - size - margin
        params.y = clampY(anchorY + offsetY, keyboard.top, size)
        if (shown) {
            windowManager.updateViewLayout(view, params)
        } else {
            runCatching { windowManager.addView(view, params) }.onSuccess { shown = true }
        }
    }

    private fun clampY(y: Int, keyboardTop: Int, size: Int): Int {
        val top = (48 * resources.displayMetrics.density).toInt()
        return y.coerceIn(top, maxOf(top, keyboardTop - size))
    }

    private fun hide() {
        val view = bubble ?: return
        if (shown) runCatching { windowManager.removeView(view) }
        shown = false
        target = null
    }

    private fun screenSize(): Pair<Int, Int> =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            windowManager.currentWindowMetrics.bounds.let { it.width() to it.height() }
        } else {
            resources.displayMetrics.let { it.widthPixels to it.heightPixels }
        }

    @SuppressLint("ClickableViewAccessibility")
    private fun createBubble() {
        val view = BubbleView(this) { RecordingService.instance?.level ?: 0f }
        layout = WindowManager.LayoutParams(
            view.sizePx,
            view.sizePx,
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
        }

        val slop = ViewConfiguration.get(this).scaledTouchSlop
        var downY = 0f
        var startY = 0
        var dragging = false
        view.setOnTouchListener { touched, event ->
            val params = layout ?: return@setOnTouchListener false
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downY = event.rawY
                    startY = params.y
                    dragging = false
                    touched.alpha = 1f
                }
                MotionEvent.ACTION_MOVE -> {
                    val delta = event.rawY - downY
                    if (dragging || abs(delta) > slop) {
                        dragging = true
                        params.y = (startY + delta).toInt()
                        if (shown) windowManager.updateViewLayout(touched, params)
                    }
                }
                MotionEvent.ACTION_UP -> {
                    if (dragging) {
                        // Remember where people like the bubble, relative to the chat box.
                        offsetY = params.y - anchorY
                        getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putInt(KEY_OFFSET, offsetY).apply()
                    } else {
                        touched.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY)
                        touched.performClick()
                        Dictation.toggle(this)
                    }
                    (touched as BubbleView).phase = Dictation.phase
                }
                MotionEvent.ACTION_CANCEL -> (touched as BubbleView).phase = Dictation.phase
            }
            true
        }
        bubble = view
    }

    override fun onPhaseChanged(phase: Dictation.Phase) {
        bubble?.phase = phase
        if (phase == Dictation.Phase.RECORDING) {
            bubble?.performHapticFeedback(HapticFeedbackConstants.LONG_PRESS)
        }
        if (phase == Dictation.Phase.IDLE) scheduleRefresh()
    }

    override fun onTranscript(text: String) {
        val node = target ?: runCatching { findFocus(AccessibilityNodeInfo.FOCUS_INPUT) }.getOrNull()
        if (TextInserter.insert(node, text)) {
            bubble?.performHapticFeedback(HapticFeedbackConstants.KEYBOARD_TAP)
        } else {
            // Some apps refuse direct edits; the clipboard keeps the dictation safe.
            Dictation.copyToClipboard(text)
            Toast.makeText(this, "Dictation copied. Paste it where you want it.", Toast.LENGTH_LONG).show()
        }
        scheduleRefresh()
    }

    private companion object {
        const val PREFS = "bubble"
        const val KEY_OFFSET = "offset_y"
        const val REFRESH_DELAY_MS = 120L
        const val SETTLE_DELAY_MS = 650L
        const val WATCH_INTERVAL_MS = 800L
        const val SETTINGS_TTL_MS = 1_500L
    }
}
