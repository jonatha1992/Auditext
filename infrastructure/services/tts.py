"""Offline text-to-speech (SAPI5 via pyttsx3) for pronunciation playback."""

from __future__ import annotations

import threading

from config import logger


def _pick_english_voice(engine) -> None:
    try:
        for voice in engine.getProperty("voices"):
            langs = " ".join(str(v).lower() for v in (getattr(voice, "languages", None) or []))
            if "en" in langs or "english" in (voice.name or "").lower():
                engine.setProperty("voice", voice.id)
                return
    except Exception:
        pass


def _speak(text: str) -> None:
    try:
        import pyttsx3
    except ImportError:
        logger.warning("pyttsx3 no está instalado; no se puede reproducir pronunciación.")
        return
    try:
        engine = pyttsx3.init()
        _pick_english_voice(engine)
        engine.say(text)
        engine.runAndWait()
        engine.stop()
    except Exception as exc:
        logger.exception("TTS failed for text=%r: %s", text, exc)


def speak_async(text: str) -> None:
    """Speak `text` on a background thread (non-blocking, one engine per call)."""
    clean = (text or "").strip()
    if not clean:
        return
    threading.Thread(target=_speak, args=(clean,), daemon=True).start()
