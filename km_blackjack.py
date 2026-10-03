"""Blackjack bot for Knight Manager's tavern (hit/stand only; the game offers no double or split).

Decisions use the structured fields (expects_number, reprompt) rather than sentences, because the
game rephrases its prompts every hand. The card names in the text are used for dealer up-card and soft-hand tracking.
"""
import random
import re
import time

CARD = re.compile(r"\b(Ace|King|Queen|Jack|10|[2-9]) of (?:Clubs|Hearts|Spades|Diamonds)")
TOTAL = re.compile(r"(\d+) points")
# The game phrases outcomes many ways, so the outcome is read from the gold sentence:
#   win  : "You receive/get/win N gold pieces."       (N includes the stake; blackjack pays about 2.5x)
#   tie  : "... gets his stake/bet/wager of N gold pieces back/returned"  or "Equal score" / "tie"
#   loss : the hand ended with no payout at all (bust, or the house had more points)
PAYOUT = re.compile(r"(?:receive|gets?|win|won)\s+(?:his |her )?(?:stake |bet |wager )?(?:of )?(\d+) gold pieces( back| returned)?", re.I)
TIE = re.compile(r"equal score|\btie\b|\btied\b|\bpush\b", re.I)
DRAW_PROMPT = re.compile(r"draw another card|another card", re.I)
END_PROMPT = re.compile(r"another round|play(ing)? again|try again|try your luck", re.I)


def card_value(rank):
    return 11 if rank == "Ace" else 10 if rank in ("King", "Queen", "Jack") else int(rank)


def hand_state(ranks):
    """(total, is_soft) with aces as 1 unless one can count as 11."""
    hard = sum(1 if r == "Ace" else card_value(r) for r in ranks)
    if "Ace" in ranks and hard + 10 <= 21:
        return hard + 10, True
    return hard, False


def should_hit(total, soft, dealer):
    """Basic strategy restricted to hit/stand. dealer: 2..11 (Ace = 11)."""
    if soft:
        if total <= 17:
            return True
        if total == 18:
            return dealer >= 9          # stand vs 2-8, hit vs 9, 10, Ace
        return False
    if total <= 11:
        return True
    if total == 12:
        return dealer not in (4, 5, 6)
    if total <= 16:
        return dealer >= 7
    return False


def classify(text, bet=0):
    """Returns (result, net_gold) for a finished hand's reply text."""
    pay = PAYOUT.search(text)
    if pay and pay.group(2):
        return "tied", 0
    if pay:
        return "won", int(pay.group(1)) - bet
    if TIE.search(text):
        return "tied", 0
    return "lost", -bet


class BlackjackBot:
    def __init__(self, km, hands=5000, bet_min=10, bet_max=50, announce_every=50,
                 say=print, announce=print, log_path=None, stop_check=lambda: False):
        self.km, self.hands, self.bet_min, self.bet_max = km, hands, bet_min, bet_max
        self.announce_every, self.say, self.announce = announce_every, say, announce
        self.log_path, self.stop_check = log_path, stop_check
        self.stats = {"won": 0, "lost": 0, "tied": 0, "other": 0}
        self.done = 0
        self.start_gold = None
        self.net = 0
        self.bet = 0

    # ---- io helpers
    def _gold(self):
        p = self.km.call("GET", "api/knight/profile")
        d = p.get("data", p) if isinstance(p, dict) else {}
        g = d.get("gold")
        return g if isinstance(g, int) else None

    def _log(self, msg):
        if self.log_path:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")

    def _send(self, text):
        txt, r = self.km.say(text)
        self._log(f"SEND {text!r} -> {txt[:400]}")
        return txt, r

    @staticmethod
    def _flags(txt, r):
        rep = (r.get("reprompt") or "")
        return bool(r.get("expects_number")), rep, DRAW_PROMPT.search(rep) is not None

    # ---- main loop
    def run(self):
        self.start_gold = self._gold()
        gold_mark = self.start_gold
        self.say(f"Blackjack bot started: {self.hands} hands, bets {self.bet_min} to {self.bet_max}. Gold {self.start_gold}.")
        txt, r = self._send("tavern")
        txt, r = self._send("yes")
        bad = 0
        block = {"won": 0, "lost": 0, "tied": 0, "other": 0}
        while self.done < self.hands and not self.stop_check():
            if r.get("_http") == 401 or r.get("error"):
                self.say(f"Stopped: server error {r.get('error') or r.get('_http')}")
                break
            expects_num, rep, wants_draw = self._flags(txt, r)
            if expects_num:                                   # bet prompt
                gold = self._gold() if self.done % 10 == 0 else None
                if gold is not None and gold < self.bet_max:
                    self.say(f"Stopped: gold {gold} is below the maximum bet of {self.bet_max}.")
                    break
                bet = random.randint(self.bet_min, self.bet_max)
                self.bet = bet
                txt, r = self._send(str(bet))
                ranks = [m.group(1) for m in CARD.finditer(txt)]
                dealer = card_value(ranks[0]) if ranks else 10
                mine = ranks[1:3]
                bad = 0
                if not (r.get("expects_number") is False and DRAW_PROMPT.search(r.get("reprompt") or "")):
                    self._finish(txt, block)                 # hand ended on the deal (e.g. blackjack)
                    txt, r = self._after_hand(txt, r)
                    block = self._maybe_announce(block)
                    continue
                while True:                                   # play the hand
                    total, soft = hand_state(mine)
                    m = TOTAL.findall(r.get("reprompt") or "")
                    if m and int(m[-1]) != total and not (soft and int(m[-1]) == total):
                        total, soft = int(m[-1]), False       # trust the game's number if my count disagrees
                    hit = should_hit(total, soft, dealer)
                    txt, r = self._send("yes" if hit else "no")
                    if hit:
                        nc = CARD.search(txt)
                        if nc:
                            mine.append(nc.group(1))
                    if not (r.get("expects_number") is False and DRAW_PROMPT.search(r.get("reprompt") or "")):
                        break
                self._finish(txt, block)
                txt, r = self._after_hand(txt, r)
                block = self._maybe_announce(block)
            else:                                             # not where we expected: get back to the card table
                bad += 1
                if bad > 3:
                    self.say("Stopped: the game is in an unexpected state.")
                    self._log(f"UNEXPECTED {txt[:400]}")
                    break
                self._send("no")
                self._send("tavern")
                txt, r = self._send("yes")
        self._summary(final=True, start=self.start_gold)

    def _finish(self, txt, block):
        result, delta = classify(txt, self.bet)
        self.done += 1
        self.stats[result] += 1
        block[result] += 1
        self.net += delta
        self._log(f"HAND {self.done}: bet {self.bet} {result} {delta:+d} (running net {self.net})")

    def _after_hand(self, txt, r):
        """A finished hand ends with a play-again prompt: say yes to reach the next bet prompt."""
        return self._send("yes")

    def _maybe_announce(self, block):
        if self.done and self.done % self.announce_every == 0:
            self._summary(block=block)
            return {"won": 0, "lost": 0, "tied": 0, "other": 0}
        return block

    def _summary(self, block=None, final=False, start=None):
        s = self.stats
        net = self.net
        net_txt = f"{net}" if net >= 0 else f"negative {abs(net)}"
        other = f", {s['other']} unclassified" if s["other"] else ""
        msg = (f"{'Finished. ' if final else ''}{self.done} hands: {s['won']} won, {s['lost']} lost, "
               f"{s['tied']} tied{other}. Net gold {net_txt}.")
        self.announce(msg)
        self._log("SUMMARY " + msg)
        if final:
            g = self._gold()   # cross-check; the profile can lag a moment behind the last hand
            self._log(f"CHECK start gold {self.start_gold}, profile gold now {g}, tracked net {net}")
