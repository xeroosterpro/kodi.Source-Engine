# Source Engine Pro

**Automated Emby/Jellyfin quality picker for Kodi**

Source Engine Pro is a Kodi addon that automatically selects the highest-fidelity media stream available across your private Emby and Jellyfin servers — no manual source picking required.

---

## Features

- **Auto-login** — authenticates with Emby and Jellyfin at startup; tokens are refreshed every 5 minutes in the background
- **Backup server failover** — configure a backup for each server; Source Engine Pro silently switches over and updates EmbyCon/JellyCon automatically if your primary goes offline, then restores when it comes back
- **13-signal confidence funnel** — provider ID matching, S/E numbers, episode names, runtime, file path, year proximity, and more — ensures the right episode is always selected, not just a name-match
- **Full scoring pipeline** — bitrate, file size, resolution (4K/HDR/DV), audio (Atmos/TrueHD/DTS-HD MA) all scored simultaneously
- **6 profile presets** — Auto Max, Audiophile, 4K Focus, Remux Focus, 1080p Focus, Light Mode
- **Deep Dive mode** — wider API searches, relaxed name matching, lower confidence thresholds for edge-case library setups
- **Playback reporting** — reports play start, progress, and watched status back to both Emby and Jellyfin
- **Head-to-head comparison** — both servers are searched in parallel; the winner is chosen by score; tie-breaker is configurable
- **Settings shortcut** — accessible directly from the addon home screen

---

## Installation

### Via Repository (recommended)

1. Download `repository.xeroosterpro-1.0.0.zip` from the `repository.xeroosterpro/` folder
2. In Kodi: **Settings → Add-ons → Install from zip file** → select the zip
3. Then: **Settings → Add-ons → Install from repository → XeroosterPro → Video add-ons → Source Engine Pro**

### Manual

1. Download `plugin.video.sourceenginepro-1.1.0.zip` from the `plugin.video.sourceenginepro/` folder
2. In Kodi: **Settings → Add-ons → Install from zip file** → select the zip

---

## Setup

1. Open **Source Engine Pro → Settings**
2. Enter your **Emby** and/or **Jellyfin** server address, username, and password
3. Click **⚡ Test Connection** to verify
4. Optionally configure backup servers, profile preset, and tie-breaker preference
5. Source Engine Pro is triggered automatically by **TMDb Helper** when you play any movie or TV episode

---

## Requirements

- Kodi 21 (Omega) or later
- [TMDb Helper](https://github.com/jurialmunkey/plugin.video.themoviedb.helper) (for playback integration)
- Emby and/or Jellyfin server

---

## Version History

| Version | Notes |
|---|---|
| 1.1.0 | Backup server failover, EmbyCon/JellyCon write-back, gzip fix, 5-min polling, token test progress bar, settings restructure, fast settings shortcut |
| 1.0.0 | Initial release |

---

*Made by XeroosterPro*
