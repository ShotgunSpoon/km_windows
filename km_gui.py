"""Knight Manager: accessible chat window (wxPython, NVDA-style layout like FA11y) speaking through Prism.

Run:  python km_gui.py
Keys: Enter = send | Ctrl+R = repeat last reply | Ctrl+L = log in with a new code
      Ctrl+. = stop speech | Alt+Q = quick actions list | Ctrl+Q = quit
      Ctrl+B = blackjack bot | Ctrl+D = Dragon Rock donations (20,000 gold in tens)
"""
import os
import queue
import threading
import time
import sys

import wx
import wx.adv

import km_client
from km_audio import GameAudio
from km_notifications import Notifications, category
from km_preferences import Preferences
from km_dialogs import PreferencesDialog, NotificationDialog, AccountsDialog
from km_storage import DATA_DIR, read_json, write_json
from km_version import VERSION

try:
    import prism
    _voice = prism.Context().create_best()
except Exception:            # no screen reader / prism missing: the window still works
    _voice = None

BORDER = 10


def speak(text, interrupt=True):
    if _voice and text:
        try:
            _voice.speak(text, interrupt)
        except Exception:
            pass


def stop_speech():
    if _voice:
        try:
            _voice.stop()
        except Exception:
            pass


class KMFrame(wx.Frame):
    def __init__(self, startup=True):
        super().__init__(None, title=f"Knight Manager Windows {VERSION}", size=(850, 670))
        self.preferences = Preferences()
        self.km = km_client.KM()
        self.notification_history = read_json(self.km.store.directory(self.km.account_id) / "notification_history.json", [])
        self.updating = False
        self.pending_update = None
        self.closing = False
        self.audio = GameAudio(lambda msg: wx.CallAfter(self._notification_status, msg))
        self.km.on_reply = self._reply_audio
        self.notifications = self._make_notifications()
        self.notification_popups = []
        self.q = queue.Queue()
        self.quick = []
        self.last_reply = ""
        threading.Thread(target=self._worker, daemon=True).start()

        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)

        root.Add(wx.StaticText(panel, label="&Game log"), 0, wx.LEFT | wx.RIGHT | wx.TOP, BORDER)
        self.log = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP, name="Game log")
        root.Add(self.log, 1, wx.EXPAND | wx.ALL, BORDER)

        root.Add(wx.StaticText(panel, label="&Command"), 0, wx.LEFT | wx.RIGHT, BORDER)
        self.entry = wx.TextCtrl(panel, style=wx.TE_PROCESS_ENTER, name="Command")
        root.Add(self.entry, 0, wx.EXPAND | wx.ALL, BORDER)

        root.Add(wx.StaticText(panel, label="&Quick actions"), 0, wx.LEFT | wx.RIGHT, BORDER)
        self.quick_list = wx.ListBox(panel, name="Quick actions")
        root.Add(self.quick_list, 0, wx.EXPAND | wx.ALL, BORDER)

        row = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_send = wx.Button(panel, label="&Send")
        self.btn_quick = wx.Button(panel, label="&Run quick action")
        self.btn_login = wx.Button(panel, label="&Log in...")
        self.btn_bot = wx.Button(panel, label="Start &blackjack bot")
        self.btn_dragon = wx.Button(panel, label="Start &Dragon Rock donations")
        self.bot_running = False
        self.bot_kind = None
        self.close_after_bot = False
        self.bot_stop = threading.Event()
        for i, b in enumerate((self.btn_send, self.btn_quick, self.btn_login, self.btn_bot)):
            row.Add(b, 0, wx.LEFT if i else 0, 7)
        root.Add(row, 0, wx.ALL, BORDER)
        root.Add(self.btn_dragon, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, BORDER)
        panel.SetSizer(root)

        self.status = self.CreateStatusBar()

        self.entry.Bind(wx.EVT_TEXT_ENTER, lambda e: self.on_send())
        self.btn_send.Bind(wx.EVT_BUTTON, lambda e: self.on_send())
        self.btn_quick.Bind(wx.EVT_BUTTON, lambda e: self.on_quick())
        self.quick_list.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self.on_quick())
        self.quick_list.Bind(wx.EVT_KEY_DOWN, self._quick_key)
        self.btn_login.Bind(wx.EVT_BUTTON, lambda e: self.on_login())
        self.btn_bot.Bind(wx.EVT_BUTTON, lambda e: self.toggle_bot())
        self.btn_dragon.Bind(wx.EVT_BUTTON, lambda e: self.toggle_bot("dragon"))
        self.Bind(wx.EVT_CLOSE, self.on_close)

        ids = {k: wx.NewIdRef() for k in ("repeat", "login", "stop", "quit", "bot", "dragon", "preferences", "notifications", "accounts", "update")}
        self.Bind(wx.EVT_MENU, lambda e: self.on_preferences(), id=ids["preferences"])
        self.Bind(wx.EVT_MENU, lambda e: self.on_notification_manager(), id=ids["notifications"])
        self.Bind(wx.EVT_MENU, lambda e: self.on_accounts(), id=ids["accounts"])
        self.Bind(wx.EVT_MENU, lambda e: self.check_updates(manual=True), id=ids["update"])
        self.Bind(wx.EVT_MENU, lambda e: self.toggle_bot("dragon"), id=ids["dragon"])
        self.Bind(wx.EVT_MENU, lambda e: self.toggle_bot(), id=ids["bot"])
        self.Bind(wx.EVT_MENU, lambda e: speak(self.last_reply), id=ids["repeat"])
        self.Bind(wx.EVT_MENU, lambda e: self.on_login(), id=ids["login"])
        self.Bind(wx.EVT_MENU, lambda e: stop_speech(), id=ids["stop"])
        self.Bind(wx.EVT_MENU, lambda e: self.Close(), id=ids["quit"])
        self.SetAcceleratorTable(wx.AcceleratorTable([
            (wx.ACCEL_CTRL, ord("R"), ids["repeat"]), (wx.ACCEL_CTRL, ord("L"), ids["login"]),
            (wx.ACCEL_CTRL, ord("."), ids["stop"]), (wx.ACCEL_CTRL, ord("Q"), ids["quit"]),
            (wx.ACCEL_CTRL, ord("B"), ids["bot"]),
            (wx.ACCEL_CTRL, ord("D"), ids["dragon"]),
            (wx.ACCEL_CTRL, ord("P"), ids["preferences"]),
            (wx.ACCEL_CTRL, ord("N"), ids["notifications"])]))
        bar = wx.MenuBar()
        menu = wx.Menu()
        for key, label in (("preferences", "&Preferences\tCtrl+P"), ("notifications", "&Notification manager\tCtrl+N"),
                           ("accounts", "&Accounts and login..."), ("update", "Check for &updates"), ("quit", "E&xit\tCtrl+Q")):
            menu.Append(ids[key], label)
        bar.Append(menu, "&Application")
        self.SetMenuBar(bar)

        self.entry.SetFocus()
        if startup:
            wx.CallAfter(self.start)

    def _make_notifications(self):
        current = self.km
        return Notifications(current,
            lambda payload, sound: wx.CallAfter(self._account_notification, current, payload, sound),
            lambda message: wx.CallAfter(self._account_notification_status, current, message))

    def _account_notification(self, account, payload, sound):
        if account is self.km:
            self._notification(payload, sound)

    def _account_notification_status(self, account, message):
        if account is self.km:
            self._notification_status(message)

    def on_preferences(self):
        dialog = PreferencesDialog(self)
        if dialog.ShowModal() == wx.ID_OK:
            self.preferences.save(dialog.values)
            self.add_log("System", "Preferences saved. Bot limits apply to the next run.")
        dialog.Destroy()

    def on_notification_manager(self):
        dialog = NotificationDialog(self)
        if dialog.ShowModal() == wx.ID_OK:
            self.preferences.save(dialog.result())
            self.add_log("System", "Notification preferences saved.")
        dialog.Destroy()

    def on_accounts(self):
        if not self._account_allowed(): return
        dialog = AccountsDialog(self)
        if dialog.ShowModal() == wx.ID_OK:
            action, account_id = dialog.action
            if action == "login": self.on_login()
            else: self.submit(("account", action, account_id))
        dialog.Destroy()

    def _account_allowed(self):
        if self.bot_running or self.q.unfinished_tasks:
            speak("Stop the bot and wait for the current command before changing accounts.")
            return False
        return True

    def check_updates(self, manual=False):
        if self.updating or self.closing: return
        self.updating = True
        def check():
            from km_updater import check_and_download
            try:
                update = check_and_download()
                wx.CallAfter(self._update_ready, update, manual)
            except Exception as ex:
                wx.CallAfter(self._update_failed, type(ex).__name__)
        threading.Thread(target=check, daemon=True).start()

    def _update_failed(self, error):
        self.updating = False
        if not self.closing:
            self.add_log("System", f"Update check failed ({error}). Your current version is still available.")

    def _update_ready(self, update, manual):
        self.updating = False
        if self.closing: return
        if not update:
            if manual:
                self.add_log("System", "You have the current release.")
                speak("You have the current release.")
            return
        if not getattr(sys, "frozen", False):
            self.add_log("System", "Update downloaded. Automatic installation is available in the executable.")
            return
        self.pending_update = update
        self.add_log("System", f"Update {update['version']} downloaded. It will install when the app is idle.")
        self._install_when_idle()

    def _install_when_idle(self):
        if self.closing or not self.pending_update: return
        if self.bot_running or self.q.unfinished_tasks or self.IsModalDialogOpen():
            wx.CallLater(1000, self._install_when_idle)
            return
        from km_updater import prepare_install
        try:
            prepare_install(self.pending_update)
        except Exception as ex:
            self.pending_update = None
            self._update_failed(type(ex).__name__)
            return
        self.pending_update = None
        speak("Installing the update and restarting.", interrupt=False)
        self.Close()

    def IsModalDialogOpen(self):
        return any(isinstance(window, wx.Dialog) and window.IsModal() for window in wx.GetTopLevelWindows())

    # ---- helpers
    def _quick_key(self, e):
        if e.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self.on_quick()
        else:
            e.Skip()

    def add_log(self, who, text):
        self.log.AppendText(f"{who}: {text}\n")
        try:  # plain-text transcript alongside probe_logs/log.jsonl, so a play session can be reviewed later
            with open(os.path.join(self.km.log_dir, "transcript.log"), "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {who}: {text}\n")
        except OSError:
            pass

    def set_busy(self, busy):
        self.status.SetStatusText("Working..." if busy else "Ready")

    def start(self):
        if self.preferences.values["auto_update"]:
            self.check_updates()
        if not self.km.s.get("token"):
            self.on_login()
        else:
            self.notifications.start()
            self.submit(("open",))

    def _reply_audio(self, reply):
        settings = self.preferences.values
        if not settings["game_sounds"] or (self.bot_running and not settings["bot_game_sounds"]):
            return
        self.audio.play("chat_receive", gain=0.4)
        self.audio.reply(reply)

    def _notification_status(self, message):
        if self.closing:
            return
        self.add_log("System", message)
        speak(message, interrupt=False)

    def _notification(self, payload, sound):
        if self.closing:
            return
        title = payload.get("title") or "Knight Manager"
        body = payload.get("body") or ""
        text = f"{title}: {body}" if body else title
        route = self.preferences.route(category=category(payload) or "other")
        if route["log"]:
            self.add_log("Notification", text)
            self.notification_history = (self.notification_history + [text])[-100]
            write_json(self.km.store.directory(self.km.account_id) / "notification_history.json", self.notification_history)
        if route["speech"]: speak(text, interrupt=False)
        if route["sound"]: self.audio.play(sound, channel="notification")
        if route["toast"]: self._toast(title, body)

    def _toast(self, title, body):
        from km_toast import show
        try:
            show(title, body)
        except OSError:
            self.add_log("System", "Windows could not display the notification.")

    # ---- actions
    def on_login(self):
        if not self._account_allowed(): return
        dlg = wx.TextEntryDialog(self, "Enter the 8 character connection code from the game:", "Log in")
        if dlg.ShowModal() == wx.ID_OK and dlg.GetValue().strip():
            self.submit(("login", dlg.GetValue().strip()))
        dlg.Destroy()

    def on_send(self):
        text = self.entry.GetValue().strip()
        if not text:
            return
        self.entry.Clear()
        self.add_log("You", text)
        self.submit(("say", text))

    def on_quick(self):
        i = self.quick_list.GetSelection()
        if i == wx.NOT_FOUND or i >= len(self.quick):
            return
        qa = self.quick[i]
        self.add_log("You", f"[{qa.get('label')}]")
        self.submit(("act", qa.get("intent"), qa.get("slots")))

    def submit(self, job):
        if self.bot_running:
            key = "D" if self.bot_kind == "dragon" else "B"
            speak(f"A bot is running. Press Control {key} to stop it first.")
            return
        self.set_busy(True)
        if job[0] in ("say", "act") and self.preferences.values["game_sounds"]:
            self.audio.play("chat_send", gain=0.4)
        self.q.put(job)

    # ---- blackjack bot (runs in its own thread; announcements go through Prism, so NVDA speaks them even when this window is not focused)
    BOT_HANDS, BOT_BET_MIN, BOT_BET_MAX, BOT_ANNOUNCE_EVERY = 5000, 10, 50, 50

    def toggle_bot(self, kind="blackjack"):
        if self.bot_running:
            if kind != self.bot_kind:
                speak("Stop the running bot before starting another one.")
                return
            self.bot_stop.set()
            speak("Stopping after the current donation." if kind == "dragon" else "Stopping the bot after this hand.")
            return
        if self.q.unfinished_tasks:
            speak("A game command is still pending. Start the bot after the reply arrives.")
            return
        if not self.km.s.get("token"):
            speak("Not logged in. Press Control L first.")
            return
        self.bot_running = True
        self.bot_kind = kind
        self.bot_stop.clear()
        if kind == "dragon":
            self.btn_dragon.SetLabel("Stop &Dragon Rock donations")
            self.btn_bot.Disable()
        else:
            self.btn_bot.SetLabel("Stop &blackjack bot")
            self.btn_dragon.Disable()
        self.set_busy(True)
        target = self._dragon_thread if kind == "dragon" else self._bot_thread
        threading.Thread(target=target, daemon=True).start()

    def _dragon_thread(self):
        try:
            from km_dragon import DragonDonationBot
            DragonDonationBot(
                self.km,
                state_path=os.path.join(self.km.log_dir, "dragon_state.json"),
                log_path=os.path.join(self.km.log_dir, "dragon.log"),
                say=lambda msg: wx.CallAfter(self._bot_msg, msg, False),
                announce=lambda msg: wx.CallAfter(self._bot_msg, msg, True),
                stop_check=self.bot_stop.is_set).run()
        except Exception as ex:
            wx.CallAfter(self._bot_msg, f"Donation bot error: {type(ex).__name__}: {ex}", True)
        finally:
            wx.CallAfter(self._bot_done)

    def on_close(self, event):
        if self.bot_running and event.CanVeto():
            self.close_after_bot = True
            self.bot_stop.set()
            speak("Stopping the bot and saving progress before closing.")
            event.Veto()
            return
        self.closing = True
        self.notifications.close()
        self.audio.close()
        event.Skip()

    def _bot_thread(self):
        import km_blackjack

        def say(msg):
            wx.CallAfter(self._bot_msg, msg, False)

        def announce(msg):
            wx.CallAfter(self._bot_msg, msg, True)

        try:
            settings = self.preferences.values.copy()
            km_blackjack.BlackjackBot(
                self.km, hands=settings["blackjack_hands"], bet_min=settings["blackjack_bet_min"], bet_max=settings["blackjack_bet_max"],
                announce_every=settings["blackjack_every"], say=say, announce=announce,
                log_path=os.path.join(self.km.log_dir, "blackjack.log"),
                stop_check=self.bot_stop.is_set).run()
        except Exception as ex:
            wx.CallAfter(self._bot_msg, f"Bot crashed: {type(ex).__name__}: {ex}", True)
        wx.CallAfter(self._bot_done)

    def _bot_msg(self, msg, queued):
        route = self.preferences.route(bot=True)
        if route["log"]: self.add_log("Bot", msg)
        if route["speech"]: speak(msg, interrupt=not queued)
        if route["sound"]: self.audio.play("push_info", channel="notification")
        if route["toast"]: self._toast("Blackjack" if self.bot_kind == "blackjack" else "Dragon Rock", msg)

    def _bot_done(self):
        self.bot_running = False
        self.bot_kind = None
        self.btn_bot.SetLabel("Start &blackjack bot")
        self.btn_dragon.SetLabel("Start or resume &Dragon Rock donations")
        self.btn_bot.Enable()
        self.btn_dragon.Enable()
        self.set_busy(False)
        if self.preferences.route(bot=True)["speech"]: speak("Bot stopped.", interrupt=False)
        if self.close_after_bot:
            self.Close()

    # ---- network worker (never touches wx directly)
    def _worker(self):
        while True:
            job = self.q.get()
            try:
                kind = job[0]
                if kind == "login":
                    self._stop_receiver()
                    candidate = km_client.KM(fresh=True, store=self.km.store)
                    r = candidate.login(job[1])
                    ok = bool(r.get("token"))
                    candidate.on_reply = self._reply_audio
                    wx.CallAfter(self._done_login, ok, r, candidate)
                    if ok:
                        wx.CallAfter(self._done_reply, candidate.open_game())
                    else:
                        wx.CallAfter(self.set_busy, False)
                elif kind == "account":
                    self._stop_receiver()
                    account_id = job[2] if job[1] == "switch" else None
                    self.km.store.activate(account_id)
                    candidate = km_client.KM(account_id=account_id, fresh=account_id is None, store=self.km.store)
                    candidate.on_reply = self._reply_audio
                    wx.CallAfter(self._changed_account, candidate)
                    if candidate.s.get("token"):
                        wx.CallAfter(self._done_reply, candidate.open_game())
                elif kind == "open":
                    wx.CallAfter(self._done_reply, self.km.open_game())
                elif kind == "say":
                    wx.CallAfter(self._done_reply, self.km.say(job[1])[1])
                elif kind == "act":
                    wx.CallAfter(self._done_reply, self.km.act(job[1], job[2])[1])
            except Exception as ex:
                wx.CallAfter(self._done_reply, {"error": f"{type(ex).__name__}: {ex}"})
                if job[0] in ("login", "account") and self.km.s.get("token"):
                    wx.CallAfter(self.notifications.start)
            finally:
                self.q.task_done()

    def _stop_receiver(self):
        self.notifications.close()
        if self.notifications.thread:
            self.notifications.thread.join(35)
            if self.notifications.thread.is_alive():
                raise RuntimeError("Notification connection is still closing; account was not changed")

    def _changed_account(self, candidate):
        self.km = candidate
        self.notifications = self._make_notifications()
        self.notification_history = read_json(self.km.store.directory(self.km.account_id) / "notification_history.json", [])
        self.log.Clear()
        self.quick = []
        self.quick_list.Clear()
        self.last_reply = ""
        self.entry.Clear()
        self.set_busy(False)
        if self.km.s.get("token"):
            self.add_log("System", f"Account: {(self.km.s.get('user') or {}).get('name') or 'Saved account'}")
            self.notifications.start()
        else:
            self.add_log("System", "Signed out. Press Control L to add an account.")

    def _done_login(self, ok, r, candidate=None):
        if ok and candidate:
            self._changed_account(candidate)
        msg = "Logged in." if ok else f"Login failed: {r.get('error') or r}"
        self.add_log("System", msg)
        speak(msg)
        if ok:
            self.notifications.start()
        elif self.km.s.get("token"):
            self.notifications.start()

    def _done_reply(self, r):
        if self.closing: return
        self.set_busy(False)
        if r.get("_http") == 401:
            msg = "Not logged in. Press Control L to enter a connection code."
            self.add_log("System", msg)
            speak(msg)
            return
        if r.get("error"):
            msg = f"Error: {r['error']}"
            self.add_log("System", msg)
            speak(msg)
            return
        text = km_client.KM.reply_text(r)
        if r.get("navigate"):
            text = f"{text} [navigate: {r['navigate']}]".strip()
        if text:
            self.last_reply = text
            self.add_log("Game", text)
            speak(text)
        self.quick = r.get("quick_actions") or []
        self.quick_list.Set([q.get("label") or q.get("intent") or "?" for q in self.quick])
        if self.quick:
            self.quick_list.SetSelection(0)


if __name__ == "__main__":
    if "--self-test" in sys.argv or os.environ.get("KM_SELF_TEST") == "1":
        from km_selftest import run
        run()
    else:
        app = wx.App(False)
        KMFrame().Show()
        app.MainLoop()
