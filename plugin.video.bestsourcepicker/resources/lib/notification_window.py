# -*- coding: utf-8 -*-
"""Custom trophy notification window for Source Engine Pro. Works on Windows and Nvidia Shield."""
from __future__ import absolute_import, unicode_literals

import threading
import xbmc
import xbmcgui
import xbmcaddon

ADDON = xbmcaddon.Addon()
ADDON_PATH = ADDON.getAddonInfo("path")
XML_NAME = "Script-SourceEngine-Trophy.xml"


class TrophyNotificationWindow(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(TrophyNotificationWindow, self).__init__(*args, **kwargs)
        self._closed = False

    def onInit(self):
        try:
            win = xbmcgui.Window(10000)
            title = win.getProperty("SourceEngine.TrophyTitle") or ""
            message = win.getProperty("SourceEngine.TrophyMessage") or ""
            win.clearProperty("SourceEngine.TrophyTitle")
            win.clearProperty("SourceEngine.TrophyMessage")
            self.getControl(3010).setLabel(title)
            self.getControl(3011).setLabel(message)
            xbmc.log("Source Engine Pro [TROPHY]: Custom window onInit OK", xbmc.LOGINFO)
        except Exception as e:
            xbmc.log("Source Engine Pro [TROPHY] onInit error: %s" % str(e), xbmc.LOGERROR)
        t = threading.Timer(7.0, self._safe_close)
        t.daemon = True
        t.start()

    def _safe_close(self):
        if not self._closed:
            self._closed = True
            try:
                self.close()
            except Exception:
                pass

    def onAction(self, action):
        if action.getId() in (10, 92):
            self._safe_close()


def show_trophy_from_properties():
    """Read title/message from Window 10000 and show trophy. Called via RunPlugin(plugin://...?action=show_trophy)."""
    try:
        gui = TrophyNotificationWindow(XML_NAME, ADDON_PATH, "Default", "720p")
        gui.doModal()
        del gui
        xbmc.log("Source Engine Pro [TROPHY]: doModal completed", xbmc.LOGINFO)
    except Exception as e:
        xbmc.log("Source Engine Pro [TROPHY] show error: %s" % str(e), xbmc.LOGERROR)


def show_trophy_notification(title, message):
    """Launch trophy via RunPlugin so it works reliably on both plugin/script contexts."""
    try:
        win = xbmcgui.Window(10000)
        win.setProperty("SourceEngine.TrophyTitle", title)
        win.setProperty("SourceEngine.TrophyMessage", message)
        # RunPlugin invokes this addon with action=show_trophy in a separate process
        xbmc.executebuiltin("RunPlugin(plugin://plugin.video.bestsourcepicker/?action=show_trophy)")
        return True
    except Exception as e:
        xbmc.log("Source Engine Pro [TROPHY] launch error: %s" % str(e), xbmc.LOGERROR)
        return False
