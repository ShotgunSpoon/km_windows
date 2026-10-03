"""Keyboard-accessible preferences, notification routing and account management."""
from copy import deepcopy
import wx
from km_audio import DEFAULT_SOUNDS
from km_preferences import validate
from km_version import VERSION


def checkbox(parent, sizer, label, value):
    control = wx.CheckBox(parent, label=label)
    control.SetValue(value)
    sizer.Add(control, 0, wx.ALL, 5)
    return control


class PreferencesDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="Preferences", size=(580, 640))
        self.parent = parent
        self.values = deepcopy(parent.preferences.values)
        root = wx.BoxSizer(wx.VERTICAL)
        self.numbers = {}
        for key, label in (("blackjack_hands", "Blackjack &hands per run"),
                           ("blackjack_every", "Notify every this many hands"),
                           ("blackjack_bet_min", "Minimum bet (gold)"), ("blackjack_bet_max", "Maximum bet (gold)")):
            root.Add(wx.StaticText(self, label=label), 0, wx.LEFT | wx.TOP, 8)
            ctrl = wx.SpinCtrl(self, min=1, max=1000000, initial=self.values[key], name=label)
            root.Add(ctrl, 0, wx.EXPAND | wx.ALL, 5)
            self.numbers[key] = ctrl
        self.checks = {key: checkbox(self, root, label, self.values[key]) for key, label in (
            ("auto_update", "Automatically download and install &updates when idle"),
            ("game_sounds", "Play &game effects and command sounds"),
            ("bot_game_sounds", "Play game effects during blackjack and donation bots"))}
        root.Add(wx.StaticText(self, label="Bot progress notifications"), 0, wx.LEFT | wx.TOP, 8)
        self.bot = {key: checkbox(self, root, label, self.values["bot_notifications"][key]) for key, label in (
            ("enabled", "Enable bot progress notifications"), ("log", "Write progress to the log"),
            ("speech", "Speak progress"), ("sound", "Play a progress sound"), ("toast", "Show a Windows notification"))}
        self.account = wx.Button(self, label="&Accounts and login...")
        self.account.Bind(wx.EVT_BUTTON, lambda event: parent.on_accounts())
        root.Add(self.account, 0, wx.ALL, 5)
        self.update = wx.Button(self, label=f"Check for updates (version {VERSION})")
        self.update.Bind(wx.EVT_BUTTON, lambda event: parent.check_updates(manual=True))
        root.Add(self.update, 0, wx.ALL, 5)
        root.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizerAndFit(root)
        self.Bind(wx.EVT_BUTTON, self.save, id=wx.ID_OK)

    def save(self, event):
        for key, ctrl in self.numbers.items(): self.values[key] = ctrl.GetValue()
        for key, ctrl in self.checks.items(): self.values[key] = ctrl.GetValue()
        for key, ctrl in self.bot.items(): self.values["bot_notifications"][key] = ctrl.GetValue()
        try:
            self.values = validate(self.values)
        except ValueError as ex:
            wx.MessageBox(str(ex), "Preferences", wx.OK | wx.ICON_ERROR, self)
            return
        self.EndModal(wx.ID_OK)


class NotificationDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="Notification manager", size=(600, 700))
        self.values = deepcopy(parent.preferences.values)
        root = wx.BoxSizer(wx.VERTICAL)
        group = self.values["notifications"]
        self.checks = {key: checkbox(self, root, label, group[key]) for key, label in (
            ("enabled", "&Enable game notifications"), ("log", "Write notifications to the &log"),
            ("speech", "&Speak notifications"), ("sound", "Play notification &sounds"),
            ("toast", "Show &Windows toast notifications"))}
        root.Add(wx.StaticText(self, label="Categories (Space toggles the selected category)"), 0, wx.ALL, 5)
        self.keys = sorted(set(DEFAULT_SOUNDS) | {"la_smith", "la_fight", "la_hunt", "report", "other"})
        self.categories = wx.CheckListBox(self, choices=[key.replace("_", " ").title() for key in self.keys], name="Notification categories")
        for index, key in enumerate(self.keys): self.categories.Check(index, group["categories"].get(key, True))
        root.Add(self.categories, 1, wx.EXPAND | wx.ALL, 5)
        root.Add(wx.StaticText(self, label="Recent notifications"), 0, wx.ALL, 5)
        self.history = wx.TextCtrl(self, value="\n".join(parent.notification_history[-100:]),
                                   style=wx.TE_MULTILINE | wx.TE_READONLY, name="Recent notifications", size=(-1, 150))
        root.Add(self.history, 1, wx.EXPAND | wx.ALL, 5)
        root.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(root)

    def result(self):
        group = self.values["notifications"]
        for key, ctrl in self.checks.items(): group[key] = ctrl.GetValue()
        group["categories"] = {key: self.categories.IsChecked(i) for i, key in enumerate(self.keys)}
        return self.values


class AccountsDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="Accounts and login", size=(460, 360))
        self.action = None
        root = wx.BoxSizer(wx.VERTICAL)
        index = parent.km.store.index()
        self.account_ids = list(index["accounts"])
        labels = [index["accounts"][key]["name"] + (" (current)" if key == index["active"] else "") for key in self.account_ids]
        self.list = wx.ListBox(self, choices=labels, name="Saved accounts")
        if labels: self.list.SetSelection(0)
        root.Add(self.list, 1, wx.EXPAND | wx.ALL, 8)
        for label, action in (("&Switch to selected account", "switch"), ("&Add account with connection code...", "login"),
                              ("&Sign out", "logout")):
            button = wx.Button(self, label=label)
            button.Bind(wx.EVT_BUTTON, lambda event, selected=action: self.choose(selected))
            root.Add(button, 0, wx.EXPAND | wx.ALL, 5)
        root.Add(self.CreateButtonSizer(wx.CANCEL), 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(root)

    def choose(self, action):
        account_id = None
        if action == "switch":
            selection = self.list.GetSelection()
            if selection == wx.NOT_FOUND: return
            account_id = self.account_ids[selection]
        self.action = (action, account_id)
        self.EndModal(wx.ID_OK)
