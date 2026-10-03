"""The game's packaged sounds and server-directed effects/ambience."""
import hashlib
import io
import os
from pathlib import Path
import queue
import threading
import urllib.parse
import urllib.request
import wave
from km_storage import DATA_DIR, RESOURCE_DIR

ROOT = RESOURCE_DIR
SOUNDS = ROOT / "assets" / "sounds"
CACHE = DATA_DIR / "sound_cache"

DEFAULT_SOUNDS = {
    "dm": "info", "group": "info", "party_msg": "welcome",
    "mention": "combat_tournament_horn", "channel": "fireball",
    "order": "orden_unlocked", "event": "sound_battle_order",
    "trade": "sound_coins", "gift": "open_package", "energy_full": "energy",
    "smith_done": "draw_sword", "fight_tournament": "fight_tournament_start",
    "hunt_tournament": "war_horn", "forest_plague": "donnerschlag",
    "tower": "battle_turm_1", "news": "herold",
    "banner_war_battle": "combat_tournament_bell", "letter": "open_letter",
}


def volume(value, default=1.0):
    try:
        n = float(value)
        return max(0.0, min(1.0, n / 100 if n > 1 else n))
    except (TypeError, ValueError):
        return default


def effect_urls(reply):
    return [b["url"] for b in reply.get("blocks", [])
            if (b.get("kind") or "").lower() == "sfx" and b.get("url")]


class GameAudio:
    def __init__(self, report=lambda msg: None):
        self.report = report
        self.jobs = queue.Queue()
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def play(self, name, channel="ui", gain=1.0):
        if name and name != "none":
            self.jobs.put(("play", name, channel, gain))

    def reply(self, reply):
        self.jobs.put(("reply", effect_urls(reply), reply.get("ambience") or []))

    def close(self):
        self.closed.set()
        if self.thread is not threading.current_thread():
            self.thread.join(25)

    def _path(self, source):
        parsed = urllib.parse.urlparse(source)
        if not parsed.scheme and not source.startswith("/"):
            # Notification IDs use push_*; full URLs use the server's exact clip.
            for stem in (source, "push_" + source):
                for ext in (".mp3", ".wav", ".m4a"):
                    p = SOUNDS / (stem + ext)
                    if p.is_file() and p.parent == SOUNDS:
                        return p
        url = urllib.parse.urljoin("https://chat.knight-manager.com/", source)
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in {
            "chat.knight-manager.com", "skill.knight-manager.com", "knight-manager.com",
            "www.knight-manager.com"}:
            raise ValueError("Unsupported game sound URL")
        CACHE.mkdir(parents=True, exist_ok=True)
        path = CACHE / (hashlib.sha256(url.encode()).hexdigest() + Path(parsed.path).suffix)
        if not path.exists():
            with urllib.request.urlopen(url, timeout=20) as response:
                data = response.read(16 * 1024 * 1024 + 1)
            if len(data) > 16 * 1024 * 1024:
                raise ValueError("Game sound exceeds cache limit")
            temp = path.with_suffix(path.suffix + ".tmp")
            temp.write_bytes(data)
            os.replace(temp, path)
        return path

    def _run(self):
        try:
            import pygame
            pygame.mixer.init(frequency=44100, size=-16, channels=2)
            pygame.mixer.set_num_channels(16)
        except Exception:
            self.report("Game audio is unavailable. Check the audio output device.")
            return
        sounds, pending, ambience = {}, [], {}

        def load(source):
            if source not in sounds:
                path = self._path(source)
                try:
                    sounds[source] = pygame.mixer.Sound(str(path))
                except pygame.error:
                    # AAC/m4a clips are present in the Android package.
                    import av
                    data = io.BytesIO()
                    with av.open(str(path)) as container, wave.open(data, "wb") as wav:
                        wav.setnchannels(2)
                        wav.setsampwidth(2)
                        wav.setframerate(44100)
                        converter = av.AudioResampler(format="s16", layout="stereo", rate=44100)
                        for frame in container.decode(audio=0):
                            for converted in converter.resample(frame):
                                wav.writeframes(converted.to_ndarray().tobytes())
                        for converted in converter.resample(None):
                            wav.writeframes(converted.to_ndarray().tobytes())
                    data.seek(0)
                    sounds[source] = pygame.mixer.Sound(file=data)
            return sounds[source]

        try:
            while not self.closed.is_set():
                try:
                    job = self.jobs.get(timeout=0.03)
                except queue.Empty:
                    job = None
                try:
                    if job and job[0] == "play":
                        _, source, target, gain = job
                        c = pygame.mixer.Channel(1 if target == "notification" else 0)
                        c.set_volume(gain)
                        c.play(load(source))
                    elif job and job[0] == "reply":
                        pending.extend(job[1])
                        desired = {a["url"]: a for a in job[2] if a.get("url")}
                        for source in list(ambience):
                            if source not in desired:
                                ambience.pop(source)[1].stop()
                        for source, spec in list(desired.items())[:12]:
                            if source not in ambience:
                                used = {pair[0] for pair in ambience.values()}
                                index = next(i for i in range(3, 15) if i not in used)
                                c = pygame.mixer.Channel(index)
                                c.set_volume(volume(spec.get("volume"), 0.25))
                                c.play(load(source), loops=-1 if spec.get("loop") else 0)
                                ambience[source] = (index, c)
                            else:
                                ambience[source][1].set_volume(volume(spec.get("volume"), 0.25))
                    if pending and not pygame.mixer.Channel(2).get_busy():
                        pygame.mixer.Channel(2).play(load(pending.pop(0)))
                except Exception as ex:
                    self.report(f"A game sound could not be played ({type(ex).__name__}).")
        finally:
            pygame.mixer.stop()
            pygame.mixer.quit()
