import queue
import threading
import datetime as dt
import wave
from pathlib import Path
import random

import numpy as np
import soundcard as sc

import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk

import transcriber
import summarizer
from config import logger
from spinner import Spinner

SAMPLE_RATE = transcriber.SAMPLE_RATE
CHUNK_SECONDS = 5
CAPTURE_BLOCK_SECONDS = 0.5
SILENCE_THRESHOLD = 1e-4

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
WHOLE_SYSTEM_LABEL = "🔊  Todo el sistema"


def to_mono(data: np.ndarray) -> np.ndarray:
    return data.mean(axis=1).astype(np.float32)


def is_silent(audio: np.ndarray, threshold: float = SILENCE_THRESHOLD) -> bool:
    return float(np.abs(audio).mean()) < threshold


class Transcriber:
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
        self._wav_file.setsampwidth(2)
        self._wav_file.setframerate(SAMPLE_RATE)

    def _emit(self, text: str):
        self._text_queue.put(text)
        if self._log_file:
            self._log_file.write(text + "\n")
            self._log_file.flush()

    def _producer(self):
        block = int(SAMPLE_RATE * CAPTURE_BLOCK_SECONDS)
        if self._source_type == "app":
            try:
                self._capture_process(block)
                return
            except Exception as exc:
                logger.exception("Process loopback failed: %s", exc)
                self._status_queue.put(
                    "Error al capturar la app; usando micrófono."
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
                pcm_data = (mono * 32767).clip(-32768, 32767).astype(np.int16)
                self._wav_file.writeframes(pcm_data.tobytes())
        except queue.Full:
            pass

    def _capture_system(self, block):
        try:
            speaker = sc.default_speaker()
            if self._source_val and self._source_type == "loopback":
                speaker = next(
                    (s for s in sc.all_speakers() if s.name == self._source_val), speaker
                )
            loopback = sc.get_microphone(id=str(speaker.name), include_loopback=True)
            self._status_queue.put(f"Escuchando: Sistema ({speaker.name[:18]}...)")
            with loopback.recorder(samplerate=SAMPLE_RATE) as rec:
                while not self._stop.is_set():
                    self._push(to_mono(rec.record(numframes=block)))
        except Exception as exc:
            logger.exception("System loopback capture failed: %s", exc)
            self._status_queue.put(f"Error sistema: {exc}")

    def _capture_mic(self, block):
        try:
            mic = sc.default_microphone()
            if self._source_val and self._source_type == "mic":
                mic = next(
                    (m for m in sc.all_microphones() if m.name == self._source_val), mic
                )
            self._status_queue.put(f"Escuchando: Micrófono ({mic.name[:18]}...)")
            with mic.recorder(samplerate=SAMPLE_RATE) as rec:
                while not self._stop.is_set():
                    self._push(to_mono(rec.record(numframes=block)))
        except Exception as exc:
            logger.exception("Microphone capture failed: %s", exc)
            self._status_queue.put(f"Error micrófono: {exc}")

    def _capture_process(self, block):
        import process_loopback
        self._status_queue.put(f"Escuchando App (PID {self._source_val})")
        with process_loopback.ProcessLoopbackRecorder(
            self._source_val, samplerate=SAMPLE_RATE
        ) as rec:
            while not self._stop.is_set():
                audio_data = rec.record(block, stop_event=self._stop)
                if len(audio_data) > 0:
                    self._push(to_mono(audio_data))

    def _consumer(self):
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
            transcriber.get_model()
            self._open_log()

            producer = threading.Thread(target=self._producer, daemon=True)
            consumer = threading.Thread(target=self._consumer, daemon=True)
            producer.start()
            consumer.start()
            producer.join()
            consumer.join()

            self._status_queue.put("Inactivo")
        except Exception as exc:
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


class LiveFrame(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color="transparent")

        self.text_queue: "queue.Queue[str]" = queue.Queue()
        self.status_queue: "queue.Queue[str]" = queue.Queue()
        self.translate_var = tk.BooleanVar(value=False)
        self.worker = Transcriber(self.text_queue, self.status_queue)
        self.out_dir = DEFAULT_DIR

        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill=tk.X, padx=24, pady=(20, 10))

        label_titulo = ctk.CTkLabel(
            header, text="Transcripción en vivo",
            font=("Segoe UI Semibold", 22), text_color="#FFFFFF"
        )
        label_titulo.pack(side=tk.LEFT)

        # Status badge capsule (like Screenshot 2)
        self.status_badge = ctk.CTkFrame(header, fg_color="#1E1F29", corner_radius=16, height=28, border_width=1, border_color="#2A2B36")
        self.status_badge.pack(side=tk.RIGHT, padx=5)
        self.status_badge.pack_propagate(False)

        self.spinner = Spinner(
            self.status_badge, size=14, bg="#1E1F29",
            accent_color="#7000FF", muted_color="#2A2B36"
        )

        self.status_label = ctk.CTkLabel(
            self.status_badge, text="●  Inactivo", font=("Segoe UI Semibold", 11), text_color="#8A8F9E"
        )
        self.status_label.pack(side=tk.LEFT, padx=(12, 12), pady=0)

        # Subtitle
        subtitle_frame = ctk.CTkFrame(self, fg_color="transparent")
        subtitle_frame.pack(fill=tk.X, padx=24)
        label_subtitulo = ctk.CTkLabel(
            subtitle_frame, text="Capturá el audio del sistema o micrófono en tiempo real.",
            font=("Segoe UI", 12), text_color="#8A8F9E"
        )
        label_subtitulo.pack(anchor=tk.W)

        # Toolbar
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(fill=tk.X, padx=24, pady=(12, 8))

        self.toggle_btn = ctk.CTkButton(
            toolbar, text="▶   Iniciar", font=("Segoe UI Semibold", 12),
            fg_color="#7000FF", text_color="#FFFFFF", hover_color="#5900CC",
            width=110, height=36, corner_radius=8, command=self._toggle
        )
        self.toggle_btn.pack(side=tk.LEFT)

        self.btn_save = ctk.CTkButton(
            toolbar, text="💾   Guardar", font=("Segoe UI Semibold", 12),
            fg_color="#15161E", text_color="#FFFFFF", hover_color="#1A1B26",
            width=90, height=36, corner_radius=8, command=self._save
        )
        self.btn_save.pack(side=tk.LEFT, padx=(8, 0))

        self.btn_clear = ctk.CTkButton(
            toolbar, text="🧹   Limpiar", font=("Segoe UI Semibold", 12),
            fg_color="#15161E", text_color="#FFFFFF", hover_color="#1A1B26",
            width=90, height=36, corner_radius=8, command=self._clear
        )
        self.btn_clear.pack(side=tk.LEFT, padx=(8, 0))

        self.summary_btn = ctk.CTkButton(
            toolbar, text="✨   Resumir", font=("Segoe UI Semibold", 12),
            fg_color="#15161E", text_color="#FFFFFF", hover_color="#1A1B26",
            width=100, height=36, corner_radius=8, command=self._summarize
        )
        self.summary_btn.pack(side=tk.LEFT, padx=(8, 0))

        # Main Layout split: Config options on left, equalizers and text area below
        card_config = ctk.CTkFrame(self, fg_color="#15161E", corner_radius=12, border_color="#2A2B36", border_width=1)
        card_config.pack(fill=tk.X, padx=24, pady=6)

        # Options layout inside config card
        ctk.CTkLabel(
            card_config, text="FUENTE", font=("Segoe UI Semibold", 9), text_color="#8A8F9E"
        ).grid(row=0, column=0, padx=(16, 4), pady=(8, 2), sticky=tk.W)

        try:
            default_dev = sc.default_speaker().name
        except Exception:
            default_dev = ""
        self._device_name = default_dev
        self._source_map: dict = {}
        self.source_var = tk.StringVar()
        
        self.source_combo = ctk.CTkComboBox(
            card_config, values=[WHOLE_SYSTEM_LABEL], state="readonly", width=320,
            fg_color="#1A1B26", border_color="#2A2B36", button_color="#2A2B36",
            dropdown_fg_color="#15161E", dropdown_text_color="#FFFFFF",
            dropdown_hover_color="#7000FF", variable=self.source_var
        )
        self.source_combo.grid(row=1, column=0, padx=(16, 8), pady=(0, 12), sticky=tk.W)

        self.btn_refresh = ctk.CTkButton(
            card_config, text="↻", font=("Segoe UI Semibold", 13),
            fg_color="#1A1B26", text_color="#FFFFFF", hover_color="#2A2B36",
            width=36, height=28, corner_radius=8, command=self._refresh_sources
        )
        self.btn_refresh.grid(row=1, column=1, padx=(0, 12), pady=(0, 12), sticky=tk.W)

        ctk.CTkLabel(
            card_config, text="IDIOMA", font=("Segoe UI Semibold", 9), text_color="#8A8F9E"
        ).grid(row=0, column=2, padx=(8, 4), pady=(8, 2), sticky=tk.W)

        _sys = transcriber.detect_system_language()
        _default_lang = next(
            (label for label, code in LANGUAGES.items() if code == _sys), "Auto"
        )
        self.lang_var = tk.StringVar(value=_default_lang)

        self.lang_combo = ctk.CTkComboBox(
            card_config, values=list(LANGUAGES.keys()), state="readonly", width=110,
            fg_color="#1A1B26", border_color="#2A2B36", button_color="#2A2B36",
            dropdown_fg_color="#15161E", dropdown_text_color="#FFFFFF",
            dropdown_hover_color="#7000FF", variable=self.lang_var
        )
        self.lang_combo.grid(row=1, column=2, padx=(8, 12), pady=(0, 12), sticky=tk.W)

        self.translate_check = ctk.CTkCheckBox(
            card_config, text="Traducir a Inglés", variable=self.translate_var,
            font=("Segoe UI", 12), text_color="#FFFFFF",
            fg_color="#7000FF", hover_color="#5900CC", border_color="#2A2B36"
        )
        self.translate_check.grid(row=0, column=3, rowspan=2, padx=(24, 16), pady=12, sticky=tk.E)
        card_config.grid_columnconfigure(3, weight=1)

        # Equalizer Wave Card (like Screenshot 2)
        self.wave_card = ctk.CTkFrame(self, fg_color="#15161E", corner_radius=12, border_color="#2A2B36", border_width=1)
        self.wave_card.pack(fill=tk.X, padx=24, pady=6)

        self.wave_canvas = tk.Canvas(
            self.wave_card, bg="#11121A", highlightthickness=0, height=70
        )
        self.wave_canvas.pack(fill=tk.X, padx=12, pady=(12, 6))

        # Setup equalizer bars variables
        self.bar_ids = []
        self.num_bars = 36
        self.bar_width = 5
        self.bar_gap = 3
        self.is_animating = False

        self.path_label = ctk.CTkLabel(
            self.wave_card, text=f"Pulsa Iniciar para comenzar\n{self.out_dir}",
            font=("Segoe UI", 11), text_color="#8A8F9E", justify=tk.CENTER
        )
        self.path_label.pack(pady=(4, 10))

        # Output text box
        text_card = ctk.CTkFrame(self, fg_color="#15161E", corner_radius=12, border_color="#2A2B36", border_width=1)
        text_card.pack(fill=tk.BOTH, expand=True, padx=24, pady=(6, 16))

        self.output = ctk.CTkTextbox(
            text_card, fg_color="#11121A", text_color="#FFFFFF",
            font=("Segoe UI", 12),
            corner_radius=8, border_width=0
        )
        self.output.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        self._refresh_sources()
        self.after(100, self._init_wave_bars)
        self.after(150, self._drain_queues)

    def _init_wave_bars(self):
        self.wave_canvas.delete("all")
        self.bar_ids = []
        w = self.wave_canvas.winfo_width()
        if w < 10:
            w = 500  # Default fallback width before render
        h = 70
        center_y = h / 2
        total_width = self.num_bars * self.bar_width + (self.num_bars - 1) * self.bar_gap
        start_x = (w - total_width) / 2

        for i in range(self.num_bars):
            x0 = start_x + i * (self.bar_width + self.bar_gap)
            x1 = x0 + self.bar_width
            y0 = center_y - 2
            y1 = center_y + 2
            bar_id = self.wave_canvas.create_rectangle(
                x0, y0, x1, y1, fill="#7000FF", outline="", tags=f"bar_{i}"
            )
            self.bar_ids.append(bar_id)

    def _animate_wave(self):
        if not self.is_animating:
            # Flatten all bars when stopped
            h = 70
            center_y = h / 2
            for bar_id in self.bar_ids:
                coords = self.wave_canvas.coords(bar_id)
                if coords:
                    x0, _, x1, _ = coords
                    self.wave_canvas.coords(bar_id, x0, center_y - 2, x1, center_y + 2)
            return

        h = 70
        center_y = h / 2
        for bar_id in self.bar_ids:
            # Dynamic bar scale heights
            bar_h = random.randint(4, 52)
            y0 = center_y - (bar_h / 2)
            y1 = center_y + (bar_h / 2)
            coords = self.wave_canvas.coords(bar_id)
            if coords:
                x0, _, x1, _ = coords
                self.wave_canvas.coords(bar_id, x0, y0, x1, y1)

        self.after(70, self._animate_wave)

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
            self.toggle_btn.configure(text="⏹   Detener")

    def refresh_devices(self):
        self._refresh_sources()

    def _refresh_sources(self):
        try:
            import process_loopback
            apps = process_loopback.list_audio_apps()
        except Exception:
            apps = []
        
        self._source_map = {WHOLE_SYSTEM_LABEL: ("loopback", None)}
        
        try:
            mics = sc.all_microphones()
            for mic in mics:
                label = f"🎤  Micrófono: {mic.name}"
                self._source_map[label] = ("mic", mic.name)
        except Exception as exc:
            logger.exception("Failed to list microphones: %s", exc)

        for name, pid in apps:
            self._source_map[f"💻  App: {name} (PID {pid})"] = ("app", pid)
            
        labels = list(self._source_map.keys())
        self.source_combo.configure(values=labels)
        if self.source_var.get() not in self._source_map:
            self.source_var.set(WHOLE_SYSTEM_LABEL)

    def _choose_folder(self):
        initial = self.out_dir if self.out_dir.exists() else Path.home()
        chosen = filedialog.askdirectory(
            parent=self, initialdir=str(initial), title="Carpeta de salida"
        )
        if chosen:
            self.out_dir = Path(chosen)
            self.path_label.configure(text=f"Escuchando sistema\n{self.out_dir}")

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
        self.summary_btn.configure(state="disabled")
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
        self.summary_btn.configure(state="normal")
        self._set_status("Error en el resumen")
        messagebox.showerror("Resumen", msg)

    def _show_summary(self, summary):
        self.summary_btn.configure(state="normal")
        self._set_status("Resumen listo")

        win = ctk.CTkToplevel(self)
        win.title("Resumen")
        win.geometry("560x520")
        win.configure(fg_color="#0B0C10")

        ctk.CTkLabel(
            win, text="Resumen de la transcripción",
            font=("Segoe UI Semibold", 18), text_color="#FFFFFF"
        ).pack(anchor=tk.W, padx=20, pady=(16, 8))

        card_box = ctk.CTkFrame(win, fg_color="#15161E", corner_radius=12, border_color="#2A2B36", border_width=1)
        card_box.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 16))

        box = ctk.CTkTextbox(
            card_box, fg_color="#11121A", text_color="#FFFFFF",
            font=("Segoe UI", 12), corner_radius=8
        )
        box.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        box.insert("1.0", summary)

        bar = ctk.CTkFrame(win, fg_color="transparent")
        bar.pack(fill=tk.X, padx=20, pady=(0, 20))

        def copy():
            self.clipboard_clear()
            self.clipboard_append(summary)
            self._set_status("Resumen copiado")

        ctk.CTkButton(
            bar, text="Copiar", font=("Segoe UI Semibold", 12),
            fg_color="#15161E", text_color="#FFFFFF", hover_color="#1A1B26",
            width=90, height=36, corner_radius=8, command=copy
        ).pack(side=tk.LEFT)

        ctk.CTkButton(
            bar, text="Cerrar", font=("Segoe UI Semibold", 12),
            fg_color="#7000FF", text_color="#FFFFFF", hover_color="#5900CC",
            width=90, height=36, corner_radius=8, command=win.destroy
        ).pack(side=tk.RIGHT)

    def _save(self):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = self.out_dir / f"edited_{stamp}.txt"
        path.write_text(self.output.get("1.0", tk.END).strip() + "\n", encoding="utf-8")
        self._set_status(f"Guardado: {path.name}")

    def _append(self, text: str):
        at_bottom = self.output.yview()[1] >= 0.999
        self.output.insert(tk.END, text + "\n")
        if at_bottom:
            self.output.see(tk.END)

    def _set_status(self, text: str):
        low = text.lower()
        if "escuchando" in low:
            color = "#4ec98a"  # green
        elif "error" in low:
            color = "#e0506a"  # red
        elif "cargando" in low:
            color = "#7000FF"  # purple
        else:
            color = "#8A8F9E"  # gray
        self.status_label.configure(text=f"●  {text}", text_color=color)

    def _drain_queues(self):
        while not self.status_queue.empty():
            self._set_status(self.status_queue.get_nowait())
        while not self.text_queue.empty():
            self._append(self.text_queue.get_nowait())

        # Reactive sync of toggle button and spinner based on worker state
        if self.worker.is_running():
            if self.worker._stop.is_set():
                self.is_animating = False
                if self.spinner.is_spinning:
                    self.spinner.stop()
                    self.spinner.pack_forget()
                self.toggle_btn.configure(text="⌛ Deteniendo...", state="disabled")
            else:
                if not self.is_animating:
                    self.is_animating = True
                    self._init_wave_bars()
                    self._animate_wave()
                if not self.spinner.is_spinning:
                    self.spinner.start()
                    self.spinner.pack(side=tk.LEFT, padx=(12, 0))
                self.toggle_btn.configure(text="⏹   Detener", state="normal")
        else:
            self.is_animating = False
            if self.spinner.is_spinning:
                self.spinner.stop()
                self.spinner.pack_forget()
            self.toggle_btn.configure(text="▶   Iniciar", state="normal")

        self.after(100, self._drain_queues)

    def stop_worker(self):
        self.worker.stop()
