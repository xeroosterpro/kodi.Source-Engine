"""Server statistics fetcher for Source Engine Pro.

Provides unified API calls to Emby and Jellyfin servers for:
- Session/stream information  (GET /Sessions)
- Server system info          (GET /System/Info)
- Ping latency                (GET /System/Ping)
- Library item counts         (GET /Items/Counts)
"""

import time
import requests
import xbmc

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _make_headers(token):
    return {
        'X-Emby-Token': token,
        'Accept': 'application/json',
        'User-Agent': 'Mozilla/5.0',
    }


def fetch_ping_latency(url, token=None):
    """Measure round-trip latency (ms) via GET /System/Ping.  Returns -1 on failure."""
    try:
        headers = _make_headers(token) if token else {'Accept': 'application/json'}
        start = time.monotonic()
        r = requests.get(f"{url}/System/Ping", headers=headers, timeout=5, verify=False)
        elapsed = (time.monotonic() - start) * 1000
        if r.status_code == 200:
            return round(elapsed, 1)
    except Exception as e:
        xbmc.log(f"Source Engine Pro [STATS]: Ping failed for {url} - {e}", xbmc.LOGWARNING)
    return -1


def fetch_sessions(url, token):
    """Fetch active sessions from GET /Sessions.

    Returns dict with total_streams, transcoding, direct_play, active_users,
    user_names, and raw session details. Returns {'permission_denied': True}
    when the server requires an admin token.
    """
    try:
        r = requests.get(f"{url}/Sessions", headers=_make_headers(token), timeout=8, verify=False)
        xbmc.log(f"Source Engine Pro [STATS]: /Sessions status {r.status_code} for {url}", xbmc.LOGDEBUG)
        if r.status_code in (401, 403):
            return {'permission_denied': True}
        if r.status_code != 200:
            xbmc.log(f"Source Engine Pro [STATS]: /Sessions unexpected {r.status_code} for {url}", xbmc.LOGWARNING)
            return None
        sessions = r.json()

        active_streams = []
        user_names = set()
        transcoding = 0
        direct_play = 0

        for s in sessions:
            if s.get('NowPlayingItem'):
                active_streams.append(s)
                user_names.add(s.get('UserName', 'Unknown'))
                method = (s.get('PlayState') or {}).get('PlayMethod', '')
                if method == 'Transcode':
                    transcoding += 1
                else:
                    direct_play += 1

        return {
            'total_streams': len(active_streams),
            'transcoding': transcoding,
            'direct_play': direct_play,
            'active_users': len(user_names),
            'user_names': sorted(user_names),
            'sessions': active_streams,
            'permission_denied': False,
        }
    except Exception as e:
        xbmc.log(f"Source Engine Pro [STATS]: Sessions fetch failed for {url} - {e}", xbmc.LOGWARNING)
        return None


def fetch_system_info(url, token):
    """Fetch server name, version and OS.

    Tries GET /System/Info (admin required on Jellyfin).
    Falls back to GET /System/Info/Public (no auth needed, limited info).
    """
    try:
        # Try full info first (admin token or Emby)
        r = requests.get(f"{url}/System/Info", headers=_make_headers(token), timeout=8, verify=False)
        xbmc.log(f"Source Engine Pro [STATS]: /System/Info status {r.status_code} for {url}", xbmc.LOGDEBUG)
        if r.status_code == 200:
            d = r.json()
            return {
                'server_name': d.get('ServerName', 'Unknown'),
                'version': d.get('Version', '?'),
                'os': d.get('OperatingSystemDisplayName') or d.get('OperatingSystem', '?'),
                'admin_access': True,
            }

        # Fall back to public endpoint (no auth required, available on both Emby and Jellyfin)
        xbmc.log(f"Source Engine Pro [STATS]: /System/Info returned {r.status_code}, trying /System/Info/Public", xbmc.LOGINFO)
        rp = requests.get(f"{url}/System/Info/Public", timeout=8, verify=False)
        if rp.status_code == 200:
            d = rp.json()
            return {
                'server_name': d.get('ServerName', 'Unknown'),
                'version': d.get('Version', '?'),
                'os': d.get('OperatingSystemDisplayName') or d.get('OperatingSystem', '?'),
                'admin_access': False,
            }
    except Exception as e:
        xbmc.log(f"Source Engine Pro [STATS]: System/Info failed for {url} - {e}", xbmc.LOGWARNING)
    return None


def fetch_library_counts(url, token):
    """Fetch movie / series / episode counts from GET /Items/Counts."""
    try:
        r = requests.get(f"{url}/Items/Counts", headers=_make_headers(token), timeout=8, verify=False)
        xbmc.log(f"Source Engine Pro [STATS]: /Items/Counts status {r.status_code} for {url}", xbmc.LOGDEBUG)
        if r.status_code in (401, 403):
            return {'permission_denied': True}
        if r.status_code != 200:
            xbmc.log(f"Source Engine Pro [STATS]: /Items/Counts unexpected {r.status_code} for {url}", xbmc.LOGWARNING)
            return None
        d = r.json()
        return {
            'movies': d.get('MovieCount', 0),
            'series': d.get('SeriesCount', 0),
            'episodes': d.get('EpisodeCount', 0),
            'permission_denied': False,
        }
    except Exception as e:
        xbmc.log(f"Source Engine Pro [STATS]: Items/Counts failed for {url} - {e}", xbmc.LOGWARNING)
        return None


def fetch_all_stats(url, token):
    """Fetch all server statistics in one call."""
    result = {
        'ping_ms': -1,
        'sessions': None,
        'system': None,
        'library': None,
        'error': None,
    }
    if not url or not token:
        result['error'] = 'Not configured'
        return result
    url = url.rstrip('/')
    try:
        result['ping_ms'] = fetch_ping_latency(url, token)
        result['system'] = fetch_system_info(url, token)
        result['sessions'] = fetch_sessions(url, token)
        result['library'] = fetch_library_counts(url, token)
    except Exception as e:
        result['error'] = str(e)
    return result


# ── Formatting helpers ────────────────────────────────────────────────

def format_stats_text(stats, server_label):
    """Format stats into a Kodi [COLOR]-tagged text block for Dialog().textviewer()."""
    lines = [
        f"[COLOR gold]{'=' * 40}[/COLOR]",
        f"[COLOR gold]  {server_label} Server Stats[/COLOR]",
        f"[COLOR gold]{'=' * 40}[/COLOR]",
        "",
    ]

    if stats.get('error'):
        lines.append(f"[COLOR red]  Error: {stats['error']}[/COLOR]")
        return "\n".join(lines)

    # Server Info
    si = stats.get('system')
    if si:
        lines.append("[COLOR cyan]  SERVER INFO[/COLOR]")
        lines.append(f"    Name:      {si['server_name']}")
        lines.append(f"    Version:   {si['version']}")
        if si.get('os') and si['os'] != '?':
            lines.append(f"    OS:        {si['os']}")
        if not si.get('admin_access', True):
            lines.append("    [COLOR gray](limited info — use admin token for full details)[/COLOR]")
        lines.append("")
    else:
        lines.append("[COLOR cyan]  SERVER INFO[/COLOR]")
        lines.append("    [COLOR gray]Could not fetch server info[/COLOR]")
        lines.append("")

    # Network / Latency
    ping = stats.get('ping_ms', -1)
    ping_color = 'lime' if ping < 100 else 'yellow' if ping < 300 else 'red'
    ping_str = f"{ping} ms" if ping >= 0 else "Failed"
    lines.append("[COLOR cyan]  NETWORK[/COLOR]")
    lines.append(f"    Latency:   [COLOR {ping_color}]{ping_str}[/COLOR]")
    lines.append("")

    # Active Streams
    sess = stats.get('sessions')
    lines.append("[COLOR cyan]  ACTIVE STREAMS[/COLOR]")
    if sess and sess.get('permission_denied'):
        lines.append("    [COLOR yellow]Admin token required to view sessions[/COLOR]")
        lines.append("")
    elif sess:
        lines.append(f"    Total:         {sess['total_streams']}")
        lines.append(f"    Direct Play:   [COLOR lime]{sess['direct_play']}[/COLOR]")
        lines.append(f"    Transcoding:   [COLOR orange]{sess['transcoding']}[/COLOR]")
        lines.append(f"    Users Online:  {sess['active_users']}")
        if sess['user_names']:
            lines.append(f"    Users:         {', '.join(sess['user_names'])}")
        lines.append("")

        if sess['sessions']:
            lines.append("[COLOR cyan]  STREAM DETAILS[/COLOR]")
            for s in sess['sessions']:
                item = s.get('NowPlayingItem', {})
                user = s.get('UserName', '?')
                device = s.get('DeviceName', '?')
                title = item.get('Name', '?')
                method = (s.get('PlayState') or {}).get('PlayMethod', '?')
                mc = 'lime' if method == 'DirectPlay' else 'orange'
                lines.append(f"    {user} on {device}: [COLOR {mc}]{method}[/COLOR] - {title}")
            lines.append("")
        elif sess['total_streams'] == 0:
            lines.append("    [COLOR gray]No active streams[/COLOR]")
            lines.append("")
    else:
        lines.append("    [COLOR gray]Could not fetch session data[/COLOR]")
        lines.append("")

    # Library
    lib = stats.get('library')
    lines.append("[COLOR cyan]  LIBRARY[/COLOR]")
    if lib and lib.get('permission_denied'):
        lines.append("    [COLOR yellow]Admin token required to view library counts[/COLOR]")
    elif lib:
        lines.append(f"    Movies:    {lib['movies']:,}")
        lines.append(f"    Series:    {lib['series']:,}")
        lines.append(f"    Episodes:  {lib['episodes']:,}")
    else:
        lines.append("    [COLOR gray]Could not fetch library data[/COLOR]")

    return "\n".join(lines)


def format_startup_summary(stats, server_label):
    """Format a compact one-line summary for the startup toast notification."""
    parts = [server_label]

    si = stats.get('system')
    if si:
        parts.append(f"v{si.get('version', '?')}")

    ping = stats.get('ping_ms', -1)
    if ping >= 0:
        parts.append(f"{ping}ms")

    sess = stats.get('sessions')
    if sess and not sess.get('permission_denied'):
        parts.append(f"{sess['total_streams']} streams")

    lib = stats.get('library')
    if lib and not lib.get('permission_denied'):
        parts.append(f"{lib.get('movies', 0):,} movies")

    return "  |  ".join(parts)
