"""Validated preferences and shared routing for game and bot notifications."""
from copy import deepcopy
from km_storage import DATA_DIR, read_json, write_json

DEFAULTS = {
    "blackjack_hands": 5000, "blackjack_every": 50,
    "blackjack_bet_min": 10, "blackjack_bet_max": 50,
    "auto_update": True, "game_sounds": True, "bot_game_sounds": False,
    "notifications": {"enabled": True, "log": True, "speech": True, "sound": True, "toast": True, "categories": {}},
    "bot_notifications": {"enabled": True, "log": True, "speech": True, "sound": False, "toast": False},
}


def validate(settings):
    result = deepcopy(DEFAULTS)
    for key, default in DEFAULTS.items():
        if isinstance(default, dict):
            given = settings.get(key, {})
            if isinstance(given, dict):
                for field, value in given.items():
                    if field == "categories" and isinstance(value, dict):
                        result[key][field] = {k: bool(v) for k, v in value.items()}
                    elif field in default and isinstance(value, bool):
                        result[key][field] = value
        elif isinstance(default, bool):
            if isinstance(settings.get(key), bool): result[key] = settings[key]
        else:
            value = settings.get(key, default)
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 1000000:
                raise ValueError(f"Invalid {key.replace('_', ' ')}")
            result[key] = value
    if result["blackjack_bet_min"] > result["blackjack_bet_max"]:
        raise ValueError("Minimum bet must not exceed maximum bet")
    return result


class Preferences:
    def __init__(self, path=DATA_DIR / "preferences.json"):
        self.path = path
        self.values = validate(read_json(path))

    def save(self, value):
        self.values = validate(value)
        write_json(self.path, self.values)

    def route(self, bot=False, category=None):
        group = self.values["bot_notifications" if bot else "notifications"]
        enabled = group["enabled"] and group.get("categories", {}).get(category, True)
        return {key: bool(enabled and group[key]) for key in ("log", "speech", "sound", "toast")}
