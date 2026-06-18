"""Optional online transcript summarization via Google Gemini.

This is the ONLY online feature in the app. It runs solely on demand (the
"Resumir" button); transcription itself stays fully offline. When no API key
is configured, or the network/API fails, summarization degrades gracefully
with a clear message instead of crashing.

The API key is read from the GEMINI_API_KEY environment variable, loaded from
a gitignored .env file. The key is never hardcoded.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from config import logger

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash").strip()

_PROMPT = (
    "Sos un asistente que resume transcripciones de audio. "
    "Resumi el siguiente texto en vinetas claras y concisas, en el mismo "
    "idioma del texto, destacando los puntos principales, las decisiones y "
    "las acciones a seguir si las hubiera.\n\n"
    "Transcripcion:\n{text}"
)


class SummaryError(Exception):
    """Raised when a summary cannot be produced (config, network, or API)."""


def is_configured() -> bool:
    """True when an API key is present, so the UI can enable/disable the action."""
    return bool(GEMINI_API_KEY)


def summarize(text: str) -> str:
    """Summarize a transcript with Gemini. Raises SummaryError on any failure."""
    text = (text or "").strip()
    if not text:
        raise SummaryError("No hay texto para resumir.")
    if not GEMINI_API_KEY:
        raise SummaryError("Falta GEMINI_API_KEY. Configurala en el archivo .env.")

    try:
        from google import genai
    except ImportError as exc:
        raise SummaryError(
            "Falta el paquete google-genai (pip install google-genai)."
        ) from exc

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=_PROMPT.format(text=text),
        )
        summary = (response.text or "").strip()
        if not summary:
            raise SummaryError("La API devolvio un resumen vacio.")
        return summary
    except SummaryError:
        raise
    except Exception as exc:
        # Log the full API error (e.g. the long 429 quota JSON) to the bitacora,
        # show the user a short classified message.
        logger.exception("Fallo el resumen con Gemini: %s", exc)
        detail = str(exc).lower()
        if "429" in detail or "quota" in detail or "resource_exhausted" in detail:
            friendly = (
                "Cuota de la API agotada. Proba con otro modelo "
                "(gemini-1.5-flash) o revisa tu plan/billing en Google AI Studio."
            )
        elif "api key" in detail or "permission" in detail or "401" in detail or "403" in detail:
            friendly = "Clave de API invalida o sin permisos. Revisa GEMINI_API_KEY en .env."
        elif "deadline" in detail or "connection" in detail or "timeout" in detail or "network" in detail:
            friendly = "Sin conexion con la API. Revisa tu red."
        else:
            friendly = "Error al contactar la API. Revisa la bitacora (logs/error_log.txt)."
        raise SummaryError(friendly) from exc
