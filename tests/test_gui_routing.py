import os
os.environ["KM_SELF_TEST"] = "1"
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import wx
import km_gui
from km_storage import Accounts
from km_client import KM


class SilentAudio:
    def __init__(self, *args): self.plays = []
    def play(self, *args, **kwargs): self.plays.append((args, kwargs))
    def close(self): pass


class NotificationWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = wx.App.Get() or wx.App(False)

    def test_each_route_history_and_master_off(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Accounts(folder, migrate=False)
            with patch.object(km_gui.km_client, "KM", lambda: KM(fresh=True, store=store)), patch.object(km_gui, "GameAudio", SilentAudio):
                frame = km_gui.KMFrame(startup=False)
            entries, speech, toasts = [], [], []
            frame.add_log = lambda *args: entries.append(args)
            frame._toast = lambda *args: toasts.append(args)
            with patch.object(km_gui, "speak", lambda *args, **kwargs: speech.append(args)):
                for mode in ("log", "speech", "sound", "toast"):
                    entries.clear(); speech.clear(); toasts.clear(); frame.audio.plays.clear()
                    group = frame.preferences.values["notifications"]
                    group.update(enabled=True, log=False, speech=False, sound=False, toast=False)
                    group[mode] = True
                    frame._notification({"title": "Offline check", "body": "Test", "pushCategory": "letter"}, "push_open_letter")
                    actual = {"log": bool(entries), "speech": bool(speech), "sound": bool(frame.audio.plays), "toast": bool(toasts)}
                    self.assertEqual(actual, {key: key == mode for key in actual})
                self.assertEqual(frame.notification_history[-1], "Offline check: Test")
                self.assertTrue((store.directory() / "notification_history.json").is_file())
                frame.preferences.values["notifications"]["enabled"] = False
                entries.clear(); speech.clear(); toasts.clear(); frame.audio.plays.clear()
                frame._notification({"title": "Suppressed"}, "push_info")
                self.assertFalse(any((entries, speech, toasts, frame.audio.plays)))
            frame.Close()
            self.app.ProcessPendingEvents()


if __name__ == "__main__": unittest.main()
