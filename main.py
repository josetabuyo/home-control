from __future__ import annotations

import asyncio
import base64
import logging
import os
import threading
import urllib.parse
from pathlib import Path

import httpx
import tinytuya
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

logger = logging.getLogger(__name__)

load_dotenv()

PLUG_IP         = os.getenv("PLUG_IP", "")
PLUG_DEVICE_ID  = os.getenv("PLUG_DEVICE_ID", "")
PLUG_LOCAL_KEY  = os.getenv("PLUG_LOCAL_KEY", "")
PLUG_VERSION    = float(os.getenv("PLUG_VERSION", "3.3"))
PLUG_NAME       = os.getenv("PLUG_NAME", "Smart Plug")

LIGHT_IP        = os.getenv("LIGHT_IP", "")
LIGHT_DEVICE_ID = os.getenv("LIGHT_DEVICE_ID", "")
LIGHT_LOCAL_KEY = os.getenv("LIGHT_LOCAL_KEY", "")
LIGHT_VERSION   = float(os.getenv("LIGHT_VERSION", "3.3"))
LIGHT_NAME      = os.getenv("LIGHT_NAME", "A60 RGBCW")

CAMERA_IP       = os.getenv("CAMERA_IP", "")
CAMERA_USER     = os.getenv("CAMERA_USER", "admin")
CAMERA_PASS     = os.getenv("CAMERA_PASS", "")
CAMERA_NAME     = os.getenv("CAMERA_NAME", "VisionONE")

app = FastAPI(title="Smart Home")


# ── Error messages ────────────────────────────────────────────────────────────

def _friendly(e: Exception) -> str:
    msg = str(e)
    if not PLUG_DEVICE_ID and "plug" in msg.lower():
        return "PLUG_DEVICE_ID no configurado — ejecutá tinytuya wizard"
    if not LIGHT_DEVICE_ID and "light" in msg.lower():
        return "LIGHT_DEVICE_ID no configurado — ejecutá tinytuya wizard"
    if "Payload" in msg or "payload" in msg or "ERR_PAYLOAD" in msg or "key or version" in msg.lower():
        return "LOCAL_KEY o versión incorrecta — posiblemente cambió tras re-emparejamiento. Corré: cd smart_home && python -m tinytuya wizard"
    if "Connection refused" in msg or "ConnectionRefused" in msg:
        return "Dispositivo rechazó la conexión — verificá que esté encendido y en la misma WiFi"
    if "timed out" in msg.lower() or "Elapsed" in msg or "timeout" in msg.lower():
        return "Tiempo de espera agotado — dispositivo no responde"
    if "key" in msg.lower() and ("invalid" in msg.lower() or "wrong" in msg.lower()):
        return "LOCAL_KEY incorrecta — revisá el .env"
    if "decryption" in msg.lower() or "decrypt" in msg.lower():
        return "Error de encriptación — probá cambiando PLUG_VERSION o LIGHT_VERSION en .env (3.3, 3.4, 3.5)"
    if not msg.strip() or msg == "None":
        return "Sin respuesta del dispositivo — verificá IP y que esté en la misma red"
    return msg[:150]


# ── Device factories ──────────────────────────────────────────────────────────

def _make_plug() -> tinytuya.OutletDevice:
    if not PLUG_DEVICE_ID or not PLUG_LOCAL_KEY:
        raise RuntimeError("PLUG_DEVICE_ID / PLUG_LOCAL_KEY no configurados en .env")
    d = tinytuya.OutletDevice(PLUG_DEVICE_ID, PLUG_IP, PLUG_LOCAL_KEY)
    d.set_version(PLUG_VERSION)
    d.set_socketPersistent(False)
    return d


def _make_light() -> tinytuya.BulbDevice:
    if not LIGHT_DEVICE_ID or not LIGHT_LOCAL_KEY:
        raise RuntimeError("LIGHT_DEVICE_ID / LIGHT_LOCAL_KEY no configurados en .env")
    d = tinytuya.BulbDevice(LIGHT_DEVICE_ID, LIGHT_IP, LIGHT_LOCAL_KEY)
    d.set_version(LIGHT_VERSION)
    d.set_socketPersistent(False)
    return d


# ── Tuya color helpers ────────────────────────────────────────────────────────

def _parse_tuya_color(hex_str: str) -> tuple[int, int, int]:
    """Parse Tuya 12-char HSV hex → (hue 0-360, sat 0-100, val 0-100)."""
    if not hex_str or len(hex_str) < 12:
        return 0, 0, 0
    h = int(hex_str[0:4], 16)          # 0–360
    s = round(int(hex_str[4:8], 16) / 10)   # 0–1000 → 0–100
    v = round(int(hex_str[8:12], 16) / 10)  # 0–1000 → 0–100
    return h, s, v


def _check_response(data: dict, label: str) -> dict:
    if not data:
        raise RuntimeError(f"Sin respuesta del {label}")
    if "Error" in data:
        # Prefer string message over numeric Err code
        raise RuntimeError(data["Error"])
    return data


def _check_write(data, label: str):
    # Estos dispositivos (v3.4) responden con dict en cada SET exitoso;
    # None significa que el comando no llegó (p.ej. LOCAL_KEY vencida)
    if not data:
        raise RuntimeError(f"Sin respuesta del {label} — el comando no se aplicó")
    if isinstance(data, dict) and "Error" in data:
        raise RuntimeError(data["Error"])
    return data


_plug_lock  = threading.Lock()
_light_lock = threading.Lock()


# ── Enchufe ───────────────────────────────────────────────────────────────────

def _plug_status_sync() -> dict:
    with _plug_lock:
        d = _make_plug()
        data = _check_response(d.status(), "enchufe")
    dps = data.get("dps", {})
    on = bool(dps.get("1", False))
    # Tuya plugs report power in DPS 19 (W×10) or DPS 9
    raw = dps.get("19") if dps.get("19") is not None else dps.get("9")
    power_w = round(raw / 10, 1) if raw is not None else None  # DPS 19 en 0.1W
    voltage_v = round(dps.get("20") / 10, 1) if dps.get("20") is not None else None
    return {"on": on, "name": PLUG_NAME, "power_w": power_w, "voltage_v": voltage_v}


def _plug_set_sync(state: bool) -> dict:
    with _plug_lock:
        d = _make_plug()
        _check_write(d.turn_on() if state else d.turn_off(), "enchufe")
    return {"on": state}


@app.get("/api/plug/status")
async def plug_status():
    try:
        return await asyncio.to_thread(_plug_status_sync)
    except Exception as e:
        raise HTTPException(502, detail=_friendly(e))


@app.post("/api/plug/on")
async def plug_on():
    try:
        return await asyncio.to_thread(_plug_set_sync, True)
    except Exception as e:
        raise HTTPException(502, detail=_friendly(e))


@app.post("/api/plug/off")
async def plug_off():
    try:
        return await asyncio.to_thread(_plug_set_sync, False)
    except Exception as e:
        raise HTTPException(502, detail=_friendly(e))


# ── Luz ───────────────────────────────────────────────────────────────────────

class ColorReq(BaseModel):
    hue: int        # 0–360
    saturation: int # 0–100
    brightness: int # 1–100

class TempReq(BaseModel):
    color_temp: int  # 0–100  (0=cálida, 100=fría)
    brightness: int  # 1–100

class BrightnessReq(BaseModel):
    brightness: int  # 1–100


def _light_status_sync() -> dict:
    with _light_lock:
        d = _make_light()
        data = _check_response(d.status(), "luz")
    dps = data.get("dps", {})
    # DPS keys: 20=switch, 21=mode, 22=brightness, 23=temp, 24=colour
    on   = bool(dps.get("20", dps.get("1", False)))
    mode = dps.get("21", "white")
    bri  = round(dps.get("22", 1000) / 10)   # 10–1000 → 1–100
    temp = round(dps.get("23", 0)    / 10)   # 0–1000  → 0–100
    hue, sat, v = _parse_tuya_color(dps.get("24", ""))
    if mode == "colour":
        bri = v
    return {
        "on": on, "name": LIGHT_NAME,
        "brightness": max(1, bri),
        "hue": hue, "saturation": sat,
        "color_temp": temp,
        "mode": mode,
    }


def _light_set_sync(state: bool) -> dict:
    with _light_lock:
        d = _make_light()
        _check_write(d.turn_on() if state else d.turn_off(), "luz")
    return {"on": state}


def _light_color_sync(hue: int, saturation: int, brightness: int) -> dict:
    with _light_lock:
        d = _make_light()
        _check_write(d.set_hsv(hue / 360, saturation / 100, brightness / 100), "luz")
    return {"hue": hue, "saturation": saturation, "brightness": brightness}


def _light_temp_sync(color_temp: int, brightness: int) -> dict:
    with _light_lock:
        d = _make_light()
        _check_write(d.set_white_percentage(brightness, color_temp), "luz")
    return {"color_temp": color_temp, "brightness": brightness}


def _light_brightness_sync(brightness: int) -> dict:
    with _light_lock:
        d = _make_light()
        _check_write(d.set_brightness_percentage(brightness), "luz")
    return {"brightness": brightness}


@app.get("/api/light/status")
async def light_status():
    try:
        return await asyncio.to_thread(_light_status_sync)
    except Exception as e:
        raise HTTPException(502, detail=_friendly(e))


@app.post("/api/light/on")
async def light_on():
    try:
        return await asyncio.to_thread(_light_set_sync, True)
    except Exception as e:
        raise HTTPException(502, detail=_friendly(e))


@app.post("/api/light/off")
async def light_off():
    try:
        return await asyncio.to_thread(_light_set_sync, False)
    except Exception as e:
        raise HTTPException(502, detail=_friendly(e))


@app.post("/api/light/color")
async def light_color(req: ColorReq):
    try:
        return await asyncio.to_thread(_light_color_sync, req.hue, req.saturation, req.brightness)
    except Exception as e:
        raise HTTPException(502, detail=_friendly(e))


@app.post("/api/light/temperature")
async def light_temperature(req: TempReq):
    try:
        return await asyncio.to_thread(_light_temp_sync, req.color_temp, req.brightness)
    except Exception as e:
        raise HTTPException(502, detail=_friendly(e))


@app.post("/api/light/brightness")
async def light_brightness(req: BrightnessReq):
    try:
        return await asyncio.to_thread(_light_brightness_sync, req.brightness)
    except Exception as e:
        raise HTTPException(502, detail=_friendly(e))


# ── Cámara v720 ───────────────────────────────────────────────────────────────

V720_RTSP_PATHS = [
    ("/live/ch00_0", "/live/ch00_1"),
    ("/11",          "/12"),
    ("/videoMain",   "/videoSub"),
]


@app.get("/api/camera/info")
async def camera_info():
    if not CAMERA_IP:
        raise HTTPException(404, detail="Cámara no configurada en .env")
    user  = urllib.parse.quote(CAMERA_USER, safe="")
    pwd   = urllib.parse.quote(CAMERA_PASS, safe="")
    creds = f"{user}:{pwd}@" if CAMERA_PASS else f"{user}:@"
    base  = f"rtsp://{creds}{CAMERA_IP}:554"
    return {
        "name":        CAMERA_NAME,
        "model":       "monitor2",
        "ip":          CAMERA_IP,
        "rtsp_hd":     f"{base}{V720_RTSP_PATHS[0][0]}",
        "rtsp_sd":     f"{base}{V720_RTSP_PATHS[0][1]}",
        "rtsp_alt_hd": f"{base}{V720_RTSP_PATHS[1][0]}",
        "rtsp_alt_sd": f"{base}{V720_RTSP_PATHS[1][1]}",
    }


@app.get("/api/camera/snapshot")
async def camera_snapshot():
    if not CAMERA_IP:
        raise HTTPException(404, detail="Cámara no configurada")
    user = urllib.parse.quote(CAMERA_USER, safe="")
    pwd  = urllib.parse.quote(CAMERA_PASS, safe="")
    candidates = [
        f"http://{CAMERA_IP}/cgi-bin/snapshot.cgi?user={user}&pwd={pwd}",
        f"http://{CAMERA_IP}/snapshot.jpg",
        f"http://{CAMERA_IP}/web/auto.jpg",
    ]
    async with httpx.AsyncClient(timeout=5) as client:
        for url in candidates:
            try:
                r = await client.get(url)
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                    return Response(content=r.content, media_type=r.headers["content-type"])
            except Exception:
                continue
    raise HTTPException(502, detail="Sin snapshot HTTP — usá RTSP con VLC")


# ── Voice Agent ───────────────────────────────────────────────────────────────

class VoiceRequest(BaseModel):
    audio_base64: str
    mime_type: str = "audio/webm"

class VoiceCommandRequest(BaseModel):
    text: str  # already-transcribed text from Web Speech API


@app.post("/api/voice/process")
async def process_voice(req: VoiceRequest):
    try:
        from voice_agent import process_voice_command
        audio_bytes = base64.b64decode(req.audio_base64)
        result = await process_voice_command(audio_bytes, req.mime_type)
        if "error" in result:
            raise HTTPException(400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(400, detail="Invalid base64 audio")
    except Exception as e:
        raise HTTPException(502, detail=str(e))


@app.post("/api/voice/command")
async def voice_command(req: VoiceCommandRequest):
    try:
        from voice_agent import process_text_command
        result = await process_text_command(req.text)
        if "error" in result:
            raise HTTPException(400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, detail=str(e))


# ── Config ───────────────────────────────────────────────────────────────────

@app.get("/api/config")
async def config():
    """Static device config — always available, no Tuya connection needed."""
    return {
        "plug":   {"name": PLUG_NAME},
        "light":  {"name": LIGHT_NAME},
        "camera": {"name": CAMERA_NAME},
    }


