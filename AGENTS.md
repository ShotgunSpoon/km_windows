# KM Windows

This is a Python/wxPython Windows client, not an NVGT game. The parent Downloads guide applies to NVGT language work only.

- `km_gui.py`: accessible window, keyboard shortcuts and worker coordination.
- `km_dialogs.py`, `km_preferences.py`: preferences, notification routing and account UI.
- `km_client.py`, `km_storage.py`: HTTP client, account isolation, AppData and Windows DPAPI.
- `km_audio.py`, `km_notifications.py`, `km_toast.py`: audio, real Web Push and silent Windows toasts.
- `km_updater.py`, `km_version.py`: GitHub release updates and version.
- `km_blackjack.py`, `km_dragon.py`: existing bounded, explicitly started bots.
- `km_selftest.py`, `tests/`: offline runtime and behavior checks.
- `assets/sounds`: original game sound files. Writable caches belong in AppData.

Keep wx operations on the UI thread. Never publish accounts, credentials, logs, captures, APKs or decompiled game sources. Keep saved accounts isolated; stop the old receiver before switching, and ignore queued callbacks from an inactive account. Protect session and push credentials using Windows DPAPI. Updates must verify their checksum and wait for running bots and pending requests. No game command or spending automation should run during tests or builds. Preserve existing donation limits and uncertain-spend checkpoints.

Run `python -m pytest tests -q` and `build.ps1` for release changes. Publish only a tested executable and its checksum, and increment `km_version.py` for later releases. No time estimates in user-facing communication.
