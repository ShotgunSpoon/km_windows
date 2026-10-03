# Knight Manager Windows

An unofficial, keyboard-accessible Windows client for Knight Manager, with screen-reader speech, original game sounds, automatic game notifications, and configurable blackjack progress announcements.

## Download and run

Download **km_windows.exe** from the [latest release](https://github.com/ShotgunSpoon/km_windows/releases/latest). Run it from a folder where your Windows account can replace the executable when an update arrives. Python is included; no separate Python installation is required. Windows 10 or 11, 64-bit, is required.

Enter the eight-character connection code supplied by the game. The client remembers your login. Preferences contains **Accounts and login**, where you can add another account, switch between saved accounts, or sign out. Account changes are blocked while a bot or game command is pending.

## Keyboard controls

| Shortcut | Action |
| --- | --- |
| Enter | Send the typed command |
| Ctrl+N | Open the notification manager |
| Ctrl+P | Open preferences |
| Ctrl+L | Log in with another connection code |
| Ctrl+R | Repeat the last reply |
| Ctrl+period | Stop speech |
| Ctrl+B | Start or stop the blackjack bot |
| Ctrl+D | Start or resume the bounded Dragon Rock donation bot |
| Ctrl+Q | Exit |

The Application menu also provides account management and a manual update check. Quick actions work with Enter or the Run quick action button.

## Preferences and notifications

Ctrl+P sets the number of blackjack hands, the bet range, and how many hands pass between progress announcements. Limits apply to the next bot run. The bot does not start automatically. Dragon Rock donations retain their existing total cap of 20,000 gold in 10-gold donations, saved checkpoints, and scroll discovery stop.

Ctrl+N controls game notification categories and delivery. Speech, sound, log entries, and Windows toast notifications are separate switches. For log-only mode, leave logging enabled and disable the other three outputs. For sound-only mode, enable sound and disable the other outputs. The master switch disables every delivery mode. Preferences offers equivalent delivery controls for bot progress. Windows toast system audio is silent; the game's notification sound is controlled by the separate sound switch.

Real game Web Push notifications are delivered whether the client is focused or in the background. The client must be running. Windows notification settings can still control whether toasts are displayed. Unknown notification categories use the information sound. Screen-reader narration remains text-based; effects and ambience come from the game replies. Bot game sounds can be enabled separately.

## Saved data

All writable application data lives in:

```text
%LOCALAPPDATA%\blindmg\km_python
```

Preferences and the account list are JSON files in that directory. Each account has its own logs, notification history, session and notification subscription. Session tokens and private notification credentials are encrypted with Windows DPAPI for the current Windows user. They cannot simply be copied to a different Windows account as a login method. Logs can contain private gameplay and chat text; keep them private. New request logs redact tokens, connection codes, and subscription credentials.

The development client's old `km_state.json`, `km_push_credentials.json`, `probe_logs`, and sound cache are migrated on first launch when present beside the script or executable. Plaintext credential files are removed only after the protected copies have been verified. Existing historical logs are moved, not rewritten.

## Automatic updates

Updates are enabled by default and can be disabled in Preferences. On startup the executable checks this repository's latest stable GitHub release. Newer releases must include `km_windows.exe` and `km_windows.exe.sha256`. Downloads are checked against the release checksum and GitHub's asset digest when available. Installation waits until bots, game commands, and modal dialogs have finished. The application exits, replaces its executable, and restarts; the prior executable is retained as `.previous`. Saved accounts and preferences remain in AppData. Manual checks are available through the Application menu and Preferences.

## Diamond purchases

This client does not implement diamond checkout. The inspected app API exposes diamond history, but no confirmed purchase endpoint. Amazon's in-skill purchases use an authenticated Alexa session and Amazon's purchase flow; identifying an HTTP request as an Echo is not sufficient. Purchases should be completed through the game's supported Alexa purchase flow. See [Amazon's in-skill purchase documentation](https://developer.amazon.com/en-US/docs/alexa/in-skill-purchase/add-isps-to-a-skill.html).

## Development and releases

Use 64-bit Python 3.12 on Windows:

```powershell
python -m pip install -r requirements-build.txt
python km_gui.py
.\build.ps1
```

The build produces a standalone executable and SHA-256 file in `dist`. Its offline packaged runtime check verifies the dialogs, credential encryption, speech-library loading, and all 59 bundled audio clips without sending any game command. `KM_DATA_DIR` can redirect application data for isolated tests; `km_windows.exe --self-test` runs the packaged checks.

To publish a later release, update `km_version.py`, commit the source, and run the GitHub **Build and publish release** workflow. The version must be increased for installed clients to recognize an update. Account data, logs, decompiled APKs and packet captures are excluded from the repository and executable.

The client is not affiliated with Knight Manager. Original game audio belongs to its respective rights holders; bundled dependencies retain their own licenses.
