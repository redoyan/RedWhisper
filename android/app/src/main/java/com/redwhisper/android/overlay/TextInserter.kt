package com.redwhisper.android.overlay

import android.os.Bundle
import android.view.accessibility.AccessibilityNodeInfo
import com.redwhisper.android.dictation.Dictation

/** Inserts dictation at the cursor of the chat box, the Android equivalent of Cmd-V. */
object TextInserter {
    fun insert(node: AccessibilityNodeInfo?, text: String): Boolean {
        if (node == null || !node.refresh() || !node.isEditable) return false
        val raw = node.text?.toString().orEmpty()
        val hint = node.hintText?.toString()
        val empty = raw.isEmpty() || node.isShowingHintText || (hint != null && raw == hint)

        // A blank box takes the text directly, leaving the clipboard alone.
        if (empty && setText(node, text, text.length)) return true

        // Some apps (WhatsApp among them) report their "Message" placeholder as real text, and
        // only the app knows what is really typed. Pasting lets the app insert at its own cursor,
        // exactly like the Mac app's Cmd-V, and never overwrites an existing draft.
        Dictation.copyToClipboard(text)
        if (node.performAction(AccessibilityNodeInfo.ACTION_PASTE)) return true

        if (empty) return false
        var start = node.textSelectionStart
        var end = node.textSelectionEnd
        if (start < 0 || end < 0 || start > raw.length || end > raw.length) {
            start = raw.length
            end = raw.length
        }
        if (start > end) start = end.also { end = start }
        val merged = merge(raw, start, end, text)
        return setText(node, merged.text, merged.cursor)
    }

    private fun setText(node: AccessibilityNodeInfo, value: String, cursor: Int): Boolean {
        val arguments = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value)
        }
        if (!node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments)) return false
        node.performAction(
            AccessibilityNodeInfo.ACTION_SET_SELECTION,
            Bundle().apply {
                putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, cursor)
                putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, cursor)
            },
        )
        return true
    }

    data class Merge(val text: String, val cursor: Int)

    /** Replaces the selection, adding a space when dictation follows a word directly. */
    fun merge(current: String, start: Int, end: Int, dictation: String): Merge {
        val prefix = current.substring(0, start)
        val suffix = current.substring(end)
        val separator = if (prefix.isNotEmpty() && !prefix.last().isWhitespace()) " " else ""
        val inserted = separator + dictation
        return Merge(prefix + inserted + suffix, prefix.length + inserted.length)
    }
}
