"""
Voice Agent for Home Control

Processes voice commands to control smart devices (lights, plugs, etc.)
Uses Groq Llama with tool use for device control.
"""

import asyncio
import logging
import os
from io import BytesIO

logger = logging.getLogger(__name__)

# Model configuration (easy to swap)
_MODEL = "llama-3.3-70b-versatile"


# ── Device Control (sync wrappers for async context) ───────────────────────────

async def turn_on_light() -> None:
    from main import _light_set_sync
    await asyncio.to_thread(_light_set_sync, True)


async def turn_off_light() -> None:
    from main import _light_set_sync
    await asyncio.to_thread(_light_set_sync, False)


async def set_light_brightness(brightness: int) -> int:
    from main import _light_brightness_sync
    brightness = max(1, min(100, brightness))
    await asyncio.to_thread(_light_brightness_sync, brightness)
    return brightness


async def turn_on_plug() -> None:
    from main import _plug_set_sync
    await asyncio.to_thread(_plug_set_sync, True)


async def turn_off_plug() -> None:
    from main import _plug_set_sync
    await asyncio.to_thread(_plug_set_sync, False)


# ── Audio transcription ───────────────────────────────────────────────────────

def _transcribe_groq_sync(audio_bytes: bytes, mime_type: str = "audio/webm") -> str:
    from groq import Groq
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY not configured")

    ext = "mp4" if "mp4" in mime_type else "webm"
    client = Groq(api_key=api_key)
    audio_file = BytesIO(audio_bytes)
    audio_file.name = f"audio.{ext}"
    result = client.audio.transcriptions.create(
        model="whisper-large-v3",
        file=audio_file,
        language="es",
    )
    return result.text


# ── Voice agent ───────────────────────────────────────────────────────────────

async def _run_llm_command(text: str) -> dict:
    """Send already-transcribed text to LLM and execute device tools."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return {"error": "GROQ_API_KEY not configured"}

    from langchain_groq import ChatGroq
    from langchain_core.tools import tool

    @tool
    def turn_on_light_tool():
        """Turn on the smart light."""

    @tool
    def turn_off_light_tool():
        """Turn off the smart light."""

    @tool
    def set_light_brightness_tool(brightness: int):
        """Set light brightness (1-100%)."""

    @tool
    def turn_on_plug_tool():
        """Turn on the smart plug (patio lights)."""

    @tool
    def turn_off_plug_tool():
        """Turn off the smart plug (patio lights)."""

    tools = [turn_on_light_tool, turn_off_light_tool, set_light_brightness_tool,
             turn_on_plug_tool, turn_off_plug_tool]

    llm = ChatGroq(model=_MODEL, api_key=api_key, temperature=0, max_tokens=256)
    llm_with_tools = llm.bind_tools(tools)

    system_msg = """Sos un asistente de control de hogar inteligente.
El usuario da comandos en voz para controlar:
- Luz de pared / Luz Pared de Iván (on/off, brillo, color)
- Luces del patio / enchufe inteligente (on/off)

Reglas:
- Si dice "prender todo", "encender todo", "prender todas las luces" o similar → llamá turn_on_light_tool Y turn_on_plug_tool.
- Si dice "apagar todo", "apagar las luces" o similar → llamá turn_off_light_tool Y turn_off_plug_tool.
- Si el comando es ambiguo sobre cuál dispositivo, controlá ambos.
- Ejecutá las herramientas sin pedir confirmación. Respondé brevemente con lo que hiciste."""

    messages = [{"role": "system", "content": system_msg}, {"role": "user", "content": text}]

    logger.info("Calling LLM with tools...")
    response = await asyncio.to_thread(llm_with_tools.invoke, messages)

    actions = []
    errors = []
    for tool_call in getattr(response, "tool_calls", None) or []:
        tool_name = tool_call.get("name")
        tool_args = tool_call.get("args", {})
        logger.info(f"Tool call: {tool_name} {tool_args}")
        try:
            if tool_name == "turn_on_light_tool":
                await turn_on_light()
                actions.append("Luz encendida")
            elif tool_name == "turn_off_light_tool":
                await turn_off_light()
                actions.append("Luz apagada")
            elif tool_name == "set_light_brightness_tool":
                bri = await set_light_brightness(tool_args.get("brightness", 100))
                actions.append(f"Brillo al {bri}%")
            elif tool_name == "turn_on_plug_tool":
                await turn_on_plug()
                actions.append("Luces del patio encendidas")
            elif tool_name == "turn_off_plug_tool":
                await turn_off_plug()
                actions.append("Luces del patio apagadas")
        except Exception as e:
            logger.error(f"Tool {tool_name} failed: {e}")
            errors.append(f"Error en {tool_name}: {e}")

    if errors:
        return {"success": False, "response": "; ".join(errors)}

    content = getattr(response, "content", "") or ""
    reply = content or (", ".join(actions) if actions else "No entendí el comando")
    return {"success": True, "response": reply}


async def process_text_command(text: str) -> dict:
    """Process an already-transcribed voice command (from Web Speech API)."""
    try:
        logger.info(f"Text command: {text}")
        result = await _run_llm_command(text)
        result["transcription"] = text
        return result
    except Exception as e:
        logger.error(f"Text command error: {e}", exc_info=True)
        return {"error": str(e)}


async def process_voice_command(audio_bytes: bytes, mime_type: str = "audio/webm") -> dict:
    """Process a voice command: transcribe with Groq Whisper, then run LLM."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return {"error": "GROQ_API_KEY not configured"}

    try:
        logger.info("Transcribing audio...")
        text = await asyncio.to_thread(_transcribe_groq_sync, audio_bytes, mime_type)
        logger.info(f"Transcribed: {text}")
        result = await _run_llm_command(text)
        result["transcription"] = text
        return result
    except Exception as e:
        logger.error(f"Voice processing error: {e}", exc_info=True)
        return {"error": str(e)}
