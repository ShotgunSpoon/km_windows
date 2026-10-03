"""Windows toast delivery with silent system audio; game sounds are separate."""
import base64
import os
from pathlib import Path
import subprocess
import sys
import winreg
from xml.sax.saxutils import escape
from km_storage import DATA_DIR, atomic_write

APP_ID = "blindmg.km_windows"
SCRIPT = r'''param([string]$Payload,[string]$ApplicationId)
$ErrorActionPreference = 'Stop'
$null = [Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]
$null = [Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom,ContentType=WindowsRuntime]
$xml = [Windows.Data.Xml.Dom.XmlDocument]::new()
$xml.LoadXml([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Payload)))
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($ApplicationId).Show($toast)
'''


def show(title, body):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Software\\Classes\\AppUserModelId\\" + APP_ID) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "Knight Manager Windows")
    script = DATA_DIR / "helpers" / "toast.ps1"
    if not script.exists() or script.read_text(encoding="utf-8") != SCRIPT:
        atomic_write(script, SCRIPT.encode("utf-8"))
    xml = f'<toast><visual><binding template="ToastGeneric"><text>{escape(title)}</text><text>{escape(body)}</text></binding></visual><audio silent="true"/></toast>'
    payload = base64.b64encode(xml.encode("utf-8")).decode("ascii")
    return subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                             "-WindowStyle", "Hidden", "-File", str(script), "-Payload", payload,
                             "-ApplicationId", APP_ID], creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)
