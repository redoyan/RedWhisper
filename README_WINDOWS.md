# RedWhisper for Windows

RedWhisper is a Windows system-tray dictation app. Hold a global shortcut,
speak, and release it; RedWhisper transcribes the recording and inserts the
result at the active cursor.

## Install

Run `RedWhisper-Setup-1.6.1.exe`. The installer:

- installs the app for the current user by default;
- adds a Start menu shortcut;
- optionally creates a desktop shortcut;
- optionally starts RedWhisper when you sign in;
- does not require Python.

At first launch, select a transcription engine, language, and push-to-talk
shortcut. By default, RedWhisper ranks the live Windows WASAPI inputs whenever
recording starts: it follows a usable physical Windows default, avoids silent
virtual inputs, and otherwise prefers a connected physical microphone or headset.
You can also choose a specific, non-duplicated microphone endpoint in Settings.
With local transcription, the Whisper model is downloaded on first use and
cached in the current Windows user profile.

## Windows requirements

- 64-bit Windows 10 or later
- A working microphone
- Internet access on first use to download the local model, or whenever a cloud
  transcription engine is selected
- Approximately 2 GB of free storage for the default local model and runtime

Local transcription automatically uses an available NVIDIA CUDA device when the
installed runtime supports it; otherwise it uses an optimized CPU path. CPU
transcription works without a dedicated GPU but is slower.

## Use

1. Start RedWhisper from the Start menu.
2. Wait for the “RedWhisper is ready” notification. First-time local model
   loading can take several minutes.
3. Hold the configured single key (default: **Right Ctrl**).
4. Speak while the transparent circular timer is visible. Its radial segments
   animate from the live microphone spectrum. Clear, short chimes mark both the
   start and finish of recording.
5. Release the shortcut. Keep the destination application focused.
6. RedWhisper copies the transcript to the clipboard and sends **Ctrl + V**.

Right-click the tray icon to record manually, change settings, open the
diagnostic log, or quit.

## Transcription and privacy

### Local Whisper

The default engine is `faster-whisper` with Whisper Large v3 Turbo. Audio stays
on the computer. Temporary WAV files are deleted after each transcription.
RedWhisper does not persist transcripts.

### OpenAI transcription

This is opt-in. Each recording is sent to the selected OpenAI transcription
model. The API key is stored by Python keyring in Windows Credential Manager,
not in `settings.json` or the repository.

### OpenRouter transcription

Enter an OpenRouter API key and an STT model identifier in Settings. RedWhisper
uses OpenRouter's dedicated `/api/v1/audio/transcriptions` endpoint. The default
is `openai/whisper-large-v3`; another current OpenRouter transcription model can
be entered manually.

### ElevenLabs transcription

Enter an ElevenLabs API key and select `scribe_v2` or `scribe_v1`. RedWhisper
uploads each completed recording to ElevenLabs' Speech-to-Text endpoint and
inserts the returned transcript.

OpenAI, OpenRouter, and ElevenLabs keys are stored as separate entries in
Windows Credential Manager. Only the key for the selected provider is used.

### Transcript restructuring

This is an independent, opt-in stage after speech-to-text. Choose **OpenAI** or
**OpenRouter** under **Restructuring** in Settings. RedWhisper then sends the
completed transcript text to the selected LLM, fixes grammar and punctuation,
removes filler and false starts, improves spoken phrasing, and groups longer
dictation into coherent paragraphs before inserting it.

The speech-to-text and restructuring providers do not need to match. For
example, Local Whisper can keep the recording on the computer and then send
only its transcript to an OpenAI rewrite model. OpenAI restructuring uses the
OpenAI model selected separately in Settings and the OpenAI key stored in
Windows Credential Manager. It makes one additional text API request per
recording and may incur API charges.

Restructuring uses a natural message-style tone by default. Begin a recording
with the word **professional** to request formal prose for that recording; the
command word is removed from the inserted result. The rewrite is treated only
as source text and is instructed not to answer dictated questions or follow
instructions inside the transcript.

Cloud restructuring sends transcript text, but not the recording, to its
provider. If restructuring is off, transcripts are not sent to a text LLM.
Local term replacement remains available through a JSON mapping file and runs
before the optional LLM stage.

## Files

- Settings: `%LOCALAPPDATA%\RedWhisper\settings.json`
- Diagnostic log: `%LOCALAPPDATA%\RedWhisper\RedWhisper.log`
- Model cache: the standard Hugging Face cache in the current user profile

The log records startup, device, timing, and error information. It does not
intentionally record audio or transcript text.

## Troubleshooting

### Nothing happens when I hold the shortcut

Choose **Right Ctrl**, **Right Alt**, **F8**, or click **Capture key…** in tray
icon **Settings…** and press your preferred key. RedWhisper reserves the
selected key while it is running, so it does not type characters or activate
commands in the focused application. An `Fn` key can be captured only when the
keyboard exposes it to Windows; many laptop keyboards handle `Fn` in firmware.

RedWhisper also checks the physical key state while recording. If Windows
swallows a key-release event, the recording stops automatically instead of
remaining latched.

### Text is copied but not inserted

Some elevated applications reject simulated input from a non-elevated process.
Run both applications at the same privilege level, or paste the clipboard
manually with **Ctrl + V**.

### The microphone fails to open

Enable desktop microphone access in **Settings > Privacy & security >
Microphone**, then open RedWhisper **Settings...** and choose **Auto-detect** or
the physical headset/microphone explicitly. RedWhisper shows each Windows WASAPI
endpoint once, without the MME and DirectSound duplicates.

### My USB webcam microphone disappears after a restart

Some USB controllers occasionally fail to start a webcam at boot. Device Manager
then shows **Unknown USB Device (Device Descriptor Request Failed)**, error
Code 43, and RedWhisper reports that the USB webcam or microphone could not
start. Install the automatic repair once from an elevated PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\windows_usb_recovery.ps1 -Install
```

This registers the SYSTEM task **RedWhisper\Repair USB devices**. It resets
failed USB devices 30 seconds after startup, after resume from sleep, and
whenever Windows logs a device start failure. RedWhisper also starts it when it
finds no usable microphone. Repairs are logged in
`%ProgramData%\RedWhisperUsbRecovery\usb-recovery.log`. Use `-Check` to list
failed devices and `-Uninstall` to remove the task.

The task restarts the device, its hub, and its USB controller. That fixes
devices Windows merely failed to enumerate. If the log still says
**Still failing**, the webcam's own firmware has hung and only losing power
resets it; Windows cannot switch USB port power off, so no software can do that
for you. Fixes that remove the hang, from cheapest to most certain:

- Move the webcam to a port on the chipset USB controller rather than the
  CPU's. Both ports that failed on the test PC were on the CPU controller.
- Enable **ErP Ready** (Advanced > APM Configuration) in an ASUS BIOS, or the
  equivalent "USB power off in S5" option elsewhere, so the webcam is fully
  powered off whenever the PC is, and every start is a clean power cycle.
- Check the webcam maker's support site for a firmware update.
- Put the webcam on a desktop USB hub with per-port power switches, so a
  hung webcam is reset with a switch at the desk instead of a replug.

### Local model loading fails

Open the diagnostic log from the tray menu. Check network access and free disk
space. On systems with an incompatible CUDA installation, RedWhisper should use
CPU; updating the graphics driver may restore GPU acceleration.

## Build from source

Open PowerShell in the repository and run:

```powershell
.\build_windows.ps1
```

The build script:

1. creates an isolated `.venv`;
2. installs `requirements-windows.txt`;
3. generates the multi-resolution Windows icon;
4. runs the cross-platform and Windows tests;
5. builds a no-console application with PyInstaller;
6. installs Inno Setup through `winget` if necessary;
7. creates `dist\RedWhisper-Setup-1.6.1.exe`.

Useful development variants:

```powershell
# Recreate the virtual environment and all build output
.\build_windows.ps1 -Clean

# Build the portable application folder without the installer
.\build_windows.ps1 -SkipInstaller

# Run the source entry point
.\.venv\Scripts\python.exe windows_app.py --settings
```

The unpacked application is written to `dist\RedWhisper\RedWhisper.exe`.
