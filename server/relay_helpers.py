"""
Helper utilities for managing the BLE relay subprocess.

Handles WSL ↔ Windows bridging, bleak availability checks,
and building the command lines to launch the relay script.
"""

import asyncio
import importlib.util
import os
import shlex
import shutil
import subprocess
from pathlib import Path


def relay_script() -> Path:
    """Absolute path to the relay main.py."""
    return Path(__file__).resolve().parents[1] / "ble-relay-server-python" / "main.py"


def relay_running(proc: subprocess.Popen | None) -> bool:
    return proc is not None and proc.poll() is None


def bleak_ready() -> bool:
    return importlib.util.find_spec("bleak") is not None


def is_wsl() -> bool:
    try:
        return "microsoft" in os.uname().release.lower()
    except Exception:
        return False


def windows_bridge_ready() -> bool:
    return is_wsl() and shutil.which("powershell.exe") is not None and shutil.which("wslpath") is not None


def wsl_to_windows_path(path: Path) -> str:
    return subprocess.check_output(["wslpath", "-w", str(path)], text=True).strip()


def build_windows_relay_cmd(keyword: str, scan_only: bool = False, timeout: float = 6.0) -> list[str]:
    relay_win = wsl_to_windows_path(relay_script())
    safe_keyword = keyword.replace('"', "").strip()
    parts = [
        "py -3",
        shlex.quote(relay_win).replace("'", '"'),
        "--device-name",
        shlex.quote(safe_keyword).replace("'", '"'),
        "--scan-timeout",
        str(timeout),
    ]
    if scan_only:
        parts.append("--scan-only")
    else:
        parts.extend(["--flask-url", '"http://localhost:5000/api/pulse"', "--disable-tcp"])
    cmdline = " ".join(parts)
    return ["powershell.exe", "-NoProfile", "-Command", cmdline]


def format_ble_backend_error(exc: Exception) -> str:
    text = str(exc)
    if "org.bluez" in text or "DBus.Error.ServiceUnknown" in text:
        return (
            "BLE backend unavailable in this Linux/WSL runtime (org.bluez missing). "
            "Run relay on Windows host Bluetooth, or enable BlueZ on native Linux."
        )
    return f"ble search failed: {text}"


def ble_backend_preflight(timeout: float = 2.0) -> str | None:
    if windows_bridge_ready():
        return None
    try:
        from bleak import BleakScanner

        asyncio.run(BleakScanner.discover(timeout=timeout))
        return None
    except Exception as e:
        return format_ble_backend_error(e)
