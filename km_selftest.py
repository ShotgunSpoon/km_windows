"""Offline packaged-runtime checks. Never connects or sends a game command."""
import hashlib
import os
import time
from pathlib import Path
from km_storage import DATA_DIR, RESOURCE_DIR, save_secret, load_secret, write_json
from km_version import VERSION


def run():
    os.environ["SDL_AUDIODRIVER"] = "dummy"
    import wx
    import pygame
    import av
    import prism
    import km_gui
    from km_dialogs import PreferencesDialog, NotificationDialog, AccountsDialog
    result = {"version": VERSION, "ok": False}
    try:
        # Prove the protected store works in the packaged process.
        private = DATA_DIR / "self-test-secret.dat"
        save_secret(private, {"token": "offline-test"})
        assert load_secret(private) == {"token": "offline-test"}
        private.unlink()
        km_gui._voice = None
        app = wx.App(False)
        frame = km_gui.KMFrame(startup=False)
        dialogs = [PreferencesDialog(frame), NotificationDialog(frame), AccountsDialog(frame)]
        assert len(frame.GetMenuBar().GetMenu(0).GetMenuItems()) == 5
        for dialog in dialogs: dialog.Destroy()
        sound_count = 0
        for path in (RESOURCE_DIR / "assets" / "sounds").iterdir():
            if path.suffix in (".wav", ".mp3", ".m4a"):
                with av.open(str(path)) as container:
                    assert next(container.decode(audio=0), None) is not None
                sound_count += 1
        frame.Close()
        app.ProcessPendingEvents()
        result.update(ok=True, sounds=sound_count, dialogs=3, encryption="Windows DPAPI", game_requests=0,
                      resource_dir=str(RESOURCE_DIR), data_dir=str(DATA_DIR), speech_library="Prism loaded")
    except Exception as ex:
        result["error"] = f"{type(ex).__name__}: {ex}"
    write_json(DATA_DIR / "self-test-result.json", result)
    if not result["ok"]:
        raise SystemExit(1)
