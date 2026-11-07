package com.redwhisper.android.ui

import android.Manifest
import android.app.Application
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioDeviceCallback
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.net.Uri
import android.os.Build
import android.provider.OpenableColumns
import android.provider.Settings
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.content.ContextCompat
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.redwhisper.android.RedWhisperApp
import com.redwhisper.android.audio.InputDeviceOption
import com.redwhisper.android.audio.InputDevices
import com.redwhisper.android.core.ChatGPTError
import com.redwhisper.android.core.ChatGPTSnapshot
import com.redwhisper.android.core.ConfigurationError
import com.redwhisper.android.core.Providers
import com.redwhisper.android.core.TermReplacer
import com.redwhisper.android.data.AppSettings
import com.redwhisper.android.data.SecretStore
import com.redwhisper.android.overlay.RedWhisperAccessibilityService
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

data class KeyDrafts(val openai: String = "", val elevenlabs: String = "", val openrouter: String = "")

data class SavedKeys(val openai: Boolean = false, val elevenlabs: Boolean = false, val openrouter: Boolean = false) {
    fun has(engine: String): Boolean = when (engine) {
        "openai" -> openai
        "elevenlabs" -> elevenlabs
        "openrouter" -> openrouter
        else -> true
    }
}

data class SetupState(val microphone: Boolean = true, val notifications: Boolean = true, val bubble: Boolean = true) {
    val complete: Boolean get() = microphone && notifications && bubble
}

data class AppEntry(val packageName: String, val label: String)

/** A replacement dictionary waiting for Save: a new file, or removal of the current one. */
sealed interface ReplacementsDraft {
    data class Import(val json: String) : ReplacementsDraft
    data object Clear : ReplacementsDraft
}

class SettingsViewModel(application: Application) : AndroidViewModel(application) {
    private val app = application as RedWhisperApp
    private val audioManager = application.getSystemService(AudioManager::class.java)
    private var chatGptCancel = AtomicBoolean(false)

    var settings by mutableStateOf(app.settingsStore.load())
    var keyDrafts by mutableStateOf(KeyDrafts())
    var savedKeys by mutableStateOf(readSavedKeys())
        private set
    var setup by mutableStateOf(SetupState())
        private set
    var inputDevices by mutableStateOf(emptyList<InputDeviceOption>())
        private set
    var replacementsDraft by mutableStateOf<ReplacementsDraft?>(null)
        private set
    var replacementsCount by mutableStateOf(currentReplacementCount())
        private set

    var chatGpt by mutableStateOf(ChatGPTSnapshot())
        private set
    var chatGptBusy by mutableStateOf(false)
        private set
    var chatGptOperation by mutableStateOf("")
        private set
    var chatGptMessage by mutableStateOf("")
        private set

    /** Counts successful saves, each of which shows the "Settings saved" confirmation. */
    var saveCount by mutableStateOf(0)
        private set

    /** Errors for the alert the Mac app shows when settings can't be saved. */
    var error by mutableStateOf<String?>(null)

    private val deviceCallback = object : AudioDeviceCallback() {
        override fun onAudioDevicesAdded(addedDevices: Array<out AudioDeviceInfo>) = refreshDevices()
        override fun onAudioDevicesRemoved(removedDevices: Array<out AudioDeviceInfo>) = refreshDevices()
    }

    init {
        audioManager.registerAudioDeviceCallback(deviceCallback, null)
        refreshEnvironment()
        runChatGpt("refresh")
    }

    override fun onCleared() {
        audioManager.unregisterAudioDeviceCallback(deviceCallback)
        chatGptCancel.set(true)
    }

    fun update(transform: (AppSettings) -> AppSettings) {
        settings = transform(settings)
    }

    fun refreshEnvironment() {
        val context = getApplication<Application>()
        fun granted(permission: String) =
            ContextCompat.checkSelfPermission(context, permission) == PackageManager.PERMISSION_GRANTED
        setup = SetupState(
            microphone = granted(Manifest.permission.RECORD_AUDIO),
            notifications = Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
                granted(Manifest.permission.POST_NOTIFICATIONS),
            bubble = bubbleServiceEnabled(context),
        )
        refreshDevices()
    }

    private fun refreshDevices() {
        inputDevices = InputDevices.list(getApplication())
    }

    fun launchableApps(): List<AppEntry> {
        val packageManager = getApplication<Application>().packageManager
        val launcher = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        return packageManager.queryIntentActivities(launcher, 0)
            .map { AppEntry(it.activityInfo.packageName, it.loadLabel(packageManager).toString()) }
            .filter { it.packageName != app.packageName }
            .distinctBy { it.packageName }
            .sortedWith(compareByDescending<AppEntry> { it.packageName in settings.chatApps }.thenBy { it.label.lowercase() })
    }

    fun importReplacements(uri: Uri) {
        val resolver = getApplication<Application>().contentResolver
        try {
            val json = resolver.openInputStream(uri)?.use { stream ->
                val bytes = stream.readBytes()
                if (bytes.size > MAX_REPLACEMENTS_BYTES) throw ConfigurationError("Replacement files must be under 1 MB")
                bytes.toString(Charsets.UTF_8)
            } ?: throw ConfigurationError("Could not read the replacements file")
            val replacer = TermReplacer.fromJson(json)
            val name = resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cursor ->
                if (cursor.moveToFirst()) cursor.getString(0) else null
            } ?: "replacements.json"
            replacementsDraft = ReplacementsDraft.Import(json)
            replacementsCount = replacer.size
            update { it.copy(replacementsName = name) }
        } catch (failure: Exception) {
            error = failure.message ?: "Could not read the replacements file"
        }
    }

    fun clearReplacements() {
        replacementsDraft = ReplacementsDraft.Clear
        replacementsCount = 0
        update { it.copy(replacementsName = null) }
    }

    /** Mirrors the Mac Settings window's Save checks, including when keys must already exist. */
    fun save() {
        if (chatGptBusy) {
            error = "Wait for the ChatGPT account operation to finish, or cancel sign-in."
            return
        }
        val openrouterModel = settings.openrouterModel.trim().ifEmpty { Providers.DEFAULT_OPENROUTER_MODEL }
        val updated = settings.copy(
            language = settings.language.trim().ifEmpty { "auto" },
            openrouterModel = openrouterModel,
            openrouterModels = (settings.openrouterModels + openrouterModel).distinct().takeLast(50),
        )
        try {
            updated.validate()
            if (updated.rewriteEngine == "chatgpt") {
                val models = chatGpt.models.map { it.slug }.toSet()
                if (!chatGpt.connected || models.isEmpty()) {
                    throw ConfigurationError("Connect your ChatGPT subscription and refresh models in ChatGPT Account first.")
                }
                if (updated.chatgptRewriteModel.isNotEmpty() && updated.chatgptRewriteModel !in models) {
                    throw ConfigurationError("The selected ChatGPT model is unavailable. Choose a model from the synchronized list.")
                }
            }
            val secrets = app.secrets
            keyDrafts.openai.trim().takeIf { it.isNotEmpty() }?.let { secrets.put(SecretStore.OPENAI_API_KEY, it) }
            keyDrafts.openrouter.trim().takeIf { it.isNotEmpty() }?.let { secrets.put(SecretStore.OPENROUTER_API_KEY, it) }
            keyDrafts.elevenlabs.trim().takeIf { it.isNotEmpty() }?.let { secrets.put(SecretStore.ELEVENLABS_API_KEY, it) }
            keyDrafts = KeyDrafts()
            savedKeys = readSavedKeys()
            if ((updated.engine == "openai" || updated.rewriteEngine == "openai") && !savedKeys.openai) {
                throw ConfigurationError("Enter an OpenAI API key before selecting an OpenAI feature.")
            }
            if (updated.rewriteEngine == "openrouter" && !savedKeys.openrouter) {
                throw ConfigurationError("Enter an OpenRouter API key before selecting OpenRouter.")
            }
            if (updated.engine == "elevenlabs" && !savedKeys.elevenlabs) {
                throw ConfigurationError("Enter an ElevenLabs API key before selecting ElevenLabs.")
            }
            when (val draft = replacementsDraft) {
                is ReplacementsDraft.Import -> app.settingsStore.replacementsJson = draft.json
                ReplacementsDraft.Clear -> app.settingsStore.replacementsJson = null
                null -> Unit
            }
            replacementsDraft = null
            app.settingsStore.save(updated)
            settings = updated
            saveCount += 1
        } catch (failure: Exception) {
            error = failure.message ?: "Could not save settings."
        }
    }

    fun runChatGpt(operation: String, accountId: String? = null) {
        if (chatGptBusy) return
        chatGptBusy = true
        chatGptOperation = operation
        chatGptCancel = AtomicBoolean(false)
        val cancel = chatGptCancel
        chatGptMessage = if (operation in SIGN_IN_OPERATIONS) "Connecting in your browser…" else "Synchronizing ChatGPT…"
        viewModelScope.launch {
            val (state, message) = withContext(Dispatchers.IO) {
                val client = app.chatGpt
                var message = ""
                try {
                    when (operation) {
                        "connect", "add" -> {
                            client.signIn(newAccount = operation == "add", cancel = cancel)
                            message = "You're using your ChatGPT plan. Choose a model, then Save."
                        }
                        "disconnect" -> if (!client.disconnect()) {
                            message = "Disconnected locally. Remote revocation was not confirmed; disconnect in ChatGPT settings too."
                        }
                        "select" -> client.selectAccount(accountId.orEmpty())
                        else -> if (client.snapshot().connected) client.syncModels()
                    }
                } catch (failure: ChatGPTError) {
                    message = failure.message.orEmpty()
                } catch (_: Exception) {
                    message = "Could not update the ChatGPT connection. Please try again."
                }
                val state = runCatching { client.snapshot() }.getOrDefault(ChatGPTSnapshot())
                state to message
            }
            val previousAccount = chatGpt.active
            chatGpt = state
            chatGptBusy = false
            chatGptMessage = message
            if (previousAccount.isNotEmpty() && previousAccount != state.active) {
                update { it.copy(chatgptRewriteModel = "") }
            }
            if (operation in SIGN_IN_OPERATIONS && state.connected && state.models.isNotEmpty()) {
                update { it.copy(rewriteEngine = "chatgpt") }
            }
        }
    }

    fun cancelChatGptSignIn() {
        chatGptCancel.set(true)
    }

    fun openUsage() {
        app.openBrowser(com.redwhisper.android.core.ChatGPT.USAGE_URL)
    }

    private fun readSavedKeys() = SavedKeys(
        openai = app.secrets.has(SecretStore.OPENAI_API_KEY),
        elevenlabs = app.secrets.has(SecretStore.ELEVENLABS_API_KEY),
        openrouter = app.secrets.has(SecretStore.OPENROUTER_API_KEY),
    )

    private fun currentReplacementCount(): Int =
        app.settingsStore.replacementsJson?.let { json -> runCatching { TermReplacer.fromJson(json).size }.getOrNull() } ?: 0

    companion object {
        private val SIGN_IN_OPERATIONS = setOf("connect", "add")
        private const val MAX_REPLACEMENTS_BYTES = 1_048_576

        fun bubbleServiceEnabled(context: Context): Boolean {
            val enabled = Settings.Secure.getString(
                context.contentResolver,
                Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
            ) ?: return false
            val component = ComponentName(context, RedWhisperAccessibilityService::class.java)
            return enabled.split(':').any { ComponentName.unflattenFromString(it) == component }
        }
    }
}
