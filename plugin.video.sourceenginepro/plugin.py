import sys, urllib.parse as _ul

# ── Fast-path: skip heavy imports for lightweight actions ─────────────────────
if len(sys.argv) > 2 and sys.argv[2]:
    _fp_action = dict(_ul.parse_qsl(sys.argv[2].lstrip('?'))).get('action', '')
    if _fp_action == 'open_settings':
        import xbmc
        xbmc.executebuiltin('Addon.OpenSettings(plugin.video.sourceenginepro)')
        sys.exit(0)
# ─────────────────────────────────────────────────────────────────────────────

import sys, xbmc, xbmcgui, xbmcplugin, xbmcaddon, requests, urllib.parse, threading, time, re, json, os, datetime

import urllib3
try:
    from resources.lib.notification_window import show_trophy_notification
except Exception:
    show_trophy_notification = None

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {"the", "a", "an", "and", "of"}
_SE_RE = re.compile(r'[Ss]0*(\d+)\s*[Ee]0*(\d+)')

_tmdb_key_cache = None
def _get_tmdb_api_key():
    """Resolve TMDB API key: own addon setting > TMDb Helper > hardcoded fallback."""
    global _tmdb_key_cache
    if _tmdb_key_cache:
        return _tmdb_key_cache

    # 1. Our own addon setting (user-configured)
    try:
        own_key = xbmcaddon.Addon().getSetting('tmdb_api_key')
        if own_key and len(own_key) > 20:
            _tmdb_key_cache = own_key
            xbmc.log("Source Engine [TMDB KEY]: Using user-configured API key", xbmc.LOGINFO)
            return own_key
    except:
        pass

    # 2. Borrow from TMDb Helper (the user's metadata middleman)
    try:
        from resources.tmdbhelper.lib.api.api_keys.tmdb import TMDB_API
        key = TMDB_API.API_KEY
        if key and len(key) > 20:
            _tmdb_key_cache = key
            xbmc.log("Source Engine [TMDB KEY]: Using TMDb Helper's internal API key", xbmc.LOGINFO)
            return key
    except:
        pass

    # 2b. Try reading TMDb Helper's key file directly
    try:
        import os
        tmdb_helper_path = xbmcaddon.Addon('plugin.video.themoviedb.helper').getAddonInfo('path')
        key_file = os.path.join(tmdb_helper_path, 'resources', 'tmdbhelper', 'lib', 'api', 'api_keys', 'tmdb.py')
        if os.path.exists(key_file):
            with open(key_file, 'r') as f:
                content = f.read()
            match = re.search(r"API_KEY\s*=\s*['\"]([a-f0-9]{20,})['\"]", content)
            if match:
                key = match.group(1)
                _tmdb_key_cache = key
                xbmc.log(f"Source Engine [TMDB KEY]: Extracted TMDb Helper API key from file", xbmc.LOGINFO)
                return key
    except:
        pass

    # 3. Hardcoded fallback
    _tmdb_key_cache = "a07324c669cac4d96789197134ce272b"
    xbmc.log("Source Engine [TMDB KEY]: Using hardcoded fallback key", xbmc.LOGINFO)
    return _tmdb_key_cache

def _title_tokens(s):
    if not s:
        return []
    return _WORD_RE.findall(str(s).lower())

def _title_token_set(s):
    return {t for t in _title_tokens(s) if t and t not in _STOPWORDS}

def _title_similarity(a, b):
    A = _title_token_set(a)
    B = _title_token_set(b)
    if not A or not B:
        return 0.0
    return len(A & B) / float(len(A | B) or 1)

def _titles_match(a, b):
    """
    Best-effort title matching. Emby/Jellyfin titles frequently differ in punctuation,
    casing, and articles, so strict equality causes false misses.
    """
    A = _title_token_set(a)
    B = _title_token_set(b)
    if not A or not B:
        return False
    if A == B:
        return True
    if A.issubset(B) or B.issubset(A):
        return True
    return _title_similarity(a, b) >= 0.7

def get_int(addon, key, default=0):
    try:
        val = addon.getSetting(key)
        if val != '':
            return int(val)
    except:
        pass
    return default

def notify(title, msg, time=5000):
    c_title = title.replace('"', '').replace("'", "")
    c_msg = msg.replace('"', '').replace("'", "")
    xbmc.executebuiltin(f'Notification("{c_title}", "{c_msg}", {time}, "")')

# ─────────────────────────────────────────────────────────────────────────────
# SOURCE BATTLE HISTORY / SCOREBOARD
# ─────────────────────────────────────────────────────────────────────────────
_HISTORY_MAX = 500

def _history_path():
    addon = xbmcaddon.Addon()
    profile = addon.getAddonInfo('profile')
    try:
        import xbmcvfs
        profile = xbmcvfs.translatePath(profile)
    except Exception:
        profile = xbmc.translatePath(profile)
    os.makedirs(profile, exist_ok=True)
    return os.path.join(profile, 'source_history.json')

def _read_history():
    path = _history_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []

def _append_history(entry):
    entries = _read_history()
    entries.insert(0, entry)
    if len(entries) > _HISTORY_MAX:
        entries = entries[:_HISTORY_MAX]
    try:
        with open(_history_path(), 'w', encoding='utf-8') as f:
            json.dump(entries, f, ensure_ascii=False)
    except Exception as e:
        xbmc.log(f"Source Engine Pro [HISTORY]: Write failed — {e}", xbmc.LOGWARNING)

def retrieve_embycon_settings():
    """Read connection info from the EmbyCon addon and populate our Emby settings."""
    try:
        embycon = xbmcaddon.Addon('plugin.video.embycon')
    except Exception:
        xbmcgui.Dialog().notification(
            'Source Engine Pro', 'EmbyCon not installed or not found.',
            xbmcgui.NOTIFICATION_WARNING, 3500
        )
        return

    protocol_idx = embycon.getSetting('protocol') or '1'
    # EmbyCon: 0 = http, 1 = https
    protocol  = 'https' if protocol_idx == '1' else 'http'
    ipaddress = embycon.getSetting('ipaddress') or ''
    port      = embycon.getSetting('port') or ''
    username  = embycon.getSetting('username') or ''
    password  = embycon.getSetting('password') or ''

    if not ipaddress:
        xbmcgui.Dialog().notification(
            'Source Engine Pro', 'EmbyCon has no server address configured.',
            xbmcgui.NOTIFICATION_WARNING, 3500
        )
        return

    # Build URL — omit port if it is the standard port for the protocol
    std_ports = {'https': '443', 'http': '80'}
    if port and port != std_ports.get(protocol, ''):
        url = f"{protocol}://{ipaddress}:{port}"
    else:
        url = f"{protocol}://{ipaddress}"

    our = xbmcaddon.Addon()
    our.setSetting('emby_url',  url)
    our.setSetting('emby_user', username)
    if password:
        our.setSetting('emby_pass', password)

    xbmcgui.Dialog().notification(
        'Source Engine Pro',
        f"Emby filled: {url}  ({username})",
        xbmcgui.NOTIFICATION_INFO, 4000
    )
    xbmc.log(f"Source Engine Pro [IMPORT]: Emby settings imported from EmbyCon — {url}", xbmc.LOGINFO)


def retrieve_jellycon_settings():
    """Read connection info from the JellyCon addon and populate our Jellyfin settings."""
    try:
        jellycon = xbmcaddon.Addon('plugin.video.jellycon')
    except Exception:
        xbmcgui.Dialog().notification(
            'Source Engine Pro', 'JellyCon not installed or not found.',
            xbmcgui.NOTIFICATION_WARNING, 3500
        )
        return

    # JellyCon stores the full URL in server_address; fallback to parts
    server_address = jellycon.getSetting('server_address') or ''
    username       = jellycon.getSetting('username') or ''

    if not server_address:
        protocol_idx = jellycon.getSetting('protocol') or '0'
        protocol  = 'https' if protocol_idx == '1' else 'http'
        ipaddress = jellycon.getSetting('ipaddress') or ''
        port      = jellycon.getSetting('port') or ''
        if not ipaddress:
            xbmcgui.Dialog().notification(
                'Source Engine Pro', 'JellyCon has no server address configured.',
                xbmcgui.NOTIFICATION_WARNING, 3500
            )
            return
        std_ports = {'https': '443', 'http': '80'}
        if port and port != std_ports.get(protocol, ''):
            server_address = f"{protocol}://{ipaddress}:{port}"
        else:
            server_address = f"{protocol}://{ipaddress}"

    our = xbmcaddon.Addon()
    our.setSetting('jelly_url',  server_address)
    our.setSetting('jelly_user', username)
    # JellyCon does not store a plain-text password — user must enter it manually

    msg = f"Jellyfin filled: {server_address}  ({username})"
    if not username:
        msg += " — enter password in settings to complete"
    xbmcgui.Dialog().notification('Source Engine Pro', msg, xbmcgui.NOTIFICATION_INFO, 4000)
    xbmc.log(f"Source Engine Pro [IMPORT]: Jellyfin settings imported from JellyCon — {server_address}", xbmc.LOGINFO)


def _friendly_exc(exc):
    """Convert a requests exception into a short, readable message for notifications."""
    s = str(exc).lower()
    if 'gzip' in s or 'decompressing' in s or 'content-encoding' in s or 'decod' in s:
        return "Server sent a bad response — it may be starting up or misconfigured."
    if 'timed out' in s or 'timeout' in s:
        return "Connection timed out — server may be offline or unreachable."
    if 'connection' in s or 'refused' in s or 'remote' in s or 'network' in s:
        return "Cannot reach server — check the URL and your network."
    if '503' in s or 'unavailable' in s:
        return "Server is unavailable (503) — it may be offline or restarting."
    return "Connection failed — check the server address and try again."


def _run_token_test(label, url, token, uid=None):
    """
    Shared token-test implementation used by all four test functions.
    Shows a DialogProgressBG while connecting, then a notification with the result.

    Strategy: use /System/Info (proven reliable across both Emby and Jellyfin,
    and unaffected by per-user permission quirks that can cause /Users/Me to 500).
    Then fetch the username from /Users/{uid} if a UID is stored.
    """
    progress = xbmcgui.DialogProgressBG()
    progress.create(f'{label} — Token Test', 'Connecting to server...')
    try:
        headers = {
            'X-Emby-Token': token,
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0',
            'Accept-Encoding': 'identity',
        }

        progress.update(25, message='Verifying token...')
        r = requests.get(f"{url}/System/Info", headers=headers, timeout=8, verify=False)

        if r.status_code == 200:
            progress.update(65, message='Fetching user info...')
            user_name = None
            if uid:
                try:
                    r2 = requests.get(
                        f"{url}/Users/{uid}", headers=headers, timeout=5, verify=False
                    )
                    if r2.status_code == 200:
                        user_name = r2.json().get('Name')
                except Exception:
                    pass
            progress.update(100, message='Done.')
            progress.close()
            name_str = f"  —  logged in as: {user_name}" if user_name else ""
            xbmcgui.Dialog().notification(
                f'{label} [OK]  Token Valid',
                f"Connected{name_str}",
                xbmcgui.NOTIFICATION_INFO, 6000
            )
            xbmc.log(f"Source Engine Pro [TOKEN TEST]: {label} OK — user={user_name}", xbmc.LOGINFO)

        elif r.status_code == 401:
            progress.close()
            xbmcgui.Dialog().notification(
                f'{label} [FAIL]  Token Invalid',
                'Auth refused.  Re-enter password in settings and restart Kodi.',
                xbmcgui.NOTIFICATION_ERROR, 6000
            )
            xbmc.log(f"Source Engine Pro [TOKEN TEST]: {label} 401 — token rejected", xbmc.LOGWARNING)

        else:
            progress.close()
            xbmcgui.Dialog().notification(
                f'{label} [FAIL]  Server Error',
                f"HTTP {r.status_code} — check the Server Address setting.",
                xbmcgui.NOTIFICATION_ERROR, 6000
            )
            xbmc.log(f"Source Engine Pro [TOKEN TEST]: {label} HTTP {r.status_code}", xbmc.LOGWARNING)

    except Exception as exc:
        progress.close()
        xbmcgui.Dialog().notification(
            f'{label} [FAIL]  Unreachable',
            _friendly_exc(exc),
            xbmcgui.NOTIFICATION_ERROR, 7000
        )
        xbmc.log(f"Source Engine Pro [TOKEN TEST]: {label} failed — {exc}", xbmc.LOGERROR)


def test_emby_token():
    our   = xbmcaddon.Addon()
    url   = (our.getSetting('emby_url')   or '').rstrip('/').replace(':443', '')
    token = (our.getSetting('emby_token') or '').strip()
    uid   = (our.getSetting('emby_uid')   or '').strip()
    if not url:
        xbmcgui.Dialog().notification('Emby — Token Test', 'No server URL configured.', xbmcgui.NOTIFICATION_WARNING, 5000)
        return
    if not token:
        xbmcgui.Dialog().notification('Emby — Token Test', 'No token stored. Enter credentials and restart Kodi.', xbmcgui.NOTIFICATION_WARNING, 5000)
        return
    _run_token_test('Emby', url, token, uid)


def test_jelly_token():
    our   = xbmcaddon.Addon()
    url   = (our.getSetting('jelly_url')   or '').rstrip('/').replace(':443', '')
    token = (our.getSetting('jelly_token') or '').strip()
    uid   = (our.getSetting('jelly_uid')   or '').strip()
    if not url:
        xbmcgui.Dialog().notification('Jellyfin — Token Test', 'No server URL configured.', xbmcgui.NOTIFICATION_WARNING, 5000)
        return
    if not token:
        xbmcgui.Dialog().notification('Jellyfin — Token Test', 'No token stored. Enter credentials and restart Kodi.', xbmcgui.NOTIFICATION_WARNING, 5000)
        return
    _run_token_test('Jellyfin', url, token, uid)


def _res_shorthand(res):
    r = str(res)
    if '2160' in r or '4K' in r.upper():  return '4K'
    if '1440' in r:                        return '1440p'
    if '1080' in r:                        return '1080p'
    if '720'  in r:                        return '720p'
    if '480'  in r:                        return '480p'
    return r

def show_history():
    handle = int(sys.argv[1])
    entries = _read_history()
    xbmcplugin.setPluginCategory(handle, 'Source Battle History')
    xbmcplugin.setContent(handle, 'files')

    if not entries:
        li = xbmcgui.ListItem(label='[No history yet — play something first]')
        xbmcplugin.addDirectoryItem(handle, '', li, False)
    else:
        # ── Scoreboard tally header ───────────────────────────────────────
        emby_wins  = sum(1 for e in entries if e.get('winner', '') == 'Emby'     and not e.get('is_tie'))
        jelly_wins = sum(1 for e in entries if e.get('winner', '') == 'Jellyfin' and not e.get('is_tie'))
        ties       = sum(1 for e in entries if e.get('is_tie'))
        total      = len(entries)
        tally = (
            f"[COLOR gold]  ★  SCOREBOARD[/COLOR]   "
            f"[COLOR lime]Emby: {emby_wins} wins[/COLOR]   "
            f"[COLOR cyan]Jellyfin: {jelly_wins} wins[/COLOR]   "
            f"[COLOR yellow]Ties: {ties}[/COLOR]   "
            f"[COLOR gray]({total} battles total)[/COLOR]"
        )
        li_tally = xbmcgui.ListItem(label=tally)
        li_tally.setProperty('IsPlayable', 'false')
        xbmcplugin.addDirectoryItem(handle, '', li_tally, False)

        for e in entries:
            media_title = e.get('title', 'Unknown')
            ep_tag = ''
            if e.get('type') == 'episode' and e.get('season') and e.get('episode'):
                ep_tag = f" S{str(e['season']).zfill(2)}E{str(e['episode']).zfill(2)}"

            winner       = e.get('winner', '?')
            loser        = e.get('loser', '')
            win_reason   = e.get('win_reason', '')
            loser_reason = e.get('loser_reason', '')
            ts           = e.get('timestamp', '')
            w_res        = e.get('winner_resolution', '')
            w_score      = e.get('winner_score', '')
            l_score      = e.get('loser_score', '')
            w_codec      = e.get('winner_codec', '')
            w_audio      = e.get('winner_audio', '')
            w_size       = e.get('winner_size_gb', '')

            # Shorten timestamp — drop the year (MM-DD HH:MM)
            ts_short = ts[5:] if len(ts) > 5 else ts

            # Color-coded names
            winner_c = f"[COLOR lime]{winner}[/COLOR]"
            loser_c  = f"[COLOR orange]{loser}[/COLOR]" if loser else ''
            ts_c     = f"[COLOR gray][{ts_short}][/COLOR]"

            # Build result segment
            is_tie    = e.get('is_tie', False)
            is_manual = e.get('is_manual_pick', False)
            if is_tie:
                result = f"[COLOR yellow]TIE →[/COLOR] {winner_c} picked  vs  {loser_c}"
            elif is_manual:
                _ls_disp = l_score if l_score not in (None, '') else '?'
                result = f"[COLOR violet]PICK →[/COLOR] {winner_c} chosen  vs  {loser_c}  ({w_score} vs {_ls_disp} pts)"
            elif loser and l_score is not None:
                result = f"{winner_c} WON ({win_reason}) vs {loser_c} ({loser_reason}, {l_score}pt)"
            elif loser:
                result = f"{winner_c} WON  |  {loser_c}: {loser_reason}"
            else:
                result = f"{winner_c} WON"

            # Everything in label — resolution abbreviated to save space
            label = (
                f"{ts_c}  {media_title}{ep_tag}"
                f"  —  {result}"
                f"  |  {_res_shorthand(w_res)} {w_codec}  {w_audio}  {w_size}GB"
            )

            # Full detail in plot for info panel (skin-dependent bonus)
            plot_lines = [
                f"Date: {ts}",
                f"Title: {media_title}{ep_tag}",
                ("⚖  PERFECT TIE — User Picked" if is_tie else "[ MANUAL PICK — User Chose ]" if is_manual else ""),
                f"Winner: {winner}  ({win_reason})" + (f"  {w_score}pt" if w_score else ""),
                f"Loser:  {loser}  ({loser_reason})" + (f"  {l_score}pt" if l_score is not None else ""),
                f"Quality: {w_res} {w_codec}",
                f"Audio:   {w_audio}",
                f"Size:    {w_size} GB  ({e.get('winner_bitrate_mb', '?')} Mbps)",
            ]
            plot_lines = [x for x in plot_lines if x]  # drop empty lines
            plot = "\n".join(plot_lines)

            li = xbmcgui.ListItem(label=label)
            li.setInfo('video', {'title': label, 'plot': plot})
            li.setProperty('IsPlayable', 'false')
            xbmcplugin.addDirectoryItem(handle, '', li, False)

    li_clear = xbmcgui.ListItem(label='[COLOR red][ Clear All History ][/COLOR]')
    xbmcplugin.addDirectoryItem(handle, f"{sys.argv[0]}?action=clear_history", li_clear, False)
    xbmcplugin.endOfDirectory(handle)

def clear_history():
    path = _history_path()
    if os.path.exists(path):
        os.remove(path)
    xbmcgui.Dialog().notification(
        'Source Engine Pro', 'History cleared!', xbmcgui.NOTIFICATION_INFO, 2000
    )

def show_main_menu():
    handle = int(sys.argv[1])
    addon  = xbmcaddon.Addon()
    icon   = addon.getAddonInfo('icon')
    xbmcplugin.setPluginCategory(handle, 'Source Engine Pro')
    xbmcplugin.setContent(handle, 'files')

    li_history = xbmcgui.ListItem(label='Source Battle History')
    li_history.setArt({'icon': icon})
    xbmcplugin.addDirectoryItem(handle, f"{sys.argv[0]}?action=show_history", li_history, True)

    li_settings = xbmcgui.ListItem(label='Settings')
    li_settings.setArt({'icon': icon})
    xbmcplugin.addDirectoryItem(handle, f"{sys.argv[0]}?action=open_settings", li_settings, False)

    xbmcplugin.endOfDirectory(handle)


def open_settings():
    xbmc.executebuiltin('Addon.OpenSettings(plugin.video.sourceenginepro)')

# ─────────────────────────────────────────────────────────────────────────────

def tmdb_id_from_tvdb(tvdb_id):
    try:
        api_key = _get_tmdb_api_key()
        url = f"https://api.themoviedb.org/3/find/{tvdb_id}?api_key={api_key}&external_source=tvdb_id"
        r = requests.get(url, timeout=10).json()
        results = r.get('tv_results', [])
        if results:
            tmdb_id = results[0].get('id')
            if tmdb_id:
                return str(tmdb_id)
    except:
        pass
    return None

def _tmdb_get(url, label, retries=2):
    """TMDB API call with retry, backoff, and full diagnostic logging."""
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=12)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                wait = float(r.headers.get('Retry-After', 2))
                xbmc.log(
                    f"Source Engine [TMDB RATE-LIMIT]: {label} got 429, "
                    f"retry-after={wait}s (attempt {attempt+1}/{retries})",
                    xbmc.LOGWARNING
                )
                time.sleep(min(wait, 5))
                continue
            else:
                xbmc.log(
                    f"Source Engine [TMDB HTTP {r.status_code}]: {label} "
                    f"(attempt {attempt+1}/{retries}) body={r.text[:200]}",
                    xbmc.LOGWARNING
                )
                if attempt < retries - 1:
                    time.sleep(1)
        except Exception as e:
            xbmc.log(
                f"Source Engine [TMDB EXCEPTION]: {label} "
                f"(attempt {attempt+1}/{retries}) — {e}",
                xbmc.LOGWARNING
            )
            if attempt < retries - 1:
                time.sleep(1)
    return None

def get_tmdb_episode_context(show_tmdb_id, season, episode):
    """
    Comprehensive TMDB context for the confidence funnel.
    Returns a dict with episode IDs, name, runtime, season episode count, and show name.
    Uses retry-capable _tmdb_get for resilience against rate-limits and transient failures.
    """
    api_key = _get_tmdb_api_key()
    base = "https://api.themoviedb.org/3"
    ctx = {
        'ep_tmdb': None, 'ep_tvdb': None, 'ep_imdb': None,
        'ep_name': None, 'ep_runtime_min': None,
        'season_episode_count': None, 'show_name': None
    }

    # Call 1: Direct episode lookup with external_ids appended
    data = _tmdb_get(
        f"{base}/tv/{show_tmdb_id}/season/{season}/episode/{episode}"
        f"?api_key={api_key}&append_to_response=external_ids",
        f"episode S{season}E{episode}"
    )
    if data:
        ctx['ep_tmdb'] = str(data['id']) if data.get('id') else None
        ctx['ep_name'] = data.get('name')
        ctx['ep_runtime_min'] = data.get('runtime')
        ext = data.get('external_ids', {})
        ctx['ep_tvdb'] = str(ext['tvdb_id']) if ext.get('tvdb_id') else None
        ctx['ep_imdb'] = ext.get('imdb_id') if ext.get('imdb_id') else None

    # Call 1b: If append_to_response didn't return external_ids, fetch them separately
    if ctx['ep_tmdb'] and not ctx['ep_tvdb'] and not ctx['ep_imdb']:
        ext_data = _tmdb_get(
            f"{base}/tv/{show_tmdb_id}/season/{season}/episode/{episode}"
            f"/external_ids?api_key={api_key}",
            f"episode S{season}E{episode} external_ids"
        )
        if ext_data:
            if not ctx['ep_tvdb'] and ext_data.get('tvdb_id'):
                ctx['ep_tvdb'] = str(ext_data['tvdb_id'])
            if not ctx['ep_imdb'] and ext_data.get('imdb_id'):
                ctx['ep_imdb'] = ext_data['imdb_id']

    # Call 2: Season listing (episode count + fallback episode data)
    season_data = _tmdb_get(
        f"{base}/tv/{show_tmdb_id}/season/{season}?api_key={api_key}",
        f"season {season} listing"
    )
    if season_data:
        episodes_list = season_data.get('episodes', [])
        ctx['season_episode_count'] = len(episodes_list)

        if not ctx['ep_tmdb'] and not ctx['ep_name']:
            for ep_data in episodes_list:
                if ep_data.get('episode_number') == int(episode):
                    ctx['ep_tmdb'] = str(ep_data['id']) if ep_data.get('id') else None
                    ctx['ep_name'] = ep_data.get('name')
                    ctx['ep_runtime_min'] = ep_data.get('runtime')
                    xbmc.log(
                        f"Source Engine [TMDB FALLBACK]: Found '{ctx['ep_name']}' "
                        f"via season listing",
                        xbmc.LOGWARNING
                    )
                    break

    # Call 3: Show details (name for cross-reference + alternative titles)
    show_data = _tmdb_get(
        f"{base}/tv/{show_tmdb_id}?api_key={api_key}",
        f"show {show_tmdb_id} details"
    )
    if show_data:
        ctx['show_name'] = show_data.get('name')

    xbmc.log(
        f"Source Engine [TMDB CONTEXT]: Show {show_tmdb_id} S{season}E{episode} => "
        f"ep_tmdb={ctx['ep_tmdb']} ep_tvdb={ctx['ep_tvdb']} ep_imdb={ctx['ep_imdb']} "
        f"name='{ctx['ep_name']}' runtime={ctx['ep_runtime_min']}min "
        f"season_eps={ctx['season_episode_count']} show='{ctx['show_name']}'",
        xbmc.LOGWARNING
    )
    return ctx

def get_best_source(tmdb_id, imdb_id, tvdb_id, media_type, query, target_year=None, season=None, episode=None):
    addon = xbmcaddon.Addon()
    results = []
    failed = []
    data_lock = threading.Lock()

    master_preset = get_int(addon, 'master_preset', 0)
    tie_breaker = get_int(addon, 'tie_breaker', 0)
    max_res_index = get_int(addon, 'max_resolution', 0)
    deep_dive = addon.getSetting('deep_dive') == 'true'

    max_res = [float('inf'), 1080, 720][max_res_index] if 0 <= max_res_index <= 2 else float('inf')

    try:
        max_size = float(addon.getSetting('max_size_gb').replace(',', '.') or 0)
    except:
        max_size = 0.0

    preferred_server = None if tie_breaker == 0 else ('Emby' if tie_breaker == 1 else 'Jellyfin')

    def _active_server_config(prefix, name):
        """Return the correct server config (primary or backup) based on the failover flag."""
        on_backup = addon.getSetting(f'{prefix}_on_backup') == 'true'
        if on_backup:
            return {
                "name": name,
                "url": addon.getSetting(f'{prefix}2_url').rstrip('/').replace(':443', ''),
                "token": addon.getSetting(f'{prefix}2_token'),
                "uid": addon.getSetting(f'{prefix}2_uid'),
            }
        return {
            "name": name,
            "url": addon.getSetting(f'{prefix}_url').rstrip('/').replace(':443', ''),
            "token": addon.getSetting(f'{prefix}_token'),
            "uid": addon.getSetting(f'{prefix}_uid'),
        }

    configs = [
        _active_server_config('emby', 'Emby'),
        _active_server_config('jelly', 'Jellyfin'),
    ]

    v_tmdb = str(tmdb_id) if tmdb_id and not str(tmdb_id).startswith('{') else None
    v_imdb = str(imdb_id) if imdb_id and not str(imdb_id).startswith('{') else None
    v_tvdb = str(tvdb_id) if tvdb_id and not str(tvdb_id).startswith('{') else None

    # =====================================================================
    # TMDB CONTEXT: Comprehensive episode metadata for the confidence funnel
    # =====================================================================
    tmdb_ctx = {}
    v_ep_tmdb = v_ep_tvdb = v_ep_imdb = v_ep_name = None
    v_ep_runtime = None
    v_season_ep_count = None
    v_show_name = None

    if media_type == 'episode' and season and episode:
        resolved_tmdb = v_tmdb or (tmdb_id_from_tvdb(v_tvdb) if v_tvdb else None)
        xbmc.log(
            f"Source Engine [INIT]: type={media_type} query='{query}' S{season}E{episode} | "
            f"tmdb={v_tmdb} tvdb={v_tvdb} imdb={v_imdb} resolved_tmdb={resolved_tmdb}",
            xbmc.LOGWARNING
        )
        if resolved_tmdb:
            tmdb_ctx = get_tmdb_episode_context(resolved_tmdb, season, episode)
            v_ep_tmdb = tmdb_ctx.get('ep_tmdb')
            v_ep_tvdb = tmdb_ctx.get('ep_tvdb')
            v_ep_imdb = tmdb_ctx.get('ep_imdb')
            v_ep_name = tmdb_ctx.get('ep_name')
            v_ep_runtime = tmdb_ctx.get('ep_runtime_min')
            v_season_ep_count = tmdb_ctx.get('season_episode_count')
            v_show_name = tmdb_ctx.get('show_name')

    elif media_type == 'movie':
        xbmc.log(
            f"Source Engine [INIT]: type={media_type} query='{query}' | "
            f"tmdb={v_tmdb} tvdb={v_tvdb} imdb={v_imdb} year={target_year}",
            xbmc.LOGWARNING
        )

    safe_query = urllib.parse.quote(query) if query and not query.startswith('{') else ""

    if not query and not v_tmdb and not v_tvdb and not v_imdb:
        return None, [], []

    def search_server(s):
        if not s['token'] or not s['url']:
            xbmc.log(
                f"Source Engine [SKIP]: {s['name']} — URL or token not configured in addon settings. "
                f"(url={'set' if s['url'] else 'EMPTY'}, token={'set' if s['token'] else 'EMPTY'})",
                xbmc.LOGWARNING
            )
            with data_lock: failed.append(s['name'])
            return

        session = requests.Session()
        session.verify = False
        session.headers.update({
            'X-Emby-Token': s['token'],
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json'
        })

        s_uid = (s.get('uid') or '').strip() or None
        items_base = f"{s['url']}/Users/{s_uid}/Items" if s_uid else f"{s['url']}/Items"
        user_q = f"&userId={s_uid}" if s_uid else ""

        try:
            fields = "MediaSources,RunTimeTicks,MediaStreams,ProviderIds,Path,ProductionYear,UserData,SeriesName"
            items_to_process = []

            p_tmdb = "Tmdb"
            p_tvdb = "Tvdb"
            p_imdb = "Imdb"

            if media_type == 'episode':
                if not season or not episode:
                    with data_lock: failed.append(s['name'])
                    return

                xbmc.log(f"Source Engine [EPISODE]: {s['name']} searching for '{query}' S{season}E{episode}", xbmc.LOGWARNING)
                xbmc.log(
                    f"Source Engine [EPISODE IDS]: ep_tmdb={v_ep_tmdb} ep_tvdb={v_ep_tvdb} "
                    f"ep_imdb={v_ep_imdb} ep_name='{v_ep_name}' "
                    f"ep_runtime={v_ep_runtime}min season_eps={v_season_ep_count}",
                    xbmc.LOGWARNING
                )

                # =============================================================
                # STAGE 1: WIDE NET SERIES COLLECTION
                # Run ALL search strategies simultaneously, tag each with source.
                # We let everything in; the confidence funnel does the filtering.
                # =============================================================
                series_candidates = {}
                provider_verified_sids = set()

                # Strategy A: Provider ID lookup (all provider types)
                _dd_series_limit = 200 if deep_dive else 50
                for id_type, id_val in [(p_tmdb, v_tmdb), (p_tvdb, v_tvdb), (p_imdb, v_imdb)]:
                    if not id_val:
                        continue
                    try:
                        r = session.get(
                            f"{items_base}?Recursive=True&IncludeItemTypes=Series"
                            f"&AnyProviderIdEquals={id_type}.{id_val}"
                            f"&Fields=ProviderIds,ProductionYear&Limit={_dd_series_limit}{user_q}",
                            timeout=15
                        ).json()
                        api_items = r.get('Items', [])
                        if len(api_items) > 20:
                            xbmc.log(
                                f"Source Engine [API BUG]: {s['name']} returned {len(api_items)} series "
                                f"for {id_type}={id_val}. Verifying each item.",
                                xbmc.LOGWARNING
                            )
                        for item in api_items:
                            pids = {k.lower(): str(v) for k, v in item.get('ProviderIds', {}).items() if v}
                            if pids.get(id_type.lower()) == str(id_val):
                                sid = str(item.get('Id', ''))
                                if not sid:
                                    continue
                                series_candidates[sid] = {
                                    'Id': sid,
                                    'Name': item.get('Name', ''),
                                    'ProductionYear': item.get('ProductionYear'),
                                    'verified': True,
                                    'reason': f"provider:{id_type}={id_val}",
                                }
                                provider_verified_sids.add(sid)
                                xbmc.log(
                                    f"Source Engine [SERIES A]: {s['name']} '{item.get('Name')}' "
                                    f"verified by {id_type}={id_val}",
                                    xbmc.LOGWARNING
                                )
                    except:
                        pass

                # Strategy B: Name search (ALWAYS runs — wide net, not just fallback)
                _dd_sim_floor = 0.3 if deep_dive else 0.5
                if safe_query:
                    try:
                        r = session.get(
                            f"{items_base}?Recursive=True&IncludeItemTypes=Series"
                            f"&SearchTerm={safe_query}"
                            f"&Fields=ProviderIds,ProductionYear&Limit={_dd_series_limit}{user_q}",
                            timeout=15
                        ).json()
                        for item in r.get('Items', []):
                            name = item.get('Name', '')
                            sim = _title_similarity(query, name)
                            if sim < _dd_sim_floor:
                                continue

                            sid = str(item.get('Id', ''))
                            if not sid or sid in series_candidates:
                                continue

                            pids = {k.lower(): str(v) for k, v in item.get('ProviderIds', {}).items() if v}
                            conflict = False
                            if v_tmdb and pids.get('tmdb') and pids['tmdb'] != str(v_tmdb):
                                conflict = True
                            if v_tvdb and pids.get('tvdb') and pids['tvdb'] != str(v_tvdb):
                                conflict = True
                            if v_imdb and pids.get('imdb') and pids['imdb'] != str(v_imdb):
                                conflict = True

                            if conflict:
                                xbmc.log(
                                    f"Source Engine [SERIES B CONFLICT]: {s['name']} '{name}' "
                                    f"rejected — provider ID mismatch (server: {pids})",
                                    xbmc.LOGWARNING
                                )
                                continue

                            # PROMOTION CHECK: If this name-matched series actually has
                            # matching provider IDs, promote it to verified. This is critical
                            # for Jellyfin where AnyProviderIdEquals is broken but name search
                            # still returns the correct series WITH valid ProviderIds.
                            is_verified = False
                            if v_tmdb and pids.get('tmdb') == str(v_tmdb):
                                is_verified = True
                            elif v_tvdb and pids.get('tvdb') == str(v_tvdb):
                                is_verified = True
                            elif v_imdb and pids.get('imdb') == str(v_imdb):
                                is_verified = True

                            series_candidates[sid] = {
                                'Id': sid,
                                'Name': name,
                                'ProductionYear': item.get('ProductionYear'),
                                'verified': is_verified,
                                'reason': f"name_sim={sim:.2f}" + ("+pid_confirmed" if is_verified else ""),
                            }
                            if is_verified:
                                provider_verified_sids.add(sid)
                                xbmc.log(
                                    f"Source Engine [SERIES B PROMOTED]: {s['name']} '{name}' "
                                    f"sim={sim:.2f} — name matched AND provider IDs confirmed!",
                                    xbmc.LOGWARNING
                                )
                            else:
                                xbmc.log(
                                    f"Source Engine [SERIES B]: {s['name']} '{name}' "
                                    f"sim={sim:.2f} added by name (no PID match, pids={pids})",
                                    xbmc.LOGWARNING
                                )
                    except:
                        pass

                # Strategy C: TMDB show name (if different from query — catches localized titles)
                if v_show_name and v_show_name != query:
                    safe_show = urllib.parse.quote(v_show_name)
                    _dd_c_limit = 100 if deep_dive else 20
                    try:
                        r = session.get(
                            f"{items_base}?Recursive=True&IncludeItemTypes=Series"
                            f"&SearchTerm={safe_show}"
                            f"&Fields=ProviderIds,ProductionYear&Limit={_dd_c_limit}{user_q}",
                            timeout=15
                        ).json()
                        for item in r.get('Items', []):
                            sid = str(item.get('Id', ''))
                            if not sid or sid in series_candidates:
                                continue
                            name = item.get('Name', '')
                            sim = max(_title_similarity(query, name), _title_similarity(v_show_name, name))
                            if sim < _dd_sim_floor:
                                continue
                            pids = {k.lower(): str(v) for k, v in item.get('ProviderIds', {}).items() if v}
                            conflict = False
                            if v_tmdb and pids.get('tmdb') and pids['tmdb'] != str(v_tmdb):
                                conflict = True
                            if v_tvdb and pids.get('tvdb') and pids['tvdb'] != str(v_tvdb):
                                conflict = True
                            if v_imdb and pids.get('imdb') and pids['imdb'] != str(v_imdb):
                                conflict = True
                            if conflict:
                                continue
                            is_verified = False
                            if v_tmdb and pids.get('tmdb') == str(v_tmdb):
                                is_verified = True
                            elif v_tvdb and pids.get('tvdb') == str(v_tvdb):
                                is_verified = True
                            elif v_imdb and pids.get('imdb') == str(v_imdb):
                                is_verified = True
                            series_candidates[sid] = {
                                'Id': sid,
                                'Name': name,
                                'ProductionYear': item.get('ProductionYear'),
                                'verified': is_verified,
                                'reason': f"tmdb_name_sim={sim:.2f}" + ("+pid" if is_verified else ""),
                            }
                            if is_verified:
                                provider_verified_sids.add(sid)
                            xbmc.log(
                                f"Source Engine [SERIES C]: {s['name']} '{name}' sim={sim:.2f} "
                                f"via TMDB name '{v_show_name}'"
                                f"{' [VERIFIED]' if is_verified else ''}",
                                xbmc.LOGWARNING
                            )
                    except:
                        pass

                xbmc.log(
                    f"Source Engine [SERIES TOTAL]: {s['name']} collected {len(series_candidates)} "
                    f"candidate(s) ({len(provider_verified_sids)} provider-verified)",
                    xbmc.LOGWARNING
                )

                if not series_candidates:
                    xbmc.log(
                        f"Source Engine [NO SERIES]: {s['name']} found 0 candidates for '{query}'",
                        xbmc.LOGWARNING
                    )

                # =============================================================
                # STAGE 2: WIDE NET EPISODE DOWNLOAD
                # Download episodes from ALL candidates + direct episode search.
                # Tag each episode with its source series for funnel scoring.
                # =============================================================
                all_episodes = []
                series_season_data = {}

                _dd_top = 15 if deep_dive else 5
                sorted_candidates = sorted(
                    series_candidates.values(),
                    key=lambda c: (0 if c['verified'] else 1, -_title_similarity(query, c.get('Name', '')))
                )[:_dd_top]

                for cand in sorted_candidates:
                    sid = cand['Id']
                    try:
                        season_id = None
                        server_season_ep_count = None
                        has_season = None

                        if s_uid:
                            seasons_url = (
                                f"{s['url']}/Shows/{sid}/Seasons?userId={s_uid}"
                                f"&Fields=ItemCounts&Limit=200"
                            )
                            seasons_resp = session.get(seasons_url, timeout=20).json()
                            seasons_list = seasons_resp.get('Items', [])
                            has_season = False
                            for sea in seasons_list:
                                if str(sea.get('IndexNumber')) == str(season):
                                    season_id = sea.get('Id')
                                    server_season_ep_count = sea.get('ChildCount')
                                    has_season = True
                                    break

                            series_season_data[sid] = {
                                'has_season': has_season,
                                'season_ep_count': server_season_ep_count,
                                'total_seasons': len(seasons_list),
                            }

                            if not has_season:
                                xbmc.log(
                                    f"Source Engine [SEASON MISS]: {s['name']} '{cand['Name']}' "
                                    f"does NOT have season {season} "
                                    f"(has {len(seasons_list)} seasons: "
                                    f"{[sea.get('IndexNumber') for sea in seasons_list]})",
                                    xbmc.LOGWARNING
                                )

                        if s_uid and season_id:
                            eps_url = (
                                f"{s['url']}/Shows/{sid}/Episodes?userId={s_uid}"
                                f"&seasonId={season_id}&Fields={fields}"
                                f"&IsMissing=false&IsVirtualUnaired=false&Limit=2000"
                            )
                        elif s_uid:
                            eps_url = (
                                f"{s['url']}/Shows/{sid}/Episodes?userId={s_uid}"
                                f"&Fields={fields}"
                                f"&IsMissing=false&IsVirtualUnaired=false&Limit=2000"
                            )
                        else:
                            eps_url = (
                                f"{s['url']}/Shows/{sid}/Episodes?Fields={fields}"
                                f"&IsMissing=false&IsVirtualUnaired=false&Limit=2000"
                            )

                        r_eps = session.get(eps_url, timeout=30).json()
                        eps = r_eps.get('Items', []) or []
                        for ep in eps:
                            ep['_src_sid'] = sid
                            ep['_src_verified'] = cand['verified']
                        all_episodes.extend(eps)
                        xbmc.log(
                            f"Source Engine [EPISODES]: {s['name']} loaded {len(eps)} "
                            f"from '{cand['Name']}' ({cand['reason']})",
                            xbmc.LOGWARNING
                        )
                    except Exception as e:
                        xbmc.log(
                            f"Source Engine [EPISODE DL ERROR]: {s['name']} "
                            f"'{cand['Name']}' — {e}",
                            xbmc.LOGWARNING
                        )

                # Fallback: Items API if Shows API returned nothing
                if not all_episodes and sorted_candidates:
                    xbmc.log(
                        f"Source Engine [FALLBACK]: {s['name']} Shows API returned 0 episodes, "
                        f"trying Items API with SeriesId...",
                        xbmc.LOGWARNING
                    )
                    for cand in sorted_candidates:
                        sid = cand['Id']
                        try:
                            fb_url = (
                                f"{items_base}?Recursive=True&IncludeItemTypes=Episode"
                                f"&SeriesId={sid}&Fields={fields}"
                                f"&EnableTotalRecordCount=false&Limit=2000{user_q}"
                            )
                            r_fb = session.get(fb_url, timeout=30).json()
                            eps = r_fb.get('Items', []) or []
                            for ep in eps:
                                ep['_src_sid'] = sid
                                ep['_src_verified'] = cand['verified']
                            all_episodes.extend(eps)
                            xbmc.log(
                                f"Source Engine [FALLBACK]: {s['name']} loaded {len(eps)} "
                                f"via Items for '{cand['Name']}'",
                                xbmc.LOGWARNING
                            )
                        except:
                            pass

                # BONUS: Direct episode search (catches orphan episodes, misindexed
                # shows, or cases where series search failed entirely).
                # Runs ALWAYS — cheap API calls that massively improve recall.
                existing_ep_ids = {ep.get('Id') for ep in all_episodes}
                bonus_count = 0

                def _ingest_direct(items, label):
                    nonlocal bonus_count
                    for ep in items:
                        eid = ep.get('Id')
                        if not eid or eid in existing_ep_ids:
                            continue
                        ep['_src_sid'] = str(ep.get('SeriesId', ''))
                        ep['_src_verified'] = ep['_src_sid'] in provider_verified_sids
                        ep['_direct_search'] = True
                        all_episodes.append(ep)
                        existing_ep_ids.add(eid)
                        bonus_count += 1

                # Direct search 1: By episode name (strongest — unique episode titles)
                _dd_d1_lim = 200 if deep_dive else 50
                _dd_d2_lim = 500 if deep_dive else 100
                if v_ep_name:
                    try:
                        safe_ep_name = urllib.parse.quote(v_ep_name)
                        r_d1 = session.get(
                            f"{items_base}?Recursive=True&IncludeItemTypes=Episode"
                            f"&SearchTerm={safe_ep_name}&Fields={fields}&Limit={_dd_d1_lim}{user_q}",
                            timeout=20
                        ).json()
                        _ingest_direct(r_d1.get('Items', []), "ep_name")
                    except:
                        pass

                # Direct search 2: By show name (catches episodes when series lookup
                # failed but the show IS on the server — capped to prevent flood)
                if safe_query:
                    try:
                        r_d2 = session.get(
                            f"{items_base}?Recursive=True&IncludeItemTypes=Episode"
                            f"&SearchTerm={safe_query}&Fields={fields}&Limit={_dd_d2_lim}{user_q}",
                            timeout=25
                        ).json()
                        _ingest_direct(r_d2.get('Items', []), "show_name")
                    except:
                        pass

                if bonus_count:
                    xbmc.log(
                        f"Source Engine [DIRECT SEARCH]: {s['name']} added {bonus_count} "
                        f"episode(s) via direct search",
                        xbmc.LOGWARNING
                    )

                # Deduplicate episodes by ID before funnel processing.
                # Same episode can appear from multiple series candidates or search paths.
                raw_count = len(all_episodes)
                if raw_count > 0:
                    seen_dedup = {}
                    for ep in all_episodes:
                        eid = ep.get('Id')
                        if not eid:
                            continue
                        existing = seen_dedup.get(eid)
                        if not existing:
                            seen_dedup[eid] = ep
                        elif ep.get('_src_verified') and not existing.get('_src_verified'):
                            seen_dedup[eid] = ep
                    all_episodes = list(seen_dedup.values())

                # =============================================================
                # STAGE 3: THE CONFIDENCE FUNNEL
                # Every candidate episode is scored against ALL signals
                # simultaneously. Hard vetoes eliminate bad matches; additive
                # signals build confidence. Only episodes above the threshold
                # survive.
                #
                # SIGNALS:
                #   S1  Episode Provider ID match       +40 (first), +5 (additional)
                #   S2  Series was provider-verified     +20
                #   S3  Episode name exact match         +30
                #   S3b Episode name fuzzy (>=0.8)       +20
                #   S3c Episode name partial (>=0.6)     +10
                #   S3d Episode name substring contain   +15
                #   S4  S/E number match                 +15
                #   S5  Series title similarity           +0–10 (scaled)
                #   S6  Season exists on server           +5
                #   S7  Episode count sanity              +5 / -10
                #   S8  Runtime comparison (TMDB)         +5 / +2 / -5
                #   S9  File path SxxExx match            +10
                #   S10 Production year proximity         +5 / +3 / -10
                #   S11 TMDB show name cross-check        +8
                #   S12 Multi-signal consensus bonus      +5 (3+ independent categories)
                #   S13 File path contains show name      +5
                #
                # HARD VETOES:
                #   V1  Episode provider ID conflict     -> skip
                #   V2  Series provider ID conflict      -> skip (already filtered in Stage 1)
                #
                # THRESHOLD: 20 (deep dive) / 40 (normal)
                # =============================================================
                MIN_CONFIDENCE = 20 if deep_dive else 40

                xbmc.log(
                    f"Source Engine [FUNNEL INTAKE{'—DEEP DIVE' if deep_dive else ''}]: "
                    f"{s['name']} collected {raw_count} raw "
                    f"→ {len(all_episodes)} unique episode(s) from "
                    f"{len(sorted_candidates)} series + {bonus_count} direct search "
                    f"(min_conf={MIN_CONFIDENCE})",
                    xbmc.LOGWARNING
                )
                scored_episodes = []
                veto_count = 0
                near_count = 0
                below_count = 0

                for ep in all_episodes:
                    confidence = 0.0
                    reasons = []

                    ep_pids = {k.lower(): str(v) for k, v in ep.get('ProviderIds', {}).items() if v}
                    ep_parent_season = ep.get('ParentIndexNumber')
                    ep_episode_num = ep.get('IndexNumber')

                    # --- HARD VETOES ---
                    vetoed = False
                    if v_ep_tmdb and ep_pids.get('tmdb') and ep_pids['tmdb'] != v_ep_tmdb:
                        vetoed = True
                    if v_ep_tvdb and ep_pids.get('tvdb') and ep_pids['tvdb'] != v_ep_tvdb:
                        vetoed = True
                    if v_ep_imdb and ep_pids.get('imdb') and ep_pids['imdb'] != v_ep_imdb:
                        vetoed = True
                    if vetoed:
                        veto_count += 1
                        continue

                    # --- S1: Episode Provider ID match (strongest signal) ---
                    pid_hits = 0
                    if v_ep_tmdb and ep_pids.get('tmdb') == v_ep_tmdb:
                        pid_hits += 1
                        confidence += 40
                        reasons.append(f"TMDB={v_ep_tmdb}")
                    if v_ep_tvdb and ep_pids.get('tvdb') == v_ep_tvdb:
                        pid_hits += 1
                        confidence += (40 if pid_hits == 1 else 5)
                        reasons.append(f"TVDB={v_ep_tvdb}")
                    if v_ep_imdb and ep_pids.get('imdb') == v_ep_imdb:
                        pid_hits += 1
                        confidence += (40 if pid_hits == 1 else 5)
                        reasons.append(f"IMDB={v_ep_imdb}")

                    # --- S2: Series was provider-verified ---
                    src_sid = str(ep.get('_src_sid', ''))
                    if src_sid in provider_verified_sids:
                        confidence += 20
                        reasons.append("series_verified")

                    # --- S3: Episode name match (multi-strategy) ---
                    name_matched = False
                    if v_ep_name:
                        c_target = "".join(c for c in v_ep_name.lower() if c.isalnum())
                        ep_name_raw = ep.get('Name', '')
                        c_name = "".join(c for c in ep_name_raw.lower() if c.isalnum())
                        if c_target and c_name:
                            if c_target == c_name:
                                confidence += 30
                                reasons.append("name_exact")
                                name_matched = True
                            else:
                                name_sim = _title_similarity(v_ep_name, ep_name_raw)
                                if name_sim >= 0.8:
                                    confidence += 20
                                    reasons.append(f"name_fuzzy={name_sim:.2f}")
                                    name_matched = True
                                elif name_sim >= 0.6:
                                    confidence += 10
                                    reasons.append(f"name_partial={name_sim:.2f}")
                                    name_matched = True
                                elif (len(c_target) >= 6
                                      and (c_target in c_name or c_name in c_target)):
                                    confidence += 15
                                    reasons.append("name_substring")
                                    name_matched = True

                    # --- S4: S/E number match ---
                    if (str(ep_parent_season) == str(season)
                            and str(ep_episode_num) == str(episode)):
                        confidence += 15
                        reasons.append(f"S{season}E{episode}")

                    # --- S5: Series title similarity ---
                    ep_series_name = ep.get('SeriesName', '')
                    if ep_series_name and query:
                        series_sim = _title_similarity(query, ep_series_name)
                        bonus = round(series_sim * 10, 1)
                        if bonus > 0:
                            confidence += bonus
                            if series_sim >= 0.7:
                                reasons.append(f"series={series_sim:.2f}")

                    # --- S6: Season exists on server ---
                    if src_sid and src_sid in series_season_data:
                        sdata = series_season_data[src_sid]
                        if sdata.get('has_season'):
                            confidence += 5
                            reasons.append("season_ok")

                            # --- S7: Episode count sanity ---
                            if v_season_ep_count and sdata.get('season_ep_count'):
                                try:
                                    srv_count = int(sdata['season_ep_count'])
                                    tmdb_count = int(v_season_ep_count)
                                    if srv_count > 0 and tmdb_count > 0:
                                        ratio = (max(srv_count, tmdb_count)
                                                 / max(min(srv_count, tmdb_count), 1))
                                        if ratio <= 1.5:
                                            confidence += 5
                                            reasons.append(
                                                f"ep_count_ok({srv_count}/{tmdb_count})"
                                            )
                                        elif ratio > 3:
                                            confidence -= 10
                                            reasons.append(
                                                f"ep_count_BAD({srv_count}/{tmdb_count})"
                                            )
                                except:
                                    pass
                        elif sdata.get('has_season') is False:
                            confidence -= 15
                            reasons.append("season_MISSING")

                    # --- S8: Runtime comparison ---
                    if v_ep_runtime:
                        ep_ticks = ep.get('RunTimeTicks') or 0
                        if ep_ticks > 0:
                            srv_min = (ep_ticks / 10000000.0) / 60.0
                            diff = abs(srv_min - float(v_ep_runtime))
                            if diff <= 3:
                                confidence += 5
                                reasons.append(
                                    f"runtime_close({srv_min:.0f}~{v_ep_runtime}m)"
                                )
                            elif diff <= 8:
                                confidence += 2
                                reasons.append(
                                    f"runtime_ok({srv_min:.0f}~{v_ep_runtime}m)"
                                )
                            elif diff > 20:
                                confidence -= 5
                                reasons.append(
                                    f"runtime_BAD({srv_min:.0f}!={v_ep_runtime}m)"
                                )

                    # --- S9: File path SxxExx match ---
                    path = ep.get('Path', '')
                    if path:
                        se_match = _SE_RE.search(path)
                        if se_match:
                            p_s, p_e = se_match.group(1), se_match.group(2)
                            if (str(int(p_s)) == str(season)
                                    and str(int(p_e)) == str(episode)):
                                confidence += 10
                                reasons.append("path_SxE")

                    # --- S10: Production year proximity ---
                    if target_year and str(target_year).isdigit():
                        py = None
                        if src_sid in series_candidates:
                            py = series_candidates[src_sid].get('ProductionYear')
                        if py:
                            try:
                                yd = abs(int(target_year) - int(py))
                                if yd == 0:
                                    confidence += 5
                                    reasons.append("year_exact")
                                elif yd <= 1:
                                    confidence += 3
                                    reasons.append("year_close")
                                elif yd > 5:
                                    confidence -= 10
                                    reasons.append(f"year_BAD({py}!={target_year})")
                            except:
                                pass

                    # --- S11: TMDB show name cross-check ---
                    if v_show_name and ep_series_name:
                        show_sim = _title_similarity(v_show_name, ep_series_name)
                        if show_sim >= 0.9:
                            confidence += 8
                            reasons.append(f"tmdb_show={show_sim:.2f}")
                        elif show_sim >= 0.7:
                            confidence += 4
                            reasons.append(f"tmdb_show_partial={show_sim:.2f}")

                    # --- S12: Multi-signal consensus bonus ---
                    # Independent signal categories: provider_id, series_verified,
                    # name_match, se_number, path_match, runtime, season_data
                    signal_categories = 0
                    if pid_hits > 0:
                        signal_categories += 1
                    if src_sid in provider_verified_sids:
                        signal_categories += 1
                    if v_ep_name and name_matched:
                        signal_categories += 1
                    if (str(ep_parent_season) == str(season)
                            and str(ep_episode_num) == str(episode)):
                        signal_categories += 1
                    if 'path_SxE' in reasons:
                        signal_categories += 1
                    if any(r.startswith('runtime_close') for r in reasons):
                        signal_categories += 1
                    if signal_categories >= 3:
                        confidence += 5
                        reasons.append(f"consensus({signal_categories})")

                    # --- S13: File path contains show name ---
                    if path and query:
                        path_lower = path.lower()
                        query_clean = "".join(
                            c for c in query.lower() if c.isalnum() or c == ' '
                        ).strip()
                        if query_clean and query_clean in path_lower:
                            confidence += 5
                            reasons.append("path_title")

                    # --- S_PENALTY: Non-target episode ---
                    # If this episode doesn't match the requested S/E AND has no
                    # episode-specific signals (provider ID or name match), it's just
                    # a neighbor riding the same series/season base signals.
                    is_target_se = (
                        str(ep_parent_season) == str(season)
                        and str(ep_episode_num) == str(episode)
                    )
                    if not is_target_se and pid_hits == 0 and not name_matched:
                        confidence -= 10
                        reasons.append("wrong_ep")

                    # --- THRESHOLD GATE ---
                    if confidence >= MIN_CONFIDENCE:
                        ep['match_confidence'] = confidence
                        ep['match_reasons'] = reasons
                        scored_episodes.append(ep)
                        xbmc.log(
                            f"Source Engine [FUNNEL PASS]: {s['name']} "
                            f"'{ep.get('SeriesName', '')} - {ep.get('Name', '')}' "
                            f"S{ep_parent_season}E{ep_episode_num} "
                            f"CONFIDENCE={confidence:.0f} "
                            f"[{', '.join(reasons)}]",
                            xbmc.LOGWARNING
                        )
                    elif confidence > 20:
                        near_count += 1
                        xbmc.log(
                            f"Source Engine [FUNNEL NEAR]: {s['name']} "
                            f"'{ep.get('SeriesName', '')} - {ep.get('Name', '')}' "
                            f"S{ep_parent_season}E{ep_episode_num} "
                            f"conf={confidence:.0f} (below {MIN_CONFIDENCE}) "
                            f"[{', '.join(reasons)}]",
                            xbmc.LOGWARNING
                        )
                    else:
                        below_count += 1

                # Deduplicate: keep highest confidence per item ID
                if scored_episodes:
                    seen_ids = {}
                    for ep in scored_episodes:
                        eid = ep.get('Id')
                        if (eid not in seen_ids
                                or ep.get('match_confidence', 0)
                                > seen_ids[eid].get('match_confidence', 0)):
                            seen_ids[eid] = ep
                    items_to_process = list(seen_ids.values())

                    for ep in items_to_process:
                        conf = ep.get('match_confidence', MIN_CONFIDENCE)
                        if conf >= 60:
                            ep['match_quality'] = 1.0
                        else:
                            ep['match_quality'] = 0.85

                    xbmc.log(
                        f"Source Engine [FUNNEL RESULT]: {s['name']} "
                        f"{len(items_to_process)} episode(s) passed confidence threshold "
                        f"(best={max(e.get('match_confidence', 0) for e in items_to_process):.0f})",
                        xbmc.LOGWARNING
                    )
                    xbmc.log(
                        f"Source Engine [FUNNEL STATS]: {s['name']} "
                        f"raw_eps={len(all_episodes)} vetoed={veto_count} "
                        f"passed={len(items_to_process)} near={near_count} "
                        f"below_threshold={below_count}",
                        xbmc.LOGWARNING
                    )
                else:
                    xbmc.log(
                        f"Source Engine [FUNNEL EMPTY]: {s['name']} "
                        f"no episodes passed confidence threshold of {MIN_CONFIDENCE}",
                        xbmc.LOGWARNING
                    )
                    xbmc.log(
                        f"Source Engine [FUNNEL STATS]: {s['name']} "
                        f"raw_eps={len(all_episodes)} vetoed={veto_count} "
                        f"passed=0 near={near_count} "
                        f"below_threshold={below_count}",
                        xbmc.LOGWARNING
                    )

            else:  # Movies
                _dd_m_limit = 200 if deep_dive else 50
                _dd_m_bug   = 50  if deep_dive else 10

                # SURGICAL MOVIE STRIKE 1: Exact TMDB with post-query verification
                if v_tmdb:
                    try:
                        r = session.get(
                            f"{items_base}?Recursive=True&IncludeItemTypes=Movie"
                            f"&AnyProviderIdEquals={p_tmdb}.{v_tmdb}"
                            f"&Fields={fields}&Limit={_dd_m_limit}{user_q}",
                            timeout=15
                        ).json()
                        api_items = r.get('Items', [])
                        if len(api_items) > _dd_m_bug:
                            xbmc.log(
                                f"Source Engine [API BUG]: {s['name']} returned "
                                f"{len(api_items)} movies for TMDB={v_tmdb}. "
                                f"Filter broken, skipping to IMDB/name.",
                                xbmc.LOGWARNING
                            )
                        else:
                            for m in api_items:
                                m_pids = m.get('ProviderIds', {})
                                pids_lower = {k.lower(): str(v) for k, v in m_pids.items() if v}
                                if pids_lower.get('tmdb') == str(v_tmdb):
                                    m['match_quality'] = 1.0
                                    items_to_process.append(m)
                                    xbmc.log(
                                        f"Source Engine [MOVIE FOUND]: {s['name']} "
                                        f"'{m.get('Name')}' ({m.get('ProductionYear')}) "
                                        f"verified by TMDB={v_tmdb}",
                                        xbmc.LOGWARNING
                                    )
                    except:
                        pass

                # SURGICAL MOVIE STRIKE 2: Exact IMDB with post-query verification
                # Deep dive: always runs even if Strike 1 found something
                if v_imdb and (deep_dive or not items_to_process):
                    try:
                        r = session.get(
                            f"{items_base}?Recursive=True&IncludeItemTypes=Movie"
                            f"&AnyProviderIdEquals={p_imdb}.{v_imdb}"
                            f"&Fields={fields}&Limit={_dd_m_limit}{user_q}",
                            timeout=15
                        ).json()
                        api_items = r.get('Items', [])
                        if len(api_items) > _dd_m_bug:
                            xbmc.log(
                                f"Source Engine [API BUG]: {s['name']} returned "
                                f"{len(api_items)} movies for IMDB={v_imdb}. "
                                f"Filter broken, skipping to name.",
                                xbmc.LOGWARNING
                            )
                        else:
                            for m in api_items:
                                m_pids = m.get('ProviderIds', {})
                                pids_lower = {k.lower(): str(v) for k, v in m_pids.items() if v}
                                if pids_lower.get('imdb') == str(v_imdb):
                                    m['match_quality'] = 1.0
                                    items_to_process.append(m)
                                    xbmc.log(
                                        f"Source Engine [MOVIE FOUND]: {s['name']} "
                                        f"'{m.get('Name')}' ({m.get('ProductionYear')}) "
                                        f"verified by IMDB={v_imdb}",
                                        xbmc.LOGWARNING
                                    )
                    except:
                        pass

                # SURGICAL MOVIE STRIKE 3: Title & Year match
                # Deep dive: always runs, relaxed similarity, wider year tolerance
                if safe_query and (deep_dive or not items_to_process):
                    _dd_year_tol = 3 if deep_dive else 1
                    try:
                        r = session.get(
                            f"{items_base}?Recursive=True&IncludeItemTypes=Movie"
                            f"&SearchTerm={safe_query}&Fields={fields}&Limit={_dd_m_limit}{user_q}",
                            timeout=20
                        ).json()
                        for m in r.get('Items', []):
                            m_name = m.get('Name', '')
                            # Deep dive: accept ≥0.5 similarity; normal: strict _titles_match
                            title_ok = (
                                (_title_similarity(query, m_name) >= 0.5) if deep_dive
                                else _titles_match(query, m_name)
                            )
                            if title_ok:
                                if target_year and str(target_year).isdigit() and m.get('ProductionYear'):
                                    if abs(int(target_year) - int(m.get('ProductionYear'))) <= _dd_year_tol:
                                        m['match_quality'] = 1.0 if _titles_match(query, m_name) else 0.85
                                        items_to_process.append(m)
                                        xbmc.log(
                                            f"Source Engine [MOVIE FOUND]: {s['name']} "
                                            f"'{m_name}' matched by title+year"
                                            f"{' [DEEP DIVE]' if deep_dive else ''}",
                                            xbmc.LOGWARNING
                                        )
                                else:
                                    m['match_quality'] = 1.0 if _titles_match(query, m_name) else 0.85
                                    items_to_process.append(m)
                                    xbmc.log(
                                        f"Source Engine [MOVIE FOUND]: {s['name']} "
                                        f"'{m_name}' matched by title"
                                        f"{' [DEEP DIVE]' if deep_dive else ''}",
                                        xbmc.LOGWARNING
                                    )
                    except:
                        pass

                # Deep dive deduplication: all strikes ran so the same movie
                # may appear more than once. Keep the highest match_quality copy.
                if deep_dive and items_to_process:
                    _seen_m = {}
                    for m in items_to_process:
                        mid = m.get('Id')
                        if mid not in _seen_m or m.get('match_quality', 0) > _seen_m[mid].get('match_quality', 0):
                            _seen_m[mid] = m
                    items_to_process = list(_seen_m.values())

                if not items_to_process:
                    xbmc.log(
                        f"Source Engine [MOVIE EMPTY]: {s['name']} "
                        f"no movie match in library for "
                        f"tmdb={v_tmdb} imdb={v_imdb} title='{query}' year={target_year}",
                        xbmc.LOGWARNING
                    )

            # -----------------------------------------------------------------
            # THE SCORING ARENA
            # -----------------------------------------------------------------
            for item in items_to_process:
                match_multiplier = item.get('match_quality', 1.0)

                for source in item.get('MediaSources', []):
                    if not source.get('Id'):
                        continue
                    streams = source.get('MediaStreams') or []

                    video_streams = [st for st in streams if st.get('Type') == 'Video']
                    audio_streams = [st for st in streams if st.get('Type') == 'Audio']

                    if not video_streams:
                        continue

                    video = sorted(
                        video_streams,
                        key=lambda st: st.get('BitRate') or 0,
                        reverse=True
                    )[0]
                    height = video.get('Height') or 0
                    width = video.get('Width') or 0
                    if height > max_res:
                        continue

                    audio = max(
                        audio_streams,
                        key=lambda st: st.get('Channels') or 0,
                        default={}
                    )

                    bitrate = source.get('Bitrate') or 0
                    size = source.get('Size') or 0
                    runtime_ticks = item.get('RunTimeTicks') or 0
                    runtime_sec = runtime_ticks / 10000000.0

                    if bitrate < 1000000 and runtime_sec > 0 and size > 0:
                        bitrate = int((size * 8) / runtime_sec)

                    size_gb = size / (1024**3)
                    if max_size > 0 and size_gb > max_size:
                        continue

                    bitrate_mbps = bitrate / 1000000

                    is_4k = width >= 3840 or height >= 2160
                    is_1080p = (width >= 1920 or height >= 1080) and not is_4k

                    video_range = (video.get('VideoRangeType') or '').lower()
                    has_dv = 'dolbyvision' in video_range
                    has_hdr = any(x in video_range for x in ['hdr10', 'hlg', 'hdr'])

                    a_title = (audio.get('DisplayTitle') or '').lower()
                    a_codec = (audio.get('Codec') or '').lower()
                    a_profile = (audio.get('Profile') or '').lower()
                    audio_channels = audio.get('Channels') or 0

                    is_atmos_dtsx = (
                        'atmos' in a_title or 'dts:x' in a_title
                        or 'dtsx' in a_title or 'dts-x' in a_title
                    )
                    is_lossless = (
                        'truehd' in a_codec or 'truehd' in a_title
                        or a_profile in ('ma', 'hra') or 'dts-hd' in a_title
                        or 'dtshd' in a_codec
                        or a_codec in ('flac', 'pcm_bluray', 'pcm')
                        or 'dts-hd ma' in a_title or 'dts-hd' in a_codec
                    )
                    is_surround = audio_channels >= 5

                    score = 0
                    if master_preset == 0:
                        score = bitrate_mbps
                        if is_4k: score += 50
                        if has_dv: score += 20
                        elif has_hdr: score += 10
                        if is_atmos_dtsx or is_lossless: score += 20
                        if is_surround: score += 5
                    elif master_preset == 1:
                        score = bitrate_mbps
                        if is_atmos_dtsx: score += 300
                        elif is_lossless: score += 200
                        if is_surround: score += 50
                        if is_4k: score += 10
                    elif master_preset == 2:
                        score = bitrate_mbps
                        if is_4k: score += 300
                        if is_1080p: score += 100
                        if has_dv: score += 50
                        elif has_hdr: score += 25
                        if is_atmos_dtsx or is_lossless: score += 10
                    elif master_preset == 3:
                        score = (size_gb * 3) + (bitrate_mbps * 2)
                        if is_4k: score += 50
                    elif master_preset == 4:
                        score = bitrate_mbps
                        if is_1080p: score += 300
                        if is_4k: score -= 500
                        if is_atmos_dtsx or is_lossless: score += 20
                    elif master_preset == 5:
                        # Light Mode — slow connection / low-end device
                        # Lower bitrate wins; 1080p is the sweet spot; 4K is strongly penalised.
                        # No bonuses for lossless or surround — keep it light.
                        score = max(0.0, 40.0 - bitrate_mbps)
                        if is_1080p: score += 30
                        if is_4k:    score -= 80
                    final_score = score * match_multiplier

                    resolution = f"{width if width > 0 else '??'}x{height}"
                    video_codec = (video.get('Codec') or '').upper()

                    if not audio_streams:
                        audio_info = "Unknown Audio"
                    else:
                        audio_info = f"{audio_channels}ch {a_codec.upper()}"
                        if is_atmos_dtsx:
                            if 'atmos' in a_title:
                                audio_info = f"{audio_channels}ch Atmos ({a_codec.upper()})"
                            else:
                                audio_info = f"{audio_channels}ch DTS:X"
                        elif is_lossless:
                            if 'truehd' in a_codec or 'truehd' in a_title:
                                audio_info = f"{audio_channels}ch TrueHD"
                            elif ('dtshd' in a_codec or a_profile in ('ma', 'hra')
                                  or 'dts-hd' in a_title or 'dts-hd' in a_codec):
                                audio_info = f"{audio_channels}ch DTS-HD MA"
                            elif a_codec in ('flac',):
                                audio_info = f"{audio_channels}ch FLAC"
                            elif 'pcm' in a_codec:
                                audio_info = f"{audio_channels}ch PCM"

                    user_data = item.get('UserData') or {}
                    pb_ticks = user_data.get('PlaybackPositionTicks') or 0

                    with data_lock:
                        cont = ((source.get('Container') or 'mkv').split(',')[0].strip()) or 'mkv'
                        series_name = item.get('SeriesName', '')
                        display_name = (
                            f"{series_name} - {item.get('Name', 'Unknown')}"
                            if series_name
                            else item.get('Name', 'Unknown')
                        )
                        results.append({
                            'server': s['name'],
                            'id': item.get('Id'),
                            'source_id': source.get('Id'),
                            'score': final_score,
                            'size_gb': round(size_gb, 2),
                            'bitrate_mb': round(bitrate_mbps, 2),
                            'url': s['url'],
                            'token': s['token'],
                            'cont': cont,
                            'playback_position': pb_ticks / 10000000.0,
                            'runtime': runtime_sec,
                            'resolution': resolution,
                            'audio': audio_info,
                            'video_codec': video_codec,
                            'match_quality': match_multiplier,
                            'match_confidence': item.get('match_confidence', 0),
                            'file_name': display_name,
                        })
        except Exception as e:
            xbmc.log(f"Source Engine [EXCEPTION]: {s['name']} - {e}", xbmc.LOGWARNING)
            with data_lock: failed.append(s['name'])

    search_threads = []
    for s in configs:
        t = threading.Thread(target=search_server, args=(s,))
        search_threads.append(t)
        t.start()

    deadline = time.time() + 45
    while any(t.is_alive() for t in search_threads):
        if time.time() > deadline:
            alive = [s['name'] for s, t in zip(configs, search_threads) if t.is_alive()]
            xbmc.log(
                f"Source Engine [TIMEOUT]: 45s exceeded. Still waiting on: {alive}. "
                f"Proceeding with results so far.",
                xbmc.LOGWARNING
            )
            break
        xbmc.sleep(100)

    if results:
        def sort_key(x):
            prefer = 0 if preferred_server and x['server'] == preferred_server else 1
            # Prefer safer match (higher confidence) over higher bitrate so correct episode wins
            conf = x.get('match_confidence', 0) or 0
            return (-x.get('match_quality', 1.0), -conf, -x['score'], prefer)

        sorted_results = sorted(results, key=sort_key)
        return sorted_results[0], sorted_results, failed
    return None, [], failed

# ─────────────────────────────────────────────────────────────────────────────
# BACKUP SERVER — RETRIEVE & TEST HELPERS
# These mirror the primary retrieve_embycon/jellycon and test_emby/jelly_token
# functions but operate on the emby2_* / jelly2_* settings keys.
# ─────────────────────────────────────────────────────────────────────────────

def retrieve_emby2_settings():
    """Read connection info from EmbyCon and populate the Emby BACKUP server settings."""
    try:
        embycon = xbmcaddon.Addon('plugin.video.embycon')
    except Exception:
        xbmcgui.Dialog().notification(
            'Source Engine Pro', 'EmbyCon not installed or not found.',
            xbmcgui.NOTIFICATION_WARNING, 3500
        )
        return

    protocol_idx = embycon.getSetting('protocol') or '1'
    protocol  = 'https' if protocol_idx == '1' else 'http'
    ipaddress = embycon.getSetting('ipaddress') or ''
    port      = embycon.getSetting('port') or ''
    username  = embycon.getSetting('username') or ''
    password  = embycon.getSetting('password') or ''

    if not ipaddress:
        xbmcgui.Dialog().notification(
            'Source Engine Pro', 'EmbyCon has no server address configured.',
            xbmcgui.NOTIFICATION_WARNING, 3500
        )
        return

    std_ports = {'https': '443', 'http': '80'}
    if port and port != std_ports.get(protocol, ''):
        url = f"{protocol}://{ipaddress}:{port}"
    else:
        url = f"{protocol}://{ipaddress}"

    our = xbmcaddon.Addon()
    our.setSetting('emby2_url',  url)
    our.setSetting('emby2_user', username)
    if password:
        our.setSetting('emby2_pass', password)

    xbmcgui.Dialog().notification(
        'Source Engine Pro',
        f"Emby Backup filled: {url}  ({username})",
        xbmcgui.NOTIFICATION_INFO, 4000
    )
    xbmc.log(f"Source Engine Pro [IMPORT]: Emby BACKUP settings imported from EmbyCon — {url}", xbmc.LOGINFO)


def retrieve_jelly2_settings():
    """Read connection info from JellyCon and populate the Jellyfin BACKUP server settings."""
    try:
        jellycon = xbmcaddon.Addon('plugin.video.jellycon')
    except Exception:
        xbmcgui.Dialog().notification(
            'Source Engine Pro', 'JellyCon not installed or not found.',
            xbmcgui.NOTIFICATION_WARNING, 3500
        )
        return

    server_address = jellycon.getSetting('server_address') or ''
    username       = jellycon.getSetting('username') or ''

    if not server_address:
        protocol_idx = jellycon.getSetting('protocol') or '0'
        protocol  = 'https' if protocol_idx == '1' else 'http'
        ipaddress = jellycon.getSetting('ipaddress') or ''
        port      = jellycon.getSetting('port') or ''
        if not ipaddress:
            xbmcgui.Dialog().notification(
                'Source Engine Pro', 'JellyCon has no server address configured.',
                xbmcgui.NOTIFICATION_WARNING, 3500
            )
            return
        std_ports = {'https': '443', 'http': '80'}
        if port and port != std_ports.get(protocol, ''):
            server_address = f"{protocol}://{ipaddress}:{port}"
        else:
            server_address = f"{protocol}://{ipaddress}"

    our = xbmcaddon.Addon()
    our.setSetting('jelly2_url',  server_address)
    our.setSetting('jelly2_user', username)

    msg = f"Jellyfin Backup filled: {server_address}  ({username})"
    if not username:
        msg += " — enter password in settings to complete"
    xbmcgui.Dialog().notification('Source Engine Pro', msg, xbmcgui.NOTIFICATION_INFO, 4000)
    xbmc.log(f"Source Engine Pro [IMPORT]: Jellyfin BACKUP settings imported from JellyCon — {server_address}", xbmc.LOGINFO)


def test_emby2_token():
    our   = xbmcaddon.Addon()
    url   = (our.getSetting('emby2_url')   or '').rstrip('/').replace(':443', '')
    token = (our.getSetting('emby2_token') or '').strip()
    uid   = (our.getSetting('emby2_uid')   or '').strip()
    if not url:
        xbmcgui.Dialog().notification('Emby Backup — Token Test', 'No backup server URL configured.', xbmcgui.NOTIFICATION_WARNING, 5000)
        return
    if not token:
        xbmcgui.Dialog().notification('Emby Backup — Token Test', 'No backup token stored. Enter credentials and restart Kodi.', xbmcgui.NOTIFICATION_WARNING, 5000)
        return
    _run_token_test('Emby Backup', url, token, uid)


def test_jelly2_token():
    our   = xbmcaddon.Addon()
    url   = (our.getSetting('jelly2_url')   or '').rstrip('/').replace(':443', '')
    token = (our.getSetting('jelly2_token') or '').strip()
    uid   = (our.getSetting('jelly2_uid')   or '').strip()
    if not url:
        xbmcgui.Dialog().notification('Jellyfin Backup — Token Test', 'No backup server URL configured.', xbmcgui.NOTIFICATION_WARNING, 5000)
        return
    if not token:
        xbmcgui.Dialog().notification('Jellyfin Backup — Token Test', 'No backup token stored. Enter credentials and restart Kodi.', xbmcgui.NOTIFICATION_WARNING, 5000)
        return
    _run_token_test('Jellyfin Backup', url, token, uid)

# ─────────────────────────────────────────────────────────────────────────────

def play_video():
    params = dict(urllib.parse.parse_qsl(sys.argv[2][1:]))
    tmdb_id = params.get('tmdb_id')
    imdb_id = params.get('imdb_id')
    tvdb_id = params.get('tvdb_id')
    query = params.get('query', '')
    target_year = params.get('year', '')
    media_type = params.get('type')
    season = params.get('season')
    episode = params.get('episode')

    addon = xbmcaddon.Addon()
    master_preset = get_int(addon, 'master_preset', 0)

    preset_names = [
        "Auto Max", "Audiophile", "4K Focus",
        "Remux Focus", "1080p Focus", "Light Mode"
    ]
    p_name = preset_names[master_preset] if 0 <= master_preset < len(preset_names) else "Unknown"
    _dd_active = addon.getSetting('deep_dive') == 'true'

    xbmcgui.Dialog().notification(
        "Source Engine Pro",
        f"Engaging {'Deep Dive' if _dd_active else 'Search'} | Preset: {p_name}",
        xbmcgui.NOTIFICATION_INFO,
        2500
    )

    best, all_results, failed = get_best_source(
        tmdb_id, imdb_id, tvdb_id, media_type,
        query, target_year, season, episode
    )

    if best:
        xbmc.log(
            f"Source Engine Pro [WINNER]: Server '{best['server']}' won with "
            f"file: '{best['file_name']}' (confidence={best.get('match_confidence', '?')})",
            xbmc.LOGWARNING
        )

        tie_breaker   = get_int(addon, 'tie_breaker', 0)
        notify_colors = get_int(addon, 'notify_colors', 0)
        single_server = addon.getSetting('single_server') == 'true'

        other_server = "Jellyfin" if best['server'] == "Emby" else "Emby"
        best_other = next((x for x in all_results if x['server'] == other_server), None)

        _was_tie = False
        if not single_server and tie_breaker == 0 and best_other:
            best_conf = best.get('match_quality', 1.0)
            other_conf = best_other.get('match_quality', 1.0)
            best_match_conf = best.get('match_confidence') or 0
            other_match_conf = best_other.get('match_confidence') or 0
            max_score = max(best['score'], 1)
            score_gap_pct = (abs(best['score'] - best_other['score']) / max_score) * 100
            # Only offer choice when quality and match safety are both close (avoid offering wrong episode)
            conf_close = abs(best_match_conf - other_match_conf) <= 20
            if best_conf == other_conf and score_gap_pct <= 2.0 and conf_close:
                dialog = xbmcgui.Dialog()
                options = [
                    f"{best['server']} | {best['resolution']} | {best['size_gb']}GB | {best['bitrate_mb']}Mbps",
                    f"{best_other['server']} | {best_other['resolution']} | {best_other['size_gb']}GB | {best_other['bitrate_mb']}Mbps"
                ]
                choice = dialog.select("Perfect Tie! Which server?", options)
                if choice == 1:
                    best, best_other = best_other, best
                if choice in (0, 1):
                    _was_tie = True

        # ── Manual Server Picker ──────────────────────────────────────
        _was_manual = False
        manual_pick = addon.getSetting('manual_pick') == 'true'
        if not single_server and manual_pick and best_other and not _was_tie:
            opt_best = (
                f"\u2605  {best['server']}  |  {_res_shorthand(best['resolution'])} {best['video_codec']}"
                f"  |  {best['audio']}  |  {best['size_gb']} GB  |  {best['bitrate_mb']} Mbps"
                f"  |  Score: {int(best['score'])} pts  (ENGINE PICK)"
            )
            opt_other = (
                f"   {best_other['server']}  |  {_res_shorthand(best_other['resolution'])} {best_other['video_codec']}"
                f"  |  {best_other['audio']}  |  {best_other['size_gb']} GB  |  {best_other['bitrate_mb']} Mbps"
                f"  |  Score: {int(best_other['score'])} pts"
            )
            choice = xbmcgui.Dialog().select(
                f"Pick Your Source  —  Engine Recommends: {best['server']}",
                [opt_best, opt_other]
            )
            if choice == 1:
                best, best_other = best_other, best
            if choice in (0, 1):
                _was_manual = True
        # ─────────────────────────────────────────────────────────────

        color_themes = [
            {'win': '', 'lose': ''},
            {'win': 'lime', 'lose': 'red'},
            {'win': 'cyan', 'lose': 'yellow'},
            {'win': 'gold', 'lose': 'orange'}
        ]
        theme = color_themes[notify_colors] if 0 <= notify_colors < len(color_themes) else color_themes[0]

        c_win = f"[COLOR {theme['win']}]" if theme['win'] else ""
        c_lose = f"[COLOR {theme['lose']}]" if theme['lose'] else ""
        c_end = "[/COLOR]" if theme['win'] else ""

        if best_other:
            win_score = int(best['score'])
            lose_score = int(best_other['score'])

            forced_winner = False
            if tie_breaker != 0 and best['score'] < best_other['score']:
                forced_winner = True

            confidence = " WARNING" if best.get('match_quality', 1.0) < 1.0 else ""

            if forced_winner:
                title = f"{best['server']} WINNER (Preferred){confidence} | {best_other['server']} {lose_score}pt"
            else:
                title = f"{best['server']} WINNER {win_score}pt{confidence} | {best_other['server']} {lose_score}pt"

            reason = "Higher Score"
            if master_preset == 0:
                if (('3840' in best['resolution'] or '2160' in best['resolution'])
                        and not ('3840' in best_other['resolution']
                                 or '2160' in best_other['resolution'])):
                    reason = "4K Priority"
                elif best['bitrate_mb'] > best_other['bitrate_mb']:
                    reason = "Highest Bitrate"
            elif master_preset == 1:
                if (('Atmos' in best['audio'] or 'TrueHD' in best['audio']
                     or 'MA' in best['audio'])
                        and not ('Atmos' in best_other['audio']
                                 or 'TrueHD' in best_other['audio']
                                 or 'MA' in best_other['audio'])):
                    reason = "Premium Audio"
                else:
                    reason = "Highest Bitrate"
            elif master_preset == 2: reason = "4K Focus"
            elif master_preset == 3: reason = "Remux Size Focus"
            elif master_preset == 4: reason = "1080p Focus"
            elif master_preset == 5: reason = "Light Mode"

            _res = _res_shorthand(best['resolution'])
            msg = (
                f"{c_lose}{reason}{c_end}  "
                f"{c_win}{_res} {best['video_codec']} | {best['audio']} | {best['size_gb']}GB{c_end}"
            )
        elif other_server in failed:
            title = f"{best['server']} WINNER ({other_server} Offline)"
            _res = _res_shorthand(best['resolution'])
            msg = (
                f"{c_win}{_res} {best['video_codec']} | {best['audio']} | "
                f"{best['size_gb']}GB{c_end}"
            )
        elif single_server:
            # Single Server Mode — clean playback notification, no "vs" language
            _res = _res_shorthand(best['resolution'])
            title = f"{best['server']}  |  {_res}  {best['video_codec']}"
            msg = (
                f"{c_win}{_res} {best['video_codec']} | "
                f"{best['audio']} | {best['size_gb']}GB{c_end}"
            )
        else:
            title = f"{best['server']} WINNER (Best Safe Match)"
            _res = _res_shorthand(best['resolution'])
            msg = (
                f"{c_win}{_res} {best['video_codec']} | {best['audio']} | "
                f"{best['size_gb']}GB{c_end}"
            )

        # ── Log to history/scoreboard ─────────────────────────────── #
        try:
            if best_other:
                _h_wr = f'Preferred ({best["server"]})' if forced_winner else reason
                _h_lr = 'Lower Score'
                _h_loser = best_other['server']
                _h_ls = int(best_other['score'])
            elif other_server in failed:
                _h_wr = f'{other_server} Offline'
                _h_lr = 'Offline'
                _h_loser = other_server
                _h_ls = None
            elif single_server:
                _h_wr = 'Single Server'
                _h_lr = 'Not Used'
                _h_loser = ''
                _h_ls = None
            else:
                _h_wr = 'Only Match'
                _h_lr = 'No Match'
                _h_loser = other_server
                _h_ls = None
            _append_history({
                'timestamp':        datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
                'title':            query,
                'type':             media_type,
                'season':           season,
                'episode':          episode,
                'winner':           best['server'],
                'winner_score':     int(best['score']),
                'winner_resolution': best['resolution'],
                'winner_codec':     best['video_codec'],
                'winner_audio':     best['audio'],
                'winner_size_gb':   best['size_gb'],
                'winner_bitrate_mb': best['bitrate_mb'],
                'loser':            _h_loser,
                'loser_score':      _h_ls,
                'win_reason':       ('User Pick (Tie)' if _was_tie else 'Manual Pick' if _was_manual else _h_wr),
                'loser_reason':     _h_lr,
                'is_tie':           _was_tie,
                'is_manual_pick':   _was_manual,
            })
        except Exception as _he:
            xbmc.log(f"Source Engine Pro [HISTORY]: {_he}", xbmc.LOGWARNING)
        # ─────────────────────────────────────────────────────────── #

        notify(title, msg, 7500)

        stream_url = (
            f"{best['url']}/Videos/{best['id']}/stream.{best['cont']}"
            f"?Static=true&api_key={best['token']}&MediaSourceId={best['source_id']}"
        )

        play_item = xbmcgui.ListItem(path=stream_url)

        # Kodi 21+ API — use InfoTagVideo instead of deprecated setProperty/setInfo
        tag = play_item.getVideoInfoTag()
        tag.setResumePoint(float(best['playback_position']), float(best['runtime']))
        tag.setTitle(query)
        tag.setMediaType(media_type or 'video')

        play_item.setProperty('IsPlayable', 'true')
        # Tell Kodi not to sniff content-type from the URL — avoids intermittent
        # failures where content-lookup times out on direct Emby/Jellyfin streams.
        play_item.setContentLookup(False)

        xbmcplugin.setResolvedUrl(int(sys.argv[1]), True, listitem=play_item)
    else:
        if len(failed) >= 2 or ("Emby" in failed and "Jellyfin" in failed):
            addon_check = xbmcaddon.Addon()
            emby_cfg  = bool(addon_check.getSetting('emby_url')  and addon_check.getSetting('emby_token'))
            jelly_cfg = bool(addon_check.getSetting('jelly_url') and addon_check.getSetting('jelly_token'))
            if not emby_cfg and not jelly_cfg:
                xbmcgui.Dialog().ok(
                    "Source Engine Pro — Not Configured",
                    "No servers are set up on this device.\n\n"
                    "Open addon Settings and enter your Emby and/or Jellyfin\n"
                    "server address and API token."
                )
            elif not emby_cfg or not jelly_cfg:
                uncfg = "Emby" if not emby_cfg else "Jellyfin"
                xbmcgui.Dialog().ok(
                    "Source Engine Pro — Not Configured",
                    f"{uncfg} is not set up on this device, and the other server had no match.\n\n"
                    "Open addon Settings to add your server details."
                )
            else:
                xbmcgui.Dialog().ok(
                    "Source Engine Pro — Servers Offline",
                    "Both Emby and Jellyfin are unreachable.\n\n"
                    "Check that your server addresses are correct and\n"
                    "that both servers are running."
                )
        else:
            searched = [s for s in ['Emby', 'Jellyfin'] if s not in failed]
            server_str = ' & '.join(searched) if searched else 'servers'
            notify("Source Engine Pro", f'"{query}" — not found on {server_str}', 4000)
        xbmcplugin.setResolvedUrl(
            int(sys.argv[1]), False, listitem=xbmcgui.ListItem()
        )

if __name__ == '__main__':
    params = (
        dict(urllib.parse.parse_qsl(sys.argv[2][1:]))
        if len(sys.argv) > 2 and sys.argv[2]
        else {}
    )
    action = params.get('action')
    if action == 'show_history':
        show_history()
    elif action == 'clear_history':
        clear_history()
    elif action == 'retrieve_emby_settings':
        retrieve_embycon_settings()
        try:
            xbmcplugin.endOfDirectory(int(sys.argv[1]), succeeded=False)
        except Exception:
            pass
    elif action == 'retrieve_jelly_settings':
        retrieve_jellycon_settings()
        try:
            xbmcplugin.endOfDirectory(int(sys.argv[1]), succeeded=False)
        except Exception:
            pass
    elif action == 'retrieve_emby2_settings':
        retrieve_emby2_settings()
        try:
            xbmcplugin.endOfDirectory(int(sys.argv[1]), succeeded=False)
        except Exception:
            pass
    elif action == 'retrieve_jelly2_settings':
        retrieve_jelly2_settings()
        try:
            xbmcplugin.endOfDirectory(int(sys.argv[1]), succeeded=False)
        except Exception:
            pass
    elif action == 'test_emby_token':
        test_emby_token()
    elif action == 'test_jelly_token':
        test_jelly_token()
    elif action == 'test_emby2_token':
        test_emby2_token()
    elif action == 'test_jelly2_token':
        test_jelly2_token()
    elif action == 'open_settings':
        open_settings()
    elif not params:
        show_main_menu()
    else:
        play_video()
