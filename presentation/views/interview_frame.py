"""Focused, dual-source interview coaching experience."""

from __future__ import annotations

import queue
import re
import threading
import textwrap
from pathlib import Path

import numpy as np
import soundcard as sc
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk

import config
from config import logger
from core.domain.entities import TranscriptionRecord
from infrastructure.services import interview_live
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
ERROR = "#E0506A"

CONTEXT_SETTING_KEY = "interview_context"
CONTEXT_PLACEHOLDER = "Pegá tu CV o el contexto de la sesión (puesto, empresa, tema a practicar...)."

MODE_SETTING_KEY = "interview_assist_mode"
MODE_LABELS = {
    "Entrevista laboral": "entrevista",
    "Práctica de idioma": "practica",
    "Conversación general": "general",
    "Prueba oral (práctica, solo ideas)": "prueba_oral",
}


class CandidateListener:
    """Transcribe the candidate microphone locally and preserve speaker identity."""

    def __init__(self, on_text, on_status):
        self._on_text = on_text
        self._on_status = on_status
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, microphone_name: str | None) -> None:
        if self.is_running():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(microphone_name,), daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _run(self, microphone_name: str | None) -> None:
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
            self._on_status(f"Micrófono activo: {mic.name[:32]}")
            with mic.recorder(samplerate=sample_rate) as recorder:
                while not self._stop.is_set():
                    data = recorder.record(numframes=block)
                    mono = data.mean(axis=1).astype(np.float32)
                    chunks.append(mono)
                    if len(chunks) < 6:
                        continue
                    audio = np.concatenate(chunks)
                    chunks.clear()
                    if float(np.abs(audio).mean()) < 0.001:
                        continue
                    texts, _ = service.transcribe_array(
                        audio, language="en", translate=False
                    )
                    for text in texts:
                        clean = (text or "").strip()
                        if clean:
                            self._on_text(clean)
        except Exception as exc:
            logger.exception("Candidate microphone transcription failed: %s", exc)
            self._on_status(f"Error de micrófono: {exc}")


class InterviewFrame(ctk.CTkFrame):
    """Guided preparation, active interview, and closing flow."""

    def __init__(self, parent):
        super().__init__(parent, fg_color="transparent")
        self.out_dir = DEFAULT_DIR
        self.interviewer_queue: queue.Queue[str] = queue.Queue()
        self.candidate_queue: queue.Queue[str] = queue.Queue()
        self.status_queue: queue.Queue[str] = queue.Queue()
        self.assist_queue: queue.Queue = queue.Queue()
        self.worker = Transcriber(self.interviewer_queue, self.status_queue)
        self.candidate_listener = CandidateListener(
            self.candidate_queue.put, self.status_queue.put
        )
        self._source_map: dict[str, tuple[str, object]] = {}
        self._microphone_map: dict[str, str | None] = {}
        self._session_ended = False
        self._build_header()
        self._build_preparation()
        self._build_active()
        self._build_closing()
        self.show_preparation()
        self.refresh_devices()
        self.after(100, self._drain_queues)

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill=tk.X, padx=24, pady=(20, 8))
        ctk.CTkLabel(
            header, text="Entrevista", font=("Segoe UI Semibold", 22),
            text_color=TEXT,
        ).pack(side=tk.LEFT)
        self.status_label = ctk.CTkLabel(
            header, text="●  Preparación", font=("Segoe UI Semibold", 11),
            text_color=MUTED,
        )
        self.status_label.pack(side=tk.RIGHT)
        ctk.CTkLabel(
            self,
            text="Escuchá al entrevistador, seguí el hilo y respondé con mayor fluidez.",
            font=("Segoe UI", 12), text_color=MUTED,
        ).pack(anchor=tk.W, padx=24, pady=(0, 10))

    def _build_preparation(self) -> None:
        # Scrollable: the prep form outgrew small windows and the start button
        # was getting clipped at the bottom.
        self.prep = ctk.CTkScrollableFrame(
            self, fg_color=PANEL, corner_radius=12, border_color=BORDER, border_width=1
        )
        ctk.CTkLabel(
            self.prep, text="1. Prepará la entrevista",
            font=("Segoe UI Semibold", 17), text_color=TEXT,
        ).pack(anchor=tk.W, padx=18, pady=(16, 2))
        ctk.CTkLabel(
            self.prep,
            text="El modo y el contexto personalizan las respuestas. Se guardan al iniciar la sesión.",
            font=("Segoe UI", 11), text_color=MUTED,
        ).pack(anchor=tk.W, padx=18, pady=(0, 10))

        mode_row = ctk.CTkFrame(self.prep, fg_color="transparent")
        mode_row.pack(fill=tk.X, padx=18, pady=(0, 10))
        ctk.CTkLabel(
            mode_row, text="MODO DE SESIÓN",
            font=("Segoe UI Semibold", 9), text_color=MUTED,
        ).pack(anchor=tk.W)
        self.mode_var = tk.StringVar(value=self._load_saved_mode_label())
        self.mode_combo = ctk.CTkComboBox(
            mode_row, variable=self.mode_var, state="readonly",
            values=list(MODE_LABELS),
            fg_color=PANEL_DARK, border_color=BORDER, button_color=BORDER,
            dropdown_fg_color=PANEL, dropdown_hover_color=ACCENT,
        )
        self.mode_combo.pack(fill=tk.X, pady=(4, 0))

        context_header = ctk.CTkFrame(self.prep, fg_color="transparent")
        context_header.pack(fill=tk.X, padx=18, pady=(0, 4))
        ctk.CTkLabel(
            context_header, text="CV / CONTEXTO",
            font=("Segoe UI Semibold", 9), text_color=MUTED,
        ).pack(side=tk.LEFT)
        ctk.CTkButton(
            context_header, text="Cargar CV desde archivo…",
            command=self._upload_cv_file, width=180, height=24,
            fg_color=PANEL_DARK, hover_color=ACCENT, border_color=BORDER,
            border_width=1, font=("Segoe UI", 11),
        ).pack(side=tk.RIGHT)

        self.context_box = ctk.CTkTextbox(
            self.prep, height=110, fg_color=PANEL_DARK, text_color=TEXT,
            border_width=1, border_color=BORDER, corner_radius=8,
            font=("Segoe UI", 12),
        )
        self.context_box.pack(fill=tk.X, padx=18, pady=(0, 14))
        self.context_box.insert("1.0", self._load_saved_context() or CONTEXT_PLACEHOLDER)

        sources = ctk.CTkFrame(self.prep, fg_color="transparent")
        sources.pack(fill=tk.X, padx=18)
        sources.grid_columnconfigure(0, weight=1)
        sources.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            sources, text="ENTREVISTADOR · AUDIO DEL SISTEMA",
            font=("Segoe UI Semibold", 9), text_color=MUTED,
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        ctk.CTkLabel(
            sources, text="VOS · MICRÓFONO",
            font=("Segoe UI Semibold", 9), text_color=MUTED,
        ).grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.source_var = tk.StringVar(value=WHOLE_SYSTEM_LABEL)
        self.source_combo = ctk.CTkComboBox(
            sources, variable=self.source_var, state="readonly",
            fg_color=PANEL_DARK, border_color=BORDER, button_color=BORDER,
            dropdown_fg_color=PANEL, dropdown_hover_color=ACCENT,
        )
        self.source_combo.grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(4, 0))
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
            text="🌐 Gemini procesa el audio del entrevistador. Tu voz se transcribe localmente y se envía como texto para mantener el contexto.",
            wraplength=840, justify=tk.LEFT, font=("Segoe UI", 11), text_color=MUTED,
        ).pack(anchor=tk.W, padx=12, pady=10)
        self.record_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            self.prep, text="Grabar audio del entrevistador (opcional)",
            variable=self.record_var, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            border_color=BORDER, text_color=TEXT,
        ).pack(anchor=tk.W, padx=18)
        ctk.CTkLabel(
            self.prep, text="Recomendado: usá auriculares para evitar eco entre el sistema y el micrófono.",
            font=("Segoe UI", 10), text_color=MUTED,
        ).pack(anchor=tk.W, padx=18, pady=(6, 12))
        ctk.CTkButton(
            self.prep, text="Iniciar entrevista", command=self.start_session,
            height=40, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            font=("Segoe UI Semibold", 12),
        ).pack(anchor=tk.E, padx=18, pady=(0, 18))

    def _build_active(self) -> None:
        self.active = ctk.CTkFrame(self, fg_color="transparent")
        top = ctk.CTkFrame(self.active, fg_color="transparent")
        top.pack(fill=tk.X, padx=24, pady=(0, 8))
        ctk.CTkLabel(
            top, text="2. Sesión activa", font=("Segoe UI Semibold", 16), text_color=TEXT
        ).pack(side=tk.LEFT)
        ctk.CTkButton(
            top, text="Detener", width=90, fg_color="#B8324A", hover_color="#96283D",
            command=self.finish_session,
        ).pack(side=tk.RIGHT)

        self.question_label = self._section(
            self.active, "PREGUNTA EN ESPAÑOL", "Esperando al entrevistador...", 17
        )
        replies = ctk.CTkFrame(self.active, fg_color="transparent")
        replies.pack(fill=tk.X, padx=24, pady=6)
        replies.grid_columnconfigure(0, weight=1)
        replies.grid_columnconfigure(1, weight=1)
        self.reply_buttons = []
        for i, title in enumerate(("RESPUESTA RECOMENDADA", "ALTERNATIVA BREVE")):
            card = ctk.CTkFrame(replies, fg_color=PANEL, corner_radius=12, border_color=BORDER, border_width=1)
            card.grid(row=0, column=i, sticky="nsew", padx=(0, 6) if i == 0 else (6, 0))
            ctk.CTkLabel(card, text=title, font=("Segoe UI Semibold", 9), text_color=MUTED).pack(anchor=tk.W, padx=12, pady=(10, 4))
            btn = ctk.CTkButton(
                card, text="Aparecerá cuando detectemos una pregunta", height=74,
                anchor="w", fg_color=PANEL_DARK,
                hover_color="#20212D", text_color=TEXT,
                command=lambda n=i: self._copy_reply(n),
            )
            btn._reply_text = ""
            btn.pack(fill=tk.X, padx=12, pady=(0, 12))
            self.reply_buttons.append(btn)

        support = ctk.CTkFrame(self.active, fg_color="transparent")
        support.pack(fill=tk.X, padx=24, pady=6)
        support.grid_columnconfigure(0, weight=1)
        support.grid_columnconfigure(1, weight=1)
        self.ideas_label = self._small_card(support, 0, "IDEAS CLAVE", "Se adaptarán a tu CV y al hilo de la conversación.")
        self.bridge_button = ctk.CTkButton(
            support, text="Could you give me a moment to think?", height=58,
            anchor="w", fg_color=PANEL, border_width=1,
            border_color=BORDER, hover_color="#20212D",
            command=self._copy_bridge,
        )
        self.bridge_button._bridge_text = "Could you give me a moment to think?"
        self.bridge_button.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        transcripts = ctk.CTkFrame(self.active, fg_color="transparent")
        transcripts.pack(fill=tk.BOTH, expand=True, padx=24, pady=(6, 18))
        transcripts.grid_columnconfigure(0, weight=1)
        transcripts.grid_columnconfigure(1, weight=1)
        transcripts.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(transcripts, text="ENTREVISTADOR", font=("Segoe UI Semibold", 9), text_color=MUTED).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(transcripts, text="VOS", font=("Segoe UI Semibold", 9), text_color=MUTED).grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.interviewer_box = self._transcript_box(transcripts)
        self.interviewer_box.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=(4, 0))
        self.candidate_box = self._transcript_box(transcripts)
        self.candidate_box.grid(row=1, column=1, sticky="nsew", padx=(6, 0), pady=(4, 0))

    def _build_closing(self) -> None:
        self.closing = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=12, border_color=BORDER, border_width=1)
        ctk.CTkLabel(self.closing, text="3. Entrevista finalizada", font=("Segoe UI Semibold", 18), text_color=TEXT).pack(pady=(24, 6))
        self.closing_summary = ctk.CTkLabel(self.closing, text="", font=("Segoe UI", 12), text_color=MUTED)
        self.closing_summary.pack(pady=(0, 18))
        actions = ctk.CTkFrame(self.closing, fg_color="transparent")
        actions.pack(pady=(0, 24))
        ctk.CTkButton(actions, text="Guardar conversación", command=self.save_session, fg_color=ACCENT, hover_color=ACCENT_HOVER).pack(side=tk.LEFT, padx=6)
        ctk.CTkButton(actions, text="Nueva entrevista", command=self.reset_session, fg_color=PANEL_DARK, hover_color="#20212D").pack(side=tk.LEFT, padx=6)
        ctk.CTkButton(actions, text="Descartar", command=self.discard_session, fg_color="transparent", border_width=1, border_color=BORDER).pack(side=tk.LEFT, padx=6)

    def _section(self, parent, title, text, size):
        card = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=12, border_color=BORDER, border_width=1)
        card.pack(fill=tk.X, padx=24, pady=6)
        ctk.CTkLabel(card, text=title, font=("Segoe UI Semibold", 9), text_color=MUTED).pack(anchor=tk.W, padx=14, pady=(10, 3))
        label = ctk.CTkLabel(card, text=text, wraplength=850, justify=tk.LEFT, anchor="w", font=("Segoe UI Semibold", size), text_color=TEXT)
        label.pack(fill=tk.X, padx=14, pady=(0, 12))
        return label

    def _small_card(self, parent, column, title, text):
        card = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=10, border_color=BORDER, border_width=1)
        card.grid(row=0, column=column, sticky="nsew", padx=(0, 6))
        ctk.CTkLabel(card, text=title, font=("Segoe UI Semibold", 9), text_color=MUTED).pack(anchor=tk.W, padx=12, pady=(9, 2))
        label = ctk.CTkLabel(card, text=text, wraplength=390, justify=tk.LEFT, anchor="w", font=("Segoe UI", 11), text_color=TEXT)
        label.pack(fill=tk.X, padx=12, pady=(0, 10))
        return label

    def _transcript_box(self, parent):
        return ctk.CTkTextbox(parent, height=115, fg_color=PANEL_DARK, text_color=TEXT, border_width=1, border_color=BORDER, corner_radius=8)

    def show_preparation(self) -> None:
        self.active.pack_forget()
        self.closing.pack_forget()
        self.prep.pack(fill=tk.BOTH, expand=True, padx=24, pady=(0, 20))
        self._set_status("Preparación")

    def show_active(self) -> None:
        self.prep.pack_forget()
        self.closing.pack_forget()
        self.active.pack(fill=tk.BOTH, expand=True)

    def show_closing(self) -> None:
        self.prep.pack_forget()
        self.active.pack_forget()
        self.closing.pack(fill=tk.X, padx=24, pady=(30, 20))
        interviewer_words = len(self.interviewer_box.get("1.0", tk.END).split())
        candidate_words = len(self.candidate_box.get("1.0", tk.END).split())
        self.closing_summary.configure(text=f"Entrevistador: {interviewer_words} palabras · Vos: {candidate_words} palabras")

    def refresh_devices(self) -> None:
        self._source_map = {WHOLE_SYSTEM_LABEL: ("loopback", None)}
        try:
            from infrastructure.audio import process_loopback

            for name, pid in process_loopback.list_audio_apps():
                self._source_map[f"💻  App: {name} (PID {pid})"] = ("app", pid)
        except Exception:
            pass
        self.source_combo.configure(values=list(self._source_map))
        if self.source_var.get() not in self._source_map:
            self.source_var.set(WHOLE_SYSTEM_LABEL)
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

    def _upload_cv_file(self) -> None:
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            title="Seleccionar CV",
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
            messagebox.showerror("CV", f"No se pudo leer el archivo:\n{exc}")
            return
        if not text.strip():
            messagebox.showwarning(
                "CV", "El archivo no tiene texto extraíble (¿PDF escaneado como imagen?)."
            )
            return
        self.context_box.delete("1.0", tk.END)
        self.context_box.insert("1.0", text.strip())
        self._save_context(text.strip())

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
        default_label = next(iter(MODE_LABELS))
        if config.repository is None:
            return default_label
        try:
            saved = config.repository.get_setting(MODE_SETTING_KEY)
        except Exception as exc:
            logger.exception("Failed loading saved assist mode: %s", exc)
            return default_label
        for label, mode in MODE_LABELS.items():
            if mode == saved:
                return label
        return default_label

    def _load_saved_context(self) -> str | None:
        if config.repository is None:
            return None
        try:
            saved = config.repository.get_setting(CONTEXT_SETTING_KEY)
            return saved.strip() or None if saved else None
        except Exception as exc:
            logger.exception("Failed loading saved interview context: %s", exc)
            return None

    def _save_context(self, context: str) -> None:
        if config.repository is None:
            return
        try:
            config.repository.set_setting(CONTEXT_SETTING_KEY, context)
        except Exception as exc:
            logger.exception("Failed saving interview context: %s", exc)

    def start_session(self) -> None:
        if not interview_live.is_configured():
            messagebox.showinfo("Entrevista", "Configurá una clave Gemini en Ajustes antes de iniciar.")
            return
        context = self.context_box.get("1.0", tk.END).strip()
        if context == CONTEXT_PLACEHOLDER:
            context = ""
        self._save_context(context)
        assist_mode = MODE_LABELS.get(self.mode_var.get(), "entrevista")
        if config.repository is not None:
            try:
                config.repository.set_setting(MODE_SETTING_KEY, assist_mode)
            except Exception as exc:
                logger.exception("Failed saving assist mode: %s", exc)
        source_type, source_value = self._source_map.get(self.source_var.get(), ("loopback", None))
        self._session_ended = False
        self.show_active()
        self._set_status("Conectando...", ACCENT)
        self.worker.start(
            language="en", out_dir=self.out_dir, source_type=source_type,
            source_val=source_value, translate=False,
            save_audio=self.record_var.get(), interview_mode=True,
            interview_context=context, interview_assist_mode=assist_mode,
            assist_queue=self.assist_queue,
        )
        self.candidate_listener.start(self._microphone_map.get(self.mic_var.get()))

    def finish_session(self) -> None:
        self.stop_session()
        self._session_ended = True
        self.show_closing()
        self._set_status("Finalizada")
        # Worker thread finalizes the WAV header on close; give it a moment
        # before registering the file in the history DB.
        self.after(1500, self._register_in_history)

    def _register_in_history(self) -> None:
        """Save the finished session to the DB so Historial can list/play it."""
        if config.repository is None:
            return
        import os
        import datetime as dt

        wav = getattr(self.worker, "current_wav_path", None)
        txt = getattr(self.worker, "current_transcript_path", None)
        # 44 bytes = bare WAV header; anything at or below it has no audio.
        has_audio = bool(wav) and os.path.exists(wav) and os.path.getsize(wav) > 44
        has_txt = bool(txt) and os.path.exists(txt)
        if not has_audio and not has_txt:
            return
        transcript = ""
        if has_txt:
            try:
                with open(txt, encoding="utf-8") as fh:
                    transcript = fh.read().strip()
            except Exception as exc:
                logger.exception("Failed reading interview transcript: %s", exc)
        secs = int(getattr(self.worker, "duration_seconds", 0) or 0)
        try:
            config.repository.save(
                TranscriptionRecord(
                    file_path=str(wav if has_audio else txt),
                    file_name=f"Entrevista {dt.datetime.now():%Y-%m-%d %H.%M}",
                    duration=f"{secs // 60:02d}:{secs % 60:02d}",
                    transcription=transcript,
                    language="en",
                )
            )
            self._set_status("Guardada en Historial")
        except Exception as exc:
            logger.exception("Failed saving interview to history: %s", exc)

    def stop_session(self) -> None:
        self.worker.stop()
        self.candidate_listener.stop()

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
        self.question_label.configure(text=assist.pregunta_es or "Pregunta detectada")
        for i, button in enumerate(self.reply_buttons):
            text = assist.respuestas[i] if i < len(assist.respuestas) else ""
            button._reply_text = text
            button.configure(text=self._wrap(text) if text else "Sin sugerencia")
        ideas = assist.ideas_clave or []
        self.ideas_label.configure(text=" • ".join(ideas) if ideas else "Enfocate en una experiencia concreta y su resultado.")
        bridge = assist.frase_puente or "Could you give me a moment to think?"
        self.bridge_button._bridge_text = bridge
        self.bridge_button.configure(text=self._wrap(bridge))

    @staticmethod
    def _wrap(text: str, width: int = 52) -> str:
        return "\n".join(textwrap.wrap(text, width=width))

    def _copy_reply(self, index: int) -> None:
        text = self.reply_buttons[index]._reply_text
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self._set_status("Respuesta copiada", SUCCESS)

    def _copy_bridge(self) -> None:
        text = self.bridge_button._bridge_text
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_status("Frase copiada", SUCCESS)

    def save_session(self) -> None:
        interviewer = self.interviewer_box.get("1.0", tk.END).strip()
        candidate = self.candidate_box.get("1.0", tk.END).strip()
        if not interviewer and not candidate:
            messagebox.showwarning("Entrevista", "No hay conversación para guardar.")
            return
        dialog = ctk.CTkInputDialog(text="Nombre de la entrevista:", title="Guardar entrevista")
        name = (dialog.get_input() or "").strip()
        if not name:
            return
        safe = re.sub(r'[\\/*?:"<>|]', "_", name)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = Path(self.out_dir) / f"{safe}.txt"
        combined = f"ENTREVISTADOR\n{interviewer}\n\nCANDIDATO\n{candidate}\n"
        path.write_text(combined, encoding="utf-8")
        config.repository.save(TranscriptionRecord(
            file_path=str(path), file_name=safe, duration="00:00",
            transcription=combined, language="en",
        ))
        self._set_status("Guardada", SUCCESS)
        messagebox.showinfo("Entrevista", "La conversación se agregó al Historial.")

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
        self.question_label.configure(text="Esperando al entrevistador...")
        for button in self.reply_buttons:
            button._reply_text = ""
            button.configure(text="Aparecerá cuando detectemos una pregunta")
        self.show_preparation()

    def _set_status(self, text: str, color: str | None = None) -> None:
        if color is None:
            color = ERROR if "error" in text.lower() else SUCCESS if "activ" in text.lower() else MUTED
        self.status_label.configure(text=f"●  {text}", text_color=color)

    def _drain_queues(self) -> None:
        while not self.status_queue.empty():
            self._set_status(self.status_queue.get_nowait())
        self._append_batch(self.interviewer_box, "interviewer", self.interviewer_queue)
        self._append_batch(self.candidate_box, "candidate", self.candidate_queue)
        while not self.assist_queue.empty():
            self._apply_assist(self.assist_queue.get_nowait())
        self.after(100, self._drain_queues)
