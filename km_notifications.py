"""Receive the game's actual Web Push messages, independent of window focus.

Uses Mozilla's Web Push connection protocol, not activity polling. Credentials
remain local; the game receives only the standard subscription endpoint/keys.
"""
import asyncio
import base64
import json
import os
from pathlib import Path
import secrets
import re
import threading
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
import http_ece

from km_audio import DEFAULT_SOUNDS
from km_storage import DATA_DIR, load_secret, save_secret

VAPID_KEY = "BAPZMfi_zQZ-WENqGV24cven2-VO9AI24n9mzV0qNik83YFTw5nHCSc825_wuHRGI4Y6CjhyDG8tRrXTbjHWr4Y"
PUSH_SERVER = "wss://push.services.mozilla.com/"
CREDENTIALS = DATA_DIR / "push.dat"


def b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def category(payload):
    for field in ("pushCategory", "category", "kind"):
        if payload.get(field) in DEFAULT_SOUNDS:
            return payload[field]
    tag = payload.get("tag") or ""
    for name in sorted(DEFAULT_SOUNDS, key=len, reverse=True):
        if tag == name or tag.startswith(("game-" + name, "game_" + name, name + "-")):
            return name
    if tag.startswith("mention-"):
        return "mention"
    if tag.startswith("party-"):
        return "party_msg"
    return None


def notification_sound(payload, choices):
    key = category(payload)
    selected = payload.get("sound") or choices.get(key, "default")
    if selected == "none":
        return None
    if selected == "default":
        selected = DEFAULT_SOUNDS.get(key, "info")
    return "push_" + selected if selected in set(DEFAULT_SOUNDS.values()) or not selected.startswith("https://") else selected


def decrypt_notification(message, credentials):
    key = serialization.load_pem_private_key(credentials["private_key"].encode(), password=None)
    headers = {k.lower(): v for k, v in message.get("headers", {}).items()}
    encoding = headers.get("encoding", headers.get("content-encoding", "aes128gcm"))
    kwargs = {"private_key": key, "auth_secret": unb64(credentials["auth"]), "version": encoding}
    if encoding == "aesgcm":
        def parameter(header, name):
            for part in header.replace(",", ";").split(";"):
                k, _, value = part.strip().partition("=")
                if k == name:
                    return unb64(value.strip('"'))
            raise ValueError("Missing Web Push encryption parameter")
        kwargs["salt"] = parameter(headers.get("encryption", ""), "salt")
        kwargs["dh"] = parameter(headers.get("crypto_key", headers.get("crypto-key", "")), "dh")
    data = http_ece.decrypt(unb64(message["data"]), **kwargs)
    payload = json.loads(data)
    if not isinstance(payload, dict):
        raise ValueError("Invalid notification payload")
    return payload


class Notifications:
    def __init__(self, km, deliver, status, path=None):
        self.km, self.deliver, self.status = km, deliver, status
        self.path = Path(path) if path else km.store.directory(km.account_id) / "push.dat"
        self.stop_event = threading.Event()
        self.thread = None
        self.choices = {}
        self.conversation_categories = {}

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=lambda: asyncio.run(self._run()), daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()

    def _save(self, state):
        save_secret(self.path, state)

    def _credentials(self):
        try:
            state = load_secret(self.path)
        except (OSError, ValueError):
            state = {}
        if not state.get("private_key"):
            key = ec.generate_private_key(ec.SECP256R1())
            state.update(private_key=key.private_bytes(serialization.Encoding.PEM,
                         serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode(),
                         p256dh=b64(key.public_key().public_bytes(serialization.Encoding.X962,
                                 serialization.PublicFormat.UncompressedPoint)), auth=b64(secrets.token_bytes(16)))
        state.setdefault("channel", str(uuid.uuid4()))
        state.setdefault("seen", [])
        self._save(state)
        return state

    async def _call(self, method, path, body=None):
        r = await asyncio.to_thread(self.km.call, method, path, body)
        if r.get("error") or r.get("_http", 200) >= 400 or r.get("success") is False:
            raise RuntimeError("Game notification settings request failed")
        return r

    async def _settings(self):
        prefs = await self._call("GET", "api/push/prefs")
        for group in ("global", "game"):
            for name, enabled in prefs.get(group, {}).items():
                if not enabled:
                    await self._call("PUT", "api/push/prefs", {"type": name, "enabled": True})
        for conv, enabled in prefs.get("channels", {}).items():
            if not enabled:
                await self._call("PUT", "api/push/prefs", {"type": "channel", "convId": int(conv), "enabled": True})
        conversations = await self._call("GET", "api/conversations?deviceId=" + self.km.s["device"])
        for conv in conversations.get("conversations", []):
            conv_id, kind = conv.get("conv_id"), conv.get("conv_type")
            if conv_id is None:
                continue
            selected = kind if kind in ("dm", "group") else "order" if conv.get("conv_order_id") else "channel"
            self.conversation_categories[str(conv_id)] = selected
            if kind == "channel" and str(conv_id) not in prefs.get("channels", {}):
                await self._call("PUT", "api/push/prefs", {"type": "channel", "convId": conv_id, "enabled": True})
        self.choices = (await self._call("GET", "api/push/sounds")).get("sounds", {})

    async def _notification(self, ws, message, state):
        version = str(message.get("version", ""))
        identity = message.get("channelID", "") + ":" + version
        if identity not in state["seen"] and message.get("data"):
            payload = decrypt_notification(message, state)
            conv = re.match(r"^conv-(\d+)(?:$|\|)", payload.get("tag") or "")
            if conv and not category(payload):
                selected = self.conversation_categories.get(conv.group(1))
                if selected:
                    payload["pushCategory"] = selected
            # Sync messages clear badges; they are not user notifications.
            if payload.get("syncKind") != "unread_sync":
                self.deliver(payload, notification_sound(payload, self.choices))
            state["seen"] = (state["seen"] + [identity])[-1000:]
            self._save(state)
        await ws.send_json({"messageType": "ack", "updates": [{"channelID": message["channelID"],
                            "version": message["version"], "code": 100}]})

    async def _connect(self, session, state):
        async with session.ws_connect(PUSH_SERVER, protocols=["push-notification"], heartbeat=30) as ws:
            hello = {"messageType": "hello", "use_webpush": True, "channelIDs": [state["channel"]]}
            if state.get("uaid"):
                hello["uaid"] = state["uaid"]
            await ws.send_json(hello)
            response = await ws.receive_json(timeout=20)
            if response.get("status") != 200 or not response.get("uaid"):
                raise RuntimeError("Push connection rejected")
            if state.get("uaid") != response["uaid"]:
                state.pop("endpoint", None)
            state["uaid"] = response["uaid"]
            self._save(state)
            if not state.get("endpoint"):
                await ws.send_json({"messageType": "register", "channelID": state["channel"], "key": VAPID_KEY})
                while True:
                    response = await ws.receive_json(timeout=20)
                    if response.get("messageType") == "notification":
                        await self._notification(ws, response, state)
                    elif response.get("messageType") == "register":
                        break
                if response.get("status") != 200 or not response.get("pushEndpoint"):
                    raise RuntimeError("Push subscription rejected")
                state["endpoint"] = response["pushEndpoint"]
                self._save(state)
            await self._call("POST", "api/push/subscribe", {"endpoint": state["endpoint"],
                "keys": {"p256dh": state["p256dh"], "auth": state["auth"]}})
            self.status("Notifications connected. All categories enabled.")
            while not self.stop_event.is_set():
                try:
                    message = await ws.receive_json(timeout=1)
                except asyncio.TimeoutError:
                    continue
                if message.get("messageType") == "notification":
                    await self._notification(ws, message, state)

    async def _run(self):
        from aiohttp import ClientSession
        state = self._credentials()
        failure_reported = False
        async with ClientSession() as session:
            while not self.stop_event.is_set():
                try:
                    await self._settings()
                    await self._connect(session, state)
                    failure_reported = False
                except Exception as ex:
                    if not self.stop_event.is_set() and not failure_reported:
                        self.status(f"Notifications disconnected ({type(ex).__name__}). Reconnecting.")
                        failure_reported = True
                    for _ in range(30):
                        if self.stop_event.is_set():
                            break
                        await asyncio.sleep(1)
