package com.redwhisper.android.data

import android.content.Context

/** App-private settings storage. Like the Mac app, an unreadable file falls back to defaults. */
class SettingsStore(context: Context) {
    private val prefs = context.getSharedPreferences("settings", Context.MODE_PRIVATE)

    fun load(): AppSettings {
        val json = prefs.getString(KEY_SETTINGS, null) ?: return AppSettings()
        return try {
            AppSettings.fromJson(json)
        } catch (_: Exception) {
            AppSettings()
        }
    }

    fun save(settings: AppSettings) {
        settings.validate()
        check(prefs.edit().putString(KEY_SETTINGS, settings.toJson()).commit()) { "Could not save settings" }
    }

    /** The imported replacement dictionary, kept private to the app like `replacements.json`. */
    var replacementsJson: String?
        get() = prefs.getString(KEY_REPLACEMENTS, null)
        set(value) {
            prefs.edit().apply {
                if (value == null) remove(KEY_REPLACEMENTS) else putString(KEY_REPLACEMENTS, value)
            }.apply()
        }

    private companion object {
        const val KEY_SETTINGS = "settings"
        const val KEY_REPLACEMENTS = "replacements"
    }
}
