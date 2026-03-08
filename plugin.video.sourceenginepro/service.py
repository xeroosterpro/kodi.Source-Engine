import xbmc, xbmcaddon, requests, threading, uuid, xbmcgui, urllib.parse, os, shutil

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def check_token_health(url, token):
    if not token:
        return False
    try:
        headers = {'X-Emby-Token': token, 'Accept': 'application/json'}
        r = requests.get(f"{url}/System/Info", headers=headers, timeout=5, verify=False)
        if r.status_code == 200:
            return True
    except:
        pass
    return False

def get_auth_token(url, user, password, server_type):
    try:
        hardware_id = str(uuid.getnode())
        auth_header = f'MediaBrowser Client="Kodi", Device="Source-Engine-Pro", DeviceId="{hardware_id}", Version="1.0"'
        payload = {"Username": user, "Pw": password}

        headers = {
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'X-Emby-Authorization': auth_header,
            'Accept-Encoding': 'identity'  # Prevent gzip encoding issues with some Jellyfin/nginx setups
        }

        clean_url = url.replace(':443', '')

        r = requests.post(f"{clean_url}/Users/AuthenticateByName", json=payload, headers=headers, timeout=15, verify=False)

        if r.status_code == 200:
            data = r.json()
            return data.get('AccessToken'), data.get('User', {}).get('Id')
        else:
            xbmc.log(f"Source Engine Error: {server_type} returned {r.status_code} - {r.text}", xbmc.LOGERROR)
    except Exception as e:
        xbmc.log(f"Source Engine Error: Failed to login to {url} - {str(e)}", xbmc.LOGERROR)
    return None, None


def _write_to_embycon(url, user, password):
    """Point EmbyCon at a different server. Silently no-ops if EmbyCon is not installed."""
    try:
        parsed = urllib.parse.urlparse(url.rstrip('/'))
        protocol = '1' if parsed.scheme == 'https' else '0'
        host = parsed.hostname or ''
        port = str(parsed.port) if parsed.port else ('443' if parsed.scheme == 'https' else '80')
        embycon = xbmcaddon.Addon('plugin.video.embycon')
        embycon.setSetting('protocol', protocol)
        embycon.setSetting('ipaddress', host)
        embycon.setSetting('port', port)
        embycon.setSetting('username', user)
        if password:
            embycon.setSetting('password', password)
        xbmc.log(f"Source Engine Pro [BACKUP]: EmbyCon updated → {url}", xbmc.LOGINFO)
    except Exception as e:
        xbmc.log(f"Source Engine Pro [BACKUP]: EmbyCon update failed — {e}", xbmc.LOGWARNING)


def _write_to_jellycon(url, user):
    """Point JellyCon at a different server. Note: JellyCon does not store passwords."""
    try:
        jellycon = xbmcaddon.Addon('plugin.video.jellycon')
        jellycon.setSetting('server_address', url.rstrip('/'))
        jellycon.setSetting('username', user)
        xbmc.log(f"Source Engine Pro [BACKUP]: JellyCon updated → {url}", xbmc.LOGINFO)
    except Exception as e:
        xbmc.log(f"Source Engine Pro [BACKUP]: JellyCon update failed — {e}", xbmc.LOGWARNING)


def _show_startup_status():
    """Show a one-line toast after the first run_automation() indicating server health."""
    try:
        addon = xbmcaddon.Addon()
        parts = []
        for prefix, name in [('emby', 'Emby'), ('jelly', 'Jellyfin')]:
            url = addon.getSetting(f'{prefix}_url').strip()
            if not url:
                continue
            on_backup = addon.getSetting(f'{prefix}_on_backup') == 'true'
            display_name = f"{name} (Backup)" if on_backup else name
            token = addon.getSetting(f'{prefix}2_token' if on_backup else f'{prefix}_token').strip()
            parts.append(f"{display_name} {'[OK]' if token else '[FAIL]'}")
        if parts:
            xbmcgui.Dialog().notification(
                "Source Engine Pro",
                "  ".join(parts),
                xbmcgui.NOTIFICATION_INFO, 4000
            )
    except Exception as e:
        xbmc.log(f"Source Engine Pro [STARTUP]: Status notification failed — {e}", xbmc.LOGWARNING)


def _restore_primary(addon, prefix, name, url, user, pw, dialog):
    """Restore EmbyCon/JellyCon to the primary server after a backup period."""
    addon.setSetting(f'{prefix}_on_backup', 'false')
    if prefix == 'emby':
        _write_to_embycon(url, user, pw)
    else:
        _write_to_jellycon(url, user)
    xbmc.log(f"Source Engine Pro [BACKUP]: {name} primary restored — switching back from backup.", xbmc.LOGINFO)
    dialog.notification(
        "Source Engine Pro",
        f"{name} PRIMARY restored [OK]",
        xbmcgui.NOTIFICATION_INFO, 5000
    )


def run_automation():
    addon = xbmcaddon.Addon()
    dialog = xbmcgui.Dialog()
    window = xbmcgui.Window(10000)  # Used to track if we already warned the user

    for s_type in [("emby", "Emby"), ("jelly", "Jellyfin")]:
        prefix   = s_type[0]
        name     = s_type[1]
        url      = addon.getSetting(f'{prefix}_url')
        user     = addon.getSetting(f'{prefix}_user')
        pw       = addon.getSetting(f'{prefix}_pass')
        old_token = addon.getSetting(f'{prefix}_token')

        # ── Backup server settings ───────────────────────────────────────
        backup_url     = addon.getSetting(f'{prefix}2_url')
        backup_user    = addon.getSetting(f'{prefix}2_user')
        backup_pw      = addon.getSetting(f'{prefix}2_pass')
        on_backup      = addon.getSetting(f'{prefix}_on_backup') == 'true'
        prefer_backup  = addon.getSetting(f'{prefix}_prefer_backup') == 'true'

        if url and user:
            clean_url = url.rstrip('/')

            # ── Prefer backup: skip primary entirely ──────────────────────
            if prefer_backup and backup_url and backup_user:
                clean_backup = backup_url.rstrip('/')
                backup_old_token = addon.getSetting(f'{prefix}2_token')
                if not check_token_health(clean_backup, backup_old_token):
                    token2, uid2 = get_auth_token(clean_backup, backup_user, backup_pw, f"{name} Backup")
                    if token2:
                        addon.setSetting(f'{prefix}2_token', token2)
                        addon.setSetting(f'{prefix}2_uid', str(uid2) if uid2 else '')
                if not on_backup:
                    addon.setSetting(f'{prefix}_on_backup', 'true')
                    if prefix == 'emby':
                        _write_to_embycon(clean_backup, backup_user, backup_pw)
                    else:
                        _write_to_jellycon(clean_backup, backup_user)
                    xbmc.log(f"Source Engine Pro [BACKUP]: {name} prefer_backup enabled — routing to backup server.", xbmc.LOGINFO)
                window.clearProperty(f"SourceEngine_{prefix}_error")
                continue

            # ── Primary server health check / login ───────────────────────
            if check_token_health(clean_url, old_token):
                xbmc.log(f"Source Engine Pro: {prefix.upper()} token is still healthy. Skipping login.", xbmc.LOGINFO)
                if on_backup:
                    _restore_primary(addon, prefix, name, clean_url, user, pw, dialog)
                window.clearProperty(f"SourceEngine_{prefix}_error")
                continue

            token, uid = get_auth_token(clean_url, user, pw, name)

            if token:
                # Primary login succeeded
                if on_backup:
                    _restore_primary(addon, prefix, name, clean_url, user, pw, dialog)

                addon.setSetting(f'{prefix}_token', token)
                addon.setSetting(f'{prefix}_uid', str(uid) if uid else '')
                xbmc.log(f"Source Engine Pro: {prefix.upper()} token refreshed.", xbmc.LOGINFO)
                window.clearProperty(f"SourceEngine_{prefix}_error")

            else:
                # Primary login failed — try backup if configured
                xbmc.log(f"Source Engine Pro [BACKUP]: {name} primary auth failed. Checking backup server.", xbmc.LOGWARNING)

                if backup_url and backup_user:
                    clean_backup = backup_url.rstrip('/')
                    backup_old_token = addon.getSetting(f'{prefix}2_token')

                    if check_token_health(clean_backup, backup_old_token):
                        xbmc.log(f"Source Engine Pro [BACKUP]: {name} backup token is healthy.", xbmc.LOGINFO)
                        token2, uid2 = backup_old_token, addon.getSetting(f'{prefix}2_uid')
                    else:
                        token2, uid2 = get_auth_token(clean_backup, backup_user, backup_pw, f"{name} Backup")

                    if token2:
                        addon.setSetting(f'{prefix}2_token', token2)
                        addon.setSetting(f'{prefix}2_uid', str(uid2) if uid2 else '')

                        if not on_backup:
                            # First time switching to backup — update EmbyCon/JellyCon
                            addon.setSetting(f'{prefix}_on_backup', 'true')
                            if prefix == 'emby':
                                _write_to_embycon(clean_backup, backup_user, backup_pw)
                            else:
                                _write_to_jellycon(clean_backup, backup_user)
                            dialog.notification(
                                "Source Engine Pro",
                                f"[BACKUP] {name} — Primary offline, using backup server",
                                xbmcgui.NOTIFICATION_WARNING, 6000
                            )
                            xbmc.log(f"Source Engine Pro [BACKUP]: Switched {name} to backup server {clean_backup}.", xbmc.LOGWARNING)
                        else:
                            xbmc.log(f"Source Engine Pro [BACKUP]: {name} still on backup server.", xbmc.LOGINFO)

                        window.clearProperty(f"SourceEngine_{prefix}_error")
                        continue

                    else:
                        xbmc.log(f"Source Engine Pro [BACKUP]: {name} backup server also failed.", xbmc.LOGWARNING)

                # Both primary and backup failed (or no backup configured) — warn once
                if not window.getProperty(f"SourceEngine_{prefix}_error"):
                    if backup_url and backup_user:
                        dialog.notification(
                            "Source Engine Pro",
                            f"{name} PRIMARY + BACKUP both unreachable!",
                            xbmcgui.NOTIFICATION_ERROR, 5000
                        )
                    elif old_token:
                        dialog.notification(
                            "Source Engine Pro",
                            f"{name} API Unreachable!",
                            xbmcgui.NOTIFICATION_WARNING, 4000
                        )
                    else:
                        dialog.notification(
                            "Source Engine Pro",
                            f"Failed to connect to {name}",
                            xbmcgui.NOTIFICATION_ERROR, 4000
                        )
                    window.setProperty(f"SourceEngine_{prefix}_error", "true")


class PlaybackReporter(xbmc.Player):
    def __init__(self):
        super().__init__()
        self.is_playing = False
        self.is_paused = False
        self.server_url = None
        self.item_id = None
        self.token = None
        self.uid = None
        self.play_session_id = None
        self.position = 0
        self.timer = None

    def onAVStarted(self):
        # Clean up previous playback session if switching videos
        if self.is_playing:
            self.report_playback_stopped()
            if self.timer:
                self.timer.cancel()
            self.is_playing = False
        if self.isPlayingVideo():
            try:
                playing_file = self.getPlayingFile()
                if '/Videos/' in playing_file and '?Static=true&api_key=' in playing_file:
                    parsed = urllib.parse.urlparse(playing_file)
                    path_parts = parsed.path.split('/')

                    if 'Videos' in path_parts:
                        self.item_id = path_parts[path_parts.index('Videos') + 1]
                        query = urllib.parse.parse_qs(parsed.query)
                        self.token = query.get('api_key', [None])[0]
                        self.server_url = f"{parsed.scheme}://{parsed.netloc}"

                        addon = xbmcaddon.Addon()
                        emby_token = addon.getSetting('emby_token')
                        emby2_token = addon.getSetting('emby2_token')
                        if self.token == emby_token or self.token == emby2_token:
                            self.uid = addon.getSetting('emby_uid') if self.token == emby_token else addon.getSetting('emby2_uid')
                        else:
                            jelly_token = addon.getSetting('jelly_token')
                            self.uid = addon.getSetting('jelly_uid') if self.token == jelly_token else addon.getSetting('jelly2_uid')

                        if self.server_url and self.item_id and self.token:
                            self.play_session_id = str(uuid.uuid4()).replace('-', '')
                            self.is_playing = True
                            self.is_paused = False
                            self.report_playback_started()
                            self.timer = threading.Timer(30, self.report_progress)
                            self.timer.start()
            except Exception as e:
                xbmc.log(f"Source Engine Pro: Playback Reporter safely ignored non-plugin file. ({str(e)})", xbmc.LOGINFO)

    def onPlayBackPaused(self):
        self.is_paused = True

    def onPlayBackResumed(self):
        self.is_paused = False

    def onPlayBackStopped(self):
        if self.is_playing:
            self.report_playback_stopped()
            if self.timer:
                self.timer.cancel()
            self.is_playing = False
            self.is_paused = False

    def onPlayBackEnded(self):
        if self.is_playing and self.server_url and self.item_id and self.token and self.uid:
            try:
                url = f"{self.server_url}/Users/{self.uid}/PlayedItems/{self.item_id}"
                headers = {'X-Emby-Token': self.token}
                requests.post(url, headers=headers, verify=False, timeout=5)
                xbmc.log(f"Source Engine Pro: Successfully marked item {self.item_id} as WATCHED.", xbmc.LOGINFO)
            except Exception as e:
                xbmc.log(f"Source Engine Pro: Failed to mark as watched - {str(e)}", xbmc.LOGWARNING)

        self.onPlayBackStopped()

    def report_playback_started(self):
        url = f"{self.server_url}/Sessions/Playing"
        payload = {
            "ItemId": self.item_id,
            "PlayMethod": "DirectStream",
            "PositionTicks": 0,
            "PlaySessionId": self.play_session_id
        }
        headers = {'X-Emby-Token': self.token}
        try:
            requests.post(url, json=payload, headers=headers, verify=False, timeout=5)
        except:
            pass

    def report_progress(self):
        if not self.is_playing:
            return
        try:
            self.position = self.getTime() * 10000000
            url = f"{self.server_url}/Sessions/Playing/Progress"
            payload = {
                "ItemId": self.item_id,
                "PositionTicks": int(self.position),
                "IsPaused": self.is_paused,
                "PlaySessionId": self.play_session_id
            }
            headers = {'X-Emby-Token': self.token}
            requests.post(url, json=payload, headers=headers, verify=False, timeout=5)
        except:
            pass

        if self.is_playing:
            self.timer = threading.Timer(30, self.report_progress)
            self.timer.start()

    def report_playback_stopped(self):
        if not self.server_url or not self.item_id or not self.token:
            return
        url = f"{self.server_url}/Sessions/Playing/Stopped"
        payload = {
            "ItemId": self.item_id,
            "PositionTicks": int(self.position),
            "PlaySessionId": self.play_session_id
        }
        headers = {'X-Emby-Token': self.token}
        try:
            requests.post(url, json=payload, headers=headers, verify=False, timeout=5)
        except:
            pass

def install_player_file():
    """Copy the bundled TMDb Helper player file into place on first install or update."""
    try:
        addon        = xbmcaddon.Addon()
        addon_path   = addon.getAddonInfo('path')
        src          = os.path.join(addon_path, 'resources', 'players', 'sourceenginepro.json')
        players_dir  = xbmc.translatePath('special://userdata/addon_data/plugin.video.themoviedb.helper/players/')
        dst          = os.path.join(players_dir, 'sourceenginepro.json')

        if not os.path.isfile(src):
            xbmc.log('Source Engine Pro [PLAYER]: Bundled player file not found — skipping.', xbmc.LOGWARNING)
            return

        os.makedirs(players_dir, exist_ok=True)

        # Only overwrite if the destination doesn't exist or the source is newer
        if not os.path.isfile(dst) or os.path.getmtime(src) > os.path.getmtime(dst):
            shutil.copy2(src, dst)
            xbmc.log('Source Engine Pro [PLAYER]: Installed TMDb Helper player file.', xbmc.LOGINFO)
        else:
            xbmc.log('Source Engine Pro [PLAYER]: Player file already up to date.', xbmc.LOGINFO)
    except Exception as e:
        xbmc.log(f'Source Engine Pro [PLAYER]: Failed to install player file — {e}', xbmc.LOGWARNING)


if __name__ == '__main__':
    monitor = xbmc.Monitor()
    reporter = PlaybackReporter()

    install_player_file()

    # Wait for the Kodi UI to fully initialise before showing any notifications.
    # Calling dialog.notification() too early in the boot sequence causes it to
    # silently misfire on some skins and platforms.
    monitor.waitForAbort(15)
    if not monitor.abortRequested():
        run_automation()
        _show_startup_status()

    while not monitor.abortRequested():
        if monitor.waitForAbort(300):
            break
        run_automation()
