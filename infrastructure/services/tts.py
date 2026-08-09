"""Offline text-to-speech (SAPI5 via pyttsx3) for pronunciation playback."""

from __future__ import annotations

import threading

from config import logger


def _pick_voice(engine, language: str) -> None:
    try:
        for voice in engine.getProperty("voices"):
            langs = " ".join(str(v).lower() for v in (getattr(voice, "languages", None) or []))
            name = (voice.name or "").lower()
            wanted = (
                ("es" in langs or "spanish" in name or "español" in name)
                if language == "es"
                else ("en" in langs or "english" in name)
            )
            if wanted:
                engine.setProperty("voice", voice.id)
                return
    except Exception:
        pass


def _speak(text: str, language: str, on_done=None) -> None:
    try:
        import pyttsx3
    except ImportError:
        logger.warning("pyttsx3 no está instalado; no se puede reproducir pronunciación.")
        if on_done:
            on_done()
        return
    try:
        engine = pyttsx3.init()
        _pick_voice(engine, language)
        engine.say(text)
        engine.runAndWait()
        engine.stop()
    except Exception as exc:
        logger.exception("TTS failed for text=%r: %s", text, exc)
    finally:
        if on_done:
            on_done()


def speak_async(text: str, language: str = "en", on_done=None) -> None:
    """Speak `text` on a background thread (non-blocking, one engine per call)."""
    clean = (text or "").strip()
    if not clean:
        return
    threading.Thread(target=_speak, args=(clean, language, on_done), daemon=True).start()
