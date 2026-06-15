"""Handler para teli user listen — llama al Smart Home API local via HTTP.

Uso:
  cd ~/Development/Home/smart_home
  teli user listen punto_a_punto_bot --handler user_handler:handle

El FastAPI server debe estar corriendo en localhost:9000.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

API = os.getenv("SMARTHOME_URL", "http://localhost:9000")
PLUG_NAME  = os.getenv("PLUG_NAME",  "Enchufe")
LIGHT_NAME = os.getenv("LIGHT_NAME", "Luz")


def _hex_to_hsb(hex_str: str) -> tuple[int, int, int]:
    hex_str = hex_str.lstrip('#')
    if len(hex_str) != 6:
        raise ValueError(f"Color hex inválido: {hex_str!r}")
    r, g, b = (int(hex_str[i:i+2], 16) / 255 for i in (0, 2, 4))
    max_c, min_c = max(r, g, b), min(r, g, b)
    d = max_c - min_c
    if d == 0:
        h = 0
    elif max_c == r:
        h = ((g - b) / d) % 6
    elif max_c == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    hue = round(h * 60) % 360
    sat = round(d / max_c * 100) if max_c != 0 else 0
    bri = max(1, round(max_c * 100))
    return hue, sat, bri


async def _handle_voice(event) -> None:
    try:
        raw_msg = event._ev.message
        audio_bytes: bytes = await event._ev.download_media(file=bytes)
        if raw_msg.voice:
            mime_type = "audio/ogg"
        elif raw_msg.audio and raw_msg.audio.mime_type:
            mime_type = raw_msg.audio.mime_type
        elif raw_msg.document and raw_msg.document.mime_type:
            mime_type = raw_msg.document.mime_type
        else:
            mime_type = "audio/ogg"

        audio_b64 = base64.b64encode(audio_bytes).decode()
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                f"{API}/api/voice/process",
                json={"audio_base64": audio_b64, "mime_type": mime_type},
            )
            r.raise_for_status()
            d = r.json()

        response = json.dumps({
            "ok": True,
            "type": "voice",
            "transcription": d.get("transcription", ""),
            "msg": d.get("response", ""),
        }, ensure_ascii=False)
    except Exception as e:
        response = json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)

    await event.reply(response)


async def handle(event) -> None:
    raw_msg = event._ev.message
    print(f"[handler] text={raw_msg.text!r} voice={bool(raw_msg.voice)} audio={bool(raw_msg.audio)} doc={bool(raw_msg.document)} doc_mime={getattr(raw_msg.document, 'mime_type', None)}", flush=True)
    is_audio = bool(raw_msg.voice or raw_msg.audio or raw_msg.document) and not raw_msg.text
    if is_audio:
        await _handle_voice(event)
        return

    cmd = event.text.strip().lower()
    if not cmd:
        return
    try:
        response = await _dispatch(cmd)
    except Exception as e:
        response = json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
    await event.reply(response)


async def _dispatch(cmd: str) -> str:
    parts = cmd.split()
    verb  = parts[0] if parts else ""
    sub   = parts[1] if len(parts) > 1 else ""

    async with httpx.AsyncClient(timeout=8) as c:

        # ── enchufe ───────────────────────────────────────────────────────────
        if verb in ("enchufe", "plug"):
            if sub in ("on", "encender", "prender"):
                await c.post(f"{API}/api/plug/on")
                return _ok(f"{PLUG_NAME} encendido")
            if sub in ("off", "apagar"):
                await c.post(f"{API}/api/plug/off")
                return _ok(f"{PLUG_NAME} apagado")
            r = await c.get(f"{API}/api/plug/status")
            d = r.json()
            return json.dumps({"ok": True, "type": "plug_status", **d}, ensure_ascii=False)

        # ── luz ───────────────────────────────────────────────────────────────
        if verb in ("luz", "light"):
            if sub in ("on", "encender", "prender"):
                await c.post(f"{API}/api/light/on")
                return _ok(f"{LIGHT_NAME} encendida")
            if sub in ("off", "apagar"):
                await c.post(f"{API}/api/light/off")
                return _ok(f"{LIGHT_NAME} apagada")
            r = await c.get(f"{API}/api/light/status")
            d = r.json()
            return json.dumps({"ok": True, "type": "light_status", **d}, ensure_ascii=False)

        # ── brillo ────────────────────────────────────────────────────────────
        if verb in ("brillo", "brightness") and sub:
            val = max(1, min(100, int(sub)))
            await c.post(f"{API}/api/light/brightness", json={"brightness": val})
            return _ok(f"Brillo → {val}%")

        # ── status ────────────────────────────────────────────────────────────
        if verb in ("status", "estado"):
            rp = await c.get(f"{API}/api/plug/status")
            rl = await c.get(f"{API}/api/light/status")
            return json.dumps({
                "ok": True, "type": "status",
                "plug":  rp.json(),
                "light": rl.json(),
            }, ensure_ascii=False)

        # ── color ─────────────────────────────────────────────────────────────
        if verb in ("color", "colour") and sub:
            try:
                hue, saturation, brightness = _hex_to_hsb(sub)
            except ValueError as e:
                return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
            await c.post(f"{API}/api/light/color", json={"hue": hue, "saturation": saturation, "brightness": brightness})
            return _ok(f"Color → {sub}")

        # ── temperatura ───────────────────────────────────────────────────────
        if verb in ("temperatura", "temp", "temperature") and sub:
            try:
                kelvin = max(2500, min(6500, int(sub)))
            except ValueError:
                return json.dumps({"ok": False, "error": f"Kelvin inválido: {sub!r}"}, ensure_ascii=False)
            color_temp = round((kelvin - 2500) / 40)
            brightness_arg = int(parts[2]) if len(parts) > 2 else 100
            await c.post(f"{API}/api/light/temperature", json={"color_temp": color_temp, "brightness": brightness_arg})
            return _ok(f"Temperatura → {kelvin}K")

        # ── ayuda ─────────────────────────────────────────────────────────────
        if verb in ("ayuda", "help", "/start", "/help"):
            return _ok("Comandos: enchufe on/off · luz on/off · brillo N · color #hex · temperatura K · status")

    return _ok(f"No entendí: {cmd!r}. Escribí 'ayuda'.")


def _ok(msg: str) -> str:
    return json.dumps({"ok": True, "msg": msg}, ensure_ascii=False)
