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
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

# Fallback chain tried in order when the primary model hits a quota/rate-limit error.
# gemini-2.0-flash free tier has limit=0 in many regions — fallbacks cover that.
_FALLBACK_MODELS = [
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash",
    "gemini-flash-lite-latest",
]

_PROMPT_TEMPLATE = (
    "Sos un asistente experto en resumir transcripciones de audio que pueden "
    "contener ruido, errores de transcripcion o frases incompletas.\n\n"
    "Tu tarea: IGNORAR el ruido, palabras sueltas sin sentido y errores de "
    "transcripcion, y extraer los PUNTOS CLAVE que el hablante realmente "
    "quiso comunicar.\n\n"
    "Formato de respuesta:\n"
    "- Respondé en el MISMO IDIOMA del texto transcripto\n"
    "- Usá EXACTAMENTE 3 a 6 viñetas, cada una con un titulo corto en negrita "
    "y una descripcion de 1 a 2 oraciones\n"
    "- Destacá: ideas principales, temas discutidos, decisiones y acciones a seguir\n"
    "- Si el texto es demasiado corto o sin contenido claro, indicalo en una viñeta\n\n"
    "Transcripcion:\n"
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

    client = genai.Client(api_key=GEMINI_API_KEY)
    contents = _PROMPT_TEMPLATE + text

    # Try primary model first, then fallbacks if quota is exhausted.
    models_to_try = [GEMINI_MODEL] + [m for m in _FALLBACK_MODELS if m != GEMINI_MODEL]
    last_exc = None

    for model in models_to_try:
        try:
            response = client.models.generate_content(model=model, contents=contents)
            summary = (response.text or "").strip()
            if not summary:
                raise SummaryError("La API devolvio un resumen vacio.")
            if model != GEMINI_MODEL:
                logger.info("Resumen generado con modelo de respaldo: %s", model)
            return summary
        except SummaryError:
            raise
        except Exception as exc:
            detail = str(exc).lower()
            is_quota = "429" in detail or "quota" in detail or "resource_exhausted" in detail
            if is_quota:
                logger.warning("Cuota agotada para %s, intentando siguiente modelo...", model)
                last_exc = exc
                continue
            # Non-quota error: log and classify immediately
            logger.exception("Fallo el resumen con Gemini (%s): %s", model, exc)
            if "api key" in detail or "permission" in detail or "401" in detail or "403" in detail:
                raise SummaryError("Clave de API invalida o sin permisos. Revisa GEMINI_API_KEY en .env.") from exc
            if "deadline" in detail or "connection" in detail or "timeout" in detail or "network" in detail:
                raise SummaryError("Sin conexion con la API. Revisa tu red.") from exc
            raise SummaryError("Error al contactar la API. Revisa la bitacora (logs/error_log.txt).") from exc

    # All models returned quota errors
    logger.exception("Cuota agotada en todos los modelos: %s", last_exc)
    raise SummaryError(
        "Cuota agotada en todos los modelos disponibles. "
        "Revisá tu plan en ai.google.dev o esperá unos minutos para reintentar."
    ) from last_exc
