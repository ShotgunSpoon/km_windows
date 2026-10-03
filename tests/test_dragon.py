"""Offline replay of the two captured donation loops; never connects to the game."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from km_client import KM
from km_dragon import DragonDonationBot


CAPTURE = json.loads((Path(__file__).parent / "fixtures/dragon_donations.json").read_text(encoding="utf-8"))


class Replay:
    def __init__(self, gold=30000, mutate=None, lost_reply=False, lag=False, backpack=None):
        self.gold = gold
        self.commands = []
        self.confirmations = 0
        self.mutate = mutate
        self.lost_reply = lost_reply
        self.lag = lag
        self.backpack = backpack or {"blocks": [{"text": "Backpack categories: Weapons, Potions."}]}

    def say(self, command):
        if command == "backpack":
            self.commands.append(command)
            return KM.reply_text(self.backpack), self.backpack
        index = sum(c != "backpack" for c in self.commands) % 10
        expected = CAPTURE[index]
        if command != expected["command"]:
            raise AssertionError(f"Unexpected command {command!r}; expected {expected['command']!r}")
        self.commands.append(command)
        r = copy.deepcopy(expected["response"])
        if self.mutate:
            self.mutate(index, r)
        if index % 5 == 4:
            self.gold -= 10
            self.confirmations += 1
            if self.lost_reply:
                raise TimeoutError("Lost donation response")
        return KM.reply_text(r), r

    def call(self, method, path):
        assert (method, path) == ("GET", "api/knight/profile")
        value = self.gold + (10 if self.lag and len(self.commands) % 5 == 0 else 0)
        return {"data": {"gold": value}}


class DonationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "progress.json"
        self.messages = []
        sleep = patch("km_dragon.time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def bot(self, client, **kwargs):
        return DragonDonationBot(client, state_path=self.state,
                                 say=self.messages.append, announce=self.messages.append, **kwargs)

    def test_full_budget_and_completed_restart(self):
        client = Replay()
        bot = self.bot(client)
        bot.run()
        self.assertEqual(bot.done, 2000)
        self.assertEqual(client.confirmations, 2000)
        self.assertEqual(client.gold, 10000)
        self.assertEqual(len(client.commands), 10040)
        self.assertEqual(client.commands.count("backpack"), 40)
        self.bot(client).run()
        self.assertEqual(client.confirmations, 2000)

    def test_stop_then_resume_both_recorded_wordings(self):
        client = Replay()
        self.bot(client, stop_check=lambda: client.confirmations >= 1).run()
        self.assertEqual(json.loads(self.state.read_text())["done"], 1)
        self.bot(client, stop_check=lambda: client.confirmations >= 2).run()
        self.assertEqual(json.loads(self.state.read_text())["done"], 2)
        self.assertEqual(client.gold, 29980)

    def test_lost_reply_blocks_repeat(self):
        client = Replay(lost_reply=True)
        self.bot(client).run()
        self.assertIsNotNone(json.loads(self.state.read_text())["pending"])
        self.bot(client).run()
        self.assertEqual(client.confirmations, 1)
        self.assertEqual(len(client.commands), 5)

    def test_lagging_balance_blocks_repeat(self):
        client = Replay(lag=True)
        self.bot(client).run()
        self.bot(client).run()
        self.assertEqual(client.confirmations, 1)
        self.assertIsNotNone(json.loads(self.state.read_text())["pending"])
        self.assertTrue(any("expected 29,990, last reported 30000" in m for m in self.messages))

    def test_delayed_balance_retries_reads_without_repeating_donation(self):
        client = Replay()
        balances = iter([30000, 30000, 30000, 29990])
        with patch.object(client, "call", side_effect=lambda *a: {"data": {"gold": next(balances)}}) as read:
            bot = self.bot(client)
            bot._donate()
        self.assertEqual(read.call_count, 4)
        self.assertEqual(client.confirmations, 1)
        self.assertEqual(bot.done, 1)
        self.assertIsNone(json.loads(self.state.read_text())["pending"])

    def test_balance_retry_limit_and_recovery_after_restart(self):
        client = Replay(lag=True)
        with patch.object(client, "call", wraps=client.call) as read:
            self.bot(client).run()
        self.assertEqual(read.call_count, 7)  # before plus six verification reads
        self.assertTrue(json.loads(self.state.read_text())["pending"]["response_verified"])
        client.lag = False
        resumed = self.bot(client, stop_check=lambda: True)
        resumed.run()
        self.assertEqual(resumed.done, 1)
        self.assertEqual(client.confirmations, 1)
        self.assertEqual(len(client.commands), 5)
        self.assertIsNone(json.loads(self.state.read_text())["pending"])

    def test_profile_read_failure_can_recover(self):
        client = Replay()
        replies = [{"data": {"gold": 30000}}, TimeoutError("Profile read failed"),
                   {"data": {"gold": 29990}}]
        with patch.object(client, "call", side_effect=replies):
            bot = self.bot(client)
            bot._donate()
        self.assertEqual(bot.done, 1)
        self.assertEqual(client.confirmations, 1)

    def test_stop_interrupts_balance_wait_without_repeating_donation(self):
        client = Replay(lag=True)
        bot = self.bot(client, stop_check=lambda: client.confirmations > 0)
        with patch.object(client, "call", wraps=client.call) as read:
            bot.run()
        self.assertEqual(read.call_count, 2)
        self.assertEqual(client.confirmations, 1)
        self.assertIsNotNone(json.loads(self.state.read_text())["pending"])

    def test_wrong_amount_never_confirmed(self):
        def change(index, r):
            if index == 3:
                for b in r["blocks"]:
                    b["text"] = b.get("text", "").replace("10 gold", "100 gold")
        client = Replay(mutate=change)
        self.bot(client).run()
        self.assertEqual(client.confirmations, 0)
        self.assertEqual(client.commands[-1], "10")

    def test_wrong_prompt_never_sends_amount(self):
        def change(index, r):
            if index == 2:
                r["reprompt"] = "How much would you like to bet?"
        client = Replay(mutate=change)
        self.bot(client).run()
        self.assertEqual(client.confirmations, 0)
        self.assertNotIn("10", client.commands)

    def test_insufficient_gold_never_confirmed(self):
        client = Replay(gold=9)
        self.bot(client).run()
        self.assertEqual(client.confirmations, 0)

    def test_server_error_stops_navigation(self):
        def change(index, r):
            r["error"] = "rate_limited"
        client = Replay(mutate=change)
        self.bot(client).run()
        self.assertEqual(client.commands, ["dragon rock"])

    def test_corrupt_state_never_sends_commands(self):
        self.state.write_text("broken")
        client = Replay()
        self.bot(client).run()
        self.assertEqual(client.commands, [])

    def test_scroll_in_navigation_stops_immediately_and_on_restart(self):
        def change(index, r):
            r["blocks"].append({"kind": "voice", "text": "You have discovered a SCROLL!"})
        client = Replay(mutate=change)
        self.bot(client).run()
        self.assertEqual(client.commands, ["dragon rock"])
        self.assertTrue(any("Scroll found" in m for m in self.messages))
        self.bot(client).run()
        self.assertEqual(client.commands, ["dragon rock"])

    def test_scroll_in_donation_result_counts_payment_then_stops(self):
        def change(index, r):
            if index == 4:
                r["blocks"].append({"kind": "voice", "text": "Your knight found a scroll."})
        client = Replay(mutate=change)
        bot = self.bot(client)
        bot.run()
        self.assertEqual(bot.done, 1)
        self.assertEqual(client.confirmations, 1)
        self.assertIsNone(json.loads(self.state.read_text())["pending"])
        self.assertTrue(any("Scroll found" in m for m in self.messages))

    def test_backpack_scroll_category_stops_at_fifty(self):
        client = Replay(backpack={"blocks": [{"text": "Select a category."}],
                                  "quick_actions": [{"label": "Scrolls"}]})
        bot = self.bot(client)
        bot.done = 49
        bot._save()
        bot.run()
        self.assertEqual(bot.done, 50)
        self.assertEqual(client.commands[-1], "backpack")
        self.assertEqual(client.confirmations, 1)
        self.assertTrue(any("Scroll found in the backpack" in m for m in self.messages))

    def test_backpack_without_scrolls_continues(self):
        client = Replay()
        bot = self.bot(client, stop_check=lambda: client.confirmations >= 2)
        bot.done = 49
        bot._save()
        bot.run()
        self.assertEqual(bot.done, 51)
        self.assertEqual(client.commands[5:7], ["backpack", "dragon rock"])
        self.assertEqual(json.loads(self.state.read_text())["last_backpack_check"], 50)

    def test_resume_at_missed_backpack_checkpoint_checks_before_donating(self):
        client = Replay(backpack={"blocks": [{"text": "Categories: Scrolls, Weapons."}]})
        bot = self.bot(client)
        bot.done = 50
        bot._save()
        bot.run()
        self.assertEqual(client.commands, ["backpack"])
        self.assertEqual(client.confirmations, 0)


if __name__ == "__main__":
    unittest.main()
