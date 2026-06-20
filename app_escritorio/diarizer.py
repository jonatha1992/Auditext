"""Optional speaker diarization for the file tab.

Two backends, tried in order:

1. **pyannote / whisperx** (higher quality) — requires HF_TOKEN in .env and
   accepting pyannote model terms on huggingface.co.

2. **simple_diarizer** (no token, fully offline) — lower accuracy but works
   out of the box after `pip install simple_diarizer`.

`diarize_file()` selects the best available backend automatically.
"""

from __future__ import annotations

import os
import platform
import subprocess
import tempfile

from dotenv import load_dotenv

from config import logger

load_dotenv()

HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
DEVICE = "cpu"
COMPUTE_TYPE = "int8"
MODEL_SIZE = "small"


class DiarizationError(Exception):
    """Raised when diarization cannot run (missing dep, token, or runtime error)."""


# ---------------------------------------------------------------------------
# Backend availability checks
# ---------------------------------------------------------------------------

def _is_pyannote_available() -> bool:
    """True when whisperx is importable AND an HF token is configured."""
    try:
        import whisperx  # noqa: F401
    except Exception:
        return False
    return bool(HF_TOKEN)


def _is_simple_available() -> bool:
    """True when simple_diarizer is importable (no token needed)."""
    try:
        import simple_diarizer  # noqa: F401
        return True
    except Exception:
        return False


def is_available() -> bool:
    """True when at least one diarization backend is ready."""
    return _is_pyannote_available() or _is_simple_available()


# ---------------------------------------------------------------------------
# pyannote / whisperx backend (original)
# ---------------------------------------------------------------------------

def _diarization_pipeline():
    try:
        from whisperx.diarize import DiarizationPipeline
    except Exception:
        import whisperx
        DiarizationPipeline = whisperx.DiarizationPipeline
    return DiarizationPipeline


def _diarize_pyannote(path: str, language: str | None, progress_cb) -> str:
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

        model_a, metadata = whisperx.load_align_model(language_code=detected, device=DEVICE)
        result = whisperx.align(result["segments"], model_a, metadata, audio, DEVICE)
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
        logger.exception("Fallo la diarizacion pyannote de %s: %s", path, exc)
        raise DiarizationError(
            "Error al diferenciar hablantes. Revisa la bitacora (logs/error_log.txt)."
        ) from exc


# ---------------------------------------------------------------------------
# simple_diarizer backend (no HF token required)
# ---------------------------------------------------------------------------

def _to_wav(path: str) -> tuple[str, bool]:
    """Convert audio to 16 kHz mono WAV via ffmpeg if needed.

    Returns (wav_path, is_temp). Caller must delete wav_path when is_temp=True.
    """
    from config import ffmpeg_path

    if path.lower().endswith(".wav"):
        return path, False

    tmp = tempfile.mktemp(suffix=".wav")
    startupinfo = None
    if platform.system() == "Windows":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

    subprocess.run(
        [ffmpeg_path, "-i", path, "-ar", "16000", "-ac", "1", "-y", tmp],
        startupinfo=startupinfo,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return tmp, True


def _dominant_speaker(seg_start: float, seg_end: float, diar_segs: list) -> int:
    """Return the speaker label with the most temporal overlap with [seg_start, seg_end]."""
    best, best_overlap = 0, 0.0
    for d in diar_segs:
        overlap = max(0.0, min(seg_end, d["end"]) - max(seg_start, d["start"]))
        if overlap > best_overlap:
            best_overlap = overlap
            best = d["label"]
    return best


def _diarize_simple(path: str, language: str | None, progress_cb) -> str:
    try:
        from simple_diarizer.diarizer import Diarizer
    except ImportError as exc:
        raise DiarizationError("simple_diarizer no esta instalado.") from exc

    import transcriber

    wav_path, is_temp = _to_wav(path)
    try:
        if progress_cb:
            progress_cb(0.05)

        # Phase 1: transcribe with timestamps (0.05 → 0.55)
        text_segs = transcriber.transcribe_file_segments(
            path,
            language=language,
            progress_cb=(lambda f: progress_cb(0.05 + f * 0.5)) if progress_cb else None,
        )
        if not text_segs:
            return ""

        if progress_cb:
            progress_cb(0.55)

        # Phase 2: speaker diarization (0.55 → 0.95)
        logger.info("simple_diarizer: iniciando diarizacion de %s", path)
        diar = Diarizer(embed_model="ecapa", cluster_method="sc")
        diar_segs = diar.diarize(wav_path, num_speakers=None)
        if progress_cb:
            progress_cb(0.95)

        # Phase 3: align text to speakers
        lines = []
        for start, end, text in text_segs:
            label = _dominant_speaker(start, end, diar_segs)
            lines.append(f"[SPEAKER_{label:02d}] {text}")

        if progress_cb:
            progress_cb(1.0)
        return "\n".join(lines)

    except DiarizationError:
        raise
    except Exception as exc:
        logger.exception("simple_diarizer fallo en %s: %s", path, exc)
        raise DiarizationError(
            "Error al diferenciar hablantes. Revisa la bitacora (logs/error_log.txt)."
        ) from exc
    finally:
        if is_temp and os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def diarize_file(path: str, language: str | None = None, progress_cb=None) -> str:
    """Transcribe and label each segment by speaker.

    Tries pyannote (whisperx + HF_TOKEN) first for best quality.
    Falls back to simple_diarizer when no HF token is set.
    Raises DiarizationError when neither backend is available.
    """
    if _is_pyannote_available():
        return _diarize_pyannote(path, language, progress_cb)

    if _is_simple_available():
        logger.info("HF_TOKEN no configurado; usando simple_diarizer como alternativa")
        return _diarize_simple(path, language, progress_cb)

    raise DiarizationError(
        "No hay ningun backend de diarizacion disponible.\n"
        "Opcion 1 (mejor calidad): configurá HF_TOKEN en .env y aceptá los términos "
        "de pyannote/speaker-diarization-3.1 en huggingface.co.\n"
        "Opcion 2 (sin cuenta): pip install simple_diarizer"
    )
