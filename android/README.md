# Red Whisper for Android

The Android edition of Red Whisper. Whenever the keyboard opens, in WhatsApp, Messages, or
any other app, a small Red Whisper bubble appears at the edge of the screen. Tap it to start
recording, tap again to stop, and your words are transcribed, optionally rephrased, and
inserted where you were typing. Settings → General → Show in can limit the bubble to chosen
apps instead.

## What matches the Mac app

- The same transcription models: OpenAI GPT Transcribe, GPT-4o Transcribe, GPT-4o Mini
  Transcribe, GPT-4o Transcribe Diarize, Whisper-1, and ElevenLabs Scribe v2, Scribe v2
  Realtime, and Scribe v1.
- The same rephrasing options: OpenAI, OpenRouter (including free models), and your ChatGPT
  plan through Sign in with ChatGPT. Saying “professional” first switches to a formal tone.
- The same rewrite safeguards, term replacement file, microphone sensitivity, language
  setting, silence and short-clip guards, and Save validation.
- The same settings layout: General, Transcription, Rephrasing, and API Keys.

## What is different on Android

- **The bubble replaces the keyboard shortcut.** Android has no global hotkeys, so you tap
  the bubble instead of holding Fn. Drag it up or down to move it.
- **Transcription is cloud-only in this version.** On-device Whisper and Llama rephrasing
  are planned for a later version.
- **Keys are stored with the Android Keystore** instead of macOS Keychain. They are never
  written to settings or included in backups.
- **Recordings stay in memory** and are never written to disk.

## Build

Requires JDK 17+ and the Android SDK (platform 36). From this folder:

```bash
./gradlew assembleDebug
```

Run the unit tests, which cover the provider requests, rewrite safeguards, pipeline,
ChatGPT sign-in pieces, and settings validation:

```bash
./gradlew testDebugUnitTest
```

## Install on a phone

1. On the phone, enable **Developer options** (Settings → About phone → Software
   information → tap **Build number** seven times), then turn on **USB debugging**.
2. On Samsung phones, turn off **Auto Blocker** (Settings → Security and privacy) while
   installing; it blocks USB commands and apps from outside the Galaxy Store.
3. Connect the phone, allow USB debugging from this computer, then:

```bash
./gradlew installDebug
```

## First-run setup

Open Red Whisper and complete **Finish setup**:

1. **Microphone**: allow recording.
2. **Notifications**: shows a notice while Red Whisper is listening.
3. **Bubble access**: turn on the Red Whisper bubble under Accessibility (called
   **Device Control and Data Access** on newer Samsung and Android versions). If Android
   says the setting is restricted, open **App info**, tap **⋮**, choose **Allow restricted
   settings**, and try again. Leave **Red Whisper bubble shortcut** off: that adds Android's
   own floating accessibility button, which Red Whisper doesn't use.

Then add an API key under **API Keys**, choose your models, and tap **Save**.

## Privacy

The accessibility service only checks whether the keyboard is open, which text box has focus,
and which app it belongs to, so the bubble appears in the right place, and it inserts your
finished dictation. The bubble never appears on password fields. It does
not read, store, or send anything else on your screen. Audio goes only to the transcription
provider you choose, and only transcript text goes to the rephrasing provider. Logs contain
timing and errors, never dictated text.

Google Play does not generally allow accessibility services for this purpose, so Red
Whisper for Android is installed directly as an APK.
