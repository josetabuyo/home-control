"""Scan the LAN for known Tuya devices and update .env if IPs changed."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import tinytuya
from dotenv import dotenv_values

ENV_PATH = Path(__file__).parent / ".env"

KNOWN_KEYS = {
    "PLUG_DEVICE_ID":  ("PLUG_IP",   "PLUG_VERSION"),
    "LIGHT_DEVICE_ID": ("LIGHT_IP",  "LIGHT_VERSION"),
}


def scan() -> dict[str, dict]:
    """Return {gwId: {ip, version}} for all devices found on the LAN."""
    raw = tinytuya.deviceScan(verbose=False, maxretry=20, color=False)
    return {
        info["gwId"]: {"ip": ip, "version": info.get("version", "")}
        for ip, info in raw.items()
        if "gwId" in info
    }


def patch_env(path: Path, updates: dict[str, str]) -> bool:
    """Replace key=value lines in .env. Returns True if anything changed."""
    text = path.read_text()
    changed = False
    for key, new_val in updates.items():
        pattern = rf"^({re.escape(key)}=).*$"
        new_line = rf"\g<1>{new_val}"
        new_text = re.sub(pattern, new_line, text, flags=re.MULTILINE)
        if new_text != text:
            text = new_text
            changed = True
    if changed:
        path.write_text(text)
    return changed


def main() -> int:
    cfg = dotenv_values(ENV_PATH)

    print("Scanning LAN for Tuya devices…", flush=True)
    found = scan()

    if not found:
        print("  No devices found — is the Mac on the same WiFi?", file=sys.stderr)
        return 1

    updates: dict[str, str] = {}
    ok = True

    for id_key, (ip_key, ver_key) in KNOWN_KEYS.items():
        device_id = cfg.get(id_key, "")
        if not device_id:
            continue

        if device_id not in found:
            print(f"  ✗ {id_key}={device_id} not found on LAN", file=sys.stderr)
            ok = False
            continue

        info       = found[device_id]
        current_ip  = cfg.get(ip_key, "")
        current_ver = cfg.get(ver_key, "")

        if info["ip"] != current_ip:
            print(f"  {ip_key}: {current_ip} → {info['ip']}")
            updates[ip_key] = info["ip"]
        else:
            print(f"  {ip_key}: {current_ip} (unchanged)")

        if info["version"] and info["version"] != current_ver:
            print(f"  {ver_key}: {current_ver} → {info['version']}")
            updates[ver_key] = info["version"]

    if updates:
        patch_env(ENV_PATH, updates)
        print(f"  .env updated ({', '.join(updates)})")
        return 2  # IPs changed — caller should restart the server

    print("  .env is up to date")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
