"""Live system-audio transcription tab.

Captures whatever is playing on the PC (system loopback: video calls, videos,
anything routed to the default speaker), runs it through the shared offline
faster-whisper model, and shows the text live.

Embeddable as a ttk.Frame inside the main app's notebook. Fully offline.
"""

import queue
import threading
import datetime as dt
import wave
from pathlib import Path

import numpy as np
import soundcard as sc

import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox

import transcriber
import summarizer
from config import logger
from spinner import Spinner

SAMPLE_RATE = transcriber.SAMPLE_RATE

# Seconds of audio batched before each transcription pass.
CHUNK_SECONDS = 5

# Producer reads the device in small blocks so it never stops pulling audio
# (prevents "data discontinuity" buffer overflows).
CAPTURE_BLOCK_SECONDS = 0.5

# Below this average amplitude a chunk is treated as silence and skipped.
SILENCE_THRESHOLD = 1e-4

# Languages offered in the UI. Label -> Whisper code (None = auto-detect).
LANGUAGES = {
    "Auto": None,
    "Español": "es",
    "English": "en",
    "Português": "pt",
    "Français": "fr",
    "Deutsch": "de",
    "Italiano": "it",
}

DEFAULT_DIR = Path.home() / "Documents" / "LiveTranscribe"

# Source combobox entry that captures the whole system (device loopback).
WHOLE_SYSTEM_LABEL = "\U0001F50A  Todo el sistema"


def to_mono(data: np.ndarray) -> np.ndarray:
    """Average channels into a single mono float32 track."""
    return data.mean(axis=1).astype(np.float32)


def is_silent(audio: np.ndarray, threshold: float = SILENCE_THRESHOLD) -> bool:
    """True when a chunk is near-silent (avoids hallucinated transcriptions)."""
    return float(np.abs(audio).mean()) < threshold


def _style_scrollbar(scrolled_text):
    """Dark-theme the internal scrollbar of a ScrolledText to match the UI."""
    try:
        scrolled_text.vbar.config(
            bg=COLOR_PANEL, troughcolor=COLOR_TEXT_BG, activebackground=COLOR_ACCENT,
            borderwidth=0, highlightthickness=0,
        )
    except Exception:
        pass


class Transcriber:
    """Capture system audio -> Whisper -> text queue, using two threads.

    A producer records continuously, a consumer batches audio and transcribes.
    Decoupling them keeps the capture gap-free.
    """

    def __init__(self, text_queue, status_queue):
        self._text_queue = text_queue
        self._status_queue = status_queue
        self._stop = threading.Event()
        self._thread = None
        self._log_file = None
        self._wav_file = None
        self._language = None
        self._locked_language = None
        self._out_dir = DEFAULT_DIR
        self._source_type = "loopback"
        self._source_val = None
        self._translate = False
        self._audio_q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=200)

    def start(self, language=None, out_dir: Path = DEFAULT_DIR, source_type="loopback", source_val=None, translate=False):
        if self._thread and self._thread.is_alive():
            return
        self._language = language
        self._locked_language = None
        self._out_dir = out_dir
        self._source_type = source_type
        self._source_val = source_val
        self._translate = translate
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- internals ----------------------------------------------------------

    def _open_log(self):
        self._out_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self._log_file = open(
            self._out_dir / f"transcript_{stamp}.txt", "a", encoding="utf-8"
        )
        self._wav_file = wave.open(
            str(self._out_dir / f"audio_{stamp}.wav"), "wb"
        )
        self._wav_file.setnchannels(1)
        self._wav_file.setsampwidth(2)  # 16-bit PCM
        self._wav_file.setframerate(SAMPLE_RATE)

    def _emit(self, text: str):
        self._text_queue.put(text)
        if self._log_file:
            self._log_file.write(text + "\n")
            self._log_file.flush()

    def _producer(self):
        """Record continuously in small blocks so the buffer never overflows.

        Captures either a single application (process loopback), the whole system
        (device loopback), or a physical microphone, depending on the selected source.
        """
        block = int(SAMPLE_RATE * CAPTURE_BLOCK_SECONDS)
        if self._source_type == "app":
            try:
                self._capture_process(block)
                return
            except Exception as exc:
                logger.exception("Process loopback failed: %s", exc)
                self._status_queue.put(
                    "No se pudo capturar la app; capturando micrófono por defecto."
                )
                self._source_type = "mic"
                self._source_val = None
        
        if self._source_type == "mic":
            self._capture_mic(block)
        else:
            self._capture_system(block)

    def _push(self, mono):
        try:
            self._audio_q.put_nowait(mono)
            if self._wav_file:
                # Convert float32 array to int16 PCM
                pcm_data = (mono * 32767).clip(-32768, 32767).astype(np.int16)
                self._wav_file.writeframes(pcm_data.tobytes())
        except queue.Full:
            pass  # drop instead of growing memory if transcription lags

    def _capture_system(self, block):
        """Device loopback: whatever plays on the chosen output device."""
        try:
            speaker = sc.default_speaker()
            if self._source_val and self._source_type == "loopback":
                speaker = next(
                    (s for s in sc.all_speakers() if s.name == self._source_val), speaker
                )
            loopback = sc.get_microphone(id=str(speaker.name), include_loopback=True)
            self._status_queue.put(f"Escuchando sistema: {speaker.name}")
            with loopback.recorder(samplerate=SAMPLE_RATE) as rec:
                while not self._stop.is_set():
                    self._push(to_mono(rec.record(numframes=block)))
        except Exception as exc:
            logger.exception("System loopback capture failed: %s", exc)
            self._status_queue.put(f"Error sistema: {exc}")

    def _capture_mic(self, block):
        """Physical microphone capture."""
        try:
            mic = sc.default_microphone()
            if self._source_val and self._source_type == "mic":
                mic = next(
                    (m for m in sc.all_microphones() if m.name == self._source_val), mic
                )
            self._status_queue.put(f"Escuchando micrófono: {mic.name}")
            with mic.recorder(samplerate=SAMPLE_RATE) as rec:
                while not self._stop.is_set():
                    self._push(to_mono(rec.record(numframes=block)))
        except Exception as exc:
            logger.exception("Microphone capture failed: %s", exc)
            self._status_queue.put(f"Error micrófono: {exc}")

    def _capture_process(self, block):
        """Process loopback: audio of one application only (Windows 10 2004+)."""
        import process_loopback

        self._status_queue.put(f"Escuchando app (pid {self._source_val})...")
        with process_loopback.ProcessLoopbackRecorder(
            self._source_val, samplerate=SAMPLE_RATE
        ) as rec:
            while not self._stop.is_set():
                audio_data = rec.record(block, stop_event=self._stop)
                if len(audio_data) > 0:
                    self._push(to_mono(audio_data))

    def _consumer(self):
        """Batch ~CHUNK_SECONDS of audio, transcribe, emit text."""
        target = int(SAMPLE_RATE * CHUNK_SECONDS)
        buf: list = []
        have = 0
        while not self._stop.is_set():
            try:
                buf.append(self._audio_q.get(timeout=0.5))
            except queue.Empty:
                continue
            have += len(buf[-1])
            if have < target:
                continue

            audio = np.concatenate(buf)
            buf, have = [], 0
            if is_silent(audio):
                continue

            # On "Auto", detect the language once and lock it. Detecting per
            # chunk makes Whisper flip languages and hallucinate (Arabic/Hindi
            # gibberish on a Spanish call).
            lang = self._language if self._language is not None else self._locked_language
            texts, detected = transcriber.transcribe_array(audio, language=lang, translate=self._translate)
            if self._language is None and self._locked_language is None and detected:
                self._locked_language = detected
                self._status_queue.put(f"Idioma detectado: {detected}")
            for text in texts:
                self._emit(text)

    def _run(self):
        try:
            self._status_queue.put("Cargando modelo...")
            transcriber.get_model()  # warm up the shared model
            self._open_log()

            producer = threading.Thread(target=self._producer, daemon=True)
            consumer = threading.Thread(target=self._consumer, daemon=True)
            producer.start()
            consumer.start()
            producer.join()
            consumer.join()

            self._status_queue.put("Detenido.")
        except Exception as exc:  # surface errors in the UI instead of dying
            logger.exception("Fallo la transcripcion en vivo: %s", exc)
            self._status_queue.put(f"Error: {exc}")
        finally:
            if self._log_file:
                self._log_file.close()
                self._log_file = None
            if self._wav_file:
                try:
                    self._wav_file.close()
                except Exception:
                    pass
                self._wav_file = None


# Visual palette.
COLOR_BG = "#1e1e2e"
COLOR_PANEL = "#272739"
COLOR_TEXT_BG = "#15151f"
COLOR_TEXT_FG = "#e6e6f0"
COLOR_ACCENT = "#7c5cff"
COLOR_ACCENT_ACTIVE = "#9277ff"
COLOR_DANGER = "#e0506a"
COLOR_OK = "#4ec98a"
COLOR_MUTED = "#9a9ab0"


class LiveFrame(ttk.Frame):
    """Notebook tab: live system-audio transcription."""

    def __init__(self, parent):
        super().__init__(parent)
        self.configure(style="Live.TFrame")

        self.text_queue: "queue.Queue[str]" = queue.Queue()
        self.status_queue: "queue.Queue[str]" = queue.Queue()
        self.translate_var = tk.BooleanVar(value=False)
        self.worker = Transcriber(self.text_queue, self.status_queue)
        self.out_dir = DEFAULT_DIR

        self._setup_styles()

        # Header: title + live status pill.
        header = ttk.Frame(self, style="Live.TFrame", padding=(16, 14, 16, 6))
        header.pack(fill=tk.X)

        ttk.Label(
            header, text="Transcripcion en vivo", style="LiveTitle.TLabel"
        ).pack(side=tk.LEFT)

        status_frame = tk.Frame(header, bg=COLOR_BG)
        status_frame.pack(side=tk.RIGHT)

        self.spinner = Spinner(
            status_frame, size=20, bg=COLOR_BG,
            accent_color=COLOR_ACCENT, muted_color=COLOR_PANEL
        )

        self.status = ttk.Label(status_frame, text="●  Inactivo", style="LiveStatus.TLabel")
        self.status.pack(side=tk.LEFT)

        # Toolbar: primary action + utilities.
        toolbar = ttk.Frame(self, style="Live.TFrame", padding=(16, 0, 16, 6))
        toolbar.pack(fill=tk.X)

        self.toggle_btn = self._accent_button(toolbar, "▶  Iniciar", self._toggle)
        self.toggle_btn.pack(side=tk.LEFT)
        self._flat_button(toolbar, "Guardar", self._save).pack(side=tk.LEFT, padx=(8, 0))
        self._flat_button(toolbar, "Limpiar", self._clear).pack(side=tk.LEFT, padx=(8, 0))
        self.summary_btn = self._flat_button(toolbar, "✨  Resumir", self._summarize)
        self.summary_btn.pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(toolbar, text="Idioma", style="LiveMuted.TLabel").pack(
            side=tk.LEFT, padx=(20, 6)
        )
        # Default to the PC's system language instead of "Auto" (Auto mis-detects
        # on short chunks). Falls back to "Auto" if the locale is unknown.
        _sys = transcriber.detect_system_language()
        _default_lang = next(
            (label for label, code in LANGUAGES.items() if code == _sys), "Auto"
        )
        self.lang_var = tk.StringVar(value=_default_lang)
        ttk.Combobox(
            toolbar, textvariable=self.lang_var, values=list(LANGUAGES.keys()),
            state="readonly", width=12,
        ).pack(side=tk.LEFT)

        self.translate_check = tk.Checkbutton(
            toolbar, text="Traducir a Inglés", variable=self.translate_var,
            bg=COLOR_BG, fg=COLOR_TEXT_FG, selectcolor=COLOR_PANEL,
            activebackground=COLOR_BG, activeforeground=COLOR_TEXT_FG,
            font=("Segoe UI", 10), borderwidth=0, highlightthickness=0,
            cursor="hand2",
        )
        self.translate_check.pack(side=tk.LEFT, padx=(10, 0))

        # Source row: capture the whole system or a single application.
        source_row = ttk.Frame(self, style="Live.TFrame", padding=(16, 0, 16, 6))
        source_row.pack(fill=tk.X)

        ttk.Label(source_row, text="Fuente", style="LiveMuted.TLabel").pack(
            side=tk.LEFT, padx=(0, 6)
        )
        try:
            default_dev = sc.default_speaker().name
        except Exception:
            default_dev = ""
        self._device_name = default_dev
        # Maps a combobox label -> pid (None means whole-system device loopback).
        self._source_map: dict = {}
        self.source_var = tk.StringVar()
        self.source_combo = ttk.Combobox(
            source_row, textvariable=self.source_var, state="readonly", width=42,
        )
        self.source_combo.pack(side=tk.LEFT)
        self._flat_button(source_row, "↻", self._refresh_sources).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        self._refresh_sources()

        # Folder row: muted path + picker.
        folder_row = ttk.Frame(self, style="Live.TFrame", padding=(16, 0, 16, 10))
        folder_row.pack(fill=tk.X)

        self._flat_button(
            folder_row, "\U0001F4C1  Carpeta", self._choose_folder
        ).pack(side=tk.LEFT)
        self.folder_label = ttk.Label(
            folder_row, text=str(self.out_dir), style="LiveMuted.TLabel"
        )
        self.folder_label.pack(side=tk.LEFT, padx=(10, 0))

        # Output: editable so the user can fix mistakes while it runs.
        text_wrap = ttk.Frame(self, style="Live.TFrame", padding=(16, 0, 16, 16))
        text_wrap.pack(fill=tk.BOTH, expand=True)
        self.output = scrolledtext.ScrolledText(
            text_wrap, wrap=tk.WORD, font=("Segoe UI", 13),
            bg=COLOR_TEXT_BG, fg=COLOR_TEXT_FG, insertbackground=COLOR_TEXT_FG,
            relief=tk.FLAT, borderwidth=0, padx=14, pady=12,
            spacing3=6,
        )
        self.output.pack(fill=tk.BOTH, expand=True)
        _style_scrollbar(self.output)

        self.after(100, self._drain_queues)

    def _setup_styles(self):
        style = ttk.Style(self)
        style.configure("Live.TFrame", background=COLOR_BG)
        style.configure(
            "LiveTitle.TLabel", background=COLOR_BG, foreground=COLOR_TEXT_FG,
            font=("Segoe UI Semibold", 15),
        )
        style.configure(
            "LiveStatus.TLabel", background=COLOR_BG, foreground=COLOR_MUTED,
            font=("Segoe UI", 11),
        )
        style.configure(
            "LiveMuted.TLabel", background=COLOR_BG, foreground=COLOR_MUTED,
            font=("Segoe UI", 10),
        )
    def _accent_button(self, parent, text, command):
        # tk.Button (not ttk): the native Windows ttk theme ignores custom
        # button backgrounds, which made light text invisible on light buttons.
        return tk.Button(
            parent, text=text, command=command, cursor="hand2",
            bg=COLOR_ACCENT, fg="#ffffff",
            activebackground=COLOR_ACCENT_ACTIVE, activeforeground="#ffffff",
            relief=tk.FLAT, borderwidth=0, font=("Segoe UI Semibold", 11),
            padx=16, pady=7,
        )

    def _flat_button(self, parent, text, command):
        return tk.Button(
            parent, text=text, command=command, cursor="hand2",
            bg=COLOR_PANEL, fg=COLOR_TEXT_FG,
            activebackground="#34344a", activeforeground=COLOR_TEXT_FG,
            relief=tk.FLAT, borderwidth=0, font=("Segoe UI", 10),
            padx=14, pady=7,
        )

    def _toggle(self):
        if self.worker.is_running():
            self.worker.stop()
        else:
            source_info = self._source_map.get(self.source_var.get(), ("loopback", None))
            self.worker.start(
                language=LANGUAGES[self.lang_var.get()],
                out_dir=self.out_dir,
                source_type=source_info[0],
                source_val=source_info[1],
                translate=self.translate_var.get(),
            )
            self.toggle_btn.config(text="⏹  Detener")

    def _refresh_sources(self):
        """Reload the source list: whole-system option, physical microphones + apps emitting audio.

        Apps only show up once they have an active audio session, so this is
        wired to a refresh button the user can hit after starting playback.
        """
        try:
            import process_loopback
            apps = process_loopback.list_audio_apps()
        except Exception:
            apps = []
        
        self._source_map = {WHOLE_SYSTEM_LABEL: ("loopback", None)}
        
        # Add physical microphones
        try:
            mics = sc.all_microphones()
            for mic in mics:
                label = f"🎤 Micrófono: {mic.name}"
                self._source_map[label] = ("mic", mic.name)
        except Exception as exc:
            logger.exception("Failed to list microphones: %s", exc)

        for name, pid in apps:
            self._source_map[f"💻 App: {name} (pid {pid})"] = ("app", pid)
            
        labels = list(self._source_map.keys())
        self.source_combo.config(values=labels)
        if self.source_var.get() not in self._source_map:
            self.source_var.set(WHOLE_SYSTEM_LABEL)

    def _choose_folder(self):
        # Windows freezes the dialog when initialdir does not exist, so fall
        # back to a real path and always pass a parent for proper focus.
        initial = self.out_dir if self.out_dir.exists() else Path.home()
        chosen = filedialog.askdirectory(
            parent=self, initialdir=str(initial), title="Carpeta de salida"
        )
        if chosen:
            self.out_dir = Path(chosen)
            self.folder_label.config(text=str(self.out_dir))

    def _clear(self):
        self.output.delete("1.0", tk.END)

    def _summarize(self):
        text = self.output.get("1.0", tk.END).strip()
        if not text:
            self._set_status("Nada para resumir")
            return
        if not summarizer.is_configured():
            messagebox.showinfo(
                "Resumen no configurado",
                "Falta GEMINI_API_KEY. Crea un archivo .env en app_escritorio "
                "con tu clave para habilitar el resumen.",
            )
            return
        self.summary_btn.config(state=tk.DISABLED)
        self._set_status("Resumiendo...")
        threading.Thread(target=self._run_summary, args=(text,), daemon=True).start()

    def _run_summary(self, text):
        try:
            summary = summarizer.summarize(text)
            self.after(0, lambda: self._show_summary(summary))
        except summarizer.SummaryError as exc:
            msg = str(exc)
            self.after(0, lambda: self._summary_failed(msg))

    def _summary_failed(self, msg):
        self.summary_btn.config(state=tk.NORMAL)
        self._set_status("Error en el resumen")
        messagebox.showerror("Resumen", msg)

    def _show_summary(self, summary):
        self.summary_btn.config(state=tk.NORMAL)
        self._set_status("Resumen listo")

        win = tk.Toplevel(self)
        win.title("Resumen")
        win.geometry("560x520")
        win.configure(bg=COLOR_BG)

        ttk.Label(win, text="Resumen de la transcripcion", style="LiveTitle.TLabel").pack(
            anchor=tk.W, padx=16, pady=(14, 8)
        )
        box = scrolledtext.ScrolledText(
            win, wrap=tk.WORD, font=("Segoe UI", 12),
            bg=COLOR_TEXT_BG, fg=COLOR_TEXT_FG, insertbackground=COLOR_TEXT_FG,
            relief=tk.FLAT, borderwidth=0, padx=14, pady=12, spacing3=6,
        )
        box.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 12))
        _style_scrollbar(box)
        box.insert("1.0", summary)

        bar = ttk.Frame(win, style="Live.TFrame", padding=(16, 0, 16, 14))
        bar.pack(fill=tk.X)

        def copy():
            self.clipboard_clear()
            self.clipboard_append(summary)
            self._set_status("Resumen copiado")

        self._flat_button(bar, "Copiar", copy).pack(side=tk.LEFT)
        self._accent_button(bar, "Cerrar", win.destroy).pack(side=tk.RIGHT)

    def _save(self):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = self.out_dir / f"edited_{stamp}.txt"
        path.write_text(self.output.get("1.0", tk.END).strip() + "\n", encoding="utf-8")
        self._set_status(f"Guardado: {path.name}")

    def _append(self, text: str):
        # Only follow the bottom if the user is already there, so live text
        # never yanks the cursor away while they are editing further up.
        at_bottom = self.output.yview()[1] >= 0.999
        insert_pos = self.output.index(tk.INSERT)
        self.output.insert(tk.END, text + "\n")
        if at_bottom:
            self.output.see(tk.END)
        else:
            self.output.mark_set(tk.INSERT, insert_pos)

    def _set_status(self, text: str):
        low = text.lower()
        if "escuchando" in low:
            color = COLOR_OK
        elif "error" in low:
            color = COLOR_DANGER
        elif "cargando" in low:
            color = COLOR_ACCENT
        else:
            color = COLOR_MUTED
        self.status.config(text=f"●  {text}", foreground=color)

    def _drain_queues(self):
        while not self.status_queue.empty():
            self._set_status(self.status_queue.get_nowait())
        while not self.text_queue.empty():
            self._append(self.text_queue.get_nowait())

        # Reactive sync of toggle button and spinner based on worker state
        if self.worker.is_running():
            if self.worker._stop.is_set():
                if self.spinner.is_spinning:
                    self.spinner.stop()
                    self.spinner.pack_forget()
                self.toggle_btn.config(text="⌛ Deteniendo...", state="disabled")
            else:
                if not self.spinner.is_spinning:
                    self.spinner.start()
                    self.spinner.pack(side=tk.LEFT, padx=(0, 6))
                self.toggle_btn.config(text="⏹  Detener", state="normal")
        else:
            if self.spinner.is_spinning:
                self.spinner.stop()
                self.spinner.pack_forget()
            self.toggle_btn.config(text="▶  Iniciar", state="normal")

        self.after(100, self._drain_queues)

    def stop_worker(self):
        self.worker.stop()
