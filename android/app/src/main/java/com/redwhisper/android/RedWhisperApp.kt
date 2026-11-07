package com.redwhisper.android

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import com.redwhisper.android.core.ChatGPTSubscription
import com.redwhisper.android.core.HttpTransport
import com.redwhisper.android.data.SecretStore
import com.redwhisper.android.data.SettingsStore
import com.redwhisper.android.net.OkHttpTransport

/** Process-wide services shared by the settings screen, the bubble, and the recorder. */
class RedWhisperApp : Application() {
    val settingsStore by lazy { SettingsStore(this) }
    val secrets by lazy { SecretStore(this) }
    val http: HttpTransport by lazy { OkHttpTransport() }
    val chatGpt by lazy { ChatGPTSubscription(secrets.chatGptAccounts, http, openBrowser = ::openBrowser) }

    override fun onCreate() {
        super.onCreate()
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(DICTATION_CHANNEL, "Dictation", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Shown while Red Whisper is listening or transcribing"
                setShowBadge(false)
            },
        )
    }

    fun openBrowser(url: String): Boolean = try {
        startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        true
    } catch (_: ActivityNotFoundException) {
        false
    }

    companion object {
        const val DICTATION_CHANNEL = "dictation"
    }
}

val Context.redWhisper: RedWhisperApp get() = applicationContext as RedWhisperApp
