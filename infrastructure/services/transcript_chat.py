"""Follow-up questions over a finished transcription.

A summary always drops something. Instead of pasting a 2-hour transcript into
another tool to recover it, the question is answered here, against the FULL
transcription — not against the summary, and not against retrieved fragments.
Retrieval would risk missing exactly the passage the summary already skipped,
which is the reason this exists.

Backed by NVIDIA: it has its own key pool, so asking questions never eats the
scarce Gemini quota the Live coach depends on. Measured on a 117 017-character
transcript (~29k tokens): 4.9 s per answer.
"""

from __future__ import annotations

from config import logger
from infrastructure.services import nvidia_provider

# Well below the largest input verified to work in one call. Past this the
# transcript is trimmed rather than silently rejected by the endpoint.
MAX_CONTEXT_CHARS = 120_000
# Older turns are dropped first: the transcript matters more than chat history.
MAX_HISTORY_TURNS = 8

_SYSTEM = (
    "/no_think\n"
    "Respondés preguntas sobre una transcripción de audio que puede tener "
    "errores de transcripción y frases incompletas.\n"
    "Reglas:\n"
    "- Contestá SOLO con lo que está en la transcripción.\n"
    "- Si el dato no está, decí claramente que no aparece. No lo inventes.\n"
    "- Si un nombre propio parece mal transcripto, señalalo en vez de corregirlo "
    "por tu cuenta.\n"
    "- Respondé en español, directo y concreto."
)


class TranscriptChatError(Exception):
    """The question could not be answered (no provider, network, or empty)."""


def is_configured() -> bool:
    return nvidia_provider.is_configured()


def _fit(transcription: str) -> tuple[str, bool]:
    """Clamp the transcript to what one request can carry."""
    text = (transcription or "").strip()
    if len(text) <= MAX_CONTEXT_CHARS:
        return text, False
    # Keep the end too: questions are often about how a session closed, and a
    # head-only cut would answer "no aparece" for everything said late.
    half = MAX_CONTEXT_CHARS // 2
    return f"{text[:half]}\n\n[...tramo omitido...]\n\n{text[-half:]}", True


def build_history(messages: list[dict]) -> list[dict]:
    """Turn stored chat rows into the provider's message format."""
    recent = [m for m in messages if m.get("content")][-MAX_HISTORY_TURNS:]
    return [
        {
            "role": "assistant" if m.get("role") == "assistant" else "user",
            "content": str(m["content"]),
        }
        for m in recent
    ]


def ask(
    transcription: str,
    question: str,
    history: list[dict] | None = None,
) -> str:
    """Answer a question about the transcription. Raises TranscriptChatError."""
    question = (question or "").strip()
    if not question:
        raise TranscriptChatError("Escribí una pregunta.")

    text, trimmed = _fit(transcription)
    if not text:
        raise TranscriptChatError("No hay transcripción sobre la cual preguntar.")

    nvidia_provider.pool.reload()
    if not is_configured():
        raise TranscriptChatError(
            "Falta una clave NVIDIA en el archivo .env para poder preguntar."
        )
    if trimmed:
        logger.warning(
            "Transcripción recortada para el chat: %d caracteres", len(transcription)
        )

    prompt = f"TRANSCRIPCION:\n{text}\n\nPREGUNTA: {question}"
    try:
        answer = nvidia_provider.generate(
            prompt,
            max_tokens=1200,
            history=build_history(history or []),
            system=_SYSTEM,
        )
    except Exception as exc:
        logger.exception("Fallo el chat sobre la transcripción: %s", exc)
        raise TranscriptChatError(f"No se pudo responder: {exc}") from exc

    answer = (answer or "").strip()
    if not answer:
        raise TranscriptChatError("El modelo devolvió una respuesta vacía.")
    return answer
