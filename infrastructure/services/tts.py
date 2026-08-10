"""Natural neural speech with an offline Windows fallback."""

from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import time

from config import logger


def neural_profile(language: str) -> tuple[str, str, str]:
    """Return a calm, conversational voice profile for teaching."""
    if language == "es":
        return "es-AR-ElenaNeural", "-8%", "-2Hz"
    return "en-US-AvaNeural", "-6%", "-1Hz"


def _pick_voice(engine, language: str) -> None:
    try:
        for voice in engine.getProperty("voices"):
            langs = " ".join(
                str(value).lower()
                for value in (getattr(voice, "languages", None) or [])
            )
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


def _speak_neural(text: str, language: str) -> None:
    import edge_tts
    import pygame

    voice, rate, pitch = neural_profile(language)
    path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as audio:
            path = audio.name
        communicate = edge_tts.Communicate(
            text, voice=voice, rate=rate, pitch=pitch
        )
        asyncio.run(communicate.save(path))
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        pygame.mixer.music.load(path)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            time.sleep(0.04)
        pygame.mixer.music.unload()
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def _speak_sapi(text: str, language: str) -> None:
    import pyttsx3

    engine = pyttsx3.init()
    _pick_voice(engine, language)
    engine.setProperty("rate", 165 if language == "es" else 175)
    engine.say(text)
    engine.runAndWait()
    engine.stop()


def _speak(text: str, language: str, on_done=None) -> None:
    try:
        try:
            _speak_neural(text, language)
        except Exception as exc:
            logger.warning("Neural TTS unavailable; using offline voice: %s", exc)
            _speak_sapi(text, language)
    except Exception as exc:
        logger.exception("TTS failed for text=%r: %s", text, exc)
    finally:
        if on_done:
            on_done()


def speak_async(text: str, language: str = "en", on_done=None) -> None:
    """Speak text in a background thread."""
    clean = (text or "").strip()
    if not clean:
        return
    threading.Thread(
        target=_speak,
        args=(clean, language, on_done),
        daemon=True,
    ).start()
