import asyncio
import json
from pathlib import Path
import tempfile
import unittest

import http_ece
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from km_audio import effect_urls, volume, DEFAULT_SOUNDS
from km_notifications import Notifications, b64, unb64, decrypt_notification, notification_sound


class FakeKM:
    s = {"device": "test-device"}

    def __init__(self):
        self.calls = []

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "api/push/prefs" and method == "GET":
            return {"global": {"dm": True, "group": False}, "game": {"energy_full": False}, "channels": {"7": False}}
        if path.startswith("api/conversations"):
            return {"conversations": [{"conv_id": 7, "conv_type": "channel"}, {"conv_id": 8, "conv_type": "dm"}]}
        if path == "api/push/sounds":
            return {"sounds": {"dm": "unlock_door"}}
        return {"success": True}


class WebPushTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.delivered = []
        self.km = FakeKM()
        self.receiver = Notifications(self.km, lambda *args: self.delivered.append(args), lambda msg: None,
                                      Path(self.temp.name) / "credentials.json")
        self.state = self.receiver._credentials()

    def tearDown(self):
        self.temp.cleanup()

    def encrypt(self, payload, version="v1"):
        key = serialization.load_pem_private_key(self.state["private_key"].encode(), password=None)
        data = http_ece.encrypt(json.dumps(payload).encode(), dh=unb64(self.state["p256dh"]),
                                private_key=ec.generate_private_key(ec.SECP256R1()),
                                auth_secret=unb64(self.state["auth"]), version="aes128gcm")
        return {"channelID": self.state["channel"], "version": version, "data": b64(data),
                "headers": {"encoding": "aes128gcm"}}

    def test_encrypted_game_notification_roundtrip(self):
        payload = {"title": "Knight Manager", "body": "Energy is full.", "tag": "game-energy_full"}
        self.assertEqual(decrypt_notification(self.encrypt(payload), self.state), payload)

    def test_duplicates_acknowledged_without_repeated_speech(self):
        class WS:
            def __init__(self): self.acks = []
            async def send_json(self, data): self.acks.append(data)
        ws = WS()
        msg = self.encrypt({"title": "Game", "body": "New letter", "tag": "game-letter"})
        asyncio.run(self.receiver._notification(ws, msg, self.state))
        asyncio.run(self.receiver._notification(ws, msg, self.state))
        self.assertEqual(len(self.delivered), 1)
        self.assertEqual(self.delivered[0][1], "push_open_letter")
        self.assertEqual(len(ws.acks), 2)
        restored = self.receiver._credentials()
        self.assertEqual(restored["seen"], self.state["seen"])

    def test_same_tag_new_delivery_is_not_suppressed(self):
        class WS:
            async def send_json(self, data): pass
        for version in ("v1", "v2"):
            asyncio.run(self.receiver._notification(WS(), self.encrypt({"body": "New letter", "tag": "game-letter"}, version), self.state))
        self.assertEqual(len(self.delivered), 2)

    def test_custom_dm_sound_uses_conversation_metadata(self):
        class WS:
            async def send_json(self, data): pass
        asyncio.run(self.receiver._settings())
        asyncio.run(self.receiver._notification(WS(), self.encrypt({"title": "Player", "body": "Hello", "tag": "conv-8"}), self.state))
        self.assertEqual(self.delivered[0][1], "push_unlock_door")

    def test_settings_enable_only_disabled_prefs(self):
        asyncio.run(self.receiver._settings())
        writes = [body for method, _, body in self.km.calls if method == "PUT"]
        self.assertIn({"type": "group", "enabled": True}, writes)
        self.assertIn({"type": "energy_full", "enabled": True}, writes)
        self.assertNotIn({"type": "dm", "enabled": True}, writes)
        self.assertTrue(all(body["enabled"] for body in writes))

    def test_sync_payload_is_not_announced(self):
        class WS:
            async def send_json(self, data): pass
        asyncio.run(self.receiver._notification(WS(), self.encrypt({"syncKind": "unread_sync"}), self.state))
        self.assertEqual(self.delivered, [])

    def test_notification_sound_respects_silent_selection(self):
        self.assertIsNone(notification_sound({"pushCategory": "letter", "sound": "none"}, {}))
        self.assertIsNone(notification_sound({"pushCategory": "dm"}, {"dm": "none"}))

    def test_all_native_default_sounds_exist(self):
        root = Path(__file__).resolve().parents[1] / "assets" / "sounds"
        for sound in DEFAULT_SOUNDS.values():
            self.assertTrue((root / f"push_{sound}.mp3").exists(), sound)

    def test_reply_plays_only_effects_not_narrator_audio(self):
        self.assertEqual(effect_urls({"blocks": [{"kind": "sfx", "url": "effect.mp3"},
                         {"kind": "voice", "url": "narrator.mp3", "text": "Narration"}]}), ["effect.mp3"])
        self.assertEqual(volume(25), .25)
        self.assertEqual(volume(.5), .5)


if __name__ == "__main__":
    unittest.main()
