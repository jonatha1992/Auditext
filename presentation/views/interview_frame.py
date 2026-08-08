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
from infrastructure.services import interview_live, notebooklm_service, tts
from .app_dialog import ask_input, show_error, show_info, show_warning
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

CONTEXT_SETTING_KEY = "interview_context"
CONTEXT_PLACEHOLDER = "Pegá tu CV o el contexto de la sesión (puesto, empresa, tema a practicar...)."

MODE_SETTING_KEY = "interview_assist_mode"
RESOLVER_MODE_SETTING_KEY = "resolver_assist_mode"
NOTEBOOK_ID_SETTING_KEY = "notebooklm_selected_id"
NOTEBOOK_TITLE_SETTING_KEY = "notebooklm_selected_title"
NOTEBOOK_PLACEHOLDER = "Seleccioná una materia…"
NOTEBOOK_CONTEXT_SETTING_PREFIX = "notebooklm_context_"
MODE_LABELS = {
    "Entrevista laboral": "entrevista",
    "Práctica de idioma": "practica",
    "Conversación general": "general",
    "Examen oral (respuestas completas)": "examen_oral",
    "Práctica oral (solo ideas)": "prueba_oral",
    "Resolver preguntas": "resolver",
}

INTERVIEW_MODES = ("entrevista", "practica", "general")
RESOLVER_MODES = ("resolver", "examen_oral", "prueba_oral")

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
    "examen_oral": {
        "label": "📚  PROGRAMA / TEMARIO / CRONOGRAMA",
        "color": "#F6AD55",
        "placeholder": "Pegá el programa, temario o cronograma de la materia (unidades, temas, objetivos, bibliografía...).",
        "show_cv": True,
        "file_button": "📂  Cargar programa…",
        "file_title": "programa, temario o cronograma",
        "default_lang": "es",
    },
    "prueba_oral": {
        "label": "📝  TEMA DE LA PRÁCTICA ORAL",
        "color": "#A78BFA",
        "placeholder": "¿Sobre qué es la práctica oral? (tema, consigna, puntos a cubrir...).",
        "show_cv": False,
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


class CandidateListener:
    """Transcribe the candidate microphone locally and preserve speaker identity."""

    def __init__(self, on_text, on_status, on_audio=None):
        self._on_text = on_text
        self._on_status = on_status
        self._on_audio = on_audio
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread: threading.Thread | None = None

    def start(
        self, microphone_name: str | None, language: str | None = None
    ) -> None:
        if self.is_running():
            return
        self._stop.clear()
        self._paused.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(microphone_name, language),
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._paused.clear()

    def pause(self) -> None:
        if self.is_running():
            self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def is_paused(self) -> bool:
        return self._paused.is_set()

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

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
            service = config.transcription_service
            if service is None:
                raise RuntimeError("El servicio de transcripción local no está disponible.")
            self._on_status("Preparando reconocimiento de tu voz...")
            service.get_model()
            mic = sc.default_microphone()
            if microphone_name:
                mic = next(
                    (m for m in sc.all_microphones() if m.name == microphone_name),
                    mic,
                )
            sample_rate = config.SAMPLE_RATE
            block = int(sample_rate * 0.5)
            chunks: list[np.ndarray] = []
            last_silence_log = 0.0

            def consume(mono: np.ndarray) -> None:
                nonlocal last_silence_log
                if self._paused.is_set():
                    chunks.clear()
                    return
                if self._on_audio is not None:
                    self._on_audio(mono)
                chunks.append(mono)
                if len(chunks) < 6:
                    return
                audio = np.concatenate(chunks)
                chunks.clear()
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
                texts, _ = service.transcribe_array(
                    audio, language=language, translate=False
                )
                for text in texts:
                    clean = (text or "").strip()
                    if clean:
                        self._on_text(clean)

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
                self._capture_with_sounddevice(
                    mic.name, sample_rate, block, consume
                )
        except Exception as exc:
            logger.exception("Candidate microphone transcription failed: %s", exc)
            self._on_status(f"Error de micrófono: {exc}")
        finally:
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
        if not candidates:
            default_input = getattr(sd.default, "device", (-1, -1))[0]
            candidates = sorted(
                input_devices, key=lambda item: item[0] != default_input
            )
        last_exc: Exception | None = None

        for device_index, device in candidates:
            try:
                with sd.InputStream(
                    samplerate=sample_rate,
                    blocksize=block,
                    device=device_index,
                    channels=1,
                    dtype="float32",
                ) as stream:
                    self._on_status(
                        "Micrófono activo (alternativo): "
                        f"{str(device['name'])[:28]}"
                    )
                    logger.info(
                        "Candidate microphone fallback device=%s index=%s",
                        device["name"],
                        device_index,
                    )
                    exact_zero_blocks = 0
                    while not self._stop.is_set():
                        started = time.monotonic()
                        data, overflowed = stream.read(block)
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
                        consume(mono)
                        # Some Windows host/device combinations return an empty
                        # block immediately instead of blocking for its audio
                        # duration. Pace that broken path so it cannot spin at
                        # hundreds of iterations per second and starve the Live
                        # session after the first question.
                        elapsed = time.monotonic() - started
                        expected = block / float(sample_rate)
                        if elapsed < expected:
                            self._stop.wait(expected - elapsed)
                return
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Alternative microphone device %s failed: %s",
                    device.get("name"),
                    exc,
                )
        raise RuntimeError(
            "No se pudo abrir el micrófono con el motor alternativo: "
            f"{last_exc or 'sin dispositivos disponibles'}"
        )


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
        self._active_notebook_id: str | None = None
        self._active_notebook_title: str | None = None
        self._active_notebook_context: str | None = None
        # While a sync is in flight the sync flow owns the status line, so the
        # generic state refresh must not overwrite its progress messages.
        self._notebook_syncing = False
        self._session_ended = False
        self._session_paused = False
        self._mic_error_shown = False
        self._build_header()
        self._build_preparation()
        self._build_active()
        self._build_closing()
        self.show_preparation()
        self.refresh_devices()
        self.after(3000, self._auto_refresh_audio_sources)
        self.after(100, self._drain_queues)

    def _build_header(self) -> None:
        resolver = self._fixed_mode == "resolver"
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill=tk.X, padx=24, pady=(20, 8))
        ctk.CTkLabel(
            header,
            text="🧠  Resolver preguntas" if resolver else "🎯  Entrevista",
            font=("Segoe UI Semibold", 22),
            text_color=TEXT,
        ).pack(side=tk.LEFT)
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
                else "Escuchá al entrevistador, seguí el hilo y respondé con mayor fluidez. El audio se guarda para reproducirlo en Historial."
            ),
            font=("Segoe UI", 12), text_color=MUTED,
        ).pack(anchor=tk.W, padx=24, pady=(0, 10))

    def _mode_labels(self) -> list[str]:
        allowed = (
            RESOLVER_MODES
            if self._fixed_mode == "resolver"
            else INTERVIEW_MODES
        )
        return [
            label
            for mode in allowed
            for label, mapped_mode in MODE_LABELS.items()
            if mapped_mode == mode
        ]

    def _build_preparation(self) -> None:
        resolver = self._fixed_mode == "resolver"
        # Scrollable: the prep form outgrew small windows and the start button
        # was getting clipped at the bottom.
        self.prep = ctk.CTkScrollableFrame(
            self, fg_color=PANEL, corner_radius=12, border_color=BORDER, border_width=1
        )
        ctk.CTkLabel(
            self.prep,
            text="①  Prepará el tema" if resolver else "①  Prepará la entrevista",
            font=("Segoe UI Semibold", 17), text_color=TEXT,
        ).pack(anchor=tk.W, padx=18, pady=(16, 2))
        ctk.CTkLabel(
            self.prep,
            text=(
                "Pegá el material, elegí el audio de la llamada y el micrófono con el que vas a responder."
                if resolver
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
        self.use_written_context_var = tk.BooleanVar(value=True)
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

        if resolver:
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
            self.use_notebook_var = tk.BooleanVar(value=False)
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
                command=self._refresh_notebooks,
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
                text="Podés seguir usando material local sin conectar NotebookLM.",
                font=("Segoe UI", 10),
                text_color=MUTED,
            )
            self.notebook_status.pack(
                anchor=tk.W, padx=12, pady=(7, 10)
            )
            # The CLI keeps its authenticated browser session. On subsequent
            # launches, restore the catalog and last selected subject silently.
            self._apply_context_source_state()
            self.after(250, self._refresh_notebooks)

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
            text="🎤  TU RESPUESTA · MICRÓFONO" if resolver else "🎤  VOS · MICRÓFONO",
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
        self.active = ctk.CTkFrame(self, fg_color="transparent")
        top = ctk.CTkFrame(self.active, fg_color="transparent")
        top.pack(fill=tk.X, padx=24, pady=(0, 8))
        ctk.CTkLabel(
            top, text="②  Sesión activa", font=("Segoe UI Semibold", 16), text_color=SUCCESS
        ).pack(side=tk.LEFT)
        self.pause_button = ctk.CTkButton(
            top, text="⏸  Pausar", width=110, fg_color=PANEL_DARK,
            hover_color="#20212D", command=self.toggle_pause,
        )
        self.pause_button.pack(side=tk.RIGHT, padx=(0, 8))
        ctk.CTkButton(
            top, text="⏹  Detener", width=110, fg_color="#B8324A", hover_color="#96283D",
            command=self.finish_session,
        ).pack(side=tk.RIGHT)

        self.question_label = self._section(
            self.active,
            "🗣  PREGUNTA DETECTADA" if resolver else "🗣  PREGUNTA EN ESPAÑOL",
            "Esperando una pregunta..." if resolver else "Esperando al entrevistador...",
            17,
            title_color="#F6AD55",
        )
        ctk.CTkLabel(
            self.active,
            text="💡  Click derecho en una palabra (o seleccioná una frase) = escuchar · 📋 copiar · 🔊 escuchar toda la respuesta",
            font=("Segoe UI", 10), text_color=MUTED,
        ).pack(anchor=tk.W, padx=24, pady=(0, 2))
        replies = ctk.CTkFrame(self.active, fg_color="transparent")
        replies.pack(fill=tk.X, padx=24, pady=6)
        replies.grid_columnconfigure(0, weight=1)
        self._answer_loading = False
        self._spinner_index = 0
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
        reply_titles = (
            # Lo que se mira mientras hablás es esto, no las transcripciones de
            # abajo: se les da la altura que aquellas dejan libre.
            ("⚡  RESPUESTA", ACCENT, 155 if resolver else 95),
            ("📖  DETALLES Y EJEMPLOS", "#F6E05E", 190 if resolver else 120),
        )
        for i, (title, title_color, box_height) in enumerate(reply_titles):
            card = ctk.CTkFrame(replies, fg_color=PANEL, corner_radius=12, border_color=BORDER, border_width=1)
            card.grid(row=i + 1, column=0, sticky="ew", pady=(0, 8))
            self.reply_cards.append(card)
            head = ctk.CTkFrame(card, fg_color="transparent")
            head.pack(fill=tk.X, padx=12, pady=(10, 2))
            ctk.CTkLabel(head, text=title, font=("Segoe UI Semibold", 9), text_color=title_color).pack(side=tk.LEFT)
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
            self._set_box_text(box, "Aparecerá cuando detectemos una pregunta")
            box._textbox.bind("<Button-3>", self._speak_word_at)
            # BOTH/expand: las dos tarjetas comparten fila, así que la más baja
            # dejaba un recuadro cortado con aire muerto abajo.
            box.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))
            self.reply_boxes.append(box)

        support = ctk.CTkFrame(self.active, fg_color="transparent")
        support.pack(fill=tk.X, padx=24, pady=6)
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
        if self._fixed_mode == "resolver":
            labels = ("RESPUESTA CORTA", "RESPUESTA AMPLIADA")
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
        return f"ENTREVISTADOR\n{interviewer}\n\nCANDIDATO\n{candidate}\n"

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
        if self.mic_var.get() not in self._microphone_map:
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
        self, text: str, color: str = MUTED
    ) -> None:
        if hasattr(self, "notebook_status"):
            self.notebook_status.configure(text=text, text_color=color)

    def _login_notebooklm(self) -> None:
        try:
            notebooklm_service.launch_login()
        except Exception as exc:
            self._set_notebook_status(str(exc), ERROR)
            return
        self._set_notebook_status(
            "Completá el inicio de sesión y luego tocá «Actualizar materias».",
            ACCENT,
        )

    def _refresh_notebooks(self) -> None:
        self._set_notebook_status("Consultando materias de NotebookLM…", ACCENT)

        def work():
            try:
                notebooks = notebooklm_service.list_notebooks()
            except Exception as exc:
                self.after(
                    0, lambda message=str(exc): self._set_notebook_status(
                        message, ERROR
                    )
                )
                return

            def apply():
                self._notebook_map = {
                    notebook.label: notebook for notebook in notebooks
                }
                labels = list(self._notebook_map)
                if not labels:
                    self._set_notebook_status(
                        "No se encontraron materias con fuentes.", ERROR
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
                        f"{len(labels)} materias disponibles · seleccionada: "
                        f"{self._notebook_map[selected].title}"
                        if selected
                        else f"{len(labels)} materias disponibles · elegí una."
                    ),
                    SUCCESS,
                )

            self.after(0, apply)

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

    def _restore_saved_notebook(self) -> str | None:
        """Re-select the last used subject and reuse its cached material.

        The selection was being persisted but never read back, so every launch
        started with no subject. Restoring from the local cache keeps the flow
        offline: syncing again is the user's call via «Sincronizar materia».
        """
        saved_id = self._load_setting(NOTEBOOK_ID_SETTING_KEY)
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
            config.repository.set_setting(NOTEBOOK_ID_SETTING_KEY, notebook.id)
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
            f"Preparando material de {notebook.title}…", ACCENT
        )

        def work():
            try:
                context = notebooklm_service.sync_study_context(notebook.id)
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
        oral_mode = new_mode in interview_live.ORAL_ASSIST_MODES
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
        if not interview_live.is_configured():
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
        if self._fixed_mode == "resolver" and not context:
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
        assist_mode = MODE_LABELS.get(self.mode_var.get(), "entrevista")
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
        source_type, source_value = self._source_map.get(
            self.source_var.get(), ("loopback", None)
        )
        self._session_ended = False
        self._session_paused = False
        self.pause_button.configure(text="⏸  Pausar")
        self._mic_error_shown = False
        self.show_active()
        self._set_status("Conectando...", ACCENT)
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

    def finish_session(self) -> None:
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
                self.worker.add_candidate_turn(text)

    def _apply_assist(self, assist) -> None:
        # While the coach is still streaming, the answer line lands before the
        # question gloss and the key ideas. Blanking those fields on every
        # partial would make them flash placeholder text, so a partial only ever
        # fills in what it actually carries.
        partial = getattr(assist, "partial", False)
        self._set_answer_loading(False)

        if assist.pregunta_es or not partial:
            self.question_label.configure(text=assist.pregunta_es or "Pregunta detectada")

        for i, box in enumerate(self.reply_boxes):
            text = assist.respuestas[i] if i < len(assist.respuestas) else ""
            if partial and not text:
                continue
            box._reply_text = text
            self._set_box_text(box, text or "Sin sugerencia")

        ideas = assist.ideas_clave or []
        if ideas or not partial:
            self.ideas_label.configure(text=" • ".join(ideas) if ideas else "Enfocate en una experiencia concreta y su resultado.")

    def _set_answer_loading(self, loading: bool) -> None:
        """Show that a new answer is replacing the previous one."""
        self._answer_loading = loading
        if loading:
            self._spinner_index = 0
            self.answer_loading_label.configure(text="◌  Generando respuesta nueva…")
            self.answer_loading_label.grid()
            for box in self.reply_boxes:
                box._reply_text = ""
                self._set_box_text(box, "Esperando respuesta…")
        else:
            self.answer_loading_label.grid_remove()

    def _advance_answer_spinner(self) -> None:
        if not self._answer_loading:
            return
        frames = ("◌", "◔", "◑", "◕")
        self._spinner_index = (self._spinner_index + 1) % len(frames)
        self.answer_loading_label.configure(
            text=f"{frames[self._spinner_index]}  Generando respuesta nueva…"
        )

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
            self.clipboard_clear()
            self.clipboard_append(text)
            self._set_status("Respuesta copiada", SUCCESS)

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
        for box in (self.interviewer_box, self.candidate_box):
            box.delete("1.0", tk.END)
        self.question_label.configure(
            text=(
                "Esperando una pregunta..."
                if self._fixed_mode == "resolver"
                else "Esperando al entrevistador..."
            )
        )
        for box in self.reply_boxes:
            box._reply_text = ""
            self._set_box_text(box, "Aparecerá cuando detectemos una pregunta")
        self.show_preparation()

    def _set_status(self, text: str, color: str | None = None) -> None:
        if color is None:
            color = ERROR if "error" in text.lower() else SUCCESS if "activ" in text.lower() else MUTED
        self.status_label.configure(text=f"●  {text}", text_color=color)

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
        self._advance_answer_spinner()
        self.after(100, self._drain_queues)
