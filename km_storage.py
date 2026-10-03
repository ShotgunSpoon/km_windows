"""Writable AppData paths, Windows-protected credentials and isolated accounts."""
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import uuid

DATA_DIR = Path(os.environ.get("KM_DATA_DIR") or Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "blindmg" / "km_python")
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
_LOCK = threading.RLock()


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_bytes(data)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {} if default is None else default


def write_json(path, data):
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data, decrypt=False):
    if os.name != "nt":
        raise RuntimeError("Saved credentials require Windows")
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    if decrypt:
        fn = crypt.CryptUnprotectData
        args = (ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        fn = crypt.CryptProtectData
        args = (ctypes.byref(source), "KM Windows credentials", None, None, None, 1, ctypes.byref(target))
    fn.restype = wintypes.BOOL
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p if decrypt else wintypes.LPCWSTR,
                   ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    if not fn(*args):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        free = ctypes.WinDLL("kernel32", use_last_error=True).LocalFree
        free.argtypes = [ctypes.c_void_p]
        free(ctypes.cast(target.data, ctypes.c_void_p))


def save_secret(path, value):
    atomic_write(path, _dpapi(json.dumps(value).encode("utf-8")))


def load_secret(path):
    try:
        return json.loads(_dpapi(Path(path).read_bytes(), decrypt=True))
    except FileNotFoundError:
        return {}


class Accounts:
    def __init__(self, root=DATA_DIR, migrate=True):
        self.root = Path(root)
        self.index_path = self.root / "accounts.json"
        self.root.mkdir(parents=True, exist_ok=True)
        if migrate and not (self.root / "migration.json").exists():
            self._migrate()

    def index(self):
        return read_json(self.index_path, {"active": None, "accounts": {}})

    def directory(self, account_id=None):
        account_id = account_id or self.index().get("active") or "signed_out"
        if not account_id.replace("_", "").isalnum():
            raise ValueError("Invalid account identifier")
        path = self.root / "accounts" / account_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def load(self, account_id=None):
        account_id = account_id or self.index().get("active")
        return load_secret(self.directory(account_id) / "session.dat") if account_id else {}

    def save(self, session, account_id=None, activate=True):
        with _LOCK:
            user = session.get("user") or {}
            identity = str(user.get("id") or session.get("device"))
            account_id = account_id or "account_" + hashlib.sha256(identity.encode()).hexdigest()[:24]
            save_secret(self.directory(account_id) / "session.dat", session)
            index = self.index()
            index["accounts"][account_id] = {"name": user.get("name") or "Saved account", "id": user.get("id")}
            if activate:
                index["active"] = account_id
            write_json(self.index_path, index)
            return account_id

    def activate(self, account_id):
        with _LOCK:
            index = self.index()
            if account_id is not None and account_id not in index["accounts"]:
                raise ValueError("Account not found")
            index["active"] = account_id
            write_json(self.index_path, index)

    def _migrate(self):
        legacy_root = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
        legacy = legacy_root / "km_state.json"
        if legacy.exists():
            session = read_json(legacy)
            if session.get("token"):
                account_id = self.save(session)
                folder = self.directory(account_id)
                push = legacy_root / "km_push_credentials.json"
                if push.exists():
                    value = read_json(push)
                    save_secret(folder / "push.dat", value)
                    if load_secret(folder / "push.dat") != value:
                        raise RuntimeError("Notification credential migration failed")
                    push.unlink()
                if self.load(account_id) != session:
                    raise RuntimeError("Login migration failed")
                legacy.unlink()
                logs = legacy_root / "probe_logs"
                if logs.exists() and not (folder / "logs").exists():
                    shutil.move(str(logs), str(folder / "logs"))
                cache = legacy_root / "assets" / "sound_cache"
                if cache.exists() and not (self.root / "sound_cache").exists():
                    shutil.move(str(cache), str(self.root / "sound_cache"))
        write_json(self.root / "migration.json", {"completed": True})
