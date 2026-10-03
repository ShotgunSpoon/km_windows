"""Download GitHub releases and replace a frozen EXE after it has exited."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.parse
import urllib.request

from km_storage import DATA_DIR, atomic_write
from km_version import VERSION, REPOSITORY

ASSET = "km_windows.exe"
API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"


def version_tuple(value):
    if not re.fullmatch(r"v?\d+\.\d+\.\d+", value):
        raise ValueError("Unsupported release version")
    return tuple(int(part) for part in value.lstrip("v").split("."))


def request(url):
    p = urllib.parse.urlparse(url)
    if p.scheme != "https" or p.hostname not in {"api.github.com", "github.com"}:
        raise ValueError("Untrusted update URL")
    if p.hostname == "github.com" and not p.path.startswith(f"/{REPOSITORY}/releases/download/"):
        raise ValueError("Update is outside the release repository")
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": f"km_windows/{VERSION}",
                                  "Accept": "application/vnd.github+json"}), timeout=30)


def select_release(release, current=VERSION):
    if release.get("draft") or release.get("prerelease"):
        return None
    tag = release.get("tag_name", "")
    if version_tuple(tag) <= version_tuple(current):
        return None
    assets = {a["name"]: a for a in release.get("assets", [])}
    exe = assets.get(ASSET)
    checksum = assets.get(ASSET + ".sha256")
    if not exe or not checksum:
        raise ValueError("Release lacks the executable or its checksum")
    return tag, exe, checksum


def check_and_download(current=VERSION):
    try:
        with request(API) as r:
            selected = select_release(json.load(r), current)
    except urllib.error.HTTPError as ex:
        if ex.code == 404:
            return None
        raise
    if not selected:
        return None
    tag, asset, checksum = selected
    with request(checksum["browser_download_url"]) as r:
        expected = r.read(4096).decode("ascii").split()[0].lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("Invalid release checksum")
    if asset.get("digest") and asset["digest"] != "sha256:" + expected:
        raise ValueError("GitHub asset digest does not match release checksum")
    destination = DATA_DIR / "updates" / tag / ASSET
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(".download")
    digest = hashlib.sha256()
    try:
        with request(asset["browser_download_url"]) as r, temp.open("wb") as f:
            size = 0
            while chunk := r.read(1024 * 1024):
                size += len(chunk)
                if size > 512 * 1024 * 1024:
                    raise ValueError("Update exceeds download limit")
                digest.update(chunk)
                f.write(chunk)
        if digest.hexdigest() != expected:
            raise ValueError("Downloaded update checksum mismatch")
        if asset.get("size") and size != asset["size"]:
            raise ValueError("Downloaded update size mismatch")
        os.replace(temp, destination)
    finally:
        if temp.exists(): temp.unlink()
    return {"version": tag, "path": str(destination), "sha256": expected}


APPLY_SCRIPT = r'''param([string]$Target,[string]$Source,[int]$ParentId,[string]$Expected)
$ErrorActionPreference = 'Stop'
try {
    try { $process = [Diagnostics.Process]::GetProcessById($ParentId); $process.WaitForExit() } catch [ArgumentException] { }
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { $actual = [BitConverter]::ToString($hasher.ComputeHash([IO.File]::ReadAllBytes($Source))).Replace('-', '').ToLower() }
    finally { $hasher.Dispose() }
    if ($actual -ne $Expected) { throw 'Update checksum mismatch' }
    $newFile = $Target + '.new'
    $backup = $Target + '.previous'
    [IO.File]::Copy($Source, $newFile, $true)
    [IO.File]::Replace($newFile, $Target, $backup, $true)
    $start = [Diagnostics.ProcessStartInfo]::new($Target)
    $start.UseShellExecute = $true
    [void][Diagnostics.Process]::Start($start)
} catch {
    [IO.File]::WriteAllText([IO.Path]::Combine([IO.Path]::GetDirectoryName($Source), 'update_error.log'), $_.Exception.Message)
}
'''


def prepare_install(update, target=None, parent_id=None, launch=True):
    target = Path(target or sys.executable).resolve()
    source = Path(update["path"]).resolve()
    if hashlib.sha256(source.read_bytes()).hexdigest() != update["sha256"]:
        raise ValueError("Staged update checksum mismatch")
    if source == target or target.suffix.lower() != ".exe":
        raise ValueError("Invalid update destination")
    script = source.parent / "apply_update.ps1"
    atomic_write(script, APPLY_SCRIPT.encode("utf-8"))
    command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
               "-WindowStyle", "Hidden", "-File", str(script), "-Target", str(target),
               "-Source", str(source), "-ParentId", str(parent_id or os.getpid()), "-Expected", update["sha256"]]
    if launch:
        subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)
    return command
