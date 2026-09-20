"""Natural neural speech with an offline Windows fallback."""

from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import time

from config import logger

_state_lock = threading.Lock()
_generation = 0  # se incrementa en cada speak_async() y en cada stop()
_active_generation = None  # generación que tiene permiso de sonar ahora mismo
_active_engine = None  # motor pyttsx3 vivo, para poder cortarlo desde stop()
_speaking_count = 0

# Centinela: tests/test_tts.py (y cualquier otro caller que invoque _speak()
# directo, sin pasar por speak_async) no queda atado a ningún generation
# token. Esa llamada se considera "siempre vigente" para no romper la firma
# ni el comportamiento que esos callers ya esperan.
_ALWAYS_CURRENT = object()


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


def _is_current(generation) -> bool:
    """True si `generation` sigue siendo la locución vigente (no cancelada)."""
    if generation is _ALWAYS_CURRENT or generation is None:
        return True
    with _state_lock:
        return generation == _generation


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
        # Generación vigente al arrancar esta reproducción: stop() la corre
        # de un empujón (pygame.mixer.music.stop()) pero también la invalida
        # acá, por si get_busy() tarda un tick en reflejar el corte.
        generation = _active_generation
        while pygame.mixer.music.get_busy():
            if not _is_current(generation):
                break
            time.sleep(0.04)
        pygame.mixer.music.unload()
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def _speak_sapi(text: str, language: str) -> None:
    global _active_engine
    import pyttsx3

    engine = pyttsx3.init()
    _active_engine = engine
    try:
        _pick_voice(engine, language)
        engine.setProperty("rate", 165 if language == "es" else 175)
        engine.say(text)
        engine.runAndWait()
        engine.stop()
    finally:
        _active_engine = None


def _speak(text: str, language: str, on_done=None, _generation=_ALWAYS_CURRENT) -> None:
    global _active_generation, _speaking_count
    _active_generation = _generation
    with _state_lock:
        _speaking_count += 1
    try:
        try:
            _speak_neural(text, language)
        except Exception as exc:
            logger.warning("Neural TTS unavailable; using offline voice: %s", exc)
            _speak_sapi(text, language)
    except Exception as exc:
        logger.exception("TTS failed for text=%r: %s", text, exc)
    finally:
        with _state_lock:
            _speaking_count -= 1
        # Una locución cancelada no dispara on_done: quien llamó a stop() ya
        # avanzó el estado de la UI (por ejemplo, el alumno arrancó a
        # responder) y un on_done tardío se lo pisaría por atrás.
        if on_done and _is_current(_generation):
            on_done()


def speak_async(text: str, language: str = "en", on_done=None) -> None:
    """Speak text in a background thread."""
    clean = (text or "").strip()
    if not clean:
        return
    global _generation
    with _state_lock:
        _generation += 1
        generation = _generation
    threading.Thread(
        target=_speak,
        args=(clean, language, on_done, generation),
        daemon=True,
    ).start()


def stop() -> None:
    """Corta la locución en curso, si la hay, sin disparar su on_done.

    Es el mecanismo de "barge-in": el alumno interrumpe la lectura de la
    pregunta o de la devolución apretando un botón. Si no invalidáramos la
    generación acá, el on_done de la locución cortada llegaría igual unos
    milisegundos después (el hilo de fondo ya está corriendo) y pisaría por
    atrás el estado que el botón recién dejó. Debe ser inofensivo cuando no
    hay nada sonando: se llama también antes de arrancar una lectura nueva.
    """
    global _generation
    with _state_lock:
        _generation += 1
    try:
        import pygame

        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
    except Exception:
        pass
    try:
        if _active_engine is not None:
            _active_engine.stop()
    except Exception:
        pass


def is_speaking() -> bool:
    """True si hay una locución activa en este momento."""
    with _state_lock:
        return _speaking_count > 0
