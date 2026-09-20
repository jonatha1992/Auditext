"""Focused, dual-source interview coaching experience."""

from __future__ import annotations

import ctypes
import queue
import re
import sys
import threading
import time
import unicodedata
from pathlib import Path

import numpy as np
import soundcard as sc
import tkinter as tk
import customtkinter as ctk

import config
from config import logger
from core.domain.entities import TranscriptionRecord
from infrastructure.services import interview_live, interview_simulation, notebooklm_service, tts
from infrastructure.services.microphone_test import (
    choose_preferred_microphone_label,
    load_preferred_microphone,
)
from .app_dialog import ask_input, show_error, show_info, show_warning
from .loading import LoadingText
from infrastructure.services.live_transcriber import (
    DEFAULT_DIR,
    Transcriber,
    WHOLE_SYSTEM_LABEL,
)


BG = "#0B0C10"
PANEL = "#15161E"
PANEL_DARK = "#11121A"
BORDER = "#2A2B36"
TEXT = "#FFFFFF"
MUTED = "#8A8F9E"
ACCENT = "#7000FF"
ACCENT_HOVER = "#5900CC"
SUCCESS = "#4EC98A"
WARNING = "#F6AD55"
ERROR = "#E0506A"
INTERVIEWER = "#4DA3FF"
CANDIDATE = "#4EC98A"
REC = "#E0506A"

# Texto de la tarjeta de ideas clave cuando el turno no trae ninguna.
_NO_IDEAS = "Enfocate en una experiencia concreta y su resultado."

# Cuánto se espera con la devolución en pantalla antes de pasar sola a la
# próxima pregunta del práctico oral académico. En None el avance queda
# 100% manual: el estudiante decide cuándo apretar "Siguiente pregunta".
_NEXT_QUESTION_GRACE_MS: int | None = 4000

CONTEXT_SETTING_KEY = "interview_context"
CONTEXT_PLACEHOLDER = "Pegá tu CV o el contexto de la sesión (puesto, empresa, tema a practicar...)."

MODE_SETTING_KEY = "interview_assist_mode"
RESOLVER_MODE_SETTING_KEY = "resolver_assist_mode"
NOTEBOOK_ID_SETTING_KEY = "notebooklm_selected_id"
NOTEBOOK_TITLE_SETTING_KEY = "notebooklm_selected_title"
NOTEBOOK_PLACEHOLDER = "Seleccioná una materia…"
NOTEBOOK_CONTEXT_SETTING_PREFIX = "notebooklm_context_"
NOTEBOOK_PROFILE_SETTING_KEY = "notebooklm_profile"
PROFILE_PLACEHOLDER = "Conectá una cuenta…"


def notebook_id_setting_key(profile: str | None) -> str:
    """Clave de la materia elegida, separada por cuenta.

    Cada cuenta ve su propio catálogo, así que una sola clave global hacía que
    al cambiar de cuenta se restaurara una materia que esa cuenta no tiene.
    """
    return f"{NOTEBOOK_ID_SETTING_KEY}_{profile}" if profile else NOTEBOOK_ID_SETTING_KEY
MODE_LABELS = {
    "Entrevista laboral": "entrevista",
    "Práctica de idioma": "practica",
    "Conversación general": "general",
    "Simulacro con IA": "simulacro",
    "Examen oral (respuestas completas)": "examen_oral",
    "Práctica oral con profesor IA": "practica_oral",
    "Resolver preguntas": "resolver",
}

INTERVIEW_MODES = ("entrevista", "practica", "general", "simulacro")
RESOLVER_MODES = ("resolver", "examen_oral")
SIMULATION_MODES = frozenset({"simulacro", "practica_oral"})

LANG_SETTING_KEY = "interview_answer_lang"
LANG_LABELS = {
    "Español": "es",
    "Inglés": "en",
    "El del examinador (automático)": "auto",
}

# Cada modo pide un contexto distinto. El CV solo tiene sentido en una entrevista
# laboral; en los demás modos pedimos el tema/situación en lugar del CV.
# 'default_lang' es solo el valor inicial: el usuario puede cambiarlo y su
# elección queda guardada por modo.
MODE_CONTEXT = {
    "entrevista": {
        "label": "📄  CV / CONTEXTO",
        "color": "#F6AD55",
        "placeholder": "Pegá tu CV o el contexto de la sesión (puesto, empresa, tema a practicar...).",
        "show_cv": True,
        "default_lang": "en",
    },
    "practica": {
        "label": "🗣  TEMA A PRACTICAR",
        "color": "#4EC98A",
        "placeholder": "¿Qué querés practicar? (tema, situación, nivel de inglés, palabras que te cuestan...).",
        "show_cv": False,
        "default_lang": "en",
    },
    "general": {
        "label": "💬  TEMA DE CONVERSACIÓN",
        "color": "#63B3ED",
        "placeholder": "¿De qué querés hablar? (tema, contexto de la charla...).",
        "show_cv": False,
        "default_lang": "en",
    },
    "simulacro": {
        "label": "🎯  PUESTO / CV / CONTEXTO",
        "color": "#A78BFA",
        "placeholder": "Describí el puesto, la empresa, tu experiencia y qué querés practicar.",
        "show_cv": True,
        "default_lang": "es",
    },
    "examen_oral": {
        "label": "📚  PROGRAMA / TEMARIO / CRONOGRAMA",
        "color": "#F6AD55",
        "placeholder": "Pegá el programa, temario o cronograma de la materia (unidades, temas, objetivos, bibliografía...).",
        "show_cv": True,
        "file_button": "📂  Cargar programa…",
        "file_title": "programa, temario o cronograma",
        "default_lang": "es",
    },
    "practica_oral": {
        "label": "🧑‍🏫  TEMARIO PARA EL PROFESOR IA",
        "color": "#A78BFA",
        "placeholder": "Pegá el temario, apuntes o conceptos que querés practicar oralmente.",
        "show_cv": True,
        "file_button": "📂  Cargar material…",
        "file_title": "material para la práctica oral",
        "default_lang": "es",
    },
    "resolver": {
        "label": "📚  TEMA / PROGRAMA / APUNTES",
        "color": "#4DA3FF",
        "placeholder": "Pegá el tema, programa de la materia, cronograma, apuntes o material para responder...",
        "show_cv": True,
        "file_button": "📂  Cargar material o programa…",
        "file_title": "material o programa de estudio",
        "default_lang": "es",
    },
}

# Cualquiera de estos textos cuenta como "vacío" al iniciar la sesión.
ALL_PLACEHOLDERS = {CONTEXT_PLACEHOLDER} | {c["placeholder"] for c in MODE_CONTEXT.values()}


def _candidate_sample_rates(device: dict, target_rate: int) -> list[int]:
    """Prefer the hardware rate, then fall back to the STT target rate."""
    try:
        default_rate = int(float(device.get("default_samplerate", 0)))
    except (TypeError, ValueError):
        default_rate = 0
    return list(dict.fromkeys(rate for rate in (default_rate, target_rate) if rate > 0))


def _resample_mono(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    mono = np.asarray(audio, dtype=np.float32)
    if not mono.size or source_rate == target_rate:
        return mono
    output_size = max(1, round(len(mono) * target_rate / source_rate))
    source_points = np.linspace(0.0, 1.0, len(mono), endpoint=False)
    target_points = np.linspace(0.0, 1.0, output_size, endpoint=False)
    return np.interp(target_points, source_points, mono).astype(np.float32)


def _new_transcript_suffix(previous: str, current: str) -> str:
    """Remove words repeated by overlapping audio windows."""
    previous_words = previous.split()
    current_words = current.split()
    limit = min(len(previous_words), len(current_words), 12)
    for size in range(limit, 0, -1):
        left = [word.casefold().strip(".,;:¿?¡!") for word in previous_words[-size:]]
        right = [word.casefold().strip(".,;:¿?¡!") for word in current_words[:size]]
        if left == right:
            return " ".join(current_words[size:])
    return current.strip()


class CandidateListener:
    """Transcribe the candidate microphone locally and preserve speaker identity."""

    def __init__(self, on_text, on_status, on_audio=None):
        self._on_text = on_text
        self._on_status = on_status
        self._on_audio = on_audio
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread: threading.Thread | None = None
        self._transcribe_thread: threading.Thread | None = None
        self._audio_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=12)
        self._transcribing = threading.Event()
        self._capture_lock = threading.Lock()
        self._capture_chunks: list[np.ndarray] = []
        self._language: str | None = None

    def start(
        self, microphone_name: str | None, language: str | None = None
    ) -> None:
        if self.is_running():
            return
        self._stop.clear()
        self._paused.clear()
        self._language = language
        self._capture_chunks = []
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
                self._audio_queue.task_done()
            except queue.Empty:
                break
        self._transcribe_thread = threading.Thread(
            target=self._transcribe_loop,
            daemon=True,
        )
        self._transcribe_thread.start()
        self._thread = threading.Thread(
            target=self._run,
            args=(microphone_name, language),
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._queue_pending_audio()
        self._stop.set()
        self._paused.clear()

    def pause(self) -> None:
        if self.is_running():
            self._paused.set()
            self._queue_pending_audio()

    def resume(self) -> None:
        self._paused.clear()

    def is_paused(self) -> bool:
        return self._paused.is_set()

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _queue_audio(self, audio: np.ndarray) -> None:
        if not audio.size or float(np.abs(audio).mean()) < 0.001:
            return
        try:
            self._audio_queue.put_nowait(audio.copy())
        except queue.Full:
            logger.warning("Candidate transcription queue full; preserving newest audio")
            try:
                self._audio_queue.get_nowait()
                self._audio_queue.task_done()
                self._audio_queue.put_nowait(audio.copy())
            except queue.Empty:
                pass

    def _queue_pending_audio(self) -> None:
        with self._capture_lock:
            if not self._capture_chunks:
                return
            audio = np.concatenate(self._capture_chunks)
            self._capture_chunks = []
        self._queue_audio(audio)

    def wait_until_idle(self, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._audio_queue.unfinished_tasks == 0 and not self._transcribing.is_set():
                return True
            time.sleep(0.03)
        return False

    def _transcribe_loop(self) -> None:
        previous = ""
        try:
            service = config.transcription_service
            if service is None:
                raise RuntimeError("El servicio de transcripción local no está disponible.")
            self._on_status("Preparando reconocimiento de tu voz...")
            service.get_model()
            while not self._stop.is_set() or not self._audio_queue.empty():
                try:
                    audio = self._audio_queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                self._transcribing.set()
                try:
                    texts, _ = service.transcribe_array(
                        audio, language=self._language, translate=False
                    )
                    clean = " ".join(
                        str(text).strip() for text in texts if str(text).strip()
                    )
                    suffix = _new_transcript_suffix(previous, clean)
                    if suffix:
                        self._on_text(suffix)
                    if clean:
                        previous = clean
                finally:
                    self._transcribing.clear()
                    self._audio_queue.task_done()
        except Exception as exc:
            logger.exception("Candidate transcription worker failed: %s", exc)
            self._on_status(f"Error de reconocimiento: {exc}")

    def _run(
        self, microphone_name: str | None, language: str | None
    ) -> None:
        com_initialized = False
        if sys.platform == "win32":
            try:
                # Inicializar COM como COINIT_MULTITHREADED (0x0): soundcard usa
                # objetos COM que requieren inicialización por-hilo, y este hilo
                # es distinto al que ya inicializó COM al importar el módulo.
                hr = ctypes.windll.ole32.CoInitializeEx(None, 0)
                if hr >= 0:
                    com_initialized = True
            except Exception as exc:
                logger.exception("CoInitializeEx falló: %s", exc)
        try:
            mic = sc.default_microphone()
            if microphone_name:
                mic = next(
                    (m for m in sc.all_microphones() if m.name == microphone_name),
                    mic,
                )
            sample_rate = config.SAMPLE_RATE
            block = int(sample_rate * 0.5)
            last_silence_log = 0.0

            def consume(mono: np.ndarray) -> None:
                nonlocal last_silence_log
                if self._paused.is_set():
                    return
                if self._on_audio is not None:
                    self._on_audio(mono)
                with self._capture_lock:
                    self._capture_chunks.append(mono)
                    if len(self._capture_chunks) < 8:
                        return
                    audio = np.concatenate(self._capture_chunks)
                    self._capture_chunks = self._capture_chunks[-2:]
                level = float(np.abs(audio).mean())
                if level < 0.001:
                    now = time.monotonic()
                    if now - last_silence_log >= 10.0:
                        logger.info(
                            "Candidate mic level too low to transcribe: %.6f",
                            level,
                        )
                        last_silence_log = now
                    return
                self._queue_audio(audio)

            self._on_status(f"Micrófono activo: {mic.name[:32]}")
            try:
                with mic.recorder(samplerate=sample_rate) as recorder:
                    while not self._stop.is_set():
                        data = recorder.record(numframes=block)
                        consume(data.mean(axis=1).astype(np.float32))
            except AssertionError:
                logger.warning(
                    "Soundcard rejected microphone format; using "
                    "sounddevice fallback for %s",
                    mic.name,
                )
                try:
                    self._capture_with_sounddevice(
                        mic.name, sample_rate, block, consume
                    )
                except RuntimeError as exc:
                    logger.warning(
                        "sounddevice could not capture %s; using PyAudio: %s",
                        mic.name,
                        exc,
                    )
                    self._capture_with_pyaudio(
                        mic.name, sample_rate, block, consume
                    )
        except Exception as exc:
            logger.exception("Candidate microphone transcription failed: %s", exc)
            self._on_status(f"Error de micrófono: {exc}")
        finally:
            self._queue_pending_audio()
            self._stop.set()
            if com_initialized:
                try:
                    ctypes.windll.ole32.CoUninitialize()
                except Exception:
                    pass

    def _capture_with_sounddevice(
        self,
        microphone_name: str,
        sample_rate: int,
        block: int,
        consume,
    ) -> None:
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError(
                "El micrófono requiere sounddevice como motor alternativo."
            ) from exc

        input_devices = [
            (index, device)
            for index, device in enumerate(sd.query_devices())
            if int(device.get("max_input_channels", 0)) > 0
        ]
        exact = [
            item
            for item in input_devices
            if item[1].get("name") == microphone_name
        ]
        prefix = microphone_name[:28].casefold()
        similar = [
            item
            for item in input_devices
            if prefix in str(item[1].get("name", "")).casefold()
            and item not in exact
        ]
        # Soundcard and sounddevice name the same Windows endpoint differently
        # (for example "PD200X Podcast Microphone" vs
        # "Micrófono (PD200X Podcast Microphone)"). Match distinctive tokens
        # so we do not silently fall back to the first virtual mapper.
        def normalized_tokens(value: str) -> set[str]:
            value = unicodedata.normalize("NFKD", value.casefold())
            plain = "".join(ch for ch in value if not unicodedata.combining(ch))
            return {
                token for token in re.findall(r"[a-z0-9]+", plain)
                if len(token) >= 4 and token not in {"microfono", "microphone"}
            }

        wanted = normalized_tokens(microphone_name)
        token_matches = [
            item for item in input_devices
            if wanted & normalized_tokens(str(item[1].get("name", "")))
            and item not in exact
            and item not in similar
        ]
        candidates = exact + token_matches + similar
        default_input = getattr(sd.default, "device", (-1, -1))[0]
        if not candidates:
            candidates = sorted(
                input_devices, key=lambda item: item[0] != default_input
            )
        else:
            # Windows publica el mismo micrófono mediante varios host APIs.
            # El endpoint predeterminado suele ser el único con señal válida;
            # conservar el orden anterior hacía que DirectSound/WASAPI con
            # audio vacío ganaran solo por tener el nombre completo.
            candidates.sort(key=lambda item: item[0] != default_input)
        last_exc: Exception | None = None

        for device_index, device in candidates:
            for capture_rate in _candidate_sample_rates(device, sample_rate):
              try:
                capture_block = max(1, round(capture_rate * block / sample_rate))
                with sd.InputStream(
                    samplerate=capture_rate,
                    blocksize=capture_block,
                    device=device_index,
                    channels=1,
                    dtype="float32",
                ) as stream:
                    self._on_status(
                        "Micrófono activo (alternativo): "
                        f"{str(device['name'])[:28]}"
                    )
                    logger.info(
                        "Candidate microphone fallback device=%s index=%s capture_rate=%s target_rate=%s",
                        device["name"],
                        device_index,
                        capture_rate,
                        sample_rate,
                    )
                    exact_zero_blocks = 0
                    while not self._stop.is_set():
                        started = time.monotonic()
                        data, overflowed = stream.read(capture_block)
                        if overflowed:
                            logger.warning(
                                "Candidate microphone input overflow"
                            )
                        mono = data[:, 0].astype(np.float32)
                        if mono.size and not np.any(mono):
                            exact_zero_blocks += 1
                            if exact_zero_blocks >= 6:
                                raise RuntimeError(
                                    "el dispositivo devuelve audio vacío"
                                )
                        else:
                            exact_zero_blocks = 0
                        consume(_resample_mono(mono, capture_rate, sample_rate))
                        # Some Windows host/device combinations return an empty
                        # block immediately instead of blocking for its audio
                        # duration. Pace that broken path so it cannot spin at
                        # hundreds of iterations per second and starve the Live
                        # session after the first question.
                        elapsed = time.monotonic() - started
                        expected = capture_block / float(capture_rate)
                        if elapsed < expected:
                            self._stop.wait(expected - elapsed)
                return
              except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Alternative microphone device %s at %s Hz failed: %s",
                    device.get("name"),
                    capture_rate,
                    exc,
                )
        raise RuntimeError(
            "No se pudo abrir el micrófono con el motor alternativo: "
            f"{last_exc or 'sin dispositivos disponibles'}"
        )

    def _capture_with_pyaudio(
        self,
        microphone_name: str,
        sample_rate: int,
        block: int,
        consume,
    ) -> None:
        """Last-resort Windows capture for endpoints returning zeroed WASAPI audio."""
        try:
            import pyaudio
        except ImportError as exc:
            raise RuntimeError("Falta PyAudio para capturar este micrófono.") from exc

        engine = pyaudio.PyAudio()
        stream = None
        try:
            devices = []
            wanted = microphone_name.casefold()
            wanted_tokens = {
                token for token in re.findall(r"[a-z0-9]+", wanted)
                if len(token) >= 4 and token not in {"microfono", "microphone"}
            }
            for index in range(engine.get_device_count()):
                device = engine.get_device_info_by_index(index)
                if int(device.get("maxInputChannels", 0)) <= 0:
                    continue
                name = str(device.get("name", ""))
                score = len(wanted_tokens & set(re.findall(r"[a-z0-9]+", name.casefold())))
                devices.append((score, index, device))
            if not devices:
                raise RuntimeError("PyAudio no encontró dispositivos de entrada.")
            devices.sort(key=lambda item: item[0], reverse=True)
            last_exc = None
            for _score, device_index, device in devices:
                for capture_rate in _candidate_sample_rates(
                    {"default_samplerate": device.get("defaultSampleRate")},
                    sample_rate,
                ):
                    try:
                        capture_block = max(1, round(capture_rate * block / sample_rate))
                        stream = engine.open(
                            format=pyaudio.paInt16,
                            channels=1,
                            rate=capture_rate,
                            input=True,
                            input_device_index=device_index,
                            frames_per_buffer=capture_block,
                        )
                        self._on_status(
                            f"Micrófono activo: {str(device.get('name', ''))[:32]}"
                        )
                        logger.info(
                            "Candidate PyAudio device=%s index=%s capture_rate=%s",
                            device.get("name"), device_index, capture_rate,
                        )
                        while not self._stop.is_set():
                            raw = stream.read(capture_block, exception_on_overflow=False)
                            mono = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                            consume(_resample_mono(mono, capture_rate, sample_rate))
                        return
                    except Exception as exc:
                        last_exc = exc
                        if stream is not None:
                            stream.close()
                            stream = None
            raise RuntimeError(f"PyAudio no pudo abrir el micrófono: {last_exc}")
        finally:
            if stream is not None:
                stream.close()
            engine.terminate()


class InterviewFrame(ctk.CTkFrame):
    """Guided preparation, active interview, and closing flow."""

    def __init__(self, parent, fixed_mode: str | None = None):
        super().__init__(parent, fg_color="transparent")
        self._fixed_mode = fixed_mode if fixed_mode in MODE_LABELS.values() else None
        self.out_dir = DEFAULT_DIR
        self.interviewer_queue: queue.Queue[str] = queue.Queue()
        self.candidate_queue: queue.Queue[str] = queue.Queue()
        self.status_queue: queue.Queue[str] = queue.Queue()
        self.assist_queue: queue.Queue = queue.Queue()
        self.worker = Transcriber(self.interviewer_queue, self.status_queue)
        self.candidate_listener = CandidateListener(
            self.candidate_queue.put,
            self.status_queue.put,
            on_audio=self.worker.push_candidate_audio,
        )
        self._source_map: dict[str, tuple[str, object]] = {}
        self._microphone_map: dict[str, str | None] = {}
        self._notebook_map: dict[str, notebooklm_service.NotebookRef] = {}
        self._profile_map: dict[str, notebooklm_service.ProfileRef] = {}
        # Cuenta de NotebookLM que usan todas las llamadas del CLI. Se manda
        # explícita en cada una: el default del CLI es global de la máquina y
        # apuntaba a la cuenta equivocada.
        self._active_profile: str | None = None
        self._active_notebook_id: str | None = None
        self._active_notebook_title: str | None = None
        self._active_notebook_context: str | None = None
        # While a sync is in flight the sync flow owns the status line, so the
        # generic state refresh must not overwrite its progress messages.
        self._notebook_syncing = False
        self._session_ended = False
        self._session_paused = False
        self._mic_error_shown = False
        self._simulation_session = None
        # Modo vigente del simulacro. Vive en el frame y no sólo en la sesión
        # porque un fallo puede llegar antes de que la sesión exista, y de ese
        # modo depende qué botones hay que devolverle al usuario.
        self._simulation_type = "interview"
        self._simulation_answer_parts: list[str] = []
        self._simulation_answering = False
        self._simulation_busy = False
        self._simulation_current_question = ""
        self._pending_simulation_turn = None
        self._knowledge_check_after_id = None
        self._knowledge_dialog = None
        self._next_question_dialog = None
        self._next_question_after_id = None
        # Historial de turnos de la sesión. El panel muestra uno solo a la vez,
        # así que sin esta lista la pregunta anterior se perdía al llegar la
        # siguiente: en pantalla y también en lo que se guarda en Historial.
        self._qa_history: list[dict] = []
        # None = modo vivo (se muestra el último turno y los nuevos entran
        # solos). Un int = el usuario está mirando un turno anterior, y los que
        # llegan se graban sin pisarle la pantalla.
        self._history_index: int | None = None
        # El último turno todavía recibe deltas del stream. Sin esto cada
        # parcial de Gemini abriría una entrada nueva.
        self._history_open = False
        self._history_window = None
        self._build_loaders()
        self._build_header()
        self._build_preparation()
        self._build_active()
        self._build_closing()
        self.show_preparation()
        self.refresh_devices()
        self.after(3000, self._auto_refresh_audio_sources)
        self.after(100, self._drain_queues)

    # ------------------------------------------------------------------
    # Indicador de carga
    # ------------------------------------------------------------------
    def _build_loaders(self) -> None:
        """Un único mecanismo de carga para las cuatro etiquetas que esperan.

        Se arman antes que los widgets: ``_apply_context_source_state`` y el
        ``after(250, self._refresh_profiles)`` del armado pueden escribir estado
        mientras la pantalla todavía se construye, y sin loader eso sería un
        ``AttributeError`` en pleno arranque.
        """
        self._status_loader = LoadingText(
            self._label_writer("status_label"),
            self._after_safe,
            self._cancel_after,
            idle_prefix="●",
        )
        self._notebook_loader = LoadingText(
            self._label_writer("notebook_status"),
            self._after_safe,
            self._cancel_after,
        )
        self._question_loader = LoadingText(
            self._label_writer("question_label"),
            self._after_safe,
            self._cancel_after,
        )
        self._answer_loader = LoadingText(
            self._label_writer("answer_loading_label"),
            self._after_safe,
            self._cancel_after,
        )

    def _label_writer(self, attribute: str):
        """Devuelve el ``apply`` de un loader, inerte si la etiqueta no está.

        ``notebook_status``, ``question_label`` y ``answer_loading_label`` sólo
        existen en algunos modos, así que escribir en ellas tiene que ser un
        no-op y no una excepción.
        """

        def write(text: str, color=None) -> None:
            label = getattr(self, attribute, None)
            if label is None:
                return
            try:
                if color is None:
                    label.configure(text=text)
                else:
                    label.configure(text=text, text_color=color)
            except (RuntimeError, tk.TclError):
                pass

        return write

    def _after_safe(self, ms: int, callback):
        """``after`` que devuelve ``None`` si la UI ya se fue, en vez de explotar."""
        try:
            return self.after(ms, callback)
        except (RuntimeError, tk.TclError):
            return None

    def _cancel_after(self, token) -> None:
        try:
            self.after_cancel(token)
        except (RuntimeError, tk.TclError, ValueError):
            pass

    def _loaders(self):
        return (
            getattr(self, "_status_loader", None),
            getattr(self, "_notebook_loader", None),
            getattr(self, "_question_loader", None),
            getattr(self, "_answer_loader", None),
        )

    def destroy(self) -> None:
        # Sin esto queda un ``after`` por loader apuntando a un widget muerto:
        # el mismo bug que tiene el spinner de canvas y por el que no se usó.
        for loader in self._loaders():
            if loader is not None:
                loader.stop()
        super().destroy()

    def _build_header(self) -> None:
        resolver = self._fixed_mode == "resolver"
        practice = self._fixed_mode == "practica_oral"
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill=tk.X, padx=24, pady=(20, 8))
        self.header_title = ctk.CTkLabel(
            header,
            text=("🧠  Resolver preguntas" if resolver else "🧑‍🏫  Práctica oral" if practice else "🎯  Entrevista"),
            font=("Segoe UI Semibold", 22),
            text_color=TEXT,
        )
        self.header_title.pack(side=tk.LEFT)
        self.status_label = ctk.CTkLabel(
            header, text="●  Preparación", font=("Segoe UI Semibold", 11),
            text_color=MUTED,
        )
        self.status_label.pack(side=tk.RIGHT)
        ctk.CTkLabel(
            self,
            text=(
                "Capturá la pregunta de una llamada o videoconferencia y obtené respuestas basadas en el tema."
                if resolver
                else "El profesor IA pregunta; vos respondés por micrófono y recibís una devolución."
                if practice
                else "Escuchá al entrevistador, seguí el hilo y respondé con mayor fluidez. El audio se guarda para reproducirlo en Historial."
            ),
            font=("Segoe UI", 12), text_color=MUTED,
        ).pack(anchor=tk.W, padx=24, pady=(0, 10))

    def _mode_labels(self) -> list[str]:
        allowed = (("practica_oral",) if self._fixed_mode == "practica_oral" else RESOLVER_MODES if self._fixed_mode == "resolver" else INTERVIEW_MODES)
        return [
            label
            for mode in allowed
            for label, mapped_mode in MODE_LABELS.items()
            if mapped_mode == mode
        ]

    def _build_preparation(self) -> None:
        resolver = self._fixed_mode == "resolver"
        practice = self._fixed_mode == "practica_oral"
        # Scrollable: the prep form outgrew small windows and the start button
        # was getting clipped at the bottom.
        self.prep = ctk.CTkScrollableFrame(
            self, fg_color=PANEL, corner_radius=12, border_color=BORDER, border_width=1
        )
        ctk.CTkLabel(
            self.prep,
            text="①  Prepará el tema" if resolver or practice else "①  Prepará la entrevista",
            font=("Segoe UI Semibold", 17), text_color=TEXT,
        ).pack(anchor=tk.W, padx=18, pady=(16, 2))
        ctk.CTkLabel(
            self.prep,
            text=(
                "Pegá el material, elegí el audio de la llamada y el micrófono con el que vas a responder."
                if resolver
                else "Conectá NotebookLM, elegí la materia y el profesor IA preguntará desde ese material."
                if practice
                else "Elegí el modo, pegá tu CV y listo. Al terminar, la entrevista queda en Historial con audio para escuchar."
            ),
            font=("Segoe UI", 11), text_color=MUTED,
        ).pack(anchor=tk.W, padx=18, pady=(0, 10))

        mode_row = ctk.CTkFrame(self.prep, fg_color="transparent")
        mode_row.pack(fill=tk.X, padx=18, pady=(0, 10))
        ctk.CTkLabel(
            mode_row,
            text="🎛  TIPO DE AYUDA" if resolver else "🎛  MODO DE SESIÓN",
            font=("Segoe UI Semibold", 9), text_color="#A78BFA",
        ).pack(anchor=tk.W)
        self.mode_var = tk.StringVar(value=self._load_saved_mode_label())
        self.mode_combo = ctk.CTkComboBox(
            mode_row, variable=self.mode_var, state="readonly",
            values=self._mode_labels(),
            fg_color=PANEL_DARK, border_color=BORDER, button_color=BORDER,
            dropdown_fg_color=PANEL, dropdown_hover_color=ACCENT,
            command=self._on_mode_change,
        )
        self.mode_combo.pack(fill=tk.X, pady=(4, 0))

        lang_row = ctk.CTkFrame(self.prep, fg_color="transparent")
        lang_row.pack(fill=tk.X, padx=18, pady=(0, 10))
        ctk.CTkLabel(
            lang_row, text="🌎  IDIOMA DE LAS RESPUESTAS",
            font=("Segoe UI Semibold", 9), text_color="#63B3ED",
        ).pack(anchor=tk.W)
        self._mode_langs: dict[str, str] = {}  # idioma por modo (cache de sesión)
        self.lang_var = tk.StringVar(value=self._load_saved_lang_label())
        self.lang_combo = ctk.CTkComboBox(
            lang_row, variable=self.lang_var, state="readonly",
            values=list(LANG_LABELS),
            fg_color=PANEL_DARK, border_color=BORDER, button_color=BORDER,
            dropdown_fg_color=PANEL, dropdown_hover_color=ACCENT,
            command=self._on_lang_change,
        )
        self.lang_combo.pack(fill=tk.X, pady=(4, 0))

        context_header = ctk.CTkFrame(self.prep, fg_color="transparent")
        context_header.pack(fill=tk.X, padx=18, pady=(0, 4))
        self.context_label = ctk.CTkLabel(
            context_header, text="📄  CV / CONTEXTO",
            font=("Segoe UI Semibold", 9), text_color="#F6AD55",
        )
        self.context_label.pack(side=tk.LEFT)
        self.cv_button = ctk.CTkButton(
            context_header, text="📂  Cargar CV…",
            command=self._upload_cv_file, width=140, height=24,
            fg_color=PANEL_DARK, hover_color=ACCENT, border_color=BORDER,
            border_width=1, font=("Segoe UI", 11),
        )
        self.cv_button.pack(side=tk.RIGHT)
        self.clear_context_button = ctk.CTkButton(
            context_header, text="🧹  Limpiar",
            command=self._clear_written_context, width=82, height=24,
            fg_color=PANEL_DARK, hover_color=ACCENT, border_color=BORDER,
            border_width=1, font=("Segoe UI", 11),
        )
        self.clear_context_button.pack(side=tk.RIGHT, padx=(0, 8))
        self.use_written_context_var = tk.BooleanVar(value=not practice)
        self.use_written_context_check = ctk.CTkCheckBox(
            context_header,
            text="Usar este contexto",
            variable=self.use_written_context_var,
            command=self._select_written_context,
            width=140,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            border_color=BORDER,
            text_color=TEXT,
        )
        self.use_written_context_check.pack(side=tk.RIGHT, padx=(0, 10))

        self.context_box = ctk.CTkTextbox(
            self.prep, height=110, fg_color=PANEL_DARK, text_color=TEXT,
            border_width=1, border_color=BORDER, corner_radius=8,
            font=("Segoe UI", 12),
        )
        self.context_box.pack(fill=tk.X, padx=18, pady=(0, 14))
        self.context_box.bind(
            "<KeyRelease>", lambda _event: self._apply_context_source_state()
        )
        self._context_placeholder = CONTEXT_PLACEHOLDER
        self._current_mode = None
        self._mode_contexts: dict[str, str] = {}  # contexto por modo (cache de sesión)
        # Sincroniza etiqueta/placeholder/botón CV y carga el contexto del modo guardado.
        self._on_mode_change(self.mode_var.get())

        if resolver or practice:
            notebook_card = ctk.CTkFrame(
                self.prep, fg_color=PANEL_DARK, corner_radius=8
            )
            notebook_card.pack(fill=tk.X, padx=18, pady=(0, 14))
            notebook_header = ctk.CTkFrame(
                notebook_card, fg_color="transparent"
            )
            notebook_header.pack(fill=tk.X, padx=12, pady=(10, 2))
            ctk.CTkLabel(
                notebook_header,
                text="📓  NOTEBOOKLM · MATERIA",
                font=("Segoe UI Semibold", 9),
                text_color="#63B3ED",
            ).pack(side=tk.LEFT)
            self.use_notebook_var = tk.BooleanVar(value=practice)
            self.use_notebook_check = ctk.CTkCheckBox(
                notebook_header,
                text="Usar NotebookLM",
                variable=self.use_notebook_var,
                command=self._select_notebook_context,
                width=125,
                fg_color=ACCENT,
                hover_color=ACCENT_HOVER,
                border_color=BORDER,
                text_color=TEXT,
            )
            self.use_notebook_check.pack(side=tk.RIGHT)
            ctk.CTkLabel(
                notebook_card,
                text="CUENTA",
                font=("Segoe UI Semibold", 8),
                text_color=MUTED,
            ).pack(anchor=tk.W, padx=12, pady=(4, 0))
            self.profile_var = tk.StringVar(value=PROFILE_PLACEHOLDER)
            self.profile_combo = ctk.CTkComboBox(
                notebook_card,
                variable=self.profile_var,
                state="readonly",
                values=[self.profile_var.get()],
                fg_color=PANEL,
                border_color=BORDER,
                button_color=BORDER,
                dropdown_fg_color=PANEL,
                dropdown_hover_color=ACCENT,
                command=self._on_profile_change,
            )
            self.profile_combo.pack(fill=tk.X, padx=12, pady=(2, 6))
            ctk.CTkLabel(
                notebook_card,
                text="MATERIA",
                font=("Segoe UI Semibold", 8),
                text_color=MUTED,
            ).pack(anchor=tk.W, padx=12, pady=(0, 0))
            self.notebook_var = tk.StringVar(
                value=NOTEBOOK_PLACEHOLDER
            )
            self.notebook_combo = ctk.CTkComboBox(
                notebook_card,
                variable=self.notebook_var,
                state="readonly",
                values=[self.notebook_var.get()],
                fg_color=PANEL,
                border_color=BORDER,
                button_color=BORDER,
                dropdown_fg_color=PANEL,
                dropdown_hover_color=ACCENT,
                command=self._on_notebook_change,
            )
            self.notebook_combo.pack(fill=tk.X, padx=12, pady=(4, 8))
            notebook_actions = ctk.CTkFrame(
                notebook_card, fg_color="transparent"
            )
            notebook_actions.pack(fill=tk.X, padx=12)
            self.notebook_login_button = ctk.CTkButton(
                notebook_actions,
                text="🔐  Conectar",
                width=110,
                height=28,
                command=self._login_notebooklm,
                fg_color=PANEL,
                hover_color=ACCENT,
            )
            self.notebook_login_button.pack(side=tk.LEFT)
            self.notebook_refresh_button = ctk.CTkButton(
                notebook_actions,
                text="🔄  Actualizar materias",
                width=150,
                height=28,
                # Recarga también las cuentas: después de «Conectar» una nueva,
                # este botón es el único camino de vuelta a la lista.
                command=self._refresh_profiles,
                fg_color=PANEL,
                hover_color=ACCENT,
            )
            self.notebook_refresh_button.pack(side=tk.LEFT, padx=6)
            self.notebook_sync_button = ctk.CTkButton(
                notebook_actions,
                text="↻  Sincronizar materia",
                width=150,
                height=28,
                command=self._sync_notebook,
                fg_color=ACCENT,
                hover_color=ACCENT_HOVER,
            )
            # Kept as an internal retry control; normal synchronization starts
            # automatically when the user chooses a subject.
            self.notebook_status = ctk.CTkLabel(
                notebook_card,
                text=(
                    "Conectá NotebookLM y elegí la materia para comenzar."
                    if practice
                    else "Podés seguir usando material local sin conectar NotebookLM."
                ),
                font=("Segoe UI", 10),
                text_color=MUTED,
            )
            self.notebook_status.pack(
                anchor=tk.W, padx=12, pady=(7, 10)
            )
            # The CLI keeps its authenticated browser session. On subsequent
            # launches, restore the catalog and last selected subject silently.
            self._apply_context_source_state()
            self.after(250, self._refresh_profiles)

        sources = ctk.CTkFrame(self.prep, fg_color="transparent")
        sources.pack(fill=tk.X, padx=18)
        sources.grid_columnconfigure(0, weight=1)
        sources.grid_columnconfigure(1, weight=1)
        self.source_label = ctk.CTkLabel(
            sources,
            text=(
                "🔊  PREGUNTA · AUDIO DEL SISTEMA"
                if resolver
                else "🎧  ENTREVISTADOR · AUDIO DEL SISTEMA"
            ),
            font=("Segoe UI Semibold", 9), text_color=INTERVIEWER,
        )
        self.source_label.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.mic_label = ctk.CTkLabel(
            sources,
            text=(
                "🎤  TU RESPUESTA · MICRÓFONO"
                if resolver or practice
                else "🎤  VOS · MICRÓFONO"
            ),
            font=("Segoe UI Semibold", 9), text_color=CANDIDATE,
        )
        self.mic_label.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.source_var = tk.StringVar(value=WHOLE_SYSTEM_LABEL)
        self.source_combo = ctk.CTkComboBox(
            sources, variable=self.source_var, state="readonly",
            fg_color=PANEL_DARK, border_color=BORDER, button_color=BORDER,
            dropdown_fg_color=PANEL, dropdown_hover_color=ACCENT,
        )
        self.source_combo.grid(row=1, column=0, sticky="ew", padx=(0, 48), pady=(4, 0))
        self.refresh_sources_button = ctk.CTkButton(
            sources,
            text="↻",
            width=36,
            height=28,
            fg_color=PANEL_DARK,
            hover_color="#20212D",
            command=self._refresh_audio_sources,
        )
        self.refresh_sources_button.grid(
            row=1, column=0, sticky="e", padx=(0, 8), pady=(4, 0)
        )
        self.mic_var = tk.StringVar()
        self.mic_combo = ctk.CTkComboBox(
            sources, variable=self.mic_var, state="readonly",
            fg_color=PANEL_DARK, border_color=BORDER, button_color=BORDER,
            dropdown_fg_color=PANEL, dropdown_hover_color=ACCENT,
        )
        self.mic_combo.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(4, 0))
        consent = ctk.CTkFrame(self.prep, fg_color=PANEL_DARK, corner_radius=8)
        consent.pack(fill=tk.X, padx=18, pady=14)
        ctk.CTkLabel(
            consent,
            text=(
                "🌐  Gemini procesa la pregunta del audio del sistema. Tu respuesta del micrófono se transcribe localmente y se incorpora al historial."
                if resolver
                else "🌐  Gemini procesa el audio del entrevistador. Tu voz se transcribe localmente y se envía como texto para mantener el contexto."
            ),
            wraplength=840, justify=tk.LEFT, font=("Segoe UI", 11), text_color=MUTED,
        ).pack(anchor=tk.W, padx=12, pady=10)
        # On by default so Historial can play the interview back.
        self.record_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            self.prep,
            text=(
                "🎙  Guardar audio de las preguntas y tus respuestas"
                if resolver
                else "🎙  Guardar audio de la entrevista (ambos) — activado, podés reproducirlo en Historial"
            ),
            variable=self.record_var, fg_color=REC, hover_color="#C04058",
            border_color=BORDER, text_color=TEXT,
        ).pack(anchor=tk.W, padx=18)
        ctk.CTkLabel(
            self.prep,
            text=(
                "💡  Usá auriculares: la pregunta llega desde la llamada y tu respuesta se toma por el micrófono."
                if resolver
                else "🎧  Tip: usá auriculares para evitar eco entre el sistema y el micrófono."
            ),
            font=("Segoe UI", 10), text_color=MUTED,
        ).pack(anchor=tk.W, padx=18, pady=(6, 12))
        self.start_button = ctk.CTkButton(
            self.prep,
            text="▶  Empezar a resolver" if resolver else "▶  Iniciar entrevista",
            command=self.start_session,
            height=40, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            font=("Segoe UI Semibold", 12),
        )
        self.start_button.pack(anchor=tk.E, padx=18, pady=(0, 4 if resolver else 18))
        self._apply_simulation_mode_ui()
        if resolver:
            # Resolving without material is the one failure the user cannot see:
            # the coach answers from general knowledge and nothing says so. The
            # button stays locked until at least one source actually has content.
            self.start_ready_label = ctk.CTkLabel(
                self.prep,
                text="",
                font=("Segoe UI", 10),
                text_color=MUTED,
            )
            self.start_ready_label.pack(anchor=tk.E, padx=18, pady=(0, 18))
            self._apply_context_source_state()

    def _build_active(self) -> None:
        resolver = self._fixed_mode == "resolver"
        practice = self._fixed_mode == "practica_oral"
        toolbar_button = {
            "height": 38,
            "corner_radius": 9,
            "font": ("Segoe UI Semibold", 11),
            "border_width": 1,
            "text_color_disabled": "#777C8F",
        }
        secondary_button = {
            **toolbar_button,
            "fg_color": "#171821",
            "hover_color": "#242631",
            "border_color": "#363846",
            "text_color": "#E9EAF0",
        }
        self.active = ctk.CTkFrame(self, fg_color="transparent")
        top = ctk.CTkFrame(self.active, fg_color="transparent")
        top.pack(fill=tk.X, padx=24, pady=(0, 8))
        ctk.CTkLabel(
            top, text="②  Sesión activa", font=("Segoe UI Semibold", 16), text_color=SUCCESS
        ).pack(side=tk.LEFT)
        self.pause_button = ctk.CTkButton(
            top, text="⏸  Pausar", width=110,
            command=self.toggle_pause, **secondary_button,
        )
        self.pause_button.pack(side=tk.RIGHT, padx=(0, 8))
        self.complete_answer_button = ctk.CTkButton(
            top,
            text="✓  Terminé mi respuesta",
            width=170,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            border_color="#8D35FF",
            text_color=TEXT,
            command=self._submit_simulation_answer,
            **toolbar_button,
        )
        self.complete_answer_button.pack(side=tk.RIGHT, padx=(0, 8))
        self.complete_answer_button.pack_forget()
        self.start_answer_button = ctk.CTkButton(
            top,
            text="🎙  Voy con mi respuesta",
            width=185,
            fg_color=SUCCESS,
            hover_color="#62D69B",
            border_color="#72DDA9",
            text_color="#07130D",
            command=self._start_simulation_answer,
            **toolbar_button,
        )
        self.start_answer_button.pack(side=tk.RIGHT, padx=(0, 8))
        self.start_answer_button.pack_forget()
        self.hint_button = ctk.CTkButton(
            top, text="💡 Dame una pista", width=140,
            command=self._request_simulation_hint, **secondary_button,
        )
        self.hint_button.pack(side=tk.RIGHT, padx=(0, 8))
        self.hint_button.pack_forget()
        self.mastered_button = ctk.CTkButton(
            top, text="✓ Ya la sé · siguiente", width=155,
            command=self._skip_mastered_question, **secondary_button,
        )
        self.mastered_button.pack(side=tk.RIGHT, padx=(0, 8))
        self.mastered_button.pack_forget()
        self.dont_know_button = ctk.CTkButton(
            top, text="No lo sé · explicame", width=165,
            fg_color="#E4A11B", hover_color="#F0B535",
            border_color="#F2BC4D", text_color="#1A1002",
            command=self._submit_dont_know,
            **toolbar_button,
        )
        self.dont_know_button.pack(side=tk.RIGHT, padx=(0, 8))
        self.dont_know_button.pack_forget()
        self.read_question_button = ctk.CTkButton(
            top, text="🔊 Leer pregunta", width=140,
            command=self._speak_simulation_question, **secondary_button,
        )
        self.read_question_button.pack(side=tk.RIGHT, padx=(0, 8))
        self.read_question_button.pack_forget()
        # Reemplaza el modal "¿Listo para continuar?": queda en pantalla ni
        # bien arranca la devolución hablada, así que interrumpirla y avanzar
        # es un solo click en vez de esperar a que termine de leer.
        self.next_question_button = ctk.CTkButton(
            top, text="➡  Siguiente pregunta", width=170,
            command=self._continue_to_next_question, **secondary_button,
        )
        self.next_question_button.pack(side=tk.RIGHT, padx=(0, 8))
        self.next_question_button.pack_forget()
        self.stop_button = ctk.CTkButton(
            top, text="⏹  Detener", width=110,
            fg_color="#C83D57", hover_color="#A92F47",
            border_color="#E45B72", text_color=TEXT,
            command=self.finish_session, **toolbar_button,
        )
        self.stop_button.pack(side=tk.RIGHT)

        self.question_label = self._section(
            self.active,
            (
                "🧑‍🏫  PREGUNTA DEL PROFESOR IA"
                if practice
                else "🗣  PREGUNTA DETECTADA"
                if resolver
                else "🗣  PREGUNTA EN ESPAÑOL"
            ),
            (
                "Preparando la pregunta del profesor..."
                if practice
                else "Esperando una pregunta..."
                if resolver
                else "Esperando al entrevistador..."
            ),
            19 if practice else 17,
            title_color="#F6AD55",
        )
        usage_hint = ctk.CTkLabel(
            self.active,
            text="💡  Click derecho en una palabra (o seleccioná una frase) = escuchar · 📋 copiar · 🔊 escuchar toda la respuesta",
            font=("Segoe UI", 10), text_color=MUTED,
        )
        usage_hint.pack(anchor=tk.W, padx=24, pady=(0, 2))
        self._build_history_bar(anchor=usage_hint)
        replies = ctk.CTkFrame(self.active, fg_color="transparent")
        replies.pack(fill=tk.X, padx=24, pady=6)
        replies.grid_columnconfigure(0, weight=1)
        self._answer_loading = False
        self.answer_loading_label = ctk.CTkLabel(
            replies,
            text="◌  Generando respuesta nueva…",
            font=("Segoe UI Semibold", 11),
            text_color="#F6E05E",
        )
        self.answer_loading_label.grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.answer_loading_label.grid_remove()
        self.reply_boxes = []
        self.reply_cards = []
        # Izquierda: la frase corta para contestar ya. Derecha: la misma respuesta
        # explayada, para cuando repreguntan o hace falta más contexto. Por eso la
        # derecha es más alta: llevan cantidades de texto distintas.
        reply_specs = (
            # Lo que se mira mientras hablás es esto, no las transcripciones de
            # abajo: se les da la altura que aquellas dejan libre.
            (
                "💬  DEVOLUCIÓN DEL PROFESOR" if practice else "⚡  RESPUESTA",
                ACCENT,
                155 if resolver else 120,
            ),
            (
                "🎯  PARA MEJORAR" if practice else "📖  DETALLES Y EJEMPLOS",
                "#F6E05E",
                190 if resolver else 155,
            ),
        )
        self.reply_titles = []
        for i, (title, title_color, box_height) in enumerate(reply_specs):
            card = ctk.CTkFrame(replies, fg_color=PANEL, corner_radius=12, border_color=BORDER, border_width=1)
            card.grid(row=i + 1, column=0, sticky="ew", pady=(0, 8))
            self.reply_cards.append(card)
            head = ctk.CTkFrame(card, fg_color="transparent")
            head.pack(fill=tk.X, padx=12, pady=(10, 2))
            title_label = ctk.CTkLabel(
                head, text=title, font=("Segoe UI Semibold", 9), text_color=title_color
            )
            title_label.pack(side=tk.LEFT)
            self.reply_titles.append(title_label)
            ctk.CTkButton(
                head, text="🔊", width=30, height=24, fg_color=PANEL_DARK,
                hover_color="#20212D", command=lambda n=i: self._speak_reply(n),
            ).pack(side=tk.RIGHT, padx=(4, 0))
            ctk.CTkButton(
                head, text="📋", width=30, height=24, fg_color=PANEL_DARK,
                hover_color="#20212D", command=lambda n=i: self._copy_reply(n),
            ).pack(side=tk.RIGHT)
            box = ctk.CTkTextbox(
                card, height=box_height, fg_color=PANEL_DARK, text_color=TEXT,
                border_width=0, corner_radius=8, wrap="word", font=("Segoe UI", 16),
            )
            box._reply_text = ""
            self._set_box_text(
                box,
                "Aparecerá después de responder. Tu voz se muestra arriba en TU RESPUESTA."
                if practice
                else "Aparecerá cuando detectemos una pregunta",
            )
            box._textbox.bind("<Button-3>", self._speak_word_at)
            # BOTH/expand: las dos tarjetas comparten fila, así que la más baja
            # dejaba un recuadro cortado con aire muerto abajo.
            box.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))
            self.reply_boxes.append(box)

        support = self.support_frame = ctk.CTkFrame(
            self.active, fg_color="transparent"
        )
        support.pack(side=tk.BOTTOM, fill=tk.X, padx=24, pady=(8, 18))
        self.ideas_label = self._small_card(
            support, "💡  IDEAS CLAVE",
            (
                "Se extraerán del tema y de la respuesta."
                if resolver
                else "Se adaptarán a tu CV y al hilo de la conversación."
            ),
            title_color="#F6E05E",
        )

        transcripts = self.transcripts_frame = ctk.CTkFrame(
            self.active, fg_color="transparent"
        )
        transcripts.pack(
            fill=tk.X,
            padx=24,
            pady=(6, 10),
            before=self.question_label.master,
        )
        transcripts.grid_columnconfigure(0, weight=1)
        transcripts.grid_columnconfigure(1, weight=1)
        transcripts.grid_rowconfigure(1, weight=1)
        self.interviewer_transcript_label = ctk.CTkLabel(
            transcripts,
            text="🎤  PREGUNTA ESCUCHADA" if resolver else "🎧  ENTREVISTADOR",
            font=("Segoe UI Semibold", 9),
            text_color=INTERVIEWER,
        )
        self.interviewer_transcript_label.grid(row=0, column=0, sticky="w")
        self.candidate_transcript_label = ctk.CTkLabel(
            transcripts,
            text="🎤  TU RESPUESTA" if resolver else "🎤  VOS",
            font=("Segoe UI Semibold", 9),
            text_color=CANDIDATE,
        )
        self.candidate_transcript_label.grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.interviewer_box = self._transcript_box(transcripts)
        self.interviewer_box.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=(4, 0))
        self.interviewer_box._textbox.bind("<Button-3>", self._speak_word_at)
        self.candidate_box = self._transcript_box(transcripts)
        if practice:
            self.candidate_box.configure(height=150)
        self.candidate_box.grid(row=1, column=1, sticky="nsew", padx=(6, 0), pady=(4, 0))
        self.candidate_box._textbox.bind("<Button-3>", self._speak_word_at)
        if resolver:
            self.interviewer_box.grid_configure(columnspan=2, padx=0)
            self.candidate_transcript_label.grid_remove()
            self.candidate_box.grid_remove()

    def _build_closing(self) -> None:
        resolver = self._fixed_mode == "resolver"
        self.closing = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=12, border_color=BORDER, border_width=1)
        ctk.CTkLabel(
            self.closing,
            text="③  Resolución finalizada" if resolver else "③  Entrevista lista",
            font=("Segoe UI Semibold", 18), text_color=SUCCESS,
        ).pack(pady=(24, 6))
        self.closing_summary = ctk.CTkLabel(self.closing, text="", font=("Segoe UI", 12), text_color=MUTED)
        self.closing_summary.pack(pady=(0, 8))
        self.closing_hint = ctk.CTkLabel(
            self.closing,
            text="🎙  El audio se está guardando en Historial para que puedas escucharlo.",
            font=("Segoe UI", 11), text_color=SUCCESS,
        )
        self.closing_hint.pack(pady=(0, 14))
        actions = ctk.CTkFrame(self.closing, fg_color="transparent")
        actions.pack(pady=(0, 24))
        ctk.CTkButton(
            actions,
            text="💾  Guardar preguntas y respuestas" if resolver else "💾  Guardar con nombre",
            command=self.save_session,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
        ).pack(side=tk.LEFT, padx=6)
        ctk.CTkButton(
            actions,
            text="🔄  Resolver otra pregunta" if resolver else "🔄  Nueva entrevista",
            command=self.reset_session,
            fg_color=PANEL_DARK, hover_color="#20212D",
        ).pack(side=tk.LEFT, padx=6)
        ctk.CTkButton(
            actions, text="🗑  Descartar", command=self.discard_session,
            fg_color="transparent", border_width=1, border_color=BORDER,
        ).pack(side=tk.LEFT, padx=6)

    # Pack options of the history bar. ``pack_forget`` drops the slot, so the
    # anchor is what puts the bar back between the question card and the answer
    # cards instead of at the bottom of the panel.
    _HISTORY_BAR_PACK = {"fill": tk.X, "padx": 24, "pady": (0, 4)}

    def _build_history_bar(self, *, anchor) -> None:
        """Turn navigator shown above the answer cards."""
        self._history_bar_anchor = anchor
        bar = self.history_bar = ctk.CTkFrame(self.active, fg_color="transparent")
        nav_button = {
            "width": 34,
            "height": 24,
            "fg_color": PANEL_DARK,
            "hover_color": "#20212D",
        }
        self.history_prev_button = ctk.CTkButton(
            bar, text="◀", command=lambda: self._history_go(-1), **nav_button
        )
        self.history_prev_button.pack(side=tk.LEFT)
        self.history_counter = ctk.CTkLabel(
            bar, text="1 / 1", font=("Segoe UI Semibold", 11), text_color=MUTED
        )
        self.history_counter.pack(side=tk.LEFT, padx=8)
        self.history_next_button = ctk.CTkButton(
            bar, text="▶", command=lambda: self._history_go(1), **nav_button
        )
        self.history_next_button.pack(side=tk.LEFT)
        self.history_latest_button = ctk.CTkButton(
            bar, text="⏭  Ir a la última", width=125, height=24,
            fg_color=PANEL_DARK, hover_color="#20212D",
            command=self._history_latest,
        )
        self.history_latest_button.pack(side=tk.LEFT, padx=(12, 0))
        ctk.CTkButton(
            bar, text="📚  Ver todas", width=110, height=24,
            fg_color=PANEL_DARK, hover_color="#20212D",
            command=self._show_history_window,
        ).pack(side=tk.RIGHT)
        self.history_hint = ctk.CTkLabel(
            bar, text="", font=("Segoe UI Semibold", 11), text_color="#F6E05E"
        )
        self.history_hint.pack(side=tk.LEFT, padx=(12, 0))
        # Con un solo turno no hay nada que navegar: la pantalla queda igual a
        # como era antes de que existiera el historial.
        bar.pack_forget()

    def _section(self, parent, title, text, size, title_color: str = MUTED):
        card = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=12, border_color=BORDER, border_width=1)
        card.pack(fill=tk.X, padx=24, pady=6)
        ctk.CTkLabel(card, text=title, font=("Segoe UI Semibold", 9), text_color=title_color).pack(anchor=tk.W, padx=14, pady=(10, 3))
        label = ctk.CTkLabel(card, text=text, wraplength=850, justify=tk.LEFT, anchor="w", font=("Segoe UI Semibold", size), text_color=TEXT)
        label.pack(fill=tk.X, padx=14, pady=(0, 12))
        return label

    def _small_card(self, parent, title, text, title_color: str = MUTED):
        card = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=10, border_color=BORDER, border_width=1)
        card.pack(fill=tk.X)
        ctk.CTkLabel(card, text=title, font=("Segoe UI Semibold", 9), text_color=title_color).pack(anchor=tk.W, padx=12, pady=(9, 2))
        label = ctk.CTkLabel(card, text=text, wraplength=840, justify=tk.LEFT, anchor="w", font=("Segoe UI", 11), text_color=TEXT)
        label.pack(fill=tk.X, padx=12, pady=(0, 10))
        return label

    def _transcript_box(self, parent):
        # Bajo a propósito: es control de que el audio entra, no algo que se lea
        # durante la sesión. El alto que cede va a las cajas de respuesta.
        return ctk.CTkTextbox(parent, height=70, fg_color=PANEL_DARK, text_color=TEXT, border_width=1, border_color=BORDER, corner_radius=8)

    def show_preparation(self) -> None:
        self.active.pack_forget()
        self.closing.pack_forget()
        self.prep.pack(fill=tk.BOTH, expand=True, padx=24, pady=(0, 20))
        self._set_status("Preparación")

    def show_active(self) -> None:
        self.prep.pack_forget()
        self.closing.pack_forget()
        self.active.pack(fill=tk.BOTH, expand=True)
        if self.record_var.get():
            self._set_status("Grabando audio", REC)
        else:
            self._set_status("Sesión activa", ACCENT)

    def show_closing(self) -> None:
        self.prep.pack_forget()
        self.active.pack_forget()
        self.closing.pack(fill=tk.X, padx=24, pady=(30, 20))
        interviewer_words = len(self.interviewer_box.get("1.0", tk.END).split())
        candidate_words = len(self.candidate_box.get("1.0", tk.END).split())
        self.closing_summary.configure(
            text=(
                f"🎤 Preguntas detectadas: {interviewer_words} palabras"
                if self._fixed_mode == "resolver"
                else f"🎧 Entrevistador: {interviewer_words} palabras · 🎤 Vos: {candidate_words} palabras"
            )
        )
        recording = bool(self.record_var.get())
        self.closing_hint.configure(
            text=(
                "🎙  El audio se está guardando en Historial para que puedas escucharlo."
                if recording
                else "📄  Solo se guardará el texto. Activá «Guardar audio» la próxima vez para poder reproducir."
            ),
            text_color=SUCCESS if recording else MUTED,
        )

    def _combined_transcript(self) -> str:
        interviewer = self.interviewer_box.get("1.0", tk.END).strip()
        candidate = self.candidate_box.get("1.0", tk.END).strip()
        if self._simulation_session is not None:
            if not interviewer and not candidate and not self._qa_history:
                return ""
            academic = self._fixed_mode == "resolver"
            interviewer_role = "PROFESOR IA" if academic else "ENTREVISTADOR IA"
            candidate_role = "ESTUDIANTE" if academic else "CANDIDATO"
            report = self._simulation_session.report()
            report_text = f"\n\nDEVOLUCIÓN\n{report}" if report else ""
            if self._qa_history:
                # ``candidate_box`` se vacía en cada turno, así que leerla
                # guardaba todas las preguntas y una sola respuesta: la última.
                turns = self._simulation_history_text(
                    interviewer_role, candidate_role
                )
                return f"{turns}{report_text}\n"
            return (
                f"{interviewer_role}\n{interviewer}\n\n"
                f"{candidate_role}\n{candidate}{report_text}\n"
            )
        if self._fixed_mode == "resolver":
            labels = ("RESPUESTA CORTA", "RESPUESTA AMPLIADA")
            # Los widgets muestran un turno solo. Leerlos era guardar la última
            # pregunta y tirar el resto de la sesión.
            if self._qa_history:
                turns = self._history_as_text(labels)
                user_answer = f"\n\nTU RESPUESTA\n{candidate}" if candidate else ""
                # La glosa en español no reemplaza a lo que el micrófono oyó:
                # cuando la transcripción sale mal, el crudo es lo único que
                # después explica por qué la respuesta no venía a cuento.
                heard = f"\n\nTRANSCRIPCIÓN ESCUCHADA\n{interviewer}" if interviewer else ""
                return f"{turns}{user_answer}{heard}\n"
            answers = [
                (labels[i] if i < len(labels) else f"RESPUESTA {i + 1}", text)
                for i, box in enumerate(self.reply_boxes)
                if (text := box._reply_text.strip())
            ]
            if not interviewer and not answers:
                return ""
            answer_text = "\n\n".join(
                f"{label}\n{text}" for label, text in answers
            )
            user_answer = (
                f"\n\nTU RESPUESTA\n{candidate}"
                if candidate
                else ""
            )
            return f"PREGUNTA\n{interviewer}\n\n{answer_text}{user_answer}\n"
        if not interviewer and not candidate:
            return ""
        base = f"ENTREVISTADOR\n{interviewer}\n\nCANDIDATO\n{candidate}\n"
        # Las cajas de transcripción ya acumulan solas, pero las sugerencias del
        # coach vivían únicamente en el turno visible.
        if self._qa_history:
            turns = self._history_as_text(("RESPUESTA", "DETALLES Y EJEMPLOS"))
            return f"{base}\nSUGERENCIAS DEL COACH\n\n{turns}\n"
        return base

    def _simulation_history_text(
        self, interviewer_role: str, candidate_role: str
    ) -> str:
        """Cada turno del simulacro con su pregunta, tu respuesta y la devolución."""
        blocks = []
        for number, entry in enumerate(self._qa_history, start=1):
            question = entry["pregunta"].strip()
            answer = entry.get("respuesta_usuario", "").strip()
            feedback = "\n\n".join(
                text for raw in entry["respuestas"] if (text := raw.strip())
            )
            if not (question or answer or feedback):
                continue
            block = f"{interviewer_role} · PREGUNTA {number}\n{question}"
            if answer:
                block += f"\n\n{candidate_role}\n{answer}"
            if feedback:
                block += f"\n\nDEVOLUCIÓN\n{feedback}"
            if entry["ideas"]:
                block += "\n\nFORTALEZAS\n" + " • ".join(entry["ideas"])
            blocks.append(block)
        return "\n\n".join(blocks)

    def _history_as_text(self, labels: tuple[str, ...]) -> str:
        """Todos los turnos de la sesión, numerados, para guardar en Historial."""
        blocks = []
        for number, entry in enumerate(self._qa_history, start=1):
            answers = "\n\n".join(
                f"{labels[i] if i < len(labels) else f'RESPUESTA {i + 1}'}\n{text}"
                for i, raw in enumerate(entry["respuestas"])
                if (text := raw.strip())
            )
            question = entry["pregunta"].strip()
            if not question and not answers:
                continue
            block = f"PREGUNTA {number}\n{question}"
            if answers:
                block += f"\n\n{answers}"
            blocks.append(block)
        return "\n\n".join(blocks)

    def _audio_ready(self) -> tuple[bool, object | None]:
        import os

        wav = getattr(self.worker, "current_wav_path", None)
        has_audio = bool(wav) and os.path.exists(wav) and os.path.getsize(wav) > 44
        return has_audio, wav

    def _register_in_history(self) -> None:
        """Save the finished session to the DB so Historial can list/play it."""
        if config.repository is None:
            return
        import os
        import datetime as dt

        has_audio, wav = self._audio_ready()
        txt = getattr(self.worker, "current_transcript_path", None)
        has_txt = bool(txt) and os.path.exists(txt)
        transcript = self._combined_transcript()
        if not transcript and has_txt:
            try:
                with open(txt, encoding="utf-8") as fh:
                    transcript = fh.read().strip()
            except Exception as exc:
                logger.exception("Failed reading interview transcript: %s", exc)
        if not has_audio and not transcript and not has_txt:
            self._set_status("Sin audio ni texto para guardar", ERROR)
            return
        # Prefer WAV so Historial enables playback; fall back to transcript file.
        if has_audio:
            file_path = str(wav)
        elif has_txt:
            file_path = str(txt)
        else:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            stamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            prefix = "resolucion" if self._fixed_mode == "resolver" else "entrevista"
            file_path = str(self.out_dir / f"{prefix}_{stamp}.txt")
            Path(file_path).write_text(transcript, encoding="utf-8")
        secs = int(getattr(self.worker, "duration_seconds", 0) or 0)
        try:
            config.repository.save(
                TranscriptionRecord(
                    file_path=file_path,
                    file_name=(
                        f"Resolución {dt.datetime.now():%Y-%m-%d %H.%M}"
                        if self._fixed_mode == "resolver"
                        else f"Entrevista {dt.datetime.now():%Y-%m-%d %H.%M}"
                    ),
                    duration=f"{secs // 60:02d}:{secs % 60:02d}",
                    transcription=transcript,
                    language="es" if self._fixed_mode == "resolver" else "en",
                )
            )
            self._set_status(
                "Guardada en Historial (con audio)" if has_audio else "Guardada en Historial",
                SUCCESS,
            )
            if hasattr(self, "closing_hint"):
                self.closing_hint.configure(
                    text=(
                        "✅  Lista en Historial — abrí la pestaña y tocá ▶ para escuchar."
                        if has_audio
                        else "✅  Texto guardado en Historial (sin audio para reproducir)."
                    ),
                    text_color=SUCCESS if has_audio else MUTED,
                )
        except Exception as exc:
            logger.exception("Failed saving interview to history: %s", exc)
            self._set_status("Error al guardar", ERROR)

    def save_session(self) -> None:
        combined = self._combined_transcript()
        resolver = self._fixed_mode == "resolver"
        if not combined:
            show_warning(
                self,
                "Resolver preguntas" if resolver else "Entrevista",
                "No hay preguntas para guardar." if resolver else "No hay conversación para guardar.",
            )
            return
        name = ask_input(
            self,
            "Guardar resolución" if resolver else "Guardar entrevista",
            "Nombre del tema:" if resolver else "Nombre de la entrevista:",
        )
        if not name:
            return
        name = name.strip()
        safe = re.sub(r'[\\/*?:"<>|]', "_", name)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = Path(self.out_dir) / f"{safe}.txt"
        path.write_text(combined, encoding="utf-8")
        has_audio, wav = self._audio_ready()
        # Point Historial at the WAV when available so ▶ works.
        file_path = str(wav) if has_audio else str(path)
        secs = int(getattr(self.worker, "duration_seconds", 0) or 0)
        if config.repository is None:
            show_error(
                self,
                "Resolver preguntas" if resolver else "Entrevista",
                "No hay base de datos disponible.",
            )
            return
        config.repository.save(TranscriptionRecord(
            file_path=file_path,
            file_name=safe,
            duration=f"{secs // 60:02d}:{secs % 60:02d}",
            transcription=combined,
            language="es" if resolver else "en",
        ))
        self._set_status("Guardada", SUCCESS)
        msg = (
            "Guardada en Historial. Podés reproducir el audio con ▶."
            if has_audio
            else "Guardada en Historial (solo texto; no hay audio para reproducir)."
        )
        show_info(self, "Resolver preguntas" if resolver else "Entrevista", msg)

    def refresh_devices(self) -> None:
        self._refresh_audio_sources()
        self._microphone_map = {}
        try:
            for mic in sc.all_microphones():
                label = f"🎤  {mic.name}"
                self._microphone_map[label] = mic.name
        except Exception as exc:
            logger.exception("Failed listing interview microphones: %s", exc)
        labels = list(self._microphone_map) or ["Micrófono predeterminado"]
        if not self._microphone_map:
            self._microphone_map[labels[0]] = None
        self.mic_combo.configure(values=labels)
        preferred_label = choose_preferred_microphone_label(
            self._microphone_map,
            load_preferred_microphone(config.repository),
        )
        if preferred_label:
            self.mic_var.set(preferred_label)
        elif self.mic_var.get() not in self._microphone_map:
            self.mic_var.set(labels[0])

    def _refresh_audio_sources(self) -> None:
        """Reload active Windows audio sessions without losing selection."""
        selected = self.source_var.get()
        self._source_map = {WHOLE_SYSTEM_LABEL: ("loopback", None)}
        try:
            from infrastructure.audio import process_loopback

            for name, pid in process_loopback.list_audio_apps():
                self._source_map[f"💻  App: {name} (PID {pid})"] = ("app", pid)
        except Exception:
            pass
        self.source_combo.configure(values=list(self._source_map))
        if selected in self._source_map:
            self.source_var.set(selected)
        else:
            self.source_var.set(WHOLE_SYSTEM_LABEL)

    def _auto_refresh_audio_sources(self) -> None:
        """Discover apps opened after this screen, while session is not running."""
        if self.prep.winfo_ismapped():
            self._refresh_audio_sources()
        self.after(3000, self._auto_refresh_audio_sources)

    def _set_notebook_status(
        self, text: str, color: str = MUTED, *, busy: bool = False
    ) -> None:
        self._notebook_loader.show(text, color, busy=busy)

    def _login_notebooklm(self) -> None:
        try:
            notebooklm_service.launch_login(self._active_profile)
        except Exception as exc:
            self._set_notebook_status(str(exc), ERROR)
            return
        self._set_notebook_status(
            "Completá el inicio de sesión y luego tocá «Actualizar materias».",
            ACCENT,
        )

    def _schedule_ui(self, callback) -> bool:
        """Corre ``callback`` en el hilo de Tk, salvo que la UI ya se haya ido."""
        try:
            self.after(0, callback)
        except (RuntimeError, tk.TclError):
            return False
        return True

    def _refresh_profiles(self) -> None:
        """Carga las cuentas de NotebookLM y encadena el catálogo de la elegida."""
        self._set_notebook_status("Buscando cuentas de NotebookLM…", ACCENT, busy=True)

        def work():
            try:
                profiles = notebooklm_service.list_profiles()
            except Exception as exc:
                self._schedule_ui(
                    lambda message=str(exc): self._set_notebook_status(message, ERROR)
                )
                return
            self._schedule_ui(lambda: apply(profiles))

        def apply(profiles):
            self._profile_map = {profile.label: profile for profile in profiles}
            labels = list(self._profile_map)
            if not labels:
                self.profile_combo.configure(values=[PROFILE_PLACEHOLDER])
                self.profile_var.set(PROFILE_PLACEHOLDER)
                self._set_notebook_status(
                    "No hay cuentas conectadas. Tocá «Conectar».", ERROR
                )
                return
            self.profile_combo.configure(values=labels)
            saved = self._load_setting(NOTEBOOK_PROFILE_SETTING_KEY)
            chosen = next(
                (label for label, p in self._profile_map.items() if p.name == saved),
                labels[0],
            )
            self.profile_var.set(chosen)
            self._active_profile = self._profile_map[chosen].name
            self._refresh_notebooks()

        threading.Thread(target=work, daemon=True).start()

    def _on_profile_change(self, choice: str) -> None:
        profile = self._profile_map.get(choice)
        if profile is None or profile.name == self._active_profile:
            return
        self._active_profile = profile.name
        if config.repository is not None:
            try:
                config.repository.set_setting(
                    NOTEBOOK_PROFILE_SETTING_KEY, profile.name
                )
            except Exception as exc:
                logger.exception("Failed saving NotebookLM profile: %s", exc)
        # El catálogo y la materia activa pertenecen a la cuenta anterior: si no
        # se limpian, la sesión arranca con material de la cuenta que no es.
        self._notebook_map = {}
        self._deactivate_notebook_context()
        self.notebook_combo.configure(values=[NOTEBOOK_PLACEHOLDER])
        self.notebook_var.set(NOTEBOOK_PLACEHOLDER)
        self._apply_context_source_state()
        self._refresh_notebooks()

    def _refresh_notebooks(self) -> None:
        self._set_notebook_status("Consultando materias de NotebookLM…", ACCENT, busy=True)
        profile = self._active_profile
        schedule_ui = self._schedule_ui

        def work():
            try:
                notebooks = notebooklm_service.list_notebooks(profile=profile)
            except Exception as exc:
                schedule_ui(
                    lambda message=str(exc): self._set_notebook_status(
                        message, ERROR
                    )
                )
                return

            def apply():
                # Cambiar de cuenta con un refresh en vuelo dejaba el catálogo
                # de la anterior pisando al nuevo: el que llega tarde se tira.
                if profile != self._active_profile:
                    # Acá NO se frena el loader: cambiar de cuenta reentra en
                    # `_refresh_notebooks`, que ya lo rearmó para el pedido
                    # nuevo. Pararlo sería matarle el spinner al que sí sigue
                    # en vuelo. Si el nuevo ya terminó, su propio estado final
                    # lo apagó y este descarte no tiene nada que hacer.
                    return
                self._notebook_map = {
                    notebook.label: notebook for notebook in notebooks
                }
                labels = list(self._notebook_map)
                account = self._profile_email(profile)
                if not labels:
                    self._set_notebook_status(
                        f"{account} no tiene materias con fuentes.", ERROR
                    )
                    return
                self.notebook_combo.configure(values=labels)
                current = self.notebook_var.get()
                selected = current if current in self._notebook_map else None
                if selected is None:
                    selected = self._restore_saved_notebook()
                self.notebook_var.set(selected or NOTEBOOK_PLACEHOLDER)
                self._apply_context_source_state()
                if self._active_notebook_context:
                    return
                self._set_notebook_status(
                    (
                        f"{len(labels)} materias en {account} · seleccionada: "
                        f"{self._notebook_map[selected].title}"
                        if selected
                        else f"{len(labels)} materias en {account} · elegí una."
                    ),
                    SUCCESS,
                )

            schedule_ui(apply)

        threading.Thread(target=work, daemon=True).start()

    def _load_setting(self, key: str) -> str | None:
        if config.repository is None:
            return None
        try:
            value = config.repository.get_setting(key)
            return value.strip() if value and value.strip() else None
        except Exception as exc:
            logger.exception("Failed loading setting %s: %s", key, exc)
            return None

    def _profile_email(self, name: str | None) -> str:
        """Email de una cuenta, para nombrarla en los mensajes de estado."""
        for profile in self._profile_map.values():
            if profile.name == name:
                return profile.email or profile.name
        return name or "la cuenta"

    def _restore_saved_notebook(self) -> str | None:
        """Re-select the last used subject and reuse its cached material.

        The selection was being persisted but never read back, so every launch
        started with no subject. Restoring from the local cache keeps the flow
        offline: syncing again is the user's call via «Sincronizar materia».
        """
        saved_id = self._load_setting(notebook_id_setting_key(self._active_profile))
        if not saved_id:
            return None
        for label, notebook in self._notebook_map.items():
            if notebook.id != saved_id:
                continue
            cached = self._load_setting(
                f"{NOTEBOOK_CONTEXT_SETTING_PREFIX}{notebook.id}"
            )
            if cached:
                self._active_notebook_id = notebook.id
                self._active_notebook_title = notebook.title
                self._active_notebook_context = cached
            return label
        return None

    def _save_notebook_selection(
        self, notebook: notebooklm_service.NotebookRef
    ) -> None:
        if config.repository is None:
            return
        try:
            config.repository.set_setting(
                notebook_id_setting_key(self._active_profile), notebook.id
            )
            config.repository.set_setting(
                NOTEBOOK_TITLE_SETTING_KEY, notebook.title
            )
        except Exception as exc:
            logger.exception("Failed saving NotebookLM selection: %s", exc)

    def _on_notebook_change(self, choice: str) -> None:
        notebook = self._notebook_map.get(choice)
        if notebook is None:
            return
        self._deactivate_notebook_context()
        self.notebook_combo.configure(border_color=BORDER)
        cached = self._load_setting(
            f"{NOTEBOOK_CONTEXT_SETTING_PREFIX}{notebook.id}"
        )
        if cached:
            self._active_notebook_id = notebook.id
            self._active_notebook_title = notebook.title
            self._active_notebook_context = cached
        self._apply_context_source_state()
        self._save_notebook_selection(notebook)
        self._set_notebook_status(
            (
                f"✓ {notebook.title} lista desde caché · actualizando…"
                if cached
                else f"Sincronizando {notebook.title} automáticamente…"
            ),
            ACCENT,
            busy=True,
        )
        self._sync_notebook()

    def _deactivate_notebook_context(self) -> None:
        self._active_notebook_id = None
        self._active_notebook_title = None
        self._active_notebook_context = None

    def _select_written_context(self) -> None:
        self._apply_context_source_state()

    def _select_notebook_context(self) -> None:
        self._apply_context_source_state()
        if (
            self.use_notebook_var.get()
            and self.notebook_var.get() in self._notebook_map
            and not self._active_notebook_context
        ):
            self._sync_notebook()

    def _has_written_context(self) -> bool:
        value = self.context_box.get("1.0", tk.END).strip()
        return bool(value and value not in ALL_PLACEHOLDERS)

    def _apply_context_source_state(self) -> None:
        """Enable each context source independently; both may be active."""
        notebook_active = (
            hasattr(self, "use_notebook_var") and self.use_notebook_var.get()
        )
        written_active = self.use_written_context_var.get()
        self.context_box.configure(state="normal" if written_active else "disabled")
        self.cv_button.configure(state="normal" if written_active else "disabled")
        if not hasattr(self, "notebook_combo"):
            return
        control_state = "normal" if notebook_active else "disabled"
        self.notebook_combo.configure(
            state="readonly" if notebook_active else "disabled"
        )
        self.profile_combo.configure(
            state="readonly" if notebook_active else "disabled"
        )
        self.notebook_login_button.configure(state=control_state)
        self.notebook_refresh_button.configure(state=control_state)
        self.notebook_sync_button.configure(state=control_state)
        notebook_ready = notebook_active and bool(self._active_notebook_context)
        written_ready = written_active and self._has_written_context()
        self._update_start_readiness(notebook_ready, written_ready)
        if self._notebook_syncing:
            # A sync in flight is already narrating its own progress.
            return
        if not notebook_active:
            self._set_notebook_status("NotebookLM desactivado.", MUTED)
        elif notebook_ready:
            self._set_notebook_status(self._notebook_ready_text(), SUCCESS)
        else:
            self._set_notebook_status(
                "Elegí una materia: se sincronizará automáticamente.", ACCENT
            )

    def _notebook_ready_text(self) -> str:
        title = self._active_notebook_title or "La materia"
        if self.use_written_context_var.get() and self._has_written_context():
            return f"✓ {title} lista. Se combinará con el contexto escrito."
        return f"✓ {title} lista para usar."

    def _update_start_readiness(
        self, notebook_ready: bool, written_ready: bool
    ) -> None:
        """Unlock the session only once some source actually carries material."""
        if not hasattr(self, "start_button"):
            return
        ready = notebook_ready or written_ready
        self.start_button.configure(state="normal" if ready else "disabled")
        if not hasattr(self, "start_ready_label"):
            return
        if notebook_ready and written_ready:
            text, color = "✓ Materia y contexto escrito listos.", SUCCESS
        elif notebook_ready:
            text, color = (
                f"✓ {self._active_notebook_title or 'Materia'} lista.",
                SUCCESS,
            )
        elif written_ready:
            text, color = "✓ Contexto escrito listo.", SUCCESS
        else:
            text, color = (
                "Cargá el material: pegá el tema o sincronizá una materia.",
                WARNING,
            )
        self.start_ready_label.configure(text=text, text_color=color)

    def _sync_notebook(self) -> None:
        notebook = self._notebook_map.get(self.notebook_var.get())
        if notebook is None:
            self._set_notebook_status(
                "Primero actualizá y seleccioná una materia.", ERROR
            )
            return
        logger.info(
            "NotebookLM sync started: id=%s title=%s",
            notebook.id,
            notebook.title,
        )
        self.notebook_sync_button.configure(state="disabled")
        self._notebook_syncing = True
        self._set_notebook_status(
            f"Preparando material de {notebook.title}…", ACCENT, busy=True
        )

        profile = self._active_profile

        def work():
            try:
                context = notebooklm_service.sync_study_context(
                    notebook.id, profile=profile
                )
            except Exception as exc:
                self.after(0, lambda message=str(exc): finish_error(message))
                return
            self.after(0, lambda: finish_success(context))

        def finish_error(message: str):
            self.notebook_sync_button.configure(state="normal")
            self._notebook_syncing = False
            logger.error(
                "NotebookLM sync failed: id=%s title=%s error=%s",
                notebook.id,
                notebook.title,
                message,
            )
            if self._active_notebook_context:
                self._apply_context_source_state()
                self._set_notebook_status(
                    f"✓ Usando la copia guardada de {notebook.title}.",
                    WARNING,
                )
                return
            self._set_notebook_status(message, ERROR)

        def finish_success(context: str):
            value = f"[NotebookLM · {notebook.title}]\n{context}"
            self._active_notebook_id = notebook.id
            self._active_notebook_title = notebook.title
            self._active_notebook_context = value
            self._save_notebook_selection(notebook)
            if config.repository is not None:
                try:
                    config.repository.set_setting(
                        f"{NOTEBOOK_CONTEXT_SETTING_PREFIX}{notebook.id}",
                        value,
                    )
                except Exception as exc:
                    logger.exception(
                        "Failed caching NotebookLM context: %s", exc
                    )
            logger.info(
                "NotebookLM sync complete: id=%s title=%s chars=%s",
                notebook.id,
                notebook.title,
                len(value),
            )
            self.notebook_sync_button.configure(state="normal")
            self._notebook_syncing = False
            self.notebook_combo.configure(border_color=BORDER)
            # _apply_context_source_state now renders the ready message itself,
            # so it no longer wipes it on the next keystroke.
            self._apply_context_source_state()

        threading.Thread(target=work, daemon=True).start()

    def _upload_cv_file(self) -> None:
        from tkinter import filedialog

        cfg = MODE_CONTEXT.get(self._current_mode or "entrevista", MODE_CONTEXT["entrevista"])
        what = cfg.get("file_title", "CV")
        path = filedialog.askopenfilename(
            title=f"Seleccionar {what}",
            filetypes=[
                ("Documentos (PDF, Word, texto)", "*.pdf;*.docx;*.txt;*.md"),
                ("PDF", "*.pdf"),
                ("Word", "*.docx"),
                ("Texto", "*.txt;*.md"),
            ],
        )
        if not path:
            return
        try:
            text = self._extract_cv_text(path)
        except Exception as exc:
            logger.exception("Failed extracting CV text from %s: %s", path, exc)
            show_error(self, what, f"No se pudo leer el archivo:\n{exc}")
            return
        if not text.strip():
            show_warning(
                self, what, "El archivo no tiene texto extraíble (¿PDF escaneado como imagen?)."
            )
            return
        self.context_box.delete("1.0", tk.END)
        self.context_box.insert("1.0", text.strip())
        self._save_context(text.strip(), self._current_mode or "entrevista")
        self._apply_context_source_state()

    def _clear_written_context(self) -> None:
        """Clear the editable written context without restoring its placeholder."""
        was_disabled = self.context_box._textbox.cget("state") == "disabled"
        if was_disabled:
            self.context_box.configure(state="normal")
        self.context_box.delete("1.0", tk.END)
        if self._current_mode:
            self._mode_contexts[self._current_mode] = ""
        if was_disabled:
            self.context_box.configure(state="disabled")
        self._save_context("", self._current_mode or "entrevista")
        self._apply_context_source_state()

    @staticmethod
    def _extract_cv_text(path: str) -> str:
        lower = path.lower()
        if lower.endswith(".pdf"):
            from pypdf import PdfReader

            reader = PdfReader(path)
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        if lower.endswith(".docx"):
            import docx

            document = docx.Document(path)
            return "\n".join(p.text for p in document.paragraphs)
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return fh.read()

    def _load_saved_mode_label(self) -> str:
        default_label = self._mode_labels()[0]
        if config.repository is None:
            return default_label
        setting_key = (
            RESOLVER_MODE_SETTING_KEY
            if self._fixed_mode == "resolver"
            else MODE_SETTING_KEY
        )
        try:
            saved = config.repository.get_setting(setting_key)
        except Exception as exc:
            logger.exception("Failed loading saved assist mode: %s", exc)
            return default_label
        for label, mode in MODE_LABELS.items():
            if mode == saved and label in self._mode_labels():
                return label
        return default_label

    def _on_mode_change(self, choice: str) -> None:
        """Ajusta la etiqueta, el placeholder y el botón de CV según el modo.

        Cada modo tiene su propio contexto guardado: el CV vive solo en el modo
        entrevista. Al cambiar de modo guardamos lo que había en el modo anterior
        y cargamos el contexto propio del nuevo modo (o su placeholder).
        """
        new_mode = MODE_LABELS.get(choice, "entrevista")
        cfg = MODE_CONTEXT.get(new_mode, MODE_CONTEXT["entrevista"])
        oral_mode = (
            new_mode in interview_live.ORAL_ASSIST_MODES
            or new_mode == "practica_oral"
        )
        prev = getattr(self, "_current_mode", None)

        # Guardar lo escrito en el modo anterior antes de cambiar.
        if prev is not None and prev != new_mode:
            current = self.context_box.get("1.0", tk.END).strip()
            if current in ALL_PLACEHOLDERS:
                current = ""
            self._save_context(current, prev)

        self.context_label.configure(text=cfg["label"], text_color=cfg["color"])
        if cfg["show_cv"]:
            self.cv_button.configure(text=cfg.get("file_button", "📂  Cargar CV…"))
            self.cv_button.pack(side=tk.RIGHT)
        else:
            self.cv_button.pack_forget()

        # Cargar el contexto y el idioma propios del nuevo modo (solo si cambió).
        if prev != new_mode:
            saved = self._load_saved_context(new_mode)
            context_was_disabled = (
                self.context_box._textbox.cget("state") == "disabled"
            )
            if context_was_disabled:
                self.context_box.configure(state="normal")
            self.context_box.delete("1.0", tk.END)
            self.context_box.insert("1.0", saved or cfg["placeholder"])
            if context_was_disabled:
                self.context_box.configure(state="disabled")
            # Un examen oral se rinde en español y una entrevista suele ser en
            # inglés: cada modo arranca con su idioma y recuerda tu elección.
            self.lang_var.set(self._lang_label_for(new_mode))

        if oral_mode:
            self.lang_var.set("Español")
            self.lang_combo.configure(state="disabled")
        else:
            self.lang_combo.configure(state="readonly")

        self._context_placeholder = cfg["placeholder"]
        self._current_mode = new_mode
        self._apply_simulation_mode_ui()

    def _apply_simulation_mode_ui(self) -> None:
        """Hide external-interviewer controls when AI conducts the interview."""
        if not hasattr(self, "start_button"):
            return
        current_mode = getattr(self, "_current_mode", None)
        simulation = current_mode in SIMULATION_MODES
        if current_mode == "practica_oral":
            start_text = "▶  Iniciar práctica oral"
        elif current_mode == "simulacro":
            start_text = "▶  Iniciar simulacro"
        elif self._fixed_mode == "resolver":
            start_text = "▶  Empezar a resolver"
        else:
            start_text = "▶  Iniciar entrevista"
        self.start_button.configure(
            text=start_text
        )
        if not hasattr(self, "source_combo"):
            return
        if simulation:
            self.source_label.grid_remove()
            self.source_combo.grid_remove()
            self.refresh_sources_button.grid_remove()
        else:
            self.source_label.grid()
            self.source_combo.grid()
            self.refresh_sources_button.grid()
        if hasattr(self, "candidate_box") and self._fixed_mode in {"resolver", "practica_oral"}:
            if current_mode == "practica_oral":
                self.interviewer_box.grid_configure(columnspan=1, padx=(0, 6))
                self.candidate_transcript_label.grid(
                    row=0, column=1, sticky="w", padx=(12, 0)
                )
                self.candidate_box.grid(
                    row=1, column=1, sticky="nsew", padx=(6, 0), pady=(4, 0)
                )
            else:
                self.interviewer_box.grid_configure(columnspan=2, padx=0)
                self.candidate_transcript_label.grid_remove()
                self.candidate_box.grid_remove()

    def _lang_label_for(self, mode: str) -> str:
        """Saved answer language for `mode`, or the mode's default."""
        if mode in interview_live.ORAL_ASSIST_MODES:
            return "Español"
        cfg = MODE_CONTEXT.get(mode, MODE_CONTEXT["entrevista"])
        code = self._mode_langs.get(mode) or cfg.get("default_lang", "en")
        if mode not in self._mode_langs and config.repository is not None:
            try:
                saved = config.repository.get_setting(f"{LANG_SETTING_KEY}_{mode}")
                if saved in LANG_LABELS.values():
                    code = saved
            except Exception as exc:
                logger.exception("Failed loading saved answer language: %s", exc)
        for label, value in LANG_LABELS.items():
            if value == code:
                return label
        return next(iter(LANG_LABELS))

    def _load_saved_lang_label(self) -> str:
        return self._lang_label_for(MODE_LABELS.get(self.mode_var.get(), "entrevista"))

    def _on_lang_change(self, choice: str) -> None:
        """Persist the answer language for the mode currently selected."""
        code = LANG_LABELS.get(choice, "en")
        mode = self._current_mode or "entrevista"
        self._mode_langs[mode] = code
        if config.repository is None:
            return
        try:
            config.repository.set_setting(f"{LANG_SETTING_KEY}_{mode}", code)
        except Exception as exc:
            logger.exception("Failed saving answer language: %s", exc)

    def _load_saved_context(self, mode: str = "entrevista") -> str | None:
        # Cache de sesión primero (funciona aunque no haya base de datos).
        if mode in self._mode_contexts:
            return self._mode_contexts[mode] or None
        if config.repository is None:
            return None
        try:
            saved = config.repository.get_setting(f"{CONTEXT_SETTING_KEY}_{mode}")
            if saved and saved.strip():
                return saved.strip()
            # Migración: contexto viejo guardado en la clave global (era el CV).
            if mode == "entrevista":
                legacy = config.repository.get_setting(CONTEXT_SETTING_KEY)
                return legacy.strip() or None if legacy else None
            return None
        except Exception as exc:
            logger.exception("Failed loading saved interview context: %s", exc)
            return None

    def _save_context(self, context: str, mode: str = "entrevista") -> None:
        self._mode_contexts[mode] = context
        if config.repository is None:
            return
        try:
            config.repository.set_setting(f"{CONTEXT_SETTING_KEY}_{mode}", context)
        except Exception as exc:
            logger.exception("Failed saving interview context: %s", exc)

    def start_session(self) -> None:
        selected_mode = MODE_LABELS.get(self.mode_var.get(), "entrevista")
        configured = (
            interview_simulation.is_configured()
            if selected_mode in SIMULATION_MODES
            else interview_live.is_configured()
        )
        if not configured:
            show_info(
                self,
                "Resolver preguntas" if self._fixed_mode == "resolver" else "Entrevista",
                "Configurá una clave Gemini en Ajustes antes de iniciar.",
            )
            return
        written_context = self.context_box.get("1.0", tk.END).strip()
        if written_context in ALL_PLACEHOLDERS:
            written_context = ""
        use_notebook = (
            hasattr(self, "use_notebook_var")
            and self.use_notebook_var.get()
        )
        written_ready = self.use_written_context_var.get() and bool(written_context)
        if use_notebook and not self._active_notebook_context and not written_ready:
            self.notebook_combo.configure(border_color=ERROR)
            self._set_notebook_status(
                "Seleccioná y sincronizá una materia para continuar.",
                ERROR,
            )
            self.notebook_combo.focus_set()
            return
        if use_notebook and not self._active_notebook_context:
            logger.info(
                "Starting with written context while NotebookLM sync is pending"
            )
        context_parts = []
        if self.use_written_context_var.get() and written_context:
            context_parts.append(
                f"[Contexto escrito]\n{written_context}"
                if use_notebook
                else written_context
            )
        if use_notebook and self._active_notebook_context:
            context_parts.append(self._active_notebook_context)
        context = "\n\n".join(context_parts)
        # The button is already gated on readiness, but a stale widget state must
        # never let the resolver run blind: the coach would answer from general
        # knowledge and nothing in the UI would say the material was missing.
        if self._fixed_mode in {"resolver", "practica_oral"} and not context:
            show_warning(
                self,
                "Resolver preguntas",
                "Cargá el material antes de empezar: pegá el tema o "
                "sincronizá una materia de NotebookLM.",
            )
            self._apply_context_source_state()
            return
        # Only the user's written context is persistent. NotebookLM material is
        # opt-in for the current app session and never overwrites this field.
        self._save_context(written_context, self._current_mode or "entrevista")
        assist_mode = selected_mode
        if config.repository is not None:
            try:
                setting_key = (
                    RESOLVER_MODE_SETTING_KEY
                    if self._fixed_mode == "resolver"
                    else MODE_SETTING_KEY
                )
                config.repository.set_setting(setting_key, assist_mode)
            except Exception as exc:
                logger.exception("Failed saving assist mode: %s", exc)
        answer_lang = interview_live.resolve_answer_lang(
            assist_mode, LANG_LABELS.get(self.lang_var.get(), "en")
        )
        # El STT del interlocutor tiene que ir en el idioma en que habla, o el
        # coach recibe basura. Nunca 'auto': el tab Live fija el idioma (ver
        # Transcriber._locked_language), así que 'auto' cae al inglés histórico.
        stt_lang = answer_lang if answer_lang in ("es", "en") else "en"
        if assist_mode in SIMULATION_MODES:
            simulation_type = "academic" if assist_mode == "practica_oral" else "interview"
            self._start_simulation(context, answer_lang, simulation_type)
            return
        source_type, source_value = self._source_map.get(
            self.source_var.get(), ("loopback", None)
        )
        self._session_ended = False
        self._session_paused = False
        self.pause_button.configure(text="⏸  Pausar")
        self._mic_error_shown = False
        self.show_active()
        self._set_status("Conectando...", ACCENT, busy=True)
        self.worker.start(
            language=stt_lang, out_dir=self.out_dir, source_type=source_type,
            source_val=source_value, translate=False,
            save_audio=self.record_var.get(), interview_mode=True,
            interview_context=context, interview_assist_mode=assist_mode,
            interview_answer_lang=answer_lang,
            assist_queue=self.assist_queue,
        )
        self.candidate_listener.start(
            self._microphone_map.get(self.mic_var.get()),
            language=answer_lang if answer_lang in ("es", "en") else None,
        )

    def _start_simulation(
        self, context: str, answer_lang: str, simulation_type: str = "interview"
    ) -> None:
        self._session_ended = False
        self._session_paused = False
        self._simulation_answer_parts = []
        self._simulation_busy = True
        self._simulation_type = simulation_type
        self._simulation_session = interview_simulation.InterviewSimulationSession(
            context=context,
            answer_lang=answer_lang if answer_lang in ("es", "en") else "es",
            simulation_type=simulation_type,
        )
        self.show_active()
        if simulation_type == "academic":
            self.start_answer_button.pack(side=tk.RIGHT, padx=(0, 8))
            self.start_answer_button.configure(state="disabled")
            self.hint_button.pack(side=tk.RIGHT, padx=(0, 8))
            self.mastered_button.pack(side=tk.RIGHT, padx=(0, 8))
            self.dont_know_button.pack(side=tk.RIGHT, padx=(0, 8))
            self.read_question_button.pack(side=tk.RIGHT, padx=(0, 8))
            self.hint_button.configure(state="disabled")
            self.mastered_button.configure(state="disabled")
            self.dont_know_button.configure(state="disabled")
            self.read_question_button.configure(state="disabled")
        else:
            self.complete_answer_button.pack(side=tk.RIGHT, padx=(0, 8))
            self.complete_answer_button.configure(state="disabled")
        self.pause_button.pack_forget()
        self._set_question_text("Preparando la primera pregunta…", busy=True)
        self._set_status(
            "Preparando profesor IA"
            if simulation_type == "academic"
            else "Preparando simulacro",
            ACCENT,
            busy=True,
        )

        def work():
            try:
                turn = self._simulation_session.start()
            except Exception as exc:
                self.after(0, lambda message=str(exc): self._simulation_failed(message))
                return
            self.after(0, lambda: self._apply_simulation_turn(turn))

        threading.Thread(target=work, daemon=True).start()

    def _apply_simulation_turn(self, turn) -> None:
        self._simulation_busy = False
        # Misma regla que en el coach: si hay un turno viejo en pantalla es
        # alguien leyéndolo, y la devolución se graba sin pisárselo.
        live = self._history_index is None
        if turn.feedback and live:
            self.reply_boxes[0]._reply_text = turn.feedback
            self._set_box_text(self.reply_boxes[0], turn.feedback)
        raw_example_answer = getattr(turn, "example_answer", "")
        example_answer = (
            raw_example_answer.strip()
            if isinstance(raw_example_answer, str)
            else ""
        )
        improvement_text = None
        if turn.improvements or example_answer:
            sections = []
            if turn.improvements:
                sections.append("Para mejorar:\n" + " • ".join(turn.improvements))
            if example_answer:
                sections.append(
                    "Ejemplo de respuesta mejorada:\n" + example_answer
                )
            improvement_text = "\n\n".join(sections)
            if live:
                self.reply_boxes[1]._reply_text = improvement_text
                self._set_box_text(self.reply_boxes[1], improvement_text)
        if turn.strengths and live:
            self.ideas_label.configure(text="Fortalezas: " + " • ".join(turn.strengths))
        # La devolución llega antes de la pregunta siguiente, así que pertenece
        # al turno que ``_present_simulation_question`` ya dejó abierto: lo
        # completa en vez de abrir uno nuevo. En el primer turno no hay nada
        # abierto y no hay devolución, así que no se graba nada.
        if turn.feedback or improvement_text or turn.strengths:
            self._history_record(
                respuestas=[turn.feedback or None, improvement_text],
                ideas=list(turn.strengths) if turn.strengths else None,
                sealed=True,
            )
        if turn.action == "finish":
            self._finish_simulation()
            return
        simulation_type = getattr(
            self._simulation_session, "simulation_type", "interview"
        )
        has_learning_feedback = bool(
            turn.feedback or turn.improvements or example_answer
        )
        if simulation_type == "academic" and has_learning_feedback:
            self._pending_simulation_turn = turn
            self.candidate_listener.pause()
            self.complete_answer_button.configure(state="disabled")
            self.hint_button.configure(state="disabled")
            self.mastered_button.configure(state="disabled")
            self.dont_know_button.configure(state="disabled")
            self.read_question_button.configure(state="disabled")
            self.transcripts_frame.pack_forget()
            self._set_status("Revisemos tu respuesta", ACCENT, busy=True)
            self._speak_learning_feedback(turn)
            return
        self._present_simulation_question(turn)

    def _present_simulation_question(self, turn) -> None:
        self._pending_simulation_turn = None
        if not self.transcripts_frame.winfo_manager():
            self.transcripts_frame.pack(
                fill=tk.X,
                padx=24,
                pady=(6, 10),
                before=self.question_label.master,
            )
        self._simulation_current_question = turn.question
        if self._history_index is None:
            self._set_question_text(turn.question)
        # Abre el turno que la devolución del próximo ``_apply_simulation_turn``
        # va a completar.
        self._history_open = False
        self._history_record(pregunta=turn.question)
        self.interviewer_box.insert(tk.END, ("\n" if self.interviewer_box.get("1.0", tk.END).strip() else "") + turn.question)
        self.interviewer_box.see(tk.END)
        self._simulation_answer_parts = []
        self._simulation_answering = False
        simulation_type = getattr(
            self._simulation_session, "simulation_type", "interview"
        )
        self.complete_answer_button.configure(
            state="disabled" if simulation_type == "academic" else "normal"
        )
        if simulation_type == "academic":
            self.complete_answer_button.pack_forget()
            self.start_answer_button.pack(side=tk.RIGHT, padx=(0, 8))
            self.start_answer_button.configure(state="normal")
        self.hint_button.configure(state="normal")
        self.mastered_button.configure(state="normal")
        self.dont_know_button.configure(state="normal")
        self.read_question_button.configure(state="normal")
        if simulation_type == "academic":
            self._speak_simulation_question()
            return
        self._resume_simulation_answer_capture()
        self._set_status("Respondé y confirmá cuando termines", SUCCESS)

    def _speak_learning_feedback(self, turn) -> None:
        parts = []
        if turn.feedback:
            parts.append(turn.feedback)
        if turn.improvements:
            parts.append("Para mejorar: " + ". ".join(turn.improvements))
        example = getattr(turn, "example_answer", "")
        if isinstance(example, str) and example.strip():
            parts.append("Ejemplo de respuesta mejorada: " + example.strip())
        spoken = " ".join(parts).strip()
        if not spoken:
            self._show_next_question_dialog()
            return
        # El botón queda usable desde que arranca a hablar, no recién cuando
        # termina: es el barge-in de la devolución, igual que con la pregunta.
        self.next_question_button.pack(side=tk.RIGHT, padx=(0, 8))
        self.next_question_button.configure(state="normal")
        language = getattr(self._simulation_session, "answer_lang", "es")
        tts.speak_async(
            spoken,
            language=language,
            on_done=lambda: self.after(0, self._show_next_question_dialog),
        )

    def _resume_simulation_answer_capture(self) -> None:
        answer_lang = getattr(self._simulation_session, "answer_lang", "es")
        if self.candidate_listener.is_running():
            self.candidate_listener.resume()
        else:
            self.candidate_listener.start(
                self._microphone_map.get(self.mic_var.get()),
                language=answer_lang,
            )

    def _start_simulation_answer(self) -> None:
        if self._simulation_busy or self._simulation_session is None:
            return
        # Barge-in: el alumno puede arrancar a responder con la pregunta
        # todavía sonando. tts.stop() la corta ya mismo; gracias al generation
        # token de tts, el on_done cancelado no va a llegar después a pisar lo
        # que este método está por dejar armado.
        tts.stop()
        self._cancel_knowledge_check()
        self._cancel_next_question_timer()
        self._dismiss_knowledge_dialog()
        self._simulation_answer_parts = []
        self.candidate_box.delete("1.0", tk.END)
        while True:
            try:
                self.candidate_queue.get_nowait()
            except queue.Empty:
                break
        self._simulation_answering = True
        self.start_answer_button.configure(state="disabled")
        self.start_answer_button.pack_forget()
        self.complete_answer_button.pack(side=tk.RIGHT, padx=(0, 8))
        self.complete_answer_button.configure(state="normal")
        # Si la interrupción fue durante la lectura, _question_speech_finished
        # nunca corre (on_done cancelado): sin esto el botón de re-leer
        # quedaba deshabilitado para el resto de la respuesta.
        self.read_question_button.configure(state="normal")
        self._resume_simulation_answer_capture()
        self._set_status("Te escucho; terminá cuando completes tu respuesta", SUCCESS)

    def _request_simulation_hint(self) -> None:
        if self._simulation_busy or self._simulation_session is None:
            return
        self._simulation_busy = True
        self.hint_button.configure(state="disabled")
        self.candidate_listener.pause()
        self._set_status("Preparando una pista", ACCENT, busy=True)

        def work():
            try:
                hint = self._simulation_session.hint()
            except Exception as exc:
                self.after(0, lambda message=str(exc): self._simulation_failed(message))
                return
            self.after(0, lambda: self._apply_simulation_hint(hint))

        threading.Thread(target=work, daemon=True).start()

    def _skip_mastered_question(self) -> None:
        if self._simulation_busy or self._simulation_session is None:
            return
        tts.stop()
        self._cancel_knowledge_check()
        self._cancel_next_question_timer()
        self._dismiss_knowledge_dialog()
        self._simulation_busy = True
        self.candidate_listener.pause()
        for button in (
            self.start_answer_button,
            self.complete_answer_button,
            self.hint_button,
            self.mastered_button,
            self.dont_know_button,
            self.read_question_button,
        ):
            button.configure(state="disabled")
        self._set_status("Buscando otra pregunta", ACCENT, busy=True)

        def work():
            try:
                turn = self._simulation_session.skip_mastered_question()
            except Exception as exc:
                self.after(0, lambda message=str(exc): self._simulation_failed(message))
                return
            self.after(0, lambda: self._apply_simulation_turn(turn))

        threading.Thread(target=work, daemon=True).start()

    def _submit_dont_know(self) -> None:
        if self._simulation_busy or self._simulation_session is None:
            return
        tts.stop()
        self._simulation_answer_parts = [
            "No lo sé. Explicame el concepto y mostrame un ejemplo."
        ]
        self._simulation_answering = True
        self._submit_simulation_answer()

    def _apply_simulation_hint(self, hint: str) -> None:
        self._simulation_busy = False
        text = f"Pista: {hint}"
        if self._history_index is None:
            self.reply_boxes[0]._reply_text = text
            self._set_box_text(self.reply_boxes[0], text)
        # Entra en el turno abierto igual que en pantalla: la devolución de esa
        # misma pregunta la reemplaza después, acá y allá.
        self._history_record(respuestas=[text, None])
        self.hint_button.configure(state="normal")
        if self._simulation_answering:
            self.candidate_listener.resume()
            self._set_status("Pista lista; seguí respondiendo", SUCCESS)
        else:
            self._set_status("Pista lista; empezá cuando estés listo", SUCCESS)

    def _speak_simulation_question(self) -> None:
        if not self._simulation_current_question or self._simulation_busy:
            return
        self.candidate_listener.pause()
        self.read_question_button.configure(state="disabled")
        self._set_status("Leyendo la pregunta", ACCENT, busy=True)
        language = getattr(self._simulation_session, "answer_lang", "es")
        tts.speak_async(
            self._simulation_current_question,
            language=language,
            on_done=lambda: self.after(0, self._question_speech_finished),
        )

    def _question_speech_finished(self) -> None:
        if self._session_ended:
            return
        self.read_question_button.configure(state="normal")
        if getattr(self._simulation_session, "simulation_type", "") == "academic":
            self.complete_answer_button.pack_forget()
            self.start_answer_button.pack(side=tk.RIGHT, padx=(0, 8))
            self.start_answer_button.configure(state="normal")
            self.complete_answer_button.configure(state="disabled")
            self._set_status("Cuando estés listo, iniciá tu respuesta", SUCCESS)
            self._schedule_knowledge_check()
            return
        self._resume_simulation_answer_capture()
        self._set_status("Respondé y confirmá cuando termines", SUCCESS)

    def _schedule_knowledge_check(self) -> None:
        self._cancel_knowledge_check()
        self._knowledge_check_after_id = self.after(
            10_000, self._show_knowledge_check
        )

    def _cancel_knowledge_check(self) -> None:
        timer_id = self._knowledge_check_after_id
        self._knowledge_check_after_id = None
        if timer_id is not None:
            try:
                self.after_cancel(timer_id)
            except Exception:
                pass

    def _choice_dialog(
        self,
        *,
        title: str,
        message: str,
        primary_text: str,
        primary_command,
        secondary_text: str | None = None,
        secondary_command=None,
    ):
        dialog = ctk.CTkToplevel(self)
        dialog.title(title)
        dialog.geometry("480x230")
        dialog.resizable(False, False)
        dialog.configure(fg_color=BG)
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        ctk.CTkLabel(
            dialog, text=title, font=("Segoe UI Semibold", 18),
            text_color=TEXT,
        ).pack(anchor=tk.W, padx=24, pady=(22, 8))
        ctk.CTkLabel(
            dialog, text=message, font=("Segoe UI", 13),
            text_color=MUTED, wraplength=430, justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=24)
        actions = ctk.CTkFrame(dialog, fg_color="transparent")
        actions.pack(fill=tk.X, padx=24, pady=(24, 22))

        def choose(command):
            try:
                dialog.grab_release()
            except Exception:
                pass
            dialog.destroy()
            command()

        if secondary_text and secondary_command:
            ctk.CTkButton(
                actions, text=secondary_text, height=40,
                fg_color=PANEL_DARK, hover_color="#20212D",
                border_color=BORDER, border_width=1,
                command=lambda: choose(secondary_command),
            ).pack(side=tk.LEFT)
        primary = ctk.CTkButton(
            actions, text=primary_text, height=40,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=lambda: choose(primary_command),
        )
        primary.pack(side=tk.RIGHT)
        primary.focus_set()
        return dialog

    def _show_knowledge_check(self) -> None:
        # Antes abría un CTkToplevel con grab_set() acá: le robaba el foco al
        # alumno mientras pensaba, para ofrecerle dos acciones que ya están
        # siempre visibles como botones (start_answer_button/dont_know_button).
        # Un texto de estado no bloquea nada y apunta a los mismos dos botones.
        self._knowledge_check_after_id = None
        if (
            self._session_ended
            or self._simulation_busy
            or self._simulation_answer_parts
            or self._knowledge_dialog is not None
            or self._simulation_answering
        ):
            return
        self._set_status(
            "¿La sabés? Arrancá tu respuesta o pedí la explicación", ACCENT
        )

    def _dismiss_knowledge_dialog(self) -> None:
        dialog = self._knowledge_dialog
        self._knowledge_dialog = None
        if dialog is not None:
            try:
                dialog.grab_release()
                dialog.destroy()
            except Exception:
                pass

    def _cancel_next_question_timer(self) -> None:
        timer_id = self._next_question_after_id
        self._next_question_after_id = None
        if timer_id is not None:
            try:
                self.after_cancel(timer_id)
            except Exception:
                pass

    def _show_next_question_dialog(self) -> None:
        # Ya NO es un modal: se llama cuando termina de leerse (o no hay nada
        # que leer) la devolución. El botón inline ya está packeado desde que
        # arrancó a hablar (ver _speak_learning_feedback); acá solo se
        # confirma el estado y, si corresponde, se arma el avance automático.
        if self._session_ended or self._pending_simulation_turn is None:
            return
        self.next_question_button.pack(side=tk.RIGHT, padx=(0, 8))
        self.next_question_button.configure(state="normal")
        self._next_question_dialog = True
        self._set_status("Devolución lista · continuá cuando quieras", SUCCESS)
        self._cancel_next_question_timer()
        if _NEXT_QUESTION_GRACE_MS is not None:
            self._next_question_after_id = self.after(
                _NEXT_QUESTION_GRACE_MS, self._continue_to_next_question
            )

    def _continue_to_next_question(self) -> None:
        # El timer y el click llaman a este mismo método. tts.stop() corta la
        # devolución si todavía se está leyendo (el barge-in del botón) y no
        # hace nada si ya terminó de hablar sola.
        tts.stop()
        self._cancel_next_question_timer()
        self._next_question_dialog = None
        try:
            self.next_question_button.configure(state="disabled")
            self.next_question_button.pack_forget()
        except Exception:
            pass
        # _present_simulation_question vacía _pending_simulation_turn como
        # primer paso, así que una segunda llamada (timer después del click,
        # o viceversa) encuentra None acá y no presenta la pregunta dos veces.
        turn = self._pending_simulation_turn
        if turn is not None:
            self._present_simulation_question(turn)

    def _submit_simulation_answer(self) -> None:
        if self._simulation_busy or self._simulation_session is None:
            return
        self._cancel_knowledge_check()
        self._cancel_next_question_timer()
        self._simulation_busy = True
        self.candidate_listener.pause()
        self.complete_answer_button.configure(state="disabled")
        self.start_answer_button.configure(state="disabled")
        self.hint_button.configure(state="disabled")
        self.mastered_button.configure(state="disabled")
        self.dont_know_button.configure(state="disabled")
        self.read_question_button.configure(state="disabled")
        self._set_status("Completando transcripción", ACCENT, busy=True)

        def drain_work():
            self.candidate_listener.wait_until_idle(timeout=10.0)
            self.after(0, self._submit_drained_simulation_answer)

        threading.Thread(target=drain_work, daemon=True).start()

    def _submit_drained_simulation_answer(self) -> None:
        self._append_batch(self.candidate_box, "candidate", self.candidate_queue)
        answer = " ".join(self._simulation_answer_parts).strip()
        if not answer:
            self._simulation_busy = False
            self.start_answer_button.configure(state="normal")
            self.hint_button.configure(state="normal")
            self.mastered_button.configure(state="normal")
            self.dont_know_button.configure(state="normal")
            self.read_question_button.configure(state="normal")
            show_info(self, "Simulacro", "Respondé por micrófono antes de continuar.")
            return
        # La respuesta ya quedó copiada en ``answer`` para evaluación. Limpiar
        # vista y buffer evita que el turno siguiente parezca reutilizarla o
        # agregue la nueva transcripción debajo de la anterior.
        #
        # Antes de borrarla queda en el turno abierto: la caja era el único
        # lugar donde vivía, así que lo guardado en Historial terminaba con
        # todas las preguntas y una sola respuesta, la última.
        self._history_record(respuesta_usuario=answer)
        self._simulation_answer_parts = []
        self.candidate_box.delete("1.0", tk.END)
        self._set_status("Evaluando tu respuesta", ACCENT, busy=True)

        def work():
            try:
                turn = self._simulation_session.submit_answer(answer)
            except Exception as exc:
                self.after(0, lambda message=str(exc): self._simulation_failed(message))
                return
            self.after(0, lambda: self._apply_simulation_turn(turn))

        threading.Thread(target=work, daemon=True).start()

    def _simulation_retry_buttons(self) -> list:
        """Los botones que el usuario tiene EN PANTALLA en este momento.

        El modo académico empaqueta cinco (start/complete se turnan según si
        está respondiendo) y la entrevista uno solo. Reactivar
        ``complete_answer_button`` a ciegas dejaba el práctico oral con los
        cinco botones visibles deshabilitados para siempre: sin camino de
        reintento, la pantalla quedaba inerte.
        """
        mode = getattr(self._simulation_session, "simulation_type", "") or getattr(
            self, "_simulation_type", "interview"
        )
        names = (
            (
                "start_answer_button",
                "complete_answer_button",
                "hint_button",
                "mastered_button",
                "dont_know_button",
                "read_question_button",
            )
            if mode == "academic"
            else ("complete_answer_button",)
        )
        existing = []
        for name in names:
            widget = getattr(self, name, None)
            if widget is None:
                continue
            try:
                if not widget.winfo_exists():
                    continue
            except Exception:
                continue
            existing.append(widget)
        # Lo empaquetado manda: ``_submit_dont_know`` marca ``_simulation_answering``
        # sin cambiar los botones, así que deducir el botón por ese flag erraba.
        packed = [w for w in existing if self._widget_is_packed(w)]
        return packed or existing

    @staticmethod
    def _widget_is_packed(widget) -> bool:
        try:
            return bool(widget.winfo_manager())
        except Exception:
            return False

    def _simulation_failed(self, message: str) -> None:
        self._simulation_busy = False
        for button in self._simulation_retry_buttons():
            try:
                button.configure(state="normal")
            except Exception:
                continue
        self.candidate_listener.resume()
        self._set_status("Error en el simulacro", ERROR)
        show_error(self, "Simulacro", message)

    def _finish_simulation(self) -> None:
        self.candidate_listener.stop()
        self._session_ended = True
        report = self._simulation_session.report() if self._simulation_session else "Simulacro finalizado."
        self.reply_boxes[0]._reply_text = report
        self._set_box_text(self.reply_boxes[0], report)
        self.complete_answer_button.pack_forget()
        self.start_answer_button.pack_forget()
        self.hint_button.pack_forget()
        self.mastered_button.pack_forget()
        self.dont_know_button.pack_forget()
        self.read_question_button.pack_forget()
        self._cancel_next_question_timer()
        self.next_question_button.pack_forget()
        self.show_closing()
        self.closing_summary.configure(text=report)
        self.closing_hint.configure(text="La devolución queda incluida en el texto de la sesión.", text_color=SUCCESS)
        self._set_status("Simulacro finalizado", SUCCESS)

    def finish_session(self) -> None:
        if self._simulation_session is not None and self._simulation_session.state != "completed":
            self._simulation_session.finish()
            self._finish_simulation()
            return
        self.stop_session()
        self._session_ended = True
        self.show_closing()
        self._set_status("Finalizada")
        # Worker thread finalizes the WAV header on close; give it a moment
        # before registering the file in the history DB.
        self.after(1500, self._register_in_history)

    def toggle_pause(self) -> None:
        """Pause or resume both audio sources while keeping this session open."""
        if self._session_paused:
            self.worker.resume()
            self.candidate_listener.resume()
            self._session_paused = False
            self.pause_button.configure(text="⏸  Pausar")
            self._set_status(
                "Grabando audio" if self.record_var.get() else "Sesión activa",
                REC if self.record_var.get() else ACCENT,
            )
            return

        if not (self.worker.is_running() or self.candidate_listener.is_running()):
            return
        self.worker.pause()
        self.candidate_listener.pause()
        self._session_paused = True
        self.pause_button.configure(text="▶  Reanudar")
        self._set_status("En pausa", "#F6AD55")

    def stop_session(self) -> None:
        self.worker.stop()
        self.candidate_listener.stop()
        self._session_paused = False
        self.pause_button.configure(text="⏸  Pausar")

    # Widget line cap: full transcript is always in transcript_*.txt, so trimming
    # the on-screen tail keeps the UI thread fast during long sessions.
    _BOX_MAX_LINES = 1200
    _BOX_TRIM_LINES = 300

    def _append_batch(self, box, role: str, source: "queue.Queue[str]") -> None:
        items = []
        while not source.empty():
            items.append(source.get_nowait())
        if not items:
            return
        if (
            role == "candidate"
            and self._simulation_session is not None
            and getattr(self._simulation_session, "simulation_type", "") == "academic"
            and not self._simulation_answering
        ):
            return
        if role == "interviewer":
            # Gemini Live emits streaming deltas and may split in the middle of
            # a word. Preserve its whitespace instead of putting every delta on
            # a new line.
            box.insert(tk.END, "".join(items))
        else:
            prefix = "" if box.get("1.0", "1.1") == "" else "\n"
            box.insert(tk.END, prefix + "\n".join(items))
        try:
            if int(str(box.index("end-1c")).split(".")[0]) > self._BOX_MAX_LINES:
                box.delete("1.0", f"{self._BOX_TRIM_LINES}.0")
        except Exception:
            pass
        box.see(tk.END)
        if role == "candidate":
            for text in items:
                if self._simulation_session is not None and self._simulation_session.state != "completed":
                    self._simulation_answer_parts.append(text)
                else:
                    self.worker.add_candidate_turn(text)
            if items:
                self._cancel_knowledge_check()
                self._cancel_next_question_timer()
                self._dismiss_knowledge_dialog()

    def _apply_assist(self, assist) -> None:
        # While the coach is still streaming, the answer line lands before the
        # question gloss and the key ideas. Blanking those fields on every
        # partial would make them flash placeholder text, so a partial only ever
        # fills in what it actually carries. ``None`` marks a field the delta
        # does not carry, so neither the widgets nor the history entry lose what
        # a previous delta already put there.
        partial = getattr(assist, "partial", False)
        self._set_answer_loading(False)
        # Un turno viejo en pantalla es alguien leyéndolo: se graba igual, pero
        # no se le pisa la vista.
        live = self._history_index is None

        pregunta = None
        if assist.pregunta_es or not partial:
            pregunta = assist.pregunta_es or "Pregunta detectada"
            if live:
                self._set_question_text(pregunta)

        respuestas: list[str | None] = []
        for i, box in enumerate(self.reply_boxes):
            text = assist.respuestas[i] if i < len(assist.respuestas) else ""
            if partial and not text:
                respuestas.append(None)
                continue
            respuestas.append(text)
            if live:
                box._reply_text = text
                self._set_box_text(box, text or "Sin sugerencia")

        ideas = assist.ideas_clave or []
        if ideas or not partial:
            if live:
                self.ideas_label.configure(text=" • ".join(ideas) if ideas else _NO_IDEAS)
        else:
            ideas = None

        self._history_record(
            pregunta=pregunta,
            respuestas=respuestas,
            ideas=ideas,
            sealed=not partial,
        )

    # ------------------------------------------------------------------ historial

    def _history_record(
        self,
        *,
        pregunta: str | None = None,
        respuestas: list[str | None] | None = None,
        ideas: list[str] | None = None,
        respuesta_usuario: str | None = None,
        sealed: bool = False,
    ) -> None:
        """Graba el turno actual. ``None`` en un campo = el delta no lo trae.

        Abre entrada nueva salvo que la última siga recibiendo deltas del
        stream (``_history_open``): sin eso una sola pregunta generaría una
        decena de turnos, uno por parcial de Gemini.
        """
        if not self._history_open or not self._qa_history:
            self._qa_history.append(
                {
                    "pregunta": "",
                    "respuestas": ["" for _ in self.reply_boxes],
                    "ideas": [],
                    # Lo que dijo el usuario en ese turno. Solo lo llena el
                    # simulacro: en el coach la respuesta la da el modelo.
                    "respuesta_usuario": "",
                    "hora": time.strftime("%H:%M"),
                }
            )
            self._history_open = True
        entry = self._qa_history[-1]
        if pregunta is not None:
            entry["pregunta"] = pregunta
        if respuesta_usuario is not None:
            entry["respuesta_usuario"] = respuesta_usuario
        for i, text in enumerate(respuestas or []):
            if text is None:
                continue
            while len(entry["respuestas"]) <= i:
                entry["respuestas"].append("")
            entry["respuestas"][i] = text
        if ideas is not None:
            entry["ideas"] = list(ideas)
        if sealed:
            self._history_open = False
        self._history_update_nav()
        # Reconstruir las tarjetas en cada delta del stream sería tirar trabajo:
        # la ventana se refresca cuando el turno cierra.
        if sealed and self._history_window is not None:
            self._fill_history_window()

    def _history_render(self, index: int) -> None:
        """Vuelca un turno guardado en las mismas cajas de la sesión viva."""
        if not 0 <= index < len(self._qa_history):
            return
        entry = self._qa_history[index]
        self._set_question_text(entry["pregunta"] or "Pregunta detectada")
        for i, box in enumerate(self.reply_boxes):
            text = entry["respuestas"][i] if i < len(entry["respuestas"]) else ""
            # ``_reply_text`` es lo que copian y leen en voz alta los botones de
            # cada tarjeta: se actualiza acá para que operen sobre el turno que
            # se está viendo, no sobre el último que llegó.
            box._reply_text = text
            self._set_box_text(box, text or "Sin sugerencia")
        ideas = entry["ideas"]
        self.ideas_label.configure(text=" • ".join(ideas) if ideas else _NO_IDEAS)

    def _history_go(self, delta: int) -> None:
        if not self._qa_history:
            return
        last = len(self._qa_history) - 1
        current = last if self._history_index is None else self._history_index
        target = max(0, min(last, current + delta))
        if target == last:
            self._history_latest()
            return
        self._history_index = target
        self._history_render(target)
        self._history_update_nav()

    def _history_latest(self) -> None:
        self._history_index = None
        if self._qa_history:
            self._history_render(len(self._qa_history) - 1)
        self._history_update_nav()

    def _history_update_nav(self) -> None:
        total = len(self._qa_history)
        if total <= 1:
            self.history_bar.pack_forget()
            return
        if not self.history_bar.winfo_manager():
            self.history_bar.pack(
                **self._HISTORY_BAR_PACK, after=self._history_bar_anchor
            )
        browsing = self._history_index is not None
        shown = self._history_index if browsing else total - 1
        self.history_counter.configure(text=f"{shown + 1} / {total}")
        self.history_prev_button.configure(state="normal" if shown > 0 else "disabled")
        self.history_next_button.configure(
            state="normal" if shown < total - 1 else "disabled"
        )
        self.history_latest_button.configure(state="normal" if browsing else "disabled")
        pending = total - 1 - shown
        if browsing and pending > 0:
            plural = "s" if pending > 1 else ""
            self.history_hint.configure(text=f"⏭  {pending} respuesta{plural} nueva{plural}")
        else:
            self.history_hint.configure(text="")

    def _show_history_window(self) -> None:
        """Toda la sesión en una lista scrolleable, para repasar de un saque."""
        if self._history_window is not None:
            try:
                self._history_window.deiconify()
                self._history_window.lift()
                return
            except tk.TclError:
                self._history_window = None
        window = self._history_window = ctk.CTkToplevel(self)
        window.title("Preguntas y respuestas de la sesión")
        window.geometry("760x620")
        window.configure(fg_color=BG)
        window.transient(self.winfo_toplevel())
        # A diferencia de los diálogos de la sesión, esta ventana no toma el
        # foco con ``grab_set``: la sesión sigue grabando y transcribiendo
        # mientras se repasa.
        window.protocol("WM_DELETE_WINDOW", self._close_history_window)
        ctk.CTkLabel(
            window, text="📚  Preguntas y respuestas de la sesión",
            font=("Segoe UI Semibold", 18), text_color=TEXT,
        ).pack(anchor=tk.W, padx=20, pady=(18, 10))
        self._history_window_body = ctk.CTkScrollableFrame(
            window, fg_color="transparent"
        )
        self._history_window_body.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 18))
        self._fill_history_window()

    def _close_history_window(self) -> None:
        window, self._history_window = self._history_window, None
        if window is not None:
            try:
                window.destroy()
            except tk.TclError:
                pass

    def _fill_history_window(self) -> None:
        body = getattr(self, "_history_window_body", None)
        if body is None:
            return
        try:
            for child in body.winfo_children():
                child.destroy()
        except tk.TclError:
            return
        if not self._qa_history:
            ctk.CTkLabel(
                body, text="Todavía no hay preguntas en esta sesión.",
                font=("Segoe UI", 12), text_color=MUTED,
            ).pack(anchor=tk.W, pady=8)
            return
        titles = [label.cget("text") for label in self.reply_titles]
        for number, entry in enumerate(self._qa_history, start=1):
            card = ctk.CTkFrame(
                body, fg_color=PANEL, corner_radius=12,
                border_color=BORDER, border_width=1,
            )
            card.pack(fill=tk.X, pady=(0, 10))
            ctk.CTkLabel(
                card, text=f"#{number}  ·  {entry['hora']}",
                font=("Segoe UI Semibold", 9), text_color=MUTED,
            ).pack(anchor=tk.W, padx=14, pady=(10, 2))
            ctk.CTkLabel(
                card, text=entry["pregunta"] or "Pregunta detectada",
                font=("Segoe UI Semibold", 14), text_color=WARNING,
                wraplength=660, justify=tk.LEFT, anchor="w",
            ).pack(fill=tk.X, padx=14, pady=(0, 8))
            if answer := entry.get("respuesta_usuario", "").strip():
                ctk.CTkLabel(
                    card, text="🎤  TU RESPUESTA",
                    font=("Segoe UI Semibold", 9), text_color=CANDIDATE,
                ).pack(anchor=tk.W, padx=14)
                ctk.CTkLabel(
                    card, text=answer, font=("Segoe UI", 12), text_color=TEXT,
                    wraplength=660, justify=tk.LEFT, anchor="w",
                ).pack(fill=tk.X, padx=14, pady=(2, 8))
            for i, text in enumerate(entry["respuestas"]):
                if not text.strip():
                    continue
                head = ctk.CTkFrame(card, fg_color="transparent")
                head.pack(fill=tk.X, padx=14)
                ctk.CTkLabel(
                    head,
                    text=titles[i] if i < len(titles) else f"RESPUESTA {i + 1}",
                    font=("Segoe UI Semibold", 9),
                    text_color=ACCENT if i == 0 else "#F6E05E",
                ).pack(side=tk.LEFT)
                ctk.CTkButton(
                    head, text="🔊", width=30, height=24, fg_color=PANEL_DARK,
                    hover_color="#20212D",
                    command=lambda t=text: tts.speak_async(t),
                ).pack(side=tk.RIGHT, padx=(4, 0))
                ctk.CTkButton(
                    head, text="📋", width=30, height=24, fg_color=PANEL_DARK,
                    hover_color="#20212D",
                    command=lambda t=text: self._copy_text(t),
                ).pack(side=tk.RIGHT)
                ctk.CTkLabel(
                    card, text=text, font=("Segoe UI", 12), text_color=TEXT,
                    wraplength=660, justify=tk.LEFT, anchor="w",
                ).pack(fill=tk.X, padx=14, pady=(2, 8))
            if entry["ideas"]:
                ctk.CTkLabel(
                    card, text="💡  " + " • ".join(entry["ideas"]),
                    font=("Segoe UI", 11), text_color=MUTED,
                    wraplength=660, justify=tk.LEFT, anchor="w",
                ).pack(fill=tk.X, padx=14, pady=(0, 10))
            else:
                ctk.CTkFrame(card, fg_color="transparent", height=4).pack()

    def _copy_text(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_status("Respuesta copiada", SUCCESS)

    def _set_answer_loading(self, loading: bool) -> None:
        """Show that a new answer is replacing the previous one.

        La animación es la misma ``LoadingText`` que usan el estado de sesión,
        la pregunta y NotebookLM. Antes esto tenía su propio contador avanzado
        desde ``_drain_queues``: dos sistemas de spinner conviviendo, con dos
        velocidades y dos formas distintas de olvidarse de frenar.
        """
        self._answer_loading = loading
        if loading:
            # Empieza una generación nueva: el próximo assist abre un turno
            # propio en vez de seguir escribiendo sobre el anterior.
            self._history_open = False
            self.answer_loading_label.grid()
            self._answer_loader.show("Generando respuesta nueva…", busy=True)
            # Blanquear las cajas mientras alguien lee un turno anterior le
            # borraría de la pantalla justo lo que fue a buscar.
            if self._history_index is None:
                for box in self.reply_boxes:
                    box._reply_text = ""
                    self._set_box_text(box, "Esperando respuesta…")
        else:
            self._answer_loader.stop()
            self.answer_loading_label.grid_remove()

    def _set_box_text(self, box, text: str) -> None:
        """Escribe en una caja de solo-lectura (habilita, reemplaza, deshabilita)."""
        box.configure(state="normal")
        box.delete("1.0", tk.END)
        box.insert("1.0", text)
        box.configure(state="disabled")

    def _speak_word_at(self, event) -> str | None:
        """Click secundario: si hay una frase seleccionada la lee entera; si no,
        lee la palabra bajo el cursor."""
        widget = event.widget
        text = ""
        try:
            if widget.tag_ranges("sel"):
                text = widget.get("sel.first", "sel.last").strip()
        except tk.TclError:
            text = ""
        if not text:
            idx = widget.index(f"@{event.x},{event.y}")
            start = widget.index(f"{idx} wordstart")
            end = widget.index(f"{idx} wordend")
            text = widget.get(start, end).strip()
        if not text or not re.search(r"[A-Za-z]", text):
            return None
        tts.speak_async(text)
        self._set_status(f"Reproduciendo: {text[:40]}", ACCENT)
        return "break"

    def _copy_reply(self, index: int) -> None:
        text = self.reply_boxes[index]._reply_text
        if text:
            self._copy_text(text)

    def _speak_reply(self, index: int) -> None:
        text = self.reply_boxes[index]._reply_text
        if text:
            tts.speak_async(text)
            self._set_status("Reproduciendo respuesta", ACCENT)

    def discard_session(self) -> None:
        temp = getattr(self.worker, "current_transcript_path", None)
        if temp:
            try:
                Path(temp).unlink(missing_ok=True)
            except Exception:
                pass
        self.reset_session()

    def reset_session(self) -> None:
        self.stop_session()
        self._cancel_knowledge_check()
        self._cancel_next_question_timer()
        try:
            self.next_question_button.configure(state="disabled")
            self.next_question_button.pack_forget()
        except Exception:
            pass
        for dialog_name in ("_knowledge_dialog", "_next_question_dialog"):
            dialog = getattr(self, dialog_name, None)
            if dialog is not None:
                try:
                    dialog.destroy()
                except Exception:
                    pass
                setattr(self, dialog_name, None)
        self._close_history_window()
        self._history_window_body = None
        self._qa_history = []
        self._history_index = None
        self._history_open = False
        self.history_bar.pack_forget()
        self._simulation_session = None
        self._simulation_type = "interview"
        self._simulation_answer_parts = []
        self._simulation_answering = False
        self._simulation_busy = False
        self._pending_simulation_turn = None
        self.complete_answer_button.pack_forget()
        self.start_answer_button.pack_forget()
        self.mastered_button.pack_forget()
        self.pause_button.pack(side=tk.RIGHT, padx=(0, 8))
        for box in (self.interviewer_box, self.candidate_box):
            box.delete("1.0", tk.END)
        self._set_question_text(
            (
                "Esperando una pregunta..."
                if self._fixed_mode == "resolver"
                else "Esperando al entrevistador..."
            )
        )
        for box in self.reply_boxes:
            box._reply_text = ""
            self._set_box_text(
                box,
                "Aparecerá después de responder. Tu voz se muestra arriba en TU RESPUESTA."
                if self._fixed_mode == "practica_oral"
                else "Aparecerá cuando detectemos una pregunta",
            )
        self.show_preparation()

    def _set_status(
        self, text: str, color: str | None = None, *, busy: bool = False
    ) -> None:
        if color is None:
            color = ERROR if "error" in text.lower() else SUCCESS if "activ" in text.lower() else MUTED
        # El "●" de siempre es el prefijo en reposo del loader: con ``busy`` lo
        # reemplaza el frame que gira, y cualquier estado posterior lo apaga.
        self._status_loader.show(text, color, busy=busy)

    def _set_question_text(self, text: str, *, busy: bool = False) -> None:
        """Escribe la pregunta en pantalla; ``busy`` la deja animada.

        En reposo el texto sale crudo, sin prefijo: hay tests que comparan el
        contenido de esta etiqueta por igualdad exacta.
        """
        self._question_loader.show(text, busy=busy)

    def _drain_queues(self) -> None:
        while not self.status_queue.empty():
            status = self.status_queue.get_nowait()
            if status.startswith(("Resolviendo pregunta", "Generando sugerencias")):
                self._set_answer_loading(True)
            if not self._session_paused:
                self._set_status(status)
            if status.startswith("Error de micrófono") and not self._mic_error_shown:
                self._mic_error_shown = True
                show_error(self, "Micrófono", status)
        self._append_batch(self.interviewer_box, "interviewer", self.interviewer_queue)
        self._append_batch(self.candidate_box, "candidate", self.candidate_queue)
        while not self.assist_queue.empty():
            self._apply_assist(self.assist_queue.get_nowait())
        self.after(100, self._drain_queues)
