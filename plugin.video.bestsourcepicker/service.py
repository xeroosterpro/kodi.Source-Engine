import xbmc, xbmcaddon, requests, threading, uuid, xbmcgui, urllib.parse

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
            'X-Emby-Authorization': auth_header
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

def run_automation():  
    addon = xbmcaddon.Addon()  
    dialog = xbmcgui.Dialog()
    window = xbmcgui.Window(10000) # Used to track if we already warned the user
    
    for s_type in [("emby", "Emby"), ("jelly", "Jellyfin")]:  
        prefix = s_type[0]  
        url = addon.getSetting(f'{prefix}_url')  
        user = addon.getSetting(f'{prefix}_user')  
        pw = addon.getSetting(f'{prefix}_pass')  
        old_token = addon.getSetting(f'{prefix}_token')  
          
        if url and user:  
            clean_url = url.rstrip('/')  
            
            if check_token_health(clean_url, old_token):
                xbmc.log(f"Source Engine Pro: {prefix.upper()} token is still healthy. Skipping login.", xbmc.LOGINFO)
                window.clearProperty(f"SourceEngine_{prefix}_error")
                continue

            token, uid = get_auth_token(clean_url, user, pw, s_type[1])  
            if token:  
                addon.setSetting(f'{prefix}_token', token)  
                addon.setSetting(f'{prefix}_uid', str(uid) if uid else '')  
                xbmc.log(f"Source Engine Pro: {prefix.upper()} token refreshed.", xbmc.LOGINFO)  
                window.clearProperty(f"SourceEngine_{prefix}_error")
                # PERPLEXITY UX FIX: Removed the noisy success notification. It is now completely silent.
            else:  
                # PERPLEXITY UX FIX: Only warn the user ONCE if the server is offline, don't spam them
                if not window.getProperty(f"SourceEngine_{prefix}_error"):
                    if old_token:  
                        dialog.notification("Source Engine Pro", f"{prefix.upper()} API Unreachable!", xbmcgui.NOTIFICATION_WARNING, 3000)
                    else:  
                        dialog.notification("Source Engine Pro", f"Failed to connect to {prefix.upper()}", xbmcgui.NOTIFICATION_ERROR, 3000)
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
                        if self.token == emby_token:
                            self.uid = addon.getSetting('emby_uid')
                        else:
                            self.uid = addon.getSetting('jelly_uid')

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

if __name__ == '__main__':  
    monitor = xbmc.Monitor()  
    reporter = PlaybackReporter()  
    
    run_automation()  
      
    while not monitor.abortRequested():  
        if monitor.waitForAbort(43200):  
            break  
        run_automation()  
