package com.redwhisper.android.ui

import android.Manifest
import android.content.ComponentName
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.Cloud
import androidx.compose.material.icons.outlined.GraphicEq
import androidx.compose.material.icons.outlined.Key
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.MenuAnchorType
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.LifecycleResumeEffect
import com.redwhisper.android.core.Providers
import com.redwhisper.android.core.RewriteTone
import com.redwhisper.android.data.ChatApps
import com.redwhisper.android.overlay.RedWhisperAccessibilityService
import kotlinx.coroutines.delay

private data class Tab(val label: String, val icon: ImageVector)

private val TABS = listOf(
    Tab("General", Icons.Outlined.Settings),
    Tab("Transcription", Icons.Outlined.GraphicEq),
    Tab("Rephrasing", Icons.Outlined.AutoAwesome),
    Tab("API Keys", Icons.Outlined.Key),
)

/** The Mac app's model labels, minus the on-device engines that are not on Android yet. */
private val TRANSCRIPTION_CHOICES = listOf(
    Choice("openai" to "gpt-transcribe", "GPT Transcribe — OpenAI recommended", icon = Icons.Outlined.Cloud),
    Choice("openai" to "gpt-4o-transcribe", "GPT-4o Transcribe — OpenAI accuracy", icon = Icons.Outlined.Cloud),
    Choice("openai" to "gpt-4o-mini-transcribe", "GPT-4o Mini Transcribe — OpenAI speed", icon = Icons.Outlined.Cloud),
    Choice("openai" to "gpt-4o-transcribe-diarize", "GPT-4o Transcribe Diarize — OpenAI speakers", icon = Icons.Outlined.Cloud),
    Choice("openai" to "whisper-1", "Whisper-1 — OpenAI legacy", icon = Icons.Outlined.Cloud),
    Choice("elevenlabs" to "scribe_v2", "Scribe v2 — ElevenLabs", icon = Icons.Outlined.Cloud),
    Choice("elevenlabs" to "scribe_v2_realtime", "Scribe v2 Realtime — ElevenLabs streaming", icon = Icons.Outlined.Cloud),
    Choice("elevenlabs" to "scribe_v1", "Scribe v1 — ElevenLabs legacy", icon = Icons.Outlined.Cloud),
)

/** Which service rephrases; how it should sound is the separate Tone setting. */
private val REWRITE_ENGINE_CHOICES = listOf(
    Choice("off", "Off", "Insert the transcript as recognized"),
    Choice("openai", "OpenAI API", "Billed to your OpenAI API key", icon = Icons.Outlined.Cloud),
    Choice("openrouter", "OpenRouter", "Free models available; uses your OpenRouter key", icon = Icons.Outlined.Cloud),
    Choice("chatgpt", "ChatGPT subscription", "Uses your ChatGPT plan, with no API charges", icon = Icons.Outlined.Cloud),
)

private val OPENAI_REWRITE_CHOICES = Providers.OPENAI_REWRITE_MODELS.zip(
    listOf(
        "GPT-5 Nano — cheapest",
        "GPT-5 Mini — balanced value",
        "GPT-5.6 Luna — fast",
        "GPT-5.6 Terra — balanced",
        "GPT-5.6 Sol — maximum quality",
    ),
).map { (value, label) -> Choice(value, label) }

private val GAIN_CHOICES = listOf(
    Choice(1.0f, "Standard — 1×"),
    Choice(2.0f, "Sensitive — 2×"),
    Choice(4.0f, "High — 4×"),
    Choice(8.0f, "Very high — 8×"),
)

private val REWRITE_HELP = mapOf(
    "off" to "Transcripts are inserted exactly as they were recognized.",
    "openai" to "Sends transcript text, never audio, to OpenAI. Billed to your API key.",
    "openrouter" to "Sends transcript text to OpenRouter. Free-model availability varies.",
    "chatgpt" to "Sends transcript text to ChatGPT using your plan allowance, with no API charges.",
)
private val TONE_CHOICES = listOf(
    Choice(
        RewriteTone.CASUAL.value,
        "Casual",
        "A natural, friendly everyday message in your own voice",
    ),
    Choice(
        RewriteTone.PROFESSIONAL.value,
        "Professional",
        "Completely restructured and rewritten into a refined, polished message",
    ),
    Choice(
        RewriteTone.HUMOROUS.value,
        "Humorous",
        "Light-hearted and funny, with every time, name, and plan kept exact",
    ),
    Choice(
        RewriteTone.SARCASTIC.value,
        "Sarcastic",
        "Dry, tongue-in-cheek irony that stays playful rather than mean",
    ),
)

private const val PROFESSIONAL_SWITCH = "Start a dictation with “professional” to make just that message professional."

private val TONE_HELP = mapOf(
    RewriteTone.CASUAL.value to "Casual cleans up filler and grammar and keeps it relaxed. $PROFESSIONAL_SWITCH",
    RewriteTone.PROFESSIONAL.value to "Professional reorganizes your ideas, rewrites the sentences, " +
        "and refines the wording, keeping every fact and name.",
    RewriteTone.HUMOROUS.value to "Humorous adds playful wording and wit; the facts themselves never change. " +
        PROFESSIONAL_SWITCH,
    RewriteTone.SARCASTIC.value to "Sarcastic adds deadpan irony while keeping the real message clear. " +
        PROFESSIONAL_SWITCH,
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsApp(vm: SettingsViewModel) {
    var tab by rememberSaveable { mutableIntStateOf(0) }
    var showSaved by remember { mutableStateOf(false) }
    LaunchedEffect(vm.saveCount) {
        if (vm.saveCount > 0) {
            showSaved = true
            delay(1_600)
            showSaved = false
        }
    }
    LifecycleResumeEffect(Unit) {
        vm.refreshEnvironment()
        onPauseOrDispose {}
    }

    Box(Modifier.fillMaxSize()) {
        Scaffold(
            containerColor = MaterialTheme.colorScheme.background,
            topBar = {
                TopAppBar(
                    title = { Text("Red Whisper") },
                    actions = { TextButton(onClick = vm::save) { Text("Save") } },
                )
            },
            bottomBar = {
                NavigationBar {
                    TABS.forEachIndexed { index, item ->
                        NavigationBarItem(
                            selected = tab == index,
                            onClick = { tab = index },
                            icon = { Icon(item.icon, contentDescription = null) },
                            label = { Text(item.label) },
                        )
                    }
                }
            },
        ) { padding ->
            Column(
                Modifier
                    .fillMaxSize()
                    .padding(padding)
                    .imePadding()
                    .verticalScroll(rememberScrollState())
                    .padding(horizontal = 16.dp, vertical = 12.dp),
            ) {
                when (tab) {
                    0 -> GeneralTab(vm)
                    1 -> TranscriptionTab(vm)
                    2 -> RephrasingTab(vm)
                    else -> KeysTab(vm)
                }
            }
        }
        SavedConfirmation(visible = showSaved, modifier = Modifier.align(Alignment.Center))
    }

    vm.error?.let { message ->
        AlertDialog(
            onDismissRequest = { vm.error = null },
            title = { Text("Could not save Red Whisper settings") },
            text = { Text(message) },
            confirmButton = { TextButton(onClick = { vm.error = null }) { Text("OK") } },
        )
    }
}

/** A brief, centered confirmation that fades away on its own. */
@Composable
private fun SavedConfirmation(visible: Boolean, modifier: Modifier = Modifier) {
    AnimatedVisibility(
        visible = visible,
        enter = fadeIn() + scaleIn(initialScale = 0.9f),
        exit = fadeOut(),
        modifier = modifier,
    ) {
        Surface(
            shape = RoundedCornerShape(24.dp),
            color = MaterialTheme.colorScheme.inverseSurface.copy(alpha = 0.92f),
            contentColor = MaterialTheme.colorScheme.inverseOnSurface,
            shadowElevation = 8.dp,
            modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
        ) {
            Column(
                Modifier.padding(horizontal = 36.dp, vertical = 24.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Icon(Icons.Filled.CheckCircle, contentDescription = null, Modifier.size(44.dp), tint = Color(0xFF34C759))
                Text(
                    "Settings saved",
                    style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.padding(top = 10.dp),
                )
            }
        }
    }
}

@Composable
private fun GeneralTab(vm: SettingsViewModel) {
    val context = LocalContext.current
    val settings = vm.settings
    val requestPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        vm.refreshEnvironment()
    }
    var pickingApps by remember { mutableStateOf(false) }

    if (!vm.setup.complete) {
        Section(
            "Finish setup",
            footer = "Red Whisper uses Android's accessibility access only to show its bubble while the keyboard " +
                "is open and to insert your dictation. It doesn't read, store, or send anything else " +
                "on your screen. If Android says the setting is restricted, open App info, tap ⋮, choose " +
                "Allow restricted settings, then try again. Leave the Red Whisper bubble shortcut off; " +
                "Android's floating shortcut button isn't needed.",
        ) {
            SetupRow("Microphone", "Needed to record your voice", vm.setup.microphone, "Allow") {
                requestPermission.launch(Manifest.permission.RECORD_AUDIO)
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                RowDivider()
                SetupRow("Notifications", "Shows a notice while Red Whisper listens", vm.setup.notifications, "Allow") {
                    requestPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
                }
            }
            RowDivider()
            SetupRow(
                "Bubble access",
                "In Accessibility, open Installed apps → Red Whisper bubble and turn it on",
                vm.setup.bubble,
                "Turn on",
            ) {
                openAccessibilitySettings(context)
            }
            if (!vm.setup.bubble) {
                RowDivider()
                SettingRow("App info", "For the “Allow restricted settings” step", onClick = {
                    context.startActivity(
                        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", context.packageName, null)),
                    )
                })
            }
        }
    }

    Section(
        "Bubble",
        footer = "The bubble appears whenever the keyboard opens. Tap it to start recording, and tap it again " +
            "to stop; the text appears where you were typing. Drag the bubble up or down to move it.",
    ) {
        SwitchRow("Show the bubble", "Appears whenever the keyboard opens", settings.bubbleEnabled) { enabled ->
            vm.update { it.copy(bubbleEnabled = enabled) }
        }
        RowDivider()
        ChoiceRow(
            "Show in",
            listOf(
                Choice(true, "Every app", "Whenever the keyboard opens, in any app"),
                Choice(false, "Chosen apps", "Only in the apps you pick"),
            ),
            settings.bubbleInAllApps,
            enabled = settings.bubbleEnabled,
        ) { allApps -> vm.update { it.copy(bubbleInAllApps = allApps) } }
        if (!settings.bubbleInAllApps) {
            RowDivider()
            val chosen = settings.chatApps.size
            SettingRow(
                "Chosen apps",
                if (chosen == 1) "1 app chosen" else "$chosen apps chosen",
                enabled = settings.bubbleEnabled,
                onClick = { pickingApps = true },
            )
        }
    }

    val devices = vm.inputDevices
    val deviceChoices = listOf(Choice<String?>(null, "Automatic", "The phone's current microphone")) +
        devices.map { Choice<String?>(it.key, it.label) } +
        listOfNotNull(
            settings.inputDevice?.takeIf { key -> devices.none { it.key == key } }?.let {
                Choice<String?>(it, "${it.substringAfter(':').ifEmpty { "Saved microphone" }} — not connected")
            },
        )
    Section(
        "Microphone",
        footer = if (devices.isEmpty()) {
            "No microphone found. Connect a microphone or Bluetooth headset to use it here."
        } else {
            "${devices.size} microphone${if (devices.size == 1) "" else "s"} available. Bluetooth headsets appear automatically."
        },
    ) {
        ChoiceRow("Input device", deviceChoices, settings.inputDevice) { key -> vm.update { it.copy(inputDevice = key) } }
        RowDivider()
        ChoiceRow("Sensitivity", GAIN_CHOICES, settings.microphoneGain) { gain -> vm.update { it.copy(microphoneGain = gain) } }
    }

    if (pickingApps) {
        AppPickerDialog(
            apps = remember { vm.launchableApps() },
            selected = settings.chatApps.toSet(),
            onDismiss = { pickingApps = false },
            onReset = { vm.update { it.copy(chatApps = ChatApps.DEFAULTS) } },
            onChange = { packages -> vm.update { it.copy(chatApps = packages.sorted()) } },
        )
    }
}

@Composable
private fun SetupRow(title: String, subtitle: String, done: Boolean, action: String, onClick: () -> Unit) {
    SettingRow(title, subtitle) {
        if (done) {
            Icon(Icons.Filled.CheckCircle, contentDescription = "Done", tint = Color(0xFF2E9E57))
        } else {
            FilledTonalButton(onClick = onClick) { Text(action) }
        }
    }
}

private fun openAccessibilitySettings(context: android.content.Context) {
    val component = ComponentName(context, RedWhisperAccessibilityService::class.java).flattenToString()
    val details = Intent("android.settings.ACCESSIBILITY_DETAILS_SETTINGS")
        .putExtra(Intent.EXTRA_COMPONENT_NAME, component)
    val opened = runCatching { context.startActivity(details) }.isSuccess
    if (!opened) context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
}

@Composable
private fun TranscriptionTab(vm: SettingsViewModel) {
    val settings = vm.settings
    val selected = settings.engine to if (settings.engine == "elevenlabs") settings.elevenlabsModel else settings.openaiModel
    val provider = if (settings.engine == "elevenlabs") "ElevenLabs" else "OpenAI"
    val help = "Each recording is sent to $provider and billed to your API key." +
        if (vm.savedKeys.has(settings.engine) || when (settings.engine) {
                "elevenlabs" -> vm.keyDrafts.elevenlabs.isNotBlank()
                else -> vm.keyDrafts.openai.isNotBlank()
            }
        ) "" else " Add a $provider key under API Keys."
    val pickReplacements = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        uri?.let(vm::importReplacements)
    }

    Section("Engine", footer = help) {
        ChoiceRow("Model", TRANSCRIPTION_CHOICES, selected) { (engine, model) ->
            vm.update {
                if (engine == "elevenlabs") it.copy(engine = engine, elevenlabsModel = model) else it.copy(engine = engine, openaiModel = model)
            }
        }
        RowDivider()
        SettingRow("Language", "A code such as “en”, or “auto” to detect it") {
            OutlinedTextField(
                value = settings.language,
                onValueChange = { value -> vm.update { it.copy(language = value.trim().take(20)) } },
                singleLine = true,
                placeholder = { Text("auto") },
                modifier = Modifier.width(96.dp),
            )
        }
    }

    Section(
        "Accuracy",
        footer = "Replacements run on this phone. Terms match regardless of case, and are inserted exactly as written.",
    ) {
        val name = settings.replacementsName
        SettingRow(
            "Replacement file",
            if (name == null) "Fix names and jargon after transcription" else "$name · ${vm.replacementsCount} terms",
        ) {
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp), verticalAlignment = Alignment.CenterVertically) {
                if (name != null) TextButton(onClick = vm::clearReplacements) { Text("Remove") }
                OutlinedButton(onClick = { pickReplacements.launch(arrayOf("application/json", "text/plain", "*/*")) }) {
                    Text("Choose…")
                }
            }
        }
    }
}

@Composable
private fun RephrasingTab(vm: SettingsViewModel) {
    val settings = vm.settings
    val engine = settings.rewriteEngine
    val footer = REWRITE_HELP.getValue(engine) +
        if (engine == "off") "" else "\n" + TONE_HELP.getValue(settings.rewriteTone)
    Section("Engine", footer = footer) {
        ChoiceRow("Rephrase with", REWRITE_ENGINE_CHOICES, engine) { value -> vm.update { it.copy(rewriteEngine = value) } }
        if (engine != "off") {
            RowDivider()
            ChoiceRow("Tone", TONE_CHOICES, settings.rewriteTone) { tone -> vm.update { it.copy(rewriteTone = tone) } }
        }
        when (engine) {
            "openai" -> {
                RowDivider()
                ChoiceRow("Model", OPENAI_REWRITE_CHOICES, settings.openaiRewriteModel) { model ->
                    vm.update { it.copy(openaiRewriteModel = model) }
                }
            }
            "openrouter" -> {
                RowDivider()
                OpenRouterModelRow(vm)
            }
            "chatgpt" -> {
                RowDivider()
                ChatGptModelRow(vm)
            }
        }
    }
    if (engine == "chatgpt") ChatGptAccountSection(vm)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun OpenRouterModelRow(vm: SettingsViewModel) {
    val settings = vm.settings
    var expanded by remember { mutableStateOf(false) }
    val remembered = (listOf(Providers.DEFAULT_OPENROUTER_MODEL) + settings.openrouterModels).distinct()
    Column(Modifier.padding(horizontal = 16.dp, vertical = 12.dp)) {
        Text("Model", style = MaterialTheme.typography.bodyLarge)
        Text(
            "Type any OpenRouter model ID; free ones usually end in :free",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        ExposedDropdownMenuBox(expanded = expanded, onExpandedChange = { expanded = it }) {
            OutlinedTextField(
                value = settings.openrouterModel,
                onValueChange = { value -> vm.update { it.copy(openrouterModel = value) } },
                singleLine = true,
                placeholder = { Text(Providers.DEFAULT_OPENROUTER_MODEL) },
                trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded) },
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 8.dp)
                    .menuAnchor(MenuAnchorType.PrimaryEditable),
            )
            ExposedDropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
                remembered.forEach { model ->
                    DropdownMenuItem(
                        text = { Text(model) },
                        onClick = {
                            vm.update { it.copy(openrouterModel = model) }
                            expanded = false
                        },
                    )
                }
            }
        }
    }
}

@Composable
private fun ChatGptModelRow(vm: SettingsViewModel) {
    val models = vm.chatGpt.models
    val selected = vm.settings.chatgptRewriteModel
    // Duplicate display names would be indistinguishable, so they get their slug, as on the Mac.
    val labels = models.map { it.displayName }
    val choices = listOf(Choice("", "Automatic — account default")) + models.map { model ->
        Choice(model.slug, if (labels.count { it == model.displayName } == 1) model.displayName else "${model.displayName} (${model.slug})")
    } + listOfNotNull(
        selected.takeIf { it.isNotEmpty() && models.none { model -> model.slug == it } }?.let {
            Choice(it, if (models.isEmpty()) "$it — sync needed" else "$it — unavailable")
        },
    )
    ChoiceRow("Model", choices, selected) { slug -> vm.update { it.copy(chatgptRewriteModel = slug) } }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ChatGptAccountSection(vm: SettingsViewModel) {
    val state = vm.chatGpt
    val busy = vm.chatGptBusy
    val signingIn = busy && vm.chatGptOperation in setOf("connect", "add")
    val count = state.models.size
    val status = if (state.connected) {
        "Connected · $count model${if (count == 1) "" else "s"} available"
    } else {
        "Not connected. Sign in to rephrase with your plan."
    }
    Section(
        "ChatGPT Account",
        footer = "Uses your ChatGPT plan allowance for rephrasing only. Transcription keeps its own API billing. " +
            "Sign-in finishes in your browser; come back to Red Whisper afterwards.",
    ) {
        SettingRow("ChatGPT plan", status + if (vm.chatGptMessage.isNotEmpty()) "\n${vm.chatGptMessage}" else "")
        if (state.accounts.isNotEmpty()) {
            RowDivider()
            ChoiceRow(
                "Account",
                state.accounts.map { Choice(it.id, it.label) },
                state.active,
                enabled = !busy,
            ) { id -> vm.runChatGpt("select", id) }
        }
        RowDivider()
        FlowRow(
            Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 12.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            when {
                signingIn -> OutlinedButton(onClick = vm::cancelChatGptSignIn) { Text("Cancel Sign-In") }
                !state.connected -> Button(onClick = { vm.runChatGpt("connect") }, enabled = !busy) {
                    Text("Continue with ChatGPT")
                }
                else -> {
                    OutlinedButton(onClick = { vm.runChatGpt("refresh") }, enabled = !busy) { Text("Refresh Models") }
                    OutlinedButton(onClick = { vm.runChatGpt("add") }, enabled = !busy) { Text("Add Account") }
                    OutlinedButton(onClick = vm::openUsage, enabled = !busy) { Text("Manage Usage") }
                    OutlinedButton(onClick = { vm.runChatGpt("disconnect") }, enabled = !busy) { Text("Disconnect") }
                }
            }
        }
    }
}

@Composable
private fun KeysTab(vm: SettingsViewModel) {
    val drafts = vm.keyDrafts
    Section(
        "Provider Keys",
        footer = "Keys are encrypted on this phone with the Android Keystore, never stored in settings or backups. " +
            "Paste a new key to replace a saved one, then tap Save.",
    ) {
        KeyRow("OpenAI", "Transcription and rephrasing", vm.savedKeys.openai, drafts.openai) { value ->
            vm.keyDrafts = drafts.copy(openai = value)
        }
        RowDivider()
        KeyRow("ElevenLabs", "Transcription", vm.savedKeys.elevenlabs, drafts.elevenlabs) { value ->
            vm.keyDrafts = drafts.copy(elevenlabs = value)
        }
        RowDivider()
        KeyRow("OpenRouter", "Rephrasing", vm.savedKeys.openrouter, drafts.openrouter) { value ->
            vm.keyDrafts = drafts.copy(openrouter = value)
        }
    }
}

@Composable
private fun KeyRow(title: String, usage: String, saved: Boolean, value: String, onChange: (String) -> Unit) {
    Column(Modifier.padding(horizontal = 16.dp, vertical = 12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(title, style = MaterialTheme.typography.bodyLarge)
                Text(usage, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (saved) {
                Icon(Icons.Filled.CheckCircle, contentDescription = null, Modifier.size(18.dp), tint = Color(0xFF2E9E57))
                Text(
                    " Saved",
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        OutlinedTextField(
            value = value,
            onValueChange = onChange,
            singleLine = true,
            placeholder = { Text(if (saved) "Saved securely — paste to replace" else "Paste API key") },
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, autoCorrectEnabled = false),
            modifier = Modifier
                .fillMaxWidth()
                .padding(top = 8.dp),
        )
    }
}
