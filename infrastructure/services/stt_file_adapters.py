"""File-mode adapters for the STT engines that normally read a microphone.

Google STT and Windows STT are wired to live capture in `live_transcriber`, so
they cannot be compared against Whisper on a fixed recording. These adapters
run the same engines over a file, giving `tools/bench_transcription.py` one
signature per engine: ``transcribe(path, language) -> (text, metadata)``.

Both raise `SttEngineUnavailable` when the engine is missing or unusable, so a
benchmark can report "unavailable" instead of pretending it was fast.
"""

from __future__ import annotations

import time
from pathlib import Path

from config import logger
from infrastructure.services import latency_log
from infrastructure.services.live_transcriber import (
    GOOGLE_STT_LANG,
    WINDOWS_STT_LANG,
    windows_stt_process,
)

# Google's free Web Speech endpoint is built for single utterances: a long
# recording comes back truncated or fails outright. The file is split and the
# pieces are concatenated, and the benchmark reports that it happened.
GOOGLE_CHUNK_SECONDS = 50


class SttEngineUnavailable(Exception):
    """The engine is not installed or not usable on this machine."""


def transcribe_google(
    path: str | Path, language: str | None = None
) -> tuple[str, dict]:
    """Transcribe a WAV with Google's Web Speech API, chunk by chunk."""
    audio_path = Path(path)
    if not audio_path.is_file():
        raise SttEngineUnavailable(f"No existe el archivo: {audio_path}")

    try:
        import speech_recognition as sr
    except ImportError as exc:
        raise SttEngineUnavailable(
            "Falta SpeechRecognition (pip install SpeechRecognition)."
        ) from exc

    bcp47 = GOOGLE_STT_LANG.get(language, "es-AR")
    recognizer = sr.Recognizer()

    pieces: list[str] = []
    failed = 0
    chunks = 0
    start = time.perf_counter()

    try:
        with sr.AudioFile(str(audio_path)) as source:
            while True:
                audio = recognizer.record(source, duration=GOOGLE_CHUNK_SECONDS)
                if not audio.frame_data:
                    break
                chunks += 1
                try:
                    text = recognizer.recognize_google(audio, language=bcp47)
                except sr.UnknownValueError:
                    # Silence or unintelligible audio: not an engine failure.
                    continue
                except sr.RequestError as exc:
                    logger.warning("Google STT rechazó un tramo: %s", exc)
                    failed += 1
                    continue
                if text.strip():
                    pieces.append(text.strip())
    except ValueError as exc:
        # speech_recognition only accepts PCM WAV/AIFF/FLAC.
        raise SttEngineUnavailable(
            f"Formato no soportado por Google STT: {exc}"
        ) from exc

    elapsed = (time.perf_counter() - start) * 1000.0
    text = " ".join(pieces)
    meta = {
        "chunks": chunks,
        "chunk_seconds": GOOGLE_CHUNK_SECONDS,
        "failed_chunks": failed,
        "elapsed_ms": elapsed,
        "language": bcp47,
    }
    latency_log.log_stage(
        "google_stt_file",
        "transcribe_file",
        elapsed,
        chunks=chunks,
        failed_chunks=failed,
        chars=len(text),
    )
    return text, meta


def transcribe_windows(
    path: str | Path, language: str | None = None
) -> tuple[str, dict]:
    """Transcribe a WAV with the offline Windows System.Speech recognizer."""
    audio_path = Path(path)
    if not audio_path.is_file():
        raise SttEngineUnavailable(f"No existe el archivo: {audio_path}")

    lang = WINDOWS_STT_LANG.get(language, "es-ES")
    pieces: list[str] = []
    active_lang = lang
    start = time.perf_counter()

    try:
        with windows_stt_process(lang, wav=str(audio_path)) as (proc, first):
            if first == "ERROR_NO_RECOGNIZER":
                raise SttEngineUnavailable(
                    "Windows Speech Recognition no tiene ningún paquete de voz "
                    "instalado (Configuración → Hora e idioma → Voz)."
                )
            if first.startswith("ERROR:"):
                raise SttEngineUnavailable(f"Windows STT falló: {first[6:]}")
            if first.startswith("READY:"):
                active_lang = first[6:]

            for line in proc.stdout:
                text = line.strip()
                if text:
                    pieces.append(text)
    except FileNotFoundError as exc:
        raise SttEngineUnavailable("PowerShell no encontrado en el sistema.") from exc

    elapsed = (time.perf_counter() - start) * 1000.0
    text = " ".join(pieces)
    meta = {"elapsed_ms": elapsed, "language": active_lang, "phrases": len(pieces)}
    latency_log.log_stage(
        "windows_stt_file",
        "transcribe_file",
        elapsed,
        phrases=len(pieces),
        chars=len(text),
    )
    return text, meta
