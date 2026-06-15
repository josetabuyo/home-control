"""Tests unitarios para user_handler.py.

Cubre:
  - _hex_to_hsb en aislamiento
  - _dispatch con comandos de texto (httpx mockeado)
  - handle() con mensajes de voz (evento falso con MagicMock)
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from user_handler import _dispatch, _hex_to_hsb, handle


# ─── helpers ──────────────────────────────────────────────────────────────────

def _make_httpx_mock(get_json=None, post_json=None):
    """Devuelve un patcher para httpx.AsyncClient como context manager async.

    get_json  : dict que .json() devuelve para GET requests (o lista de dicts para múltiples llamadas).
    post_json : dict que .json() devuelve para POST requests (raramente usado).
    """
    mock_response_get = MagicMock()
    if isinstance(get_json, list):
        mock_response_get.json.side_effect = get_json
    else:
        mock_response_get.json.return_value = get_json or {}

    mock_response_post = MagicMock()
    mock_response_post.raise_for_status = MagicMock()
    mock_response_post.json.return_value = post_json or {}

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response_post)
    mock_client.get = AsyncMock(return_value=mock_response_get)

    # async context manager: `async with httpx.AsyncClient(...) as c`
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    patcher = patch("user_handler.httpx.AsyncClient", return_value=mock_cm)
    return patcher, mock_client


# ─── _hex_to_hsb ──────────────────────────────────────────────────────────────

def test_hex_to_hsb_rojo():
    h, s, b = _hex_to_hsb('#ff4040')
    assert h == 0        # hue rojo
    assert s > 50
    assert b == 100


def test_hex_to_hsb_teal():
    h, s, b = _hex_to_hsb('#00d4aa')
    assert 150 < h < 190
    assert s > 50
    assert b > 50


def test_hex_to_hsb_blanco():
    h, s, b = _hex_to_hsb('#ffffff')
    assert s == 0
    assert b == 100


def test_hex_to_hsb_negro():
    h, s, b = _hex_to_hsb('#000000')
    # negro: brillo mínimo (max_c = 0 → bri = max(1, 0) = 1), sat = 0
    assert s == 0
    assert b == 1


def test_hex_to_hsb_invalido():
    with pytest.raises(ValueError):
        _hex_to_hsb('#zzz')


def test_hex_to_hsb_invalido_longitud():
    with pytest.raises(ValueError):
        _hex_to_hsb('#abc')   # 3 chars → inválido


# ─── _dispatch: enchufe ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_enchufe_on():
    patcher, mock_client = _make_httpx_mock()
    with patcher:
        result = await _dispatch("enchufe on")
    d = json.loads(result)
    assert d["ok"] is True
    mock_client.post.assert_awaited_once()
    url = mock_client.post.call_args[0][0]
    assert url.endswith("/api/plug/on")


@pytest.mark.asyncio
async def test_dispatch_enchufe_off():
    patcher, mock_client = _make_httpx_mock()
    with patcher:
        result = await _dispatch("enchufe off")
    d = json.loads(result)
    assert d["ok"] is True
    mock_client.post.assert_awaited_once()
    url = mock_client.post.call_args[0][0]
    assert url.endswith("/api/plug/off")


# ─── _dispatch: luz ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_luz_off():
    patcher, mock_client = _make_httpx_mock()
    with patcher:
        result = await _dispatch("luz off")
    d = json.loads(result)
    assert d["ok"] is True
    mock_client.post.assert_awaited_once()
    url = mock_client.post.call_args[0][0]
    assert url.endswith("/api/light/off")


@pytest.mark.asyncio
async def test_dispatch_luz_on():
    patcher, mock_client = _make_httpx_mock()
    with patcher:
        result = await _dispatch("luz on")
    d = json.loads(result)
    assert d["ok"] is True
    url = mock_client.post.call_args[0][0]
    assert url.endswith("/api/light/on")


# ─── _dispatch: brillo ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_brillo_50():
    patcher, mock_client = _make_httpx_mock()
    with patcher:
        result = await _dispatch("brillo 50")
    d = json.loads(result)
    assert d["ok"] is True
    url = mock_client.post.call_args[0][0]
    assert url.endswith("/api/light/brightness")
    sent_json = mock_client.post.call_args[1]["json"]
    assert sent_json == {"brightness": 50}


@pytest.mark.asyncio
async def test_dispatch_brillo_clamp_max():
    """Valores > 100 se clampean a 100."""
    patcher, mock_client = _make_httpx_mock()
    with patcher:
        result = await _dispatch("brillo 999")
    d = json.loads(result)
    assert d["ok"] is True
    sent_json = mock_client.post.call_args[1]["json"]
    assert sent_json == {"brightness": 100}


@pytest.mark.asyncio
async def test_dispatch_brillo_clamp_min():
    """Valores < 1 se clampean a 1."""
    patcher, mock_client = _make_httpx_mock()
    with patcher:
        result = await _dispatch("brillo 0")
    d = json.loads(result)
    assert d["ok"] is True
    sent_json = mock_client.post.call_args[1]["json"]
    assert sent_json == {"brightness": 1}


# ─── _dispatch: status ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_status():
    plug_status  = {"on": True,  "device": "plug"}
    light_status = {"on": False, "device": "light"}
    # get se llama 2 veces: primero plug, luego light
    patcher, mock_client = _make_httpx_mock(get_json=[plug_status, light_status])
    with patcher:
        result = await _dispatch("status")
    d = json.loads(result)
    assert d["ok"] is True
    assert d["type"] == "status"
    assert d["plug"] == plug_status
    assert d["light"] == light_status
    assert mock_client.get.await_count == 2


# ─── _dispatch: color ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_color_valido():
    patcher, mock_client = _make_httpx_mock()
    with patcher:
        result = await _dispatch("color #ff4040")
    d = json.loads(result)
    assert d["ok"] is True
    url = mock_client.post.call_args[0][0]
    assert url.endswith("/api/light/color")
    sent_json = mock_client.post.call_args[1]["json"]
    assert sent_json["hue"] == 0
    assert sent_json["saturation"] > 50
    assert sent_json["brightness"] == 100


@pytest.mark.asyncio
async def test_dispatch_color_invalido_no_crashea():
    """Color hex inválido → ok:false, sin excepción."""
    patcher, mock_client = _make_httpx_mock()
    with patcher:
        result = await _dispatch("color #zzz")
    d = json.loads(result)
    assert d["ok"] is False
    assert "error" in d
    mock_client.post.assert_not_awaited()


# ─── _dispatch: temperatura ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_temperatura_4000():
    patcher, mock_client = _make_httpx_mock()
    with patcher:
        result = await _dispatch("temperatura 4000")
    d = json.loads(result)
    assert d["ok"] is True
    url = mock_client.post.call_args[0][0]
    assert url.endswith("/api/light/temperature")
    sent_json = mock_client.post.call_args[1]["json"]
    # color_temp = round((4000 - 2500) / 40) = round(37.5) = 38
    assert sent_json["color_temp"] == 38
    assert sent_json["brightness"] == 100


@pytest.mark.asyncio
async def test_dispatch_temperatura_clamp_alto():
    """Kelvin > 6500 se clampea a 6500 → color_temp = round((6500-2500)/40) = 100."""
    patcher, mock_client = _make_httpx_mock()
    with patcher:
        result = await _dispatch("temperatura 9000")
    d = json.loads(result)
    assert d["ok"] is True
    sent_json = mock_client.post.call_args[1]["json"]
    assert sent_json["color_temp"] == 100


@pytest.mark.asyncio
async def test_dispatch_temperatura_clamp_bajo():
    """Kelvin < 2500 se clampea a 2500 → color_temp = 0."""
    patcher, mock_client = _make_httpx_mock()
    with patcher:
        result = await _dispatch("temperatura 1000")
    d = json.loads(result)
    assert d["ok"] is True
    sent_json = mock_client.post.call_args[1]["json"]
    assert sent_json["color_temp"] == 0


# ─── handle(): mensaje de voz ─────────────────────────────────────────────────

def _make_voice_event(fake_audio: bytes = b"fake_audio", has_voice: bool = True):
    """Construye un evento Telegram falso con mensaje de voz."""
    msg = MagicMock()
    msg.voice = MagicMock() if has_voice else None
    msg.audio = None
    msg.document = None

    event = MagicMock()
    event.message = msg
    event.download_media = AsyncMock(return_value=fake_audio)
    event.reply = AsyncMock()
    return event


@pytest.mark.asyncio
async def test_handle_voice_message():
    fake_audio = b"fake_audio_bytes"
    event = _make_voice_event(fake_audio=fake_audio)

    voice_api_response = {
        "transcription": "prendé la luz",
        "response": "Luz encendida",
        "success": True,
    }

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = voice_api_response

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("user_handler.httpx.AsyncClient", return_value=mock_cm):
        await handle(event)

    # Verificar que event.reply fue llamado
    event.reply.assert_awaited_once()
    reply_arg = event.reply.call_args[0][0]
    d = json.loads(reply_arg)

    assert d["ok"] is True
    assert d["type"] == "voice"
    assert d["transcription"] == "prendé la luz"
    assert d["msg"] == "Luz encendida"

    # Verificar que se llamó /api/voice/process
    url = mock_client.post.call_args[0][0]
    assert url.endswith("/api/voice/process")

    # Verificar que se pasó audio en base64
    import base64
    sent_json = mock_client.post.call_args[1]["json"]
    assert base64.b64decode(sent_json["audio_base64"]) == fake_audio
    assert sent_json["mime_type"] == "audio/ogg"


@pytest.mark.asyncio
async def test_handle_voice_error_responde_ok_false():
    """Si la API de voz falla, handle() responde con ok:false sin crashear."""
    event = _make_voice_event()

    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=Exception("timeout"))

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("user_handler.httpx.AsyncClient", return_value=mock_cm):
        await handle(event)

    event.reply.assert_awaited_once()
    d = json.loads(event.reply.call_args[0][0])
    assert d["ok"] is False
    assert "error" in d


# ─── handle(): mensaje de texto ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_texto_enchufe_on():
    """handle() con texto delega a _dispatch y hace reply con el resultado."""
    msg = MagicMock()
    msg.voice = None
    msg.audio = None
    msg.document = None

    event = MagicMock()
    event.message = msg
    event.text = "enchufe on"
    event.reply = AsyncMock()

    patcher, mock_client = _make_httpx_mock()
    with patcher:
        await handle(event)

    event.reply.assert_awaited_once()
    d = json.loads(event.reply.call_args[0][0])
    assert d["ok"] is True


@pytest.mark.asyncio
async def test_handle_texto_vacio_no_responde():
    """Texto vacío → handle() no llama event.reply."""
    msg = MagicMock()
    msg.voice = None
    msg.audio = None
    msg.document = None

    event = MagicMock()
    event.message = msg
    event.text = "   "
    event.reply = AsyncMock()

    await handle(event)
    event.reply.assert_not_awaited()
