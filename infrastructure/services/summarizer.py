"""Optional online transcript summarization via Google Gemini.

Online features (Resumir + Modo entrevista) share the Gemini key pool from
`.env`. Transcription itself stays fully offline. When no API key is configured,
or the network/API fails, summarization degrades gracefully with a clear message.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from config import logger
from infrastructure.services import gemini_keys, nvidia_provider

load_dotenv()

# Back-compat for any code that still reads this module-level name.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
GEMINI_SUMMARY_TIMEOUT_MS = int(
    os.getenv("GEMINI_SUMMARY_TIMEOUT_MS", "12000")
)

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
    return gemini_keys.is_configured() or nvidia_provider.is_configured()


def summarize(text: str) -> str:
    """Summarize quickly, preferring NVIDIA and falling back to Gemini."""
    text = (text or "").strip()
    if not text:
        raise SummaryError("No hay texto para resumir.")
    gemini_keys.pool.reload()
    nvidia_provider.pool.reload()
    if not is_configured():
        raise SummaryError("Falta una clave Gemini o NVIDIA en el archivo .env.")

    contents = _PROMPT_TEMPLATE + text
    # NVIDIA is consistently much faster for this short, non-conversational
    # task. Prefer it so a busy Gemini endpoint cannot hold the UI for minutes.
    if nvidia_provider.is_configured():
        try:
            summary = nvidia_provider.generate(contents, max_tokens=700)
            logger.info("Resumen generado con proveedor rápido NVIDIA")
            return summary
        except Exception as exc:
            logger.warning(
                "NVIDIA no pudo resumir; intentando Gemini: %s", exc
            )

    if not gemini_keys.is_configured():
        raise SummaryError("No se pudo generar el resumen con NVIDIA.")

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise SummaryError(
            "Falta google-genai y NVIDIA no pudo generar el resumen."
        ) from exc
    models_to_try = [GEMINI_MODEL] + [m for m in _FALLBACK_MODELS if m != GEMINI_MODEL]
    last_exc = None

    # Outer: keys. Inner: models. Quota on a key → next key; quota on model → next model.
    while True:
        key = gemini_keys.pool.current()
        if not key:
            break
        client = genai.Client(
            api_key=key,
            http_options=types.HttpOptions(
                timeout=GEMINI_SUMMARY_TIMEOUT_MS,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
        key_quota_hit = False
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
                if gemini_keys.pool.is_quota_error(exc):
                    logger.warning(
                        "Cuota agotada para %s (%s), intentando siguiente...",
                        model,
                        gemini_keys.pool.current_label(),
                    )
                    last_exc = exc
                    # If all models on this key fail quota, rotate key
                    if model == models_to_try[-1]:
                        gemini_keys.pool.mark_exhausted(key)
                        key_quota_hit = True
                    continue
                logger.exception("Fallo el resumen con Gemini (%s): %s", model, exc)
                if "api key" in detail or "permission" in detail or "401" in detail or "403" in detail:
                    gemini_keys.pool.mark_exhausted(key)
                    key_quota_hit = True
                    last_exc = exc
                    break
                if "deadline" in detail or "connection" in detail or "timeout" in detail or "network" in detail:
                    raise SummaryError("Sin conexion con la API. Revisa tu red.") from exc
                raise SummaryError("Error al contactar la API. Revisa la bitacora (logs/error_log.txt).") from exc
        if not key_quota_hit:
            break

    logger.warning("Gemini no pudo resumir; usando respaldo NVIDIA: %s", last_exc)
    try:
        return nvidia_provider.generate(contents, max_tokens=700)
    except Exception as nvidia_exc:
        logger.exception("También falló el respaldo NVIDIA: %s", nvidia_exc)
        raise SummaryError(
            "No quedan proveedores disponibles: Gemini y NVIDIA fallaron."
        ) from nvidia_exc
