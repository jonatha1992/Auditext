"""Optional speaker diarization for the file tab (whisperx + pyannote).

Heavy and online on first use: downloading the pyannote diarization model needs
a HuggingFace token (HF_TOKEN in .env) and accepting the model's terms on its
HF page. This is why diarization is opt-in and kept isolated from the offline
transcription path in transcriber.py.

Returns speaker-labeled text like:
    [SPEAKER_00] hola, como estas
    [SPEAKER_01] todo bien, gracias
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from config import logger

load_dotenv()

HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
DEVICE = "cpu"
COMPUTE_TYPE = "int8"
MODEL_SIZE = "small"


class DiarizationError(Exception):
    """Raised when diarization cannot run (missing dep, token, or runtime error)."""


def is_available() -> bool:
    """True when whisperx is importable AND an HF token is configured."""
    try:
        import whisperx  # noqa: F401
    except Exception:
        return False
    return bool(HF_TOKEN)


def _diarization_pipeline():
    # The class moved to whisperx.diarize in newer versions; support both.
    try:
        from whisperx.diarize import DiarizationPipeline
    except Exception:
        import whisperx

        DiarizationPipeline = whisperx.DiarizationPipeline
    return DiarizationPipeline


def diarize_file(path: str, language: str | None = None, progress_cb=None) -> str:
    """Transcribe a file and label each segment with its speaker. Offline model,
    but the pyannote model download (first run) and gating need HF_TOKEN."""
    if not HF_TOKEN:
        raise DiarizationError(
            "Falta HF_TOKEN en .env. Crea un token en huggingface.co/settings/tokens "
            "y acepta los terminos de pyannote/speaker-diarization-3.1."
        )
    try:
        import whisperx
    except Exception as exc:
        raise DiarizationError("whisperx no esta instalado.") from exc

    try:
        if progress_cb:
            progress_cb(0.05)
        audio = whisperx.load_audio(path)

        model = whisperx.load_model(MODEL_SIZE, DEVICE, compute_type=COMPUTE_TYPE)
        result = model.transcribe(audio, batch_size=8, language=language)
        detected = result.get("language", language)
        if progress_cb:
            progress_cb(0.5)

        model_a, metadata = whisperx.load_align_model(
            language_code=detected, device=DEVICE
        )
        result = whisperx.align(
            result["segments"], model_a, metadata, audio, DEVICE
        )
        if progress_cb:
            progress_cb(0.75)

        diarize_model = _diarization_pipeline()(use_auth_token=HF_TOKEN, device=DEVICE)
        diarize_segments = diarize_model(audio)
        result = whisperx.assign_word_speakers(diarize_segments, result)
        if progress_cb:
            progress_cb(0.95)

        lines = []
        for seg in result.get("segments", []):
            speaker = seg.get("speaker", "SPEAKER_??")
            text = (seg.get("text") or "").strip()
            if text:
                lines.append(f"[{speaker}] {text}")
        if progress_cb:
            progress_cb(1.0)
        return "\n".join(lines)
    except DiarizationError:
        raise
    except Exception as exc:
        logger.exception("Fallo la diarizacion de %s: %s", path, exc)
        raise DiarizationError(
            "Error al diferenciar hablantes. Revisa la bitacora (logs/error_log.txt)."
        ) from exc
