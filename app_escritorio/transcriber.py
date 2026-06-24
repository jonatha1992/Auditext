"""Shared offline transcription backend.

A single faster-whisper model is loaded lazily and reused by both the file
transcription tab and the live transcription tab. Everything runs locally:
no network calls, no API keys, no online speech service.
"""

from __future__ import annotations

import locale
import os
import sys
import threading

from faster_whisper import WhisperModel

# Whisper language codes we expose in the UI.
_KNOWN_LANGS = {"es", "en", "pt", "fr", "de", "it", "ca", "nl", "ru", "zh", "ja"}

import audio_preprocessor


def detect_system_language() -> str | None:
    """Map the OS locale to a Whisper language code (or None if unknown).

    Used as the default so transcription starts in the user's own language
    instead of "Auto", which mis-detects on short chunks and hallucinates.
    """
    try:
        loc = locale.getdefaultlocale()[0] or ""
    except Exception:
        loc = ""
    code = loc.split("_")[0].lower()[:2] if loc else ""
    return code if code in _KNOWN_LANGS else None

# Model size: tiny | base | small | medium | large-v3.
# "small" is a good accuracy/speed balance on CPU.
MODEL_SIZE = "small"

# "cpu" runs anywhere. Switch to "cuda" if an NVIDIA GPU is available.
DEVICE = "cpu"

# int8 on CPU, float16 on GPU.
COMPUTE_TYPE = "int8" if DEVICE == "cpu" else "float16"

# Whisper works internally at 16 kHz mono.
SAMPLE_RATE = 16000

_model: WhisperModel | None = None
_model_lock = threading.Lock()


def _resolve_model_path() -> str:
    """When frozen, load from bundled local path; otherwise use HF cache."""
    if getattr(sys, "frozen", False):
        # PyInstaller 6.x puts bundled data in sys._MEIPASS (_internal/), not next to exe.
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        local = os.path.join(base, "models", "whisper-small")
        if os.path.isdir(local):
            return local
    return MODEL_SIZE


def get_model() -> WhisperModel:
    """Return the shared model, loading it on first use (thread-safe)."""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                _model = WhisperModel(
                    _resolve_model_path(), device=DEVICE, compute_type=COMPUTE_TYPE
                )
    return _model


def transcribe_file(
    path: str,
    language: str | None = None,
    translate: bool = False,
    progress_cb=None,
    should_continue=None,
) -> str:
    """Transcribe a whole audio file offline.

    faster-whisper decodes the file itself (PyAV) and applies its own voice
    activity detection, so no manual format conversion or chunking is needed.

    Args:
        path: Audio file path (mp3, wav, m4a, flac, ogg, mp4, ...).
        language: ISO code (es, en, ...) or None to auto-detect.
        translate: When True, output is translated to English (Whisper's
            built-in translate task; English is the only supported target).
        progress_cb: Optional callable(fraction_0_to_1) for progress updates.
        should_continue: Optional callable() -> bool; transcription stops early
            when it returns False.

    Returns:
        The transcribed (or translated) text.
    """
    model = get_model()
    task = "translate" if translate else "transcribe"

    # Preprocess: normalize loudness, high-pass filter, compress dynamics
    preprocessed_path, is_temp = audio_preprocessor.preprocess(path)
    try:
        segments, info = model.transcribe(
            preprocessed_path,
            language=language,
            task=task,
            vad_filter=True,
            condition_on_previous_text=False,
        )

        duration = info.duration or 0
        parts: list[str] = []
        for seg in segments:
            if should_continue is not None and not should_continue():
                break
            text = seg.text.strip()
            if text:
                parts.append(text)
            if progress_cb is not None and duration:
                progress_cb(min(seg.end / duration, 1.0))

        return " ".join(parts).strip()
    finally:
        if is_temp and os.path.exists(preprocessed_path):
            try:
                os.remove(preprocessed_path)
            except Exception:
                pass


def transcribe_file_segments(
    path: str,
    language: str | None = None,
    translate: bool = False,
    progress_cb=None,
    should_continue=None,
) -> list[tuple[float, float, str]]:
    """Like transcribe_file but returns [(start_sec, end_sec, text), ...].

    Used by the simple_diarizer backend to align timestamps with speaker segments.
    progress_cb receives values in [0, 1].
    """
    model = get_model()
    task = "translate" if translate else "transcribe"

    # Preprocess: normalize loudness, high-pass filter, compress dynamics
    preprocessed_path, is_temp = audio_preprocessor.preprocess(path)
    try:
        segments, info = model.transcribe(
            preprocessed_path, language=language, task=task,
            vad_filter=True, condition_on_previous_text=False,
        )
        duration = info.duration or 0
        result: list[tuple[float, float, str]] = []
        for seg in segments:
            if should_continue is not None and not should_continue():
                break
            text = seg.text.strip()
            if text:
                result.append((seg.start, seg.end, text))
            if progress_cb is not None and duration:
                progress_cb(min(seg.end / duration, 1.0))
        return result
    finally:
        if is_temp and os.path.exists(preprocessed_path):
            try:
                os.remove(preprocessed_path)
            except Exception:
                pass


def transcribe_array(audio, language: str | None = None, translate: bool = False):
    """Transcribe a numpy float32 mono array (16 kHz) offline.

    Used by the live tab, which feeds short captured chunks.

    Returns:
        (texts, detected_language): a list of non-empty text segments and the
        language Whisper used/detected (so the caller can lock onto it).
    """
    model = get_model()
    task = "translate" if translate else "transcribe"
    segments, info = model.transcribe(
        audio,
        language=language,
        task=task,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    texts = [seg.text.strip() for seg in segments if seg.text.strip()]
    return texts, info.language
