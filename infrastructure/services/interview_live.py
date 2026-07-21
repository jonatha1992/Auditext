"""Gemini Live interview coach — realtime system-audio assist.

Live API models only support AUDIO response modality. We:
1. Stream PCM into Live for low-latency input transcription (interviewer EN).
2. Discard model audio (never play it — interviewer must not hear the coach).
3. On each interviewer turn, call generate_content (flash) for Spanish gloss +
   2 short English reply suggestions as JSON.

Keys: GEMINI_API_KEY / _2 / _3 or GEMINI_API_KEY1/2/3.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
from dataclasses import dataclass
from typing import Callable

import numpy as np

from config import logger
from infrastructure.services import gemini_keys

LIVE_MODELS = [
    os.getenv("GEMINI_LIVE_MODEL", "").strip() or "gemini-3.1-flash-live-preview",
    "gemini-2.5-flash-native-audio-latest",
    "gemini-2.5-flash-native-audio-preview-12-2025",
    "gemini-2.5-flash-native-audio-preview-09-2025",
]

# Latency-sensitive: lite models first, thinking disabled (see _coach_config).
COACH_MODELS = [
    os.getenv("GEMINI_COACH_MODEL", "").strip() or "gemini-2.5-flash-lite",
    "gemini-flash-lite-latest",
    os.getenv("GEMINI_MODEL", "").strip() or "gemini-2.5-flash",
    "gemini-2.0-flash-lite",
]

_LIVE_SYSTEM = """You are listening to an English speaker via system audio.
Stay silent / minimal. Do not coach out loud. The client app will handle coaching separately.
Acknowledge briefly only if needed; prefer no spoken reply.
"""

DEFAULT_ASSIST_MODE = "entrevista"

_MODE_INSTRUCTIONS = {
    "entrevista": (
        "Sos coach de entrevista laboral para un candidato hispanohablante. "
        "El INTERLOCUTOR es el entrevistador. Las respuestas deben ayudar al candidato "
        "a impresionar: profesionales, concretas, apoyadas en su CV/contexto. "
        "ideas_clave: puntos del CV o de la experiencia del candidato relevantes al turno."
    ),
    "practica": (
        "Sos asistente de práctica de idioma para un hispanohablante que practica inglés. "
        "El INTERLOCUTOR puede ser un profesor, tutor o compañero de práctica. "
        "Si pide un ejercicio o ejemplo (p. ej. 'try saying that'), las respuestas deben CUMPLIR "
        "el ejercicio con frases naturales en inglés. Si pregunta algo, las respuestas la contestan. "
        "ideas_clave: tips breves de vocabulario, gramática o pronunciación relevantes al turno."
    ),
    "general": (
        "Sos copiloto de conversación para un hispanohablante que conversa en inglés. "
        "El INTERLOCUTOR puede ser cualquiera (reunión, llamada, charla). "
        "Las respuestas deben ser naturales, adecuadas al contexto pegado por el usuario. "
        "ideas_clave: puntos del contexto o de la conversación útiles para el próximo turno."
    ),
    "prueba_oral": (
        "Sos asistente de PRÁCTICA para pruebas orales. El usuario estudia respondiendo en voz alta; "
        "el INTERLOCUTOR hace de examinador. El contexto pegado es el temario o material de estudio. "
        "REGLA CENTRAL: NO des respuestas completas — el usuario debe formular con sus palabras. "
        "ideas_clave es el campo principal: 3 o 4 conceptos o palabras clave del temario que responden "
        "la pregunta, ordenados como esqueleto de respuesta. "
        "respuestas: SOLO arranques de frase cortos (máximo 6 palabras, terminados en ...) en el idioma "
        "en que habla el interlocutor, p. ej. 'El concepto central es...' o 'The main idea is...'. "
        "pregunta_es: glosa clara de la pregunta del examinador."
    ),
}

_COACH_PROMPT = """{role_instructions}

Contexto pegado por el usuario (CV, puesto, tema de práctica, apuntes...):
---
{context}
---

Texto reciente del INTERLOCUTOR (STT en inglés, puede tener ruido):
---
{utterance}
---

Conversación reciente, separada por rol:
---
{history}
---

Respondé SOLO un JSON válido, sin markdown:
{{"pregunta_es":"glosa clara en español de lo que dijo o pidió el interlocutor","respuestas":["respuesta recomendada en inglés","alternativa breve en inglés"],"ideas_clave":["idea relevante 1","idea relevante 2"],"frase_puente":"frase breve en inglés para ganar tiempo o pedir aclaración"}}

Reglas: exactamente 2 respuestas, 1-2 oraciones cada una, naturales para decir en voz alta.
Respondé SIEMPRE que el turno tenga contenido: pregunta, instrucción, ejercicio o comentario.
Devolvé pregunta_es vacía y respuestas [] SOLO si el texto es ruido ininteligible.
Usá el historial para mantener el hilo y no repetir respuestas. Priorizá frases fáciles de pronunciar.
"""


@dataclass
class InterviewAssist:
    pregunta_es: str
    respuestas: list[str]
    ideas_clave: list[str] | None = None
    frase_puente: str = ""


class InterviewLiveError(Exception):
    """Live session cannot start or all keys/models failed."""


def float32_to_pcm16(audio: np.ndarray) -> bytes:
    """Convert mono float32 [-1,1] to little-endian 16-bit PCM."""
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    pcm = np.clip(audio.astype(np.float32) * 32767.0, -32768, 32767).astype(np.int16)
    return pcm.tobytes()


def parse_assist_text(text: str) -> InterviewAssist | None:
    """Extract InterviewAssist from model TEXT (JSON, optionally fenced)."""
    raw = (text or "").strip()
    if not raw:
        return None

    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    if fence:
        raw = fence.group(1).strip()

    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    blob = raw[start : end + 1]
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return None

    pregunta = str(data.get("pregunta_es") or "").strip()
    respuestas_raw = data.get("respuestas") or []
    if not isinstance(respuestas_raw, list):
        respuestas_raw = [respuestas_raw]
    respuestas = [str(r).strip() for r in respuestas_raw if str(r).strip()][:2]
    ideas_raw = data.get("ideas_clave") or []
    if not isinstance(ideas_raw, list):
        ideas_raw = [ideas_raw]
    ideas = [str(v).strip() for v in ideas_raw if str(v).strip()][:3]
    frase_puente = str(data.get("frase_puente") or "").strip()
    if not pregunta and not respuestas:
        return None
    return InterviewAssist(pregunta, respuestas, ideas, frase_puente)


def is_configured() -> bool:
    return gemini_keys.is_configured()


# Reuse one client per key: avoids TLS/session setup on every coach call.
_client_cache: dict[str, object] = {}


def _get_client(genai, api_key: str):
    client = _client_cache.get(api_key)
    if client is None:
        client = genai.Client(api_key=api_key)
        _client_cache[api_key] = client
    return client


def _coach_config(model: str):
    """Low-latency generation config. thinking_budget=0 skips the multi-second
    default 'thinking' phase on 2.5+ models; 2.0 models reject the field."""
    from google.genai import types

    kwargs = dict(
        temperature=0.4,
        max_output_tokens=400,
        response_mime_type="application/json",
    )
    if "2.0" not in model:
        kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
    return types.GenerateContentConfig(**kwargs)


def coach_assist(
    context: str,
    utterance: str,
    api_key: str | None = None,
    conversation_history: str = "",
    mode: str = DEFAULT_ASSIST_MODE,
) -> InterviewAssist | None:
    """Sync generate_content coach call (JSON). Tries keys + model fallbacks."""
    utterance = (utterance or "").strip()
    if len(utterance.split()) < 2:
        return None
    try:
        from google import genai
    except ImportError:
        return None

    prompt = _COACH_PROMPT.format(
        role_instructions=_MODE_INSTRUCTIONS.get(mode, _MODE_INSTRUCTIONS[DEFAULT_ASSIST_MODE]),
        context=(context or "").strip() or "(sin contexto)",
        utterance=utterance,
        history=(conversation_history or "").strip() or "(sin historial previo)",
    )
    models = []
    for m in COACH_MODELS:
        if m and m not in models:
            models.append(m)

    keys: list[str] = []
    if api_key:
        keys.append(api_key)
    for k in gemini_keys.pool.available():
        if k not in keys:
            keys.append(k)
    if not keys:
        return None

    last_exc = None
    for key in keys:
        client = _get_client(genai, key)
        for model in models:
            try:
                resp = client.models.generate_content(
                    model=model, contents=prompt, config=_coach_config(model)
                )
                return parse_assist_text(resp.text or "")
            except Exception as exc:
                last_exc = exc
                if gemini_keys.pool.is_quota_error(exc):
                    logger.warning("Coach quota on model=%s key=%s", model, gemini_keys.pool.current_label())
                    if model == models[-1]:
                        gemini_keys.pool.mark_exhausted(key)
                    continue
                logger.warning("Coach model %s failed: %s", model, exc)
                continue
    if last_exc and gemini_keys.pool.is_quota_error(last_exc):
        raise last_exc
    if last_exc:
        logger.exception("Coach assist failed: %s", last_exc)
    return None


class InterviewLiveSession:
    """Background asyncio Live session fed with PCM from capture thread."""

    def __init__(
        self,
        context: str,
        on_transcript: Callable[[str], None],
        on_assist: Callable[[InterviewAssist], None],
        on_status: Callable[[str], None],
        mode: str = DEFAULT_ASSIST_MODE,
    ):
        self._context = (context or "").strip() or "(sin contexto del candidato)"
        self._mode = mode if mode in _MODE_INSTRUCTIONS else DEFAULT_ASSIST_MODE
        self._on_transcript = on_transcript
        self._on_assist = on_assist
        self._on_status = on_status
        self._audio_q: asyncio.Queue[bytes | None] | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = threading.Event()
        self._utterance_buf = ""
        self._api_key: str | None = None
        self._coach_lock = asyncio.Lock()
        self._coach_task: asyncio.Task | None = None
        self._pending_utterance: str | None = None
        self._history: list[str] = []
        self._history_lock = threading.Lock()

    def add_candidate_turn(self, text: str) -> None:
        """Add microphone speech to context without mixing speaker roles."""
        clean = (text or "").strip()
        if not clean:
            return
        with self._history_lock:
            self._history.append(f"CANDIDATO: {clean}")
            self._history = self._history[-12:]

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not is_configured():
            raise InterviewLiveError(
                "Falta GEMINI_API_KEY / GEMINI_API_KEY1. "
                "Configurala en .env (podés sumar KEY2 / KEY3)."
            )
        self._stop.clear()
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        loop = self._loop
        q = self._audio_q
        if loop and q is not None and loop.is_running():
            def _poison():
                try:
                    q.put_nowait(None)
                except Exception:
                    pass
            loop.call_soon_threadsafe(_poison)
        if self._thread:
            self._thread.join(timeout=8)
            self._thread = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def send_audio(self, pcm: bytes) -> None:
        if not pcm or self._stop.is_set():
            return
        loop = self._loop
        q = self._audio_q
        if loop is None or q is None or not loop.is_running():
            return

        def _put():
            try:
                q.put_nowait(pcm)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(pcm)
                except Exception:
                    pass

        loop.call_soon_threadsafe(_put)

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run())
        except Exception as exc:
            logger.exception("Interview Live thread crashed: %s", exc)
            self._on_status(f"Error Live: {exc}")
        finally:
            try:
                loop.close()
            except Exception:
                pass
            self._loop = None
            self._audio_q = None

    async def _run(self) -> None:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise InterviewLiveError(
                "Falta google-genai (pip install google-genai)."
            ) from exc

        gemini_keys.pool.reload()
        keys = list(gemini_keys.pool.available())
        if not keys:
            raise InterviewLiveError("No hay claves Gemini disponibles.")

        models = [m for m in LIVE_MODELS if m]
        last_exc: BaseException | None = None

        for key in keys:
            if self._stop.is_set():
                return
            self._api_key = key
            for model in models:
                if self._stop.is_set():
                    return
                try:
                    self._on_status(
                        f"Conectando Live ({gemini_keys.pool.current_label()})..."
                    )
                    client = genai.Client(api_key=key)
                    config = types.LiveConnectConfig(
                        response_modalities=[types.Modality.AUDIO],
                        system_instruction=_LIVE_SYSTEM,
                        input_audio_transcription=types.AudioTranscriptionConfig(),
                        output_audio_transcription=types.AudioTranscriptionConfig(),
                    )
                    async with client.aio.live.connect(model=model, config=config) as session:
                        self._on_status(
                            f"Entrevista Live activa ({gemini_keys.pool.current_label()})"
                        )
                        logger.info("Interview Live connected model=%s", model)
                        self._audio_q = asyncio.Queue(maxsize=80)
                        self._utterance_buf = ""
                        await self._session_loop(session, types)
                    return
                except Exception as exc:
                    last_exc = exc
                    detail = str(exc).lower()
                    if gemini_keys.pool.is_quota_error(exc):
                        gemini_keys.pool.mark_exhausted(key)
                        self._on_status(
                            f"Cuota agotada ({gemini_keys.pool.current_label()}), rotando..."
                        )
                        break
                    if "not found" in detail or "not supported" in detail or "1007" in detail or "1008" in detail:
                        logger.warning("Live model unavailable %s: %s", model, exc)
                        continue
                    logger.exception("Live connect failed: %s", exc)
                    if "api key" in detail or "401" in detail or "403" in detail or "permission" in detail:
                        gemini_keys.pool.mark_exhausted(key)
                        break
                    continue

        msg = "No se pudo conectar a Gemini Live con las keys/modelos disponibles."
        if last_exc:
            msg = f"{msg} ({last_exc})"
        self._on_status(msg)
        raise InterviewLiveError(msg) from last_exc

    async def _session_loop(self, session, types) -> None:
        assert self._audio_q is not None
        send_task = asyncio.create_task(self._send_loop(session, types))
        recv_task = asyncio.create_task(self._recv_loop(session))
        try:
            done, pending = await asyncio.wait(
                {send_task, recv_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            for t in done:
                exc = t.exception() if not t.cancelled() else None
                if exc and not self._stop.is_set():
                    raise exc
        finally:
            send_task.cancel()
            recv_task.cancel()
            if self._coach_task and not self._coach_task.done():
                self._coach_task.cancel()

    async def _send_loop(self, session, types) -> None:
        assert self._audio_q is not None
        while not self._stop.is_set():
            chunk = await self._audio_q.get()
            if chunk is None:
                break
            try:
                await session.send_realtime_input(
                    audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
                )
            except Exception:
                if self._stop.is_set():
                    return
                raise

    async def _recv_loop(self, session) -> None:
        while not self._stop.is_set():
            async for msg in session.receive():
                if self._stop.is_set():
                    return
                await self._handle_message(msg)

    async def _handle_message(self, msg) -> None:
        sc = getattr(msg, "server_content", None)
        if sc is None:
            return

        # Input transcription = interviewer speech (what we care about)
        in_tx = getattr(sc, "input_transcription", None)
        if in_tx is not None:
            t = (getattr(in_tx, "text", None) or "").strip()
            if t:
                self._utterance_buf = (self._utterance_buf + " " + t).strip()
                self._on_transcript(t)

        # Intentionally ignore model audio / output transcription (never play coach).

        if getattr(sc, "turn_complete", False) or getattr(sc, "generation_complete", False):
            buf = self._utterance_buf.strip()
            self._utterance_buf = ""
            if buf and self._api_key:
                with self._history_lock:
                    self._history.append(f"ENTREVISTADOR: {buf}")
                    self._history = self._history[-12:]
                # Latest-wins: never cancel an in-flight coach call. If one is
                # running, stash the newest utterance; _run_coach drains it.
                if self._coach_task and not self._coach_task.done():
                    self._pending_utterance = buf
                else:
                    self._coach_task = asyncio.create_task(self._run_coach(buf))

    async def _run_coach(self, utterance: str) -> None:
        async with self._coach_lock:
            while True:
                if self._stop.is_set() or not self._api_key:
                    return
                await self._coach_once(utterance)
                if self._pending_utterance is None:
                    return
                utterance, self._pending_utterance = self._pending_utterance, None

    async def _coach_once(self, utterance: str) -> None:
        self._on_status("Generando sugerencias...")
        try:
            with self._history_lock:
                history = "\n".join(self._history[-6:])
            assist = await asyncio.to_thread(
                coach_assist,
                self._context,
                utterance,
                self._api_key,
                history,
                self._mode,
            )
            if assist:
                self._on_assist(assist)
            self._on_status(
                f"Entrevista Live activa ({gemini_keys.pool.current_label()})"
            )
        except Exception as exc:
            if gemini_keys.pool.is_quota_error(exc):
                nxt = gemini_keys.pool.mark_exhausted(self._api_key)
                if nxt:
                    self._api_key = nxt
                    self._on_status(f"Cuota coach: rotando a {gemini_keys.pool.current_label()}")
                else:
                    self._on_status("Cuota agotada en todas las keys (coach)")
            else:
                logger.exception("Coach call failed: %s", exc)
                self._on_status(f"Error coach: {exc}")
