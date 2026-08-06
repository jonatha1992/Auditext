"""Batch audio transcription via Gemini (online).

Exists so the local Whisper path can be compared against a cloud engine on the
same file — see `tools/bench_transcription.py`. It is NOT wired into the app:
`CLAUDE.md` keeps transcription offline-first, and using this would send the
user's audio to a third party and stop working without a network.

Key rotation and quota detection are reused from `gemini_keys.pool`, the same
pool `summarizer` and `interview_live` use.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from config import logger
from infrastructure.services import gemini_keys, latency_log

# Requests carrying inline bytes must stay under the API's total request cap.
# Anything larger goes through the Files API instead.
INLINE_LIMIT_BYTES = 18 * 1024 * 1024

# gemini-2.5-flash fue retirado (404 con cualquier key). 3.6-flash es el
# reemplazo actual con la misma capacidad de audio.
GEMINI_TRANSCRIBE_MODEL = (
    os.getenv("GEMINI_TRANSCRIBE_MODEL", "").strip() or "gemini-3.6-flash"
)
GEMINI_TRANSCRIBE_TIMEOUT_MS = int(
    os.getenv("GEMINI_TRANSCRIBE_TIMEOUT_MS", "180000")
)

_MIME_BY_SUFFIX = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}

# Verbatim only. Any summarising or cleanup would make the benchmark compare
# two different tasks instead of two transcription engines.
_PROMPT = (
    "Transcribí este audio palabra por palabra.\n"
    "Reglas estrictas:\n"
    "- Devolvé SOLO el texto transcripto, sin comentarios ni encabezados.\n"
    "- No resumas, no corrijas, no completes y no traduzcas.\n"
    "- No agregues marcas de tiempo ni etiquetas de hablante.\n"
    "- Si un tramo es inaudible, escribí [inaudible].\n"
)


class GeminiTranscriptionError(Exception):
    """Transcription could not be produced (config, network, quota, or empty)."""


def is_configured() -> bool:
    return gemini_keys.is_configured()


def guess_mime(path: str | Path) -> str:
    return _MIME_BY_SUFFIX.get(Path(path).suffix.lower(), "audio/wav")


def _build_prompt(language: str | None) -> str:
    if not language:
        return _PROMPT
    return f"{_PROMPT}- El audio está en '{language}'; transcribí en ese idioma.\n"


def transcribe_file(
    path: str | Path,
    language: str | None = None,
    model: str | None = None,
) -> tuple[str, dict]:
    """Transcribe an audio file with Gemini.

    Returns (text, metadata). Raises GeminiTranscriptionError on failure so the
    benchmark can tell "unavailable" apart from "slow".
    """
    audio_path = Path(path)
    if not audio_path.is_file():
        raise GeminiTranscriptionError(f"No existe el archivo: {audio_path}")

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise GeminiTranscriptionError(
            "Falta google-genai (pip install google-genai)."
        ) from exc

    gemini_keys.pool.reload()
    if not gemini_keys.pool.is_configured():
        raise GeminiTranscriptionError("Falta una clave Gemini en el archivo .env.")

    size_bytes = audio_path.stat().st_size
    use_inline = size_bytes <= INLINE_LIMIT_BYTES
    model_name = model or GEMINI_TRANSCRIBE_MODEL
    prompt = _build_prompt(language)
    meta = {
        "model": model_name,
        "bytes": size_bytes,
        "transport": "inline" if use_inline else "files_api",
        "mime": guess_mime(audio_path),
    }

    last_exc: BaseException | None = None
    start = time.perf_counter()

    while True:
        key = gemini_keys.pool.current()
        if not key:
            break
        client = genai.Client(
            api_key=key,
            http_options=types.HttpOptions(
                timeout=GEMINI_TRANSCRIBE_TIMEOUT_MS,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
        try:
            if use_inline:
                audio_part = types.Part.from_bytes(
                    data=audio_path.read_bytes(), mime_type=meta["mime"]
                )
            else:
                # Upload is billed separately in wall time; keep it inside the
                # measured window because a real user would pay it too.
                audio_part = client.files.upload(file=str(audio_path))

            response = client.models.generate_content(
                model=model_name, contents=[prompt, audio_part]
            )
            text = (response.text or "").strip()
            if not text:
                raise GeminiTranscriptionError("Gemini devolvió una transcripción vacía.")

            elapsed = (time.perf_counter() - start) * 1000.0
            latency_log.log_stage(
                "gemini_file",
                "transcribe_file",
                elapsed,
                model=model_name,
                transport=meta["transport"],
                bytes=size_bytes,
                chars=len(text),
            )
            meta["elapsed_ms"] = elapsed
            return text, meta
        except GeminiTranscriptionError:
            raise
        except Exception as exc:
            last_exc = exc
            if gemini_keys.pool.is_quota_error(exc):
                logger.warning(
                    "Cuota agotada transcribiendo con %s (%s), rotando clave...",
                    model_name,
                    gemini_keys.pool.current_label(),
                )
                if gemini_keys.pool.mark_exhausted(key) is None:
                    break
                continue
            logger.exception("Fallo la transcripción con Gemini: %s", exc)
            raise GeminiTranscriptionError(
                f"Error al transcribir con Gemini: {exc}"
            ) from exc

    raise GeminiTranscriptionError(
        f"No quedan claves Gemini disponibles. Último error: {last_exc}"
    )
