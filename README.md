# Source Engine Pro

**Automated Emby/Jellyfin quality picker for Kodi**

Source Engine Pro is a Kodi addon that automatically selects the highest-fidelity media stream available across your private Emby and Jellyfin servers — no manual source picking required.

---

## Features

### Core Engine
- **Auto-login** — authenticates with Emby and Jellyfin at startup; tokens are refreshed every 5 minutes in the background
- **13-signal confidence funnel** — provider ID matching, S/E numbers, episode names, runtime, file path, year proximity, and more — ensures the right episode is always selected, not just a name-match
- **Full scoring pipeline** — bitrate, file size, resolution (4K/HDR/DV), audio (Atmos/TrueHD/DTS-HD MA) all scored simultaneously
- **6 profile presets** — Auto Max, Audiophile, 4K Focus, Remux Focus, 1080p Focus, Light Mode
- **Deep Dive mode** — wider API searches, relaxed name matching, lower confidence thresholds for edge-case library setups
- **Head-to-head comparison** — both servers are searched in parallel; the winner is chosen by score; tie-breaker is configurable
- **Playback reporting** — reports play start, progress, and watched status back to Emby and Jellyfin

### Server Management
- **Backup server failover** — configure a backup for each server; Source Engine Pro silently switches over and updates EmbyCon/JellyCon automatically if your primary goes offline, then restores when it comes back
- **TMDb Helper auto-install** — player JSON is written automatically on first run and set as the default player; no manual configuration needed
- **Hourly ping monitor** — compares server latency every hour; fastest server is marked with a color-coded toast notification

### Server Stats (Main Menu)
- **Live server dashboard** — tap "Server Stats" from the main menu for real-time data: ping latency, active streams, direct play vs transcoding, users online, library counts
- **Startup stats** — optional toast on boot showing a compact per-server summary (toggle in Settings → Notifications)

### Notifications
- Color-coded status toasts — green `[OK]` / red `[FAIL]` on startup, orange for warnings, red for errors
- Backup failover alerts — instant notification when primary goes down or comes back
- Consistent 4-second display time throughout

---

## Installation

### Via Repository (recommended)

1. Download `repository.xeroosterpro-1.0.0.zip` from the `repository.xeroosterpro/` folder
2. In Kodi: **Settings → Add-ons → Install from zip file** → select the zip
3. Then: **Settings → Add-ons → Install from repository → XeroosterPro → Video add-ons → Source Engine Pro**

### Manual

1. Download `plugin.video.sourceenginepro-1.1.5.zip` from the `plugin.video.sourceenginepro/` folder
2. In Kodi: **Settings → Add-ons → Install from zip file** → select the zip

---

## Setup

1. Open **Source Engine Pro → Settings**
2. Enter your **Emby** and/or **Jellyfin** server address, username, and password
3. Click **Test Connection** to verify
4. Optionally configure backup servers, profile preset, and tie-breaker preference
5. Source Engine Pro is triggered automatically by **TMDb Helper** when you play any movie or TV episode
6. The TMDb Helper player is installed and set as default automatically on first run

---

## Requirements

- Kodi 21 (Omega) or later
- [TMDb Helper](https://github.com/jurialmunkey/plugin.video.themoviedb.helper) (for playback integration)
- Emby and/or Jellyfin server

---

## Version History

| Version | Highlights |
|---|---|
| **1.1.5** | Server Stats live dashboard, hourly ping monitor, color-coded notifications, Android/Shield backup failover fix (EmbyCon/JellyCon XML write), logic audit fixes, debug audit fixes |
| **1.1.4** | Server Stats module, startup stats toast, `show_server_stats()` menu item |
| **1.1.3** | Shield/Android fixes: `xbmcvfs.translatePath()` (Kodi 21 compat), TMDb Helper player auto-install via `xbmcvfs.File()`, `special://profile/` path fix, handle=-1 playback fix |
| **1.1.2** | TMDb Helper player JSON (`is_resolvable`, priority 1), cross-addon settings persisted via direct XML write on Android |
| **1.1.1** | Jellyfin auto-fill 3-tier fallback, token test progress bar improvements |
| **1.1.0** | Backup server failover, EmbyCon/JellyCon write-back, gzip fix, 5-min polling, settings restructure, fast settings shortcut |
| **1.0.0** | Initial release |

---

*Made by XeroosterPro*
