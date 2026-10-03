import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from km_storage import Accounts, load_secret, save_secret
from km_preferences import Preferences, DEFAULTS, validate
from km_updater import select_release, version_tuple, prepare_install
from km_client import _redact


class StorageTests(unittest.TestCase):
    def test_windows_encryption_and_account_switch_isolation(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Accounts(folder, migrate=False)
            first = {"token": "first-private-token", "device": "d1", "user": {"id": 1, "name": "First"}}
            second = {"token": "second-private-token", "device": "d2", "user": {"id": 2, "name": "Second"}}
            a = store.save(first)
            b = store.save(second)
            self.assertNotIn(b"first-private-token", (store.directory(a) / "session.dat").read_bytes())
            self.assertEqual(store.load(), second)
            store.activate(a)
            self.assertEqual(store.load(), first)
            self.assertEqual(store.load(b), second)
            store.activate(None)
            self.assertEqual(store.load(), {})

    def test_token_rotation_does_not_mix_accounts(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Accounts(folder, migrate=False)
            a = store.save({"token": "old", "device": "one", "user": {"id": 1}})
            b = store.save({"token": "two", "device": "two", "user": {"id": 2}})
            value = store.load(a)
            value["token"] = "renewed"
            store.save(value, a, activate=False)
            self.assertEqual(store.index()["active"], b)
            self.assertEqual(store.load(a)["token"], "renewed")

    def test_recursive_private_field_redaction(self):
        value = _redact({"user": {"webtoken": "secret"}, "token": "secret", "keys": {"auth": "secret"}})
        self.assertNotIn("secret", json.dumps(value))


class PreferenceTests(unittest.TestCase):
    def test_log_only_and_notifications_off(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Preferences(Path(folder) / "preferences.json")
            p.values["notifications"].update(log=True, sound=False, speech=False, toast=False)
            p.save(p.values)
            self.assertEqual(p.route(), {"log": True, "speech": False, "sound": False, "toast": False})
            p.values["notifications"]["enabled"] = False
            self.assertFalse(any(p.route().values()))

    def test_bot_and_game_preferences_are_independent(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Preferences(Path(folder) / "preferences.json")
            p.values["notifications"]["enabled"] = False
            p.values["bot_notifications"].update(log=False, speech=False, sound=True, toast=False)
            self.assertFalse(any(p.route().values()))
            self.assertEqual(p.route(bot=True), {"log": False, "speech": False, "sound": True, "toast": False})

    def test_categories_and_limits(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Preferences(Path(folder) / "preferences.json")
            p.values["notifications"]["categories"]["dm"] = False
            self.assertFalse(any(p.route(category="dm").values()))
            self.assertTrue(p.route(category="letter")["log"])
        for value in (0, -1, 1000001, "5000", True):
            with self.assertRaises(ValueError): validate({"blackjack_hands": value})
        with self.assertRaises(ValueError): validate({"blackjack_bet_min": 100, "blackjack_bet_max": 10})


class UpdaterTests(unittest.TestCase):
    def release(self, version="v1.1.0"):
        return {"tag_name": version, "assets": [{"name": "km_windows.exe"}, {"name": "km_windows.exe.sha256"}]}

    def test_only_new_complete_stable_releases(self):
        self.assertIsNotNone(select_release(self.release(), "1.0.0"))
        self.assertIsNone(select_release(self.release("v1.0.0"), "1.0.0"))
        self.assertIsNone(select_release(self.release("v0.9.0"), "1.0.0"))
        release = self.release(); release["prerelease"] = True
        self.assertIsNone(select_release(release))
        release = self.release(); release["assets"].pop()
        with self.assertRaises(ValueError): select_release(release)
        with self.assertRaises(ValueError): version_tuple("../../evil")

    def test_corrupt_update_never_launches_installer(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "download.exe"
            source.write_bytes(b"corrupted")
            with patch("km_updater.subprocess.Popen") as launch:
                with self.assertRaises(ValueError):
                    prepare_install({"path": str(source), "sha256": "0" * 64}, Path(folder) / "target.exe")
                launch.assert_not_called()

    def test_install_script_waits_and_retains_previous_binary(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "download.exe"
            source.write_bytes(b"valid-fixture")
            command = prepare_install({"path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()},
                                      Path(folder) / "target.exe", parent_id=123, launch=False)
            script = (Path(folder) / "apply_update.ps1").read_text()
            self.assertIn("$process.WaitForExit()", script)
            self.assertIn(".previous", script)
            self.assertIn("-WindowStyle", command)


if __name__ == "__main__": unittest.main()
