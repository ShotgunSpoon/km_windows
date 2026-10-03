"""Minimal Knight Manager client (reverse-engineered from app v3.0.137). See FINDINGS.md.
Usage: python km_client.py CODE            (8-char login code, e.g. ABCD-1234)
       python km_client.py --token          (reuse saved token, send a text: km_client.py --token "dragon lair")
"""
import json, os, sys, time, uuid, urllib.request, urllib.error
import threading
from km_storage import Accounts, DATA_DIR

_CALL_LOCK = threading.RLock()


def _redact(value):
    if isinstance(value, dict):
        return {k: "[private]" if "token" in k.lower() or k.lower() in {"endpoint", "keys", "code", "authorization", "private_key", "auth"} else _redact(v)
                for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value

BASE = "https://chat.knight-manager.com/"
LOG_DIR = str(DATA_DIR / "logs")


class KM:
    def __init__(self, account_id=None, fresh=False, store=None):
        self.store = store or Accounts()
        self.account_id = None if fresh else account_id or self.store.index().get("active")
        self.s = {} if fresh else self.store.load(self.account_id)
        self.s.setdefault("device", uuid.uuid4().hex)  # stable random per-install id (32 hex)
        self.log_dir = str(self.store.directory(self.account_id) / "logs")
        os.makedirs(self.log_dir, exist_ok=True)

    def _persist(self):
        if self.s.get("token"):
            self.account_id = self.store.save(self.s, self.account_id)
            self.log_dir = str(self.store.directory(self.account_id) / "logs")
            os.makedirs(self.log_dir, exist_ok=True)

    def call(self, method, path, body=None, retries=5):
        with _CALL_LOCK:
            return self._call(method, path, body, retries)

    def _call(self, method, path, body=None, retries=5):
        for _ in range(retries):
            req = urllib.request.Request(
                BASE + path, method=method,
                data=None if body is None else json.dumps(body).encode(),
                headers={"Content-Type": "application/json", "User-Agent": "okhttp/4.12.0",
                         "X-Client-Platform": "android", "X-Client-Build": "316",
                         "X-Client-Device": self.s["device"],
                         **({"Authorization": "Bearer " + self.s["token"]} if self.s.get("token") else {})})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    new = r.headers.get("X-Auth-Token")
                    if new:
                        self.s["token"] = new; self._persist()
                    data = json.loads(r.read() or b"{}")
            except urllib.error.HTTPError as e:
                data = json.loads(e.read() or b"{}") if e.headers.get("Content-Type", "").startswith("application/json") else {"error": str(e.code)}
                if data.get("error") == "zu_viele_zuege":       # rate limited
                    time.sleep(min(int(data.get("retryAfter") or 3), 60)); continue
                data["_http"] = e.code
                if e.code == 401:
                    self.s.pop("token", None)
                    if self.account_id:
                        self.store.save(self.s, self.account_id)
            with open(os.path.join(self.log_dir, "log.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": time.time(), "m": method, "p": path, "req": _redact(body), "res": _redact(data)}) + "\n")
            if path == "api/play" and not data.get("error") and data.get("_http", 200) < 400:
                callback = getattr(self, "on_reply", None)
                if callback:
                    callback(data)
            return data
        return {"error": "rate_limited"}

    def login(self, code):
        code = code.replace("-", "").upper()
        r = self.call("POST", "api/auth/voice-code", {"code": code[:4] + "-" + code[4:]})
        if r.get("token"):
            self.s["token"] = r["token"]; self.s["user"] = r.get("user"); self._persist()
        return r

    def say(self, text, locale="en-US"):
        """Send a typed command like the Play tab box. Returns (reply_text, raw)."""
        r = self.call("POST", "api/play", {"text": text, "locale": locale, "supports": ["timezone", "dorfzeitung"]})
        return self.reply_text(r), r

    def act(self, intent, slots=None, locale="en-US"):
        """Send a quick-action button press (intent + slots from a previous response)."""
        body = {"intent": intent, "locale": locale, "supports": ["timezone", "dorfzeitung"]}
        if slots:
            body["slots"] = slots
        r = self.call("POST", "api/play", body)
        return self.reply_text(r), r

    @staticmethod
    def reply_text(r):
        # "voice" blocks carry the dealer/narrator lines, prompts and running totals; only "sfx" is silent.
        txt = " ".join(b.get("text") or "" for b in r.get("blocks", []) if (b.get("kind") or "text").lower() != "sfx")
        return " ".join(txt.split())

    def open_game(self, locale="en-US"):
        """Open/resume the game with no utterance (what the app does on entering the Play tab)."""
        return self.call("POST", "api/play", {"locale": locale, "supports": ["timezone", "dorfzeitung"]})

    def session(self):
        return self.call("GET", "api/play/session?locale=en-US")

    def autofight_step(self, locale="en-US"):
        return self.call("POST", "api/play/autofight/step", {"action_id": uuid.uuid4().hex, "locale": locale})


if __name__ == "__main__":
    km = KM()
    a = sys.argv[1:]
    if a and a[0] != "--token":
        print(json.dumps(km.login(a[0]), indent=1)[:800])
    elif len(a) > 1:
        print(km.say(" ".join(a[1:]))[0])
    print(json.dumps(km.session(), indent=1)[:1500])
