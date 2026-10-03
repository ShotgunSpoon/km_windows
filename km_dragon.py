"""Bounded Dragon Rock donations, using the captured mountaintop conversation.

Like BlackjackBot, this uses KM, callbacks for speech, and a cooperative stop.
Progress is saved before sending the spending confirmation. An uncertain result
is never retried automatically; inspect the logs before resolving that checkpoint.
"""
import json
import os
from pathlib import Path
import re
import time


class DonationStopped(Exception):
    pass


class ScrollFound(DonationStopped):
    pass


class DragonDonationBot:
    TOTAL = 20000
    AMOUNT = 10
    BALANCE_DELAYS = (0, 0.5, 1, 2, 4, 8)

    def __init__(self, km, *, state_path, log_path=None, say=print,
                 announce=print, stop_check=lambda: False, announce_every=50):
        self.km = km
        self.state_path = Path(state_path)
        self.log_path = log_path
        self.say, self.announce, self.stop_check = say, announce, stop_check
        self.announce_every = announce_every
        self.done = 0
        self.pending = None
        self.scroll_found = None
        self.last_backpack_check = 0

    def _log(self, msg):
        if self.log_path:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")

    def _save(self):
        state = {"version": 1, "total": self.TOTAL, "amount": self.AMOUNT,
                 "done": self.done, "pending": self.pending,
                 "scroll_found": self.scroll_found,
                 "last_backpack_check": self.last_backpack_check}
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, self.state_path)

    def _load(self):
        if not self.state_path.exists():
            return
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        if (state.get("version") != 1 or state.get("total") != self.TOTAL
                or state.get("amount") != self.AMOUNT
                or type(state.get("done")) is not int
                or not 0 <= state["done"] <= self.TOTAL // self.AMOUNT
                or "pending" not in state):
            raise DonationStopped("The saved donation progress is invalid; inspect it before continuing.")
        self.done, self.pending = state["done"], state["pending"]
        self.scroll_found = state.get("scroll_found")
        self.last_backpack_check = state.get("last_backpack_check", 0)
        if type(self.last_backpack_check) is not int or not 0 <= self.last_backpack_check <= self.done:
            raise DonationStopped("The saved backpack checkpoint is invalid.")
        if self.scroll_found:
            raise ScrollFound(f"Scroll found previously: {self.scroll_found}. Donations remain stopped.")
        if self.pending is not None:
            if (isinstance(self.pending, dict)
                    and self.pending.get("response_verified") is True
                    and self.pending.get("donation") == self.done + 1
                    and self.done < self.TOTAL // self.AMOUNT
                    and type(self.pending.get("gold_before")) is int
                    and self.pending["gold_before"] >= self.AMOUNT):
                self._complete_pending()
                return
            raise DonationStopped(
                "A previous donation has an unverified result. Inspect dragon.log and "
                "dragon_state.json before continuing; it will not be sent again automatically.")

    @staticmethod
    def _check(r):
        if not isinstance(r, dict) or r.get("error") or r.get("_http", 200) >= 400:
            raise DonationStopped("The server returned an error; no further donations will be sent.")
        if r.get("should_end_session"):
            raise DonationStopped("The game ended the session.")

    def _send(self, command, *, reconcile_donation=False):
        text, r = self.km.say(command)
        self._log(f"SEND {command!r} -> {text}")
        # Check spoken text, prompts and category/action labels, not asset URLs.
        messages = [text, r.get("reprompt") or ""]
        messages.extend(b.get("text") or "" for b in r.get("blocks", []))
        messages.extend(q.get("label") or "" for q in r.get("quick_actions", []))
        match = next((msg for msg in messages if re.search(r"\bscrolls?\b", msg, re.I)), None)
        if match:
            source = "in the backpack" if command == "backpack" else "in the game response"
            self.scroll_found = f"{source}: {match}"
            notice = f"Scroll found {source}. Dragon Rock donations stopped. {match}"
            self._log(notice)
            self.say(notice)
            self._save()
            if not reconcile_donation:
                raise ScrollFound(notice)
        self._check(r)
        return text, r

    def _check_backpack(self):
        if self.done and self.done % 50 == 0 and self.last_backpack_check < self.done:
            self._send("backpack")
            self.last_backpack_check = self.done
            self._save()
            self._log(f"BACKPACK after {self.done} donations: no scroll mentioned; continuing.")

    def _gold(self):
        r = self.km.call("GET", "api/knight/profile")
        self._check(r)
        data = r.get("data", r)
        gold = data.get("gold") if isinstance(data, dict) else None
        if type(gold) is not int or gold < 0:
            raise DonationStopped("The gold balance is unavailable.")
        return gold

    def _verify_balance(self, before):
        expected = before - self.AMOUNT
        last = None
        last_error = None
        for attempt, delay in enumerate(self.BALANCE_DELAYS, 1):
            if delay:
                # A stop request can interrupt the wait, but never causes the
                # spending confirmation to be repeated.
                remaining = delay
                while remaining > 0 and not self.stop_check():
                    step = min(remaining, 0.1)
                    time.sleep(step)
                    remaining -= step
                if self.stop_check():
                    break
            try:
                last = self._gold()
                last_error = None
            except Exception as ex:
                last_error = f"{type(ex).__name__}: {ex}"
                self._log(f"BALANCE attempt {attempt}: {last_error}")
                continue
            self._log(f"BALANCE attempt {attempt}: before {before}, expected {expected}, reported {last}")
            if last == expected:
                return last
        detail = f" Last profile error: {last_error}." if last_error else ""
        raise DonationStopped(
            f"Gold balance not yet verified: before {before:,}, expected {expected:,}, "
            f"last reported {last if last is not None else 'unavailable'}. "
            "The donation was not repeated. Resume will recheck the balance before continuing."
            + detail)

    def _complete_pending(self):
        after = self._verify_balance(self.pending["gold_before"])
        self.done += 1
        self.pending = None
        self._save()
        self._log(f"DONATION {self.done}: 10 gold; total {self.done * self.AMOUNT}; balance {after}")

    @staticmethod
    def _require(condition, step):
        if not condition:
            raise DonationStopped(f"Unexpected game reply at {step}; no further commands were sent.")

    def _donate(self):
        text, r = self._send("dragon rock")
        self._require(r.get("intent_resolved") == "dragon_rock"
                      and "mountain top" in r.get("reprompt", "").lower(), "Dragon Rock")
        text, r = self._send("mountain top")
        self._require(r.get("intent_resolved") == "dragon_top"
                      and "put some gold here" in r.get("reprompt", "").lower(), "the mountaintop")
        text, r = self._send("yes")
        self._require(r.get("expects_number") is True
                      and "how many gold pieces" in r.get("reprompt", "").lower(), "the amount prompt")
        text, r = self._send(str(self.AMOUNT))
        self._require(r.get("expects_number") is False and re.search(
            r"\b(?:really want|sure you want) to drop 10 gold coins on the mountaintop\?", text, re.I),
            "the 10-gold confirmation")
        before = self._gold()
        if before < self.AMOUNT:
            raise DonationStopped("There is not enough gold for another 10-gold donation.")
        # Persist before the only request that spends gold. A lost response must
        # leave a pending checkpoint, not silently allow a duplicate donation.
        self.pending = {"donation": self.done + 1, "gold_before": before}
        self._save()
        text, r = self._send("yes", reconcile_donation=True)
        sounds = {b.get("url", "").rsplit("/", 1)[-1] for b in r.get("blocks", [])
                  if b.get("kind") == "sfx"}
        self._require(r.get("expects_number") is False
                      and {"sound_coins.mp3", "confirm.mp3"} <= sounds
                      and "gold pieces here" in text.lower(), "the donation result")
        self.pending["response_verified"] = True
        self._save()
        self._complete_pending()

    def run(self):
        try:
            self._load()
            self._check_backpack()
            if self.done == self.TOTAL // self.AMOUNT:
                self.announce("Dragon Rock donations already complete: 20,000 gold. No additional gold spent.")
                return
            self.say(f"Dragon Rock bot started. {self.done * self.AMOUNT:,} of 20,000 gold donated. "
                     "Each donation is 10 gold. Press Control D to stop after the current donation.")
            while self.done < self.TOTAL // self.AMOUNT and not self.stop_check():
                self._donate()
                if self.scroll_found:
                    raise ScrollFound("Scroll found. Dragon Rock donations stopped.")
                self._check_backpack()
                if self.done % self.announce_every == 0:
                    self.announce(f"Dragon Rock: {self.done:,} donations; "
                                  f"{self.done * self.AMOUNT:,} of 20,000 gold donated.")
            label = "complete" if self.done * self.AMOUNT == self.TOTAL else "stopped"
            self.announce(f"Dragon Rock donations {label}. {self.done:,} donations, "
                          f"{self.done * self.AMOUNT:,} of 20,000 gold confirmed.")
        except ScrollFound as ex:
            msg = f"{ex} Confirmed total: {self.done * self.AMOUNT:,} gold."
            if self.pending is not None:
                msg += " The last donation is unverified."
            self._log(msg)
            self.announce(msg)
        except Exception as ex:
            msg = f"Dragon Rock bot stopped: {type(ex).__name__}: {str(ex).rstrip('.')}. "
            msg += f"Confirmed total: {self.done * self.AMOUNT:,} gold."
            if self.pending is not None:
                if isinstance(self.pending, dict) and self.pending.get("response_verified") is True:
                    msg += " One donation awaits balance verification; resume will only recheck its balance first."
                else:
                    msg += " One donation is unverified; automatic resume is blocked to prevent duplicate spending."
            self._log(msg)
            self.announce(msg)
