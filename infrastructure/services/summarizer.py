"""Optional online transcript summarization via Google Gemini.

Online features (Resumir + Modo entrevista) share the Gemini key pool from
`.env`. Transcription itself stays fully offline. When no API key is configured,
or the network/API fails, summarization degrades gracefully with a clear message.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

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

_PROMPT_HEADER = (
    "Sos un asistente experto en resumir transcripciones de audio que pueden "
    "contener ruido, errores de transcripcion o frases incompletas.\n\n"
    "Tu tarea: IGNORAR el ruido, palabras sueltas sin sentido y errores de "
    "transcripcion, y extraer lo que el hablante realmente quiso comunicar.\n\n"
    "Formato de respuesta:\n"
    "- Respondé en el MISMO IDIOMA del texto transcripto\n"
)

# A fixed "3 to 6 bullets" instruction plus a 700-token ceiling compressed a
# 2.5 h class into ~20 lines: the model was not forgetting anything, it was
# obeying. The shape of the summary now scales with how much was actually said.
# Measured on a 20 533-word class: 6000 output tokens produced 1166 words over
# 106 lines, against ~500 words before.
@dataclass(frozen=True)
class SummaryPlan:
    instructions: str
    max_tokens: int
    chunked: bool = False


_SHORT_RULES = (
    "- Usá 3 a 6 viñetas, cada una con un titulo corto en negrita y una "
    "descripcion de 1 a 2 oraciones\n"
    "- Destacá: ideas principales, temas discutidos, decisiones y acciones a seguir\n"
    "- Si el texto es demasiado corto o sin contenido claro, indicalo en una viñeta\n"
)
_LONG_RULES = (
    "- Recorré la grabación en ORDEN CRONOLOGICO, de principio a fin\n"
    "- Organizá el resumen en secciones con título, y viñetas dentro de cada una\n"
    "- NO OMITAS temas. Es preferible ser extenso a dejar algo afuera\n"
    "- Incluí datos concretos: nombres, fechas, consignas, acuerdos y números\n"
    "- Cubrí también los tramos finales: no te quedes solo con el principio\n"
)

# Word-count tiers. A 5-minute note still gets a short summary; only long
# recordings pay the extra generation time (~32 s measured for 2.5 h).
_SHORT_MAX_WORDS = 800
_MEDIUM_MAX_WORDS = 5000
# One call comfortably handled a 20 533-word class (~29k tokens) and gave the
# best result of everything measured: 1166 words over 106 structured lines in
# 32 s. Splitting the same transcript was worse both ways — merging the pieces
# re-compressed it down to 832 words, and skipping the merge made the model
# copy the transcript verbatim instead of summarising. So chunking is only a
# safety net for transcripts far beyond anything verified, not the normal path.
_LONG_MAX_WORDS = 40000
_CHUNK_WORDS = 6000


def plan_for(text: str) -> SummaryPlan:
    """Pick summary depth from how much was actually said."""
    words = len((text or "").split())
    if words <= _SHORT_MAX_WORDS:
        return SummaryPlan(_SHORT_RULES, 700)
    if words <= _MEDIUM_MAX_WORDS:
        return SummaryPlan(_LONG_RULES, 2000)
    # 6000 is the budget that produced 106 structured lines for a 2.5 h class;
    # 4000 was measurably tighter for the same input.
    if words <= _LONG_MAX_WORDS:
        return SummaryPlan(_LONG_RULES, 6000)
    return SummaryPlan(_LONG_RULES, 6000, chunked=True)


def build_prompt(text: str, instructions: str) -> str:
    return f"{_PROMPT_HEADER}{instructions}\nTranscripcion:\n{text}"


def split_into_chunks(text: str, chunk_words: int = _CHUNK_WORDS) -> list[str]:
    """Split a transcript into ordered chunks without losing any word."""
    words = (text or "").split()
    if not words:
        return []
    return [
        " ".join(words[i : i + chunk_words])
        for i in range(0, len(words), chunk_words)
    ]


class SummaryError(Exception):
    """Raised when a summary cannot be produced (config, network, or API)."""


def is_configured() -> bool:
    """True when an API key is present, so the UI can enable/disable the action."""
    return gemini_keys.is_configured() or nvidia_provider.is_configured()


_CHUNK_INSTRUCTIONS = (
    "- Este es UN TRAMO de una grabación más larga; resumilo por sí solo\n"
    "- Escribí SIEMPRE en viñetas. NUNCA copies frases textuales ni reproduzcas "
    "la transcripción tal cual: si lo hacés, la respuesta no sirve\n"
    "- Cubrí todos los temas del tramo, con nombres, fechas, consignas y números\n"
    "- No escribas introducciones ni conclusiones sobre la grabación completa\n"
)


def _summarize_in_chunks(text: str, plan: SummaryPlan, progress_cb=None) -> str:
    """Summarise a long transcript tramo by tramo and keep every part.

    One call over a very long transcript covers the opening well and skims the
    end, so the timeline is walked in ordered chunks instead.

    The per-tramo summaries are concatenated, not merged by another model call.
    Measured on a 20 533-word class: merging produced 832 words in 69 s, while
    a single pass produced 1166. Asking a model to "unify without losing
    anything" still compresses — and not losing anything is the whole point
    here. Concatenation costs one fewer call and keeps the detail.
    """
    chunks = split_into_chunks(text)
    parts: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        if progress_cb:
            progress_cb(f"Resumiendo tramo {index} de {len(chunks)}...")
        summary = nvidia_provider.generate(
            build_prompt(chunk, _CHUNK_INSTRUCTIONS), max_tokens=1800
        )
        parts.append(summary if len(chunks) == 1 else f"## Tramo {index} de {len(chunks)}\n\n{summary}")
    return "\n\n".join(parts)


def summarize(text: str, progress_cb=None) -> str:
    """Summarize, preferring NVIDIA and falling back to Gemini.

    `progress_cb` receives status strings for the long chunked path, which can
    take well over half a minute on a multi-hour recording.
    """
    text = (text or "").strip()
    if not text:
        raise SummaryError("No hay texto para resumir.")
    gemini_keys.pool.reload()
    nvidia_provider.pool.reload()
    if not is_configured():
        raise SummaryError("Falta una clave Gemini o NVIDIA en el archivo .env.")

    plan = plan_for(text)
    contents = build_prompt(text, plan.instructions)
    logger.info(
        "Resumen: %d palabras, max_tokens=%d, por tramos=%s",
        len(text.split()),
        plan.max_tokens,
        plan.chunked,
    )
    # NVIDIA is consistently much faster than Gemini here and uses a separate
    # key pool, so it keeps the scarce Gemini quota free for the Live coach.
    if nvidia_provider.is_configured():
        try:
            if plan.chunked:
                summary = _summarize_in_chunks(text, plan, progress_cb)
            else:
                summary = nvidia_provider.generate(
                    contents, max_tokens=plan.max_tokens
                )
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
