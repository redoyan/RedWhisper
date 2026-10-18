# Repository Guidelines

## Project Structure & Module Organization

RedWhisper is a Python desktop dictation application with a mostly flat module layout. Shared audio, hotkey, and settings logic lives in root modules such as `audio_processing.py`, `audio_devices.py`, `hotkey_config.py`, and `settings_store.py`. macOS entry points and UI code use `launch_gui.py`, `voxtape.py`, `native_settings.py`, and `mlx_whisper_core.py`; Windows equivalents use `windows_app.py`, `windows_settings.py`, and `windows_whisper_core.py`. Tests are under `tests/` and mirror module names. Images and icons belong in `assets/`; macOS bundle files live in `RedWhisper.app/`, while Windows installer configuration lives in `installer/`. Treat `build/`, `dist/`, `tmp/`, and `.venv/` as generated output.

## Build, Test, and Development Commands

- `python -m unittest discover -s tests -v` runs the complete test suite from an activated environment.
- `.\.venv\Scripts\python.exe windows_app.py --settings` launches the Windows source entry point and opens settings.
- `.\build_windows.ps1 -SkipInstaller` installs Windows dependencies, runs relevant tests, and creates `dist\RedWhisper\RedWhisper.exe` without Inno Setup.
- `.\build_windows.ps1` produces both the portable Windows application and installer.
- `./build_app.sh && open RedWhisper.app` refreshes and launches the macOS bundle after `./install.sh` has prepared its environment.

Use `requirements-windows.txt` on Windows and `requirements.txt` on macOS. Keep platform-only imports isolated so shared tests remain portable.

## Coding Style & Naming Conventions

Follow existing Python style: four-space indentation, type hints on public functions, `snake_case` for functions and variables, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. Prefer small platform-neutral helpers over duplicating logic in both front ends. Keep imports grouped as standard library, third party, then local modules. No formatter or linter is configured, so match surrounding code and keep diffs focused.

## Testing Guidelines

Tests use the standard-library `unittest` framework, with `unittest.mock` for operating-system and hardware boundaries. Name files `test_<module>.py`, classes `<Feature>Tests`, and methods `test_<expected_behavior>`. Add regression tests for bug fixes and mock microphones, keyrings, APIs, and GUI hooks rather than requiring live services or devices. There is no stated coverage threshold; run the full suite before submitting.

## Commit & Pull Request Guidelines

Recent commits use short, imperative summaries such as `Add live Bluetooth microphone discovery` and `Fix Bluetooth input refresh and rewrite preambles`; optional `fix:` prefixes also appear. Keep each commit scoped to one coherent change. Pull requests should explain behavior and validation, link related issues, and include screenshots for visible UI changes. For audio or performance reports, state the OS/version, hardware, transcription engine, and microphone. Never commit recordings, API keys, local settings, logs, or personally identifying dictation samples.
