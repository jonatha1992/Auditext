import queue
import threading
from pathlib import Path
import random

import soundcard as sc
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk

from infrastructure.services import summarizer
import config
from config import logger
from infrastructure.services.live_transcriber import (
    DEFAULT_DIR,
    GOOGLE_STT_LABEL,
    LANGUAGES,
    WINDOWS_STT_LABEL,
    WHOLE_SYSTEM_LABEL,
    Transcriber,
    detect_system_language,
)
from .spinner import Spinner

class LiveFrame(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color="transparent")

        self.text_queue: "queue.Queue[str]" = queue.Queue()
        self.status_queue: "queue.Queue[str]" = queue.Queue()
        self.translate_var = tk.BooleanVar(value=False)
        self.record_var = tk.BooleanVar(value=True)
        self.worker = Transcriber(self.text_queue, self.status_queue)
        self.out_dir = DEFAULT_DIR
        self._controls_disabled = False

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

        self.btn_open_folder = ctk.CTkButton(
            toolbar, text="📁   Grabaciones", font=("Segoe UI Semibold", 12),
            fg_color="#15161E", text_color="#FFFFFF", hover_color="#1A1B26",
            width=120, height=36, corner_radius=8, command=self._open_recordings_folder
        )
        self.btn_open_folder.pack(side=tk.LEFT, padx=(8, 0))

        # Main Layout split: Config options on left, equalizers and text area below
        card_config = ctk.CTkFrame(self, fg_color="#15161E", corner_radius=12, border_color="#2A2B36", border_width=1)
        card_config.pack(fill=tk.X, padx=24, pady=6)

        # Options layout inside config card
        self.label_fuente = ctk.CTkLabel(
            card_config, text="FUENTE", font=("Segoe UI Semibold", 9), text_color="#8A8F9E"
        )
        self.label_fuente.grid(row=0, column=0, padx=(16, 4), pady=(8, 2), sticky=tk.W)

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

        self.label_idioma = ctk.CTkLabel(
            card_config, text="IDIOMA", font=("Segoe UI Semibold", 9), text_color="#8A8F9E"
        )
        self.label_idioma.grid(row=0, column=2, padx=(8, 4), pady=(8, 2), sticky=tk.W)

        _sys = detect_system_language()
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

        # Recording controls — explicit toggle + folder picker
        rec_frame = ctk.CTkFrame(card_config, fg_color="#15161E")
        rec_frame.grid(row=0, column=4, rowspan=2, padx=(0, 16), pady=12, sticky=tk.E)

        self.record_check = ctk.CTkCheckBox(
            rec_frame, text="⏺ Grabar audio", variable=self.record_var,
            font=("Segoe UI", 12), text_color="#FFFFFF",
            fg_color="#E53E3E", hover_color="#C53030", border_color="#2A2B36"
        )
        self.record_check.pack(anchor=tk.W)

        self.btn_change_folder = ctk.CTkButton(
            rec_frame, text="📂 Cambiar carpeta", font=("Segoe UI", 11),
            fg_color="transparent", text_color="#8A8F9E", hover_color="#1A1B26",
            width=130, height=20, corner_radius=6, command=self._choose_folder
        )
        self.btn_change_folder.pack(anchor=tk.W, pady=(4, 0))

        card_config.grid_columnconfigure(3, weight=0)
        card_config.grid_columnconfigure(4, weight=1)

        # Responsive layout adjustment logic
        self.last_live_width = [0]
        self._resize_after_id = None

        def _apply_live_layout(new_w):
            self._resize_after_id = None
            if new_w == self.last_live_width[0]:
                return
            self.last_live_width[0] = new_w

            # Responsive config cards grid rearrange
            _all_controls = [
                self.label_fuente, self.source_combo, self.btn_refresh,
                self.label_idioma, self.lang_combo, self.translate_check, rec_frame,
            ]
            for w in _all_controls:
                w.grid_forget()

            if new_w < 800:
                self.label_fuente.grid(row=0, column=0, padx=16, pady=(8, 2), sticky=tk.W)
                self.source_combo.grid(row=1, column=0, padx=(16, 8), pady=(0, 6), sticky=tk.W)
                self.btn_refresh.grid(row=1, column=1, padx=(0, 16), pady=(0, 6), sticky=tk.W)
                self.label_idioma.grid(row=2, column=0, padx=16, pady=(6, 2), sticky=tk.W)
                self.lang_combo.grid(row=3, column=0, padx=16, pady=(0, 6), sticky=tk.W)
                self.translate_check.grid(row=4, column=0, columnspan=2, padx=16, pady=(4, 4), sticky=tk.W)
                rec_frame.grid(row=5, column=0, columnspan=2, padx=16, pady=(4, 12), sticky=tk.W)

                card_config.grid_columnconfigure(0, weight=1)
                card_config.grid_columnconfigure(1, weight=0)
                card_config.grid_columnconfigure(2, weight=0)
                card_config.grid_columnconfigure(3, weight=0)
                card_config.grid_columnconfigure(4, weight=0)

                # Responsive toolbar buttons (Grid)
                self.toggle_btn.pack_forget()
                self.btn_save.pack_forget()
                self.btn_clear.pack_forget()
                self.summary_btn.pack_forget()
                self.btn_open_folder.pack_forget()

                self.toggle_btn.grid(row=0, column=1, padx=6, pady=4)
                self.btn_save.grid(row=0, column=2, padx=6, pady=4)
                self.btn_clear.grid(row=0, column=3, padx=6, pady=4)
                self.summary_btn.grid(row=1, column=1, padx=6, pady=4)
                self.btn_open_folder.grid(row=1, column=2, columnspan=2, padx=6, pady=4)

                toolbar.grid_columnconfigure(0, weight=1)
                toolbar.grid_columnconfigure(1, weight=0)
                toolbar.grid_columnconfigure(2, weight=0)
                toolbar.grid_columnconfigure(3, weight=0)
                toolbar.grid_columnconfigure(4, weight=1)
            else:
                self.label_fuente.grid(row=0, column=0, padx=(16, 4), pady=(8, 2), sticky=tk.W)
                self.source_combo.grid(row=1, column=0, padx=(16, 8), pady=(0, 12), sticky=tk.W)
                self.btn_refresh.grid(row=1, column=1, padx=(0, 12), pady=(0, 12), sticky=tk.W)
                self.label_idioma.grid(row=0, column=2, padx=(8, 4), pady=(8, 2), sticky=tk.W)
                self.lang_combo.grid(row=1, column=2, padx=(8, 12), pady=(0, 12), sticky=tk.W)
                self.translate_check.grid(row=0, column=3, rowspan=2, padx=(24, 16), pady=12, sticky=tk.E)
                rec_frame.grid(row=0, column=4, rowspan=2, padx=(0, 16), pady=12, sticky=tk.E)

                card_config.grid_columnconfigure(0, weight=0)
                card_config.grid_columnconfigure(1, weight=0)
                card_config.grid_columnconfigure(2, weight=0)
                card_config.grid_columnconfigure(3, weight=0)
                card_config.grid_columnconfigure(4, weight=1)

                # Reset toolbar buttons (Pack)
                self.toggle_btn.grid_forget()
                self.btn_save.grid_forget()
                self.btn_clear.grid_forget()
                self.summary_btn.grid_forget()
                self.btn_open_folder.grid_forget()

                self.toggle_btn.pack(side=tk.LEFT)
                self.btn_save.pack(side=tk.LEFT, padx=(8, 0))
                self.btn_clear.pack(side=tk.LEFT, padx=(8, 0))
                self.summary_btn.pack(side=tk.LEFT, padx=(8, 0))
                self.btn_open_folder.pack(side=tk.LEFT, padx=(8, 0))

                toolbar.grid_columnconfigure(0, weight=0)
                toolbar.grid_columnconfigure(1, weight=0)
                toolbar.grid_columnconfigure(2, weight=0)
                toolbar.grid_columnconfigure(3, weight=0)
                toolbar.grid_columnconfigure(4, weight=0)

        def on_live_configure(event):
            if event.widget != self:
                return
            new_w = event.width
            if new_w < 400: # Ignorar anchos de inicialización pequeños
                return
            if self._resize_after_id is not None:
                self.after_cancel(self._resize_after_id)
            self._resize_after_id = self.after(150, lambda w=new_w: _apply_live_layout(w))

        self.bind("<Configure>", on_live_configure)

        # Equalizer Wave Card (like Screenshot 2)
        self.wave_card = ctk.CTkFrame(self, fg_color="#15161E", corner_radius=12, border_color="#2A2B36", border_width=1)
        self.wave_card.pack(fill=tk.X, padx=24, pady=6)

        self.wave_canvas = tk.Canvas(
            self.wave_card, bg="#11121A", highlightthickness=0, height=70
        )
        self.wave_canvas.pack(fill=tk.X, padx=12, pady=(12, 6))

        # Setup equalizer bars variables
        self.bar_ids = []
        self.num_bars = 60
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
        self.wave_canvas.bind("<Configure>", lambda event: self._init_wave_bars(event.width))
        self.after(150, self._drain_queues)

    def _init_wave_bars(self, canvas_width=None):
        self.wave_canvas.delete("all")
        self.bar_ids = []
        w = canvas_width if canvas_width is not None else self.wave_canvas.winfo_width()
        if w < 10:
            w = 500  # Default fallback width before render
        
        self.bar_width = 6
        self.bar_gap = 3
        
        # Calcular cantidad de barras dinámicamente para ocupar el ancho disponible de la tarjeta
        # Dejamos 24px de margen a los costados
        visible_w = w - 48
        self.num_bars = max(20, visible_w // (self.bar_width + self.bar_gap))
        
        # Sincronizar la cantidad de barras con el worker
        self.worker.num_bars = self.num_bars
        
        h = 70
        center_y = 45  # waveform centered in bottom 50px

        total_width = self.num_bars * self.bar_width + (self.num_bars - 1) * self.bar_gap
        start_x = (w - total_width) / 2

        # Draw the baseline (horizontal dotted line)
        x_start = start_x
        x_end = start_x + total_width
        self.wave_canvas.create_line(
            x_start, center_y, x_end, center_y, fill="#1A1B26", width=1, dash=(2, 2)
        )

        for i in range(self.num_bars):
            x0 = start_x + i * (self.bar_width + self.bar_gap)
            x1 = x0 + self.bar_width
            y0 = center_y - 2
            y1 = center_y + 2
            bar_id = self.wave_canvas.create_rectangle(
                x0, y0, x1, y1, fill="#7000FF", outline="", tags=f"bar_{i}"
            )
            self.bar_ids.append(bar_id)
            
        # Draw the initial red playhead line (make it width 3 for better visibility)
        self.wave_canvas.create_line(
            start_x, 22, start_x, 68, fill="#E53E3E", width=3, tags="playhead"
        )
        
        # Initial draw of timeline
        self._draw_timeline(0.0)

    def _draw_timeline(self, elapsed):
        self.wave_canvas.delete("timeline")
        
        w = self.wave_canvas.winfo_width()
        if w < 10:
            w = 500
        total_width = self.num_bars * self.bar_width + (self.num_bars - 1) * self.bar_gap
        start_x = (w - total_width) / 2

        # Duración total que cabe en pantalla según la cantidad de barras
        duration_on_screen = self.num_bars * 0.15
        if elapsed < duration_on_screen:
            base_time = 0.0
        else:
            base_time = elapsed - duration_on_screen

        # Generar marcas de tiempo (ticks) dinámicamente cada 2 segundos
        ticks = []
        start_second = int(base_time // 2) * 2
        if start_second < base_time:
            start_second += 2.0
            
        t_val = start_second
        while True:
            idx = int((t_val - base_time) / 0.15)
            if idx >= self.num_bars:
                break
            if idx >= 0:
                ticks.append((idx, t_val))
            t_val += 2.0

        def format_time(t):
            mins = int(t // 60)
            secs = int(t % 60)
            hundredths = int((t * 100) % 100)
            return f"{mins:02d}:{secs:02d}.{hundredths:02d}"

        for idx, t_val in ticks:
            if idx < self.num_bars:
                x = start_x + idx * (self.bar_width + self.bar_gap) + self.bar_width / 2
                # Draw small tick mark
                self.wave_canvas.create_line(x, 2, x, 8, fill="#2A2B36", width=1, tags="timeline")
                # Draw text label
                self.wave_canvas.create_text(
                    x, 15, text=format_time(t_val), font=("Segoe UI Semibold", 8), fill="#8A8F9E", tags="timeline"
                )

    def _animate_wave(self):
        if not self.is_animating:
            # Flatten all bars when stopped and reset playhead/timeline to start
            h = 70
            center_y = 45
            for bar_id in self.bar_ids:
                coords = self.wave_canvas.coords(bar_id)
                if coords:
                    x0, _, x1, _ = coords
                    self.wave_canvas.coords(bar_id, x0, center_y - 2, x1, center_y + 2)
            
            # Reset playhead to start
            w = self.wave_canvas.winfo_width()
            if w < 10:
                w = 500
            total_width = self.num_bars * self.bar_width + (self.num_bars - 1) * self.bar_gap
            start_x = (w - total_width) / 2
            self.wave_canvas.coords("playhead", start_x, 22, start_x, 68)
            
            # Reset timeline
            self._draw_timeline(0.0)
            return

        h = 70
        center_y = 45

        # Check if we are running in cloud or subprocess modes (simulate playhead moving)
        if getattr(self.worker, "_source_type", None) in {"google_stt", "windows_stt"}:
            if not hasattr(self, "_fake_history") or len(self._fake_history) != self.num_bars:
                self._fake_history = [0.0] * self.num_bars
                self._fake_playhead = min(getattr(self, "_fake_playhead", 0), self.num_bars - 1)
                self._fake_counter = 0
                self._fake_elapsed = getattr(self, "_fake_elapsed", 0.0)
            
            # Update fake wave every 3 animation frames (150ms)
            self._fake_counter += 1
            if self._fake_counter >= 3:
                self._fake_counter = 0
                self._fake_elapsed += 0.15
                if random.random() < 0.15:
                    sim_val = random.uniform(0.3, 0.9)
                else:
                    sim_val = 0.0
                
                idx = self._fake_playhead
                if idx < self.num_bars - 1:
                    self._fake_history[idx] = sim_val
                    self._fake_playhead += 1
                else:
                    self._fake_history = self._fake_history[1:] + [sim_val]
                    self._fake_playhead = self.num_bars - 1
            
            bands = self._fake_history
            playhead_idx = self._fake_playhead
            elapsed = self._fake_elapsed
        else:
            # Get latest rolling waveform history from real-time worker
            bands = getattr(self.worker, "last_bands", None)
            if not bands:
                bands = [0.0] * self.num_bars
            playhead_idx = getattr(self.worker, "playhead_index", 0) or 0
            
            # Real elapsed time
            elapsed = 0.0
            if self.is_animating and getattr(self.worker, "start_time", None):
                import time
                elapsed = time.time() - self.worker.start_time

        for i, bar_id in enumerate(self.bar_ids):
            band_val = bands[i] if i < len(bands) else 0.0
            
            # Tiny random jitter on silence to keep the UI feeling "alive"
            if band_val < 0.02:
                bar_h = 4 + random.randint(0, 1)
            else:
                bar_h = 4 + int(band_val * 40)  # max height 40px

            y0 = center_y - (bar_h / 2)
            y1 = center_y + (bar_h / 2)
            coords = self.wave_canvas.coords(bar_id)
            if coords:
                x0, _, x1, _ = coords
                self.wave_canvas.coords(bar_id, x0, y0, x1, y1)

        # Update the playhead red line position
        w = self.wave_canvas.winfo_width()
        if w < 10:
            w = 500
        total_width = self.num_bars * self.bar_width + (self.num_bars - 1) * self.bar_gap
        start_x = (w - total_width) / 2
        x_playhead = start_x + playhead_idx * (self.bar_width + self.bar_gap) + self.bar_width / 2
        self.wave_canvas.coords("playhead", x_playhead, 22, x_playhead, 68)

        # Draw the dynamic timeline
        self._draw_timeline(elapsed)

        self.after(50, self._animate_wave)

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
                save_audio=self.record_var.get(),
            )
            self.toggle_btn.configure(text="⏹   Detener")

    def refresh_devices(self):
        self._refresh_sources()

    def _refresh_sources(self):
        try:
            from infrastructure.audio import process_loopback
            apps = process_loopback.list_audio_apps()
        except Exception:
            apps = []

        self._source_map = {WHOLE_SYSTEM_LABEL: ("loopback", None)}

        # Google STT — same engine as Google Docs voice typing (needs internet)
        self._source_map[GOOGLE_STT_LABEL] = ("google_stt", None)

        # Windows STT — System.Speech offline (needs Windows speech language pack)
        self._source_map[WINDOWS_STT_LABEL] = ("windows_stt", None)

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

    def _open_recordings_folder(self):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        import subprocess as _sp
        _sp.Popen(["explorer", str(self.out_dir)])

    def _choose_folder(self):
        initial = self.out_dir if self.out_dir.exists() else Path.home()
        chosen = filedialog.askdirectory(
            parent=self, initialdir=str(initial), title="Carpeta de grabaciones"
        )
        if chosen:
            self.out_dir = Path(chosen)
            self.path_label.configure(
                text=f"Pulsa Iniciar para comenzar\n{self.out_dir}",
                text_color="#8A8F9E"
            )

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
                "Falta GEMINI_API_KEY. Crea un archivo .env en la raíz del proyecto "
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
        text = self.output.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning("Advertencia", "No hay texto para guardar.")
            return

        # Pedir nombre de la grabación al usuario usando CTkInputDialog
        dialog = ctk.CTkInputDialog(
            text="Ingresá el nombre para la grabación:",
            title="Guardar grabación"
        )
        custom_name = dialog.get_input()
        if not custom_name:
            return
        custom_name = custom_name.strip()
        if not custom_name:
            return

        # Sanitizar nombre para ser compatible con Windows
        import re
        safe_name = re.sub(r'[\\/*?:"<>|]', "_", custom_name)

        self.out_dir.mkdir(parents=True, exist_ok=True)
        target_txt = self.out_dir / f"{safe_name}.txt"
        target_wav = self.out_dir / f"{safe_name}.wav"

        # Verificar si ya existe para confirmar sobreescritura
        if target_txt.exists() or (getattr(self.worker, "current_wav_path", None) and target_wav.exists()):
            overwrite = messagebox.askyesno(
                "Confirmar sobrescritura",
                f"Ya existe una grabación con el nombre '{safe_name}'.\n\n¿Querés sobrescribirla?",
                parent=self
            )
            if not overwrite:
                return

        # Guardar archivo de texto editado
        try:
            target_txt.write_text(text + "\n", encoding="utf-8")
        except Exception as exc:
            logger.exception("Failed to write transcript file: %s", exc)
            messagebox.showerror("Error", f"No se pudo guardar el archivo de texto: {exc}")
            return

        # Si hay un audio asociado, renombrarlo/moverlo al nuevo nombre
        import os
        import shutil
        wav_moved = False
        if getattr(self.worker, "current_wav_path", None) and os.path.exists(self.worker.current_wav_path):
            try:
                shutil.move(str(self.worker.current_wav_path), str(target_wav))
                self.worker.current_wav_path = target_wav
                wav_moved = True
            except Exception as exc:
                logger.exception("Failed to move WAV file: %s", exc)
                messagebox.showwarning(
                    "Advertencia",
                    f"Se guardó la transcripción pero no se pudo renombrar el archivo de audio:\n{exc}"
                )

        # Borrar el archivo de transcripción temporal original
        if getattr(self.worker, "current_transcript_path", None) and os.path.exists(self.worker.current_transcript_path):
            try:
                if target_txt != self.worker.current_transcript_path:
                    os.remove(self.worker.current_transcript_path)
            except Exception:
                pass
            self.worker.current_transcript_path = target_txt

        # Calcular duración
        duration_str = "00:00"
        if getattr(self.worker, "duration_seconds", 0) > 0:
            mins = int(self.worker.duration_seconds // 60)
            secs = int(self.worker.duration_seconds % 60)
            duration_str = f"{mins:02d}:{secs:02d}"

        # Guardar en base de datos para integrarlo con la pestaña "Historial"
        db_path = str(target_wav) if wav_moved else str(target_txt)
        db_friendly_name = safe_name
        db_lang = self.worker._locked_language or self.worker._language or ""

        from core.domain.entities import TranscriptionRecord
        try:
            config.repository.save(
                TranscriptionRecord(
                    file_path=db_path,
                    file_name=db_friendly_name,
                    duration=duration_str,
                    transcription=text,
                    summary="",
                    language=db_lang
                )
            )
            self._set_status(f"Guardado: {safe_name}")
            messagebox.showinfo("Éxito", f"Grabación guardada como '{safe_name}' y añadida al historial.")
        except Exception as exc:
            logger.exception("Failed to save to database: %s", exc)
            messagebox.showerror("Error", f"Se guardó el archivo en disco pero no se pudo indexar en el historial: {exc}")

    def _append(self, text: str):
        at_bottom = self.output.yview()[1] >= 0.999
        val = self.output.get("1.0", "end-1c")
        if val and not val.endswith(("\n", " ", "\t")):
            self.output.insert(tk.END, " " + text)
        else:
            self.output.insert(tk.END, text)
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
            msg = self.status_queue.get_nowait()
            if msg.startswith("WINSTT_SETUP:"):
                detail = msg[13:]
                self.after(
                    10,
                    lambda d=detail: messagebox.showinfo(
                        "Windows STT — Configuración necesaria",
                        f"{d}\n\nMás info: busca 'Speech' en Configuración de Windows.",
                    ),
                )
                self._set_status("Windows STT: requiere instalación")
            else:
                self._set_status(msg)
        while not self.text_queue.empty():
            self._append(self.text_queue.get_nowait())
        # Reactive sync of toggle button and spinner based on worker state
        if self.worker.is_running():
            if not getattr(self, "_controls_disabled", False):
                self._controls_disabled = True
                self.source_combo.configure(state="disabled")
                self.lang_combo.configure(state="disabled")
                self.translate_check.configure(state="disabled")
                self.record_check.configure(state="disabled")
                self.btn_change_folder.configure(state="disabled")
                self.btn_refresh.configure(state="disabled")
                self.btn_save.configure(state="disabled")
                self.summary_btn.configure(state="disabled")
                self.btn_clear.configure(state="disabled")

            if self.worker._stop.is_set():
                self.is_animating = False
                if self.spinner.is_spinning:
                    self.spinner.stop()
                    self.spinner.pack_forget()
                self.toggle_btn.configure(text="⌛ Deteniendo...", state="disabled")
                self.path_label.configure(text=f"Guardando en: {self.out_dir}")
            else:
                if not self.is_animating:
                    self.is_animating = True
                    self._init_wave_bars()
                    self._animate_wave()
                if not self.spinner.is_spinning:
                    self.spinner.start()
                    self.spinner.pack(side=tk.LEFT, padx=(12, 0))
                self.toggle_btn.configure(text="⏹   Detener", state="normal")
                # Show recording indicator
                source = self.source_var.get()
                is_rec = self.record_var.get()
                rec_tag = "  ⏺ REC" if is_rec else ""
                if GOOGLE_STT_LABEL in source:
                    self.path_label.configure(
                        text=f"🌐 Google STT activo{rec_tag}  →  {self.out_dir}",
                        text_color="#4ec98a"
                    )
                elif WINDOWS_STT_LABEL in source:
                    self.path_label.configure(
                        text=f"🖥️ Windows STT activo{rec_tag}  →  {self.out_dir}",
                        text_color="#4ec98a"
                    )
                else:
                    color = "#e0506a" if is_rec else "#4ec98a"
                    prefix = "🔴 Grabando audio + transcripción" if is_rec else "🎙️ Transcribiendo (sin grabar audio)"
                    self.path_label.configure(
                        text=f"{prefix}  →  {self.out_dir}",
                        text_color=color
                    )
        else:
            if getattr(self, "_controls_disabled", False):
                self._controls_disabled = False
                self.source_combo.configure(state="readonly")
                self.lang_combo.configure(state="readonly")
                self.translate_check.configure(state="normal")
                self.record_check.configure(state="normal")
                self.btn_change_folder.configure(state="normal")
                self.btn_refresh.configure(state="normal")
                self.btn_save.configure(state="normal")
                self.summary_btn.configure(state="normal")
                self.btn_clear.configure(state="normal")

            self.is_animating = False
            if self.spinner.is_spinning:
                self.spinner.stop()
                self.spinner.pack_forget()
            self.toggle_btn.configure(text="▶   Iniciar", state="normal")
            self.path_label.configure(
                text=f"Pulsa Iniciar para comenzar\n{self.out_dir}",
                text_color="#8A8F9E"
            )

        self.after(100, self._drain_queues)

    def stop_worker(self):
        self.worker.stop()
