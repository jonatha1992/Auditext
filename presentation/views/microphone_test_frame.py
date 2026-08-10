"""Interactive microphone diagnostics before starting an oral workflow."""

from __future__ import annotations

import threading
import tkinter as tk
import winsound

import customtkinter as ctk

import config
from infrastructure.services.microphone_test import (
    MicrophoneTestService,
    load_preferred_microphone,
    save_preferred_microphone,
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


class MicrophoneTestFrame(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color=BG)
        self.service = MicrophoneTestService()
        self.result = None
        self._testing = False

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill=tk.X, padx=24, pady=(20, 10))
        ctk.CTkLabel(
            header,
            text="🎤  Probar micrófono",
            font=("Segoe UI Semibold", 20),
            text_color=TEXT,
        ).pack(anchor=tk.W)
        ctk.CTkLabel(
            header,
            text=(
                "Comprobá señal, reproducción y transcripción antes de usar "
                "En vivo, Resolver o Práctica oral."
            ),
            font=("Segoe UI", 11),
            text_color=MUTED,
        ).pack(anchor=tk.W, pady=(4, 0))

        setup = ctk.CTkFrame(
            self, fg_color=PANEL, corner_radius=12,
            border_color=BORDER, border_width=1,
        )
        setup.pack(fill=tk.X, padx=24, pady=8)
        ctk.CTkLabel(
            setup, text="MICRÓFONO A PROBAR",
            font=("Segoe UI Semibold", 9), text_color="#63B3ED",
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 3))
        self.microphone_var = tk.StringVar()
        self.microphone_combo = ctk.CTkComboBox(
            setup, variable=self.microphone_var, state="readonly",
            fg_color=PANEL_DARK, border_color=BORDER, button_color=BORDER,
            dropdown_fg_color=PANEL, dropdown_hover_color=ACCENT,
            width=520,
        )
        self.microphone_combo.grid(
            row=1, column=0, sticky="ew", padx=16, pady=(0, 14)
        )
        setup.grid_columnconfigure(0, weight=1)
        self.refresh_button = ctk.CTkButton(
            setup, text="↻  Actualizar", width=110, height=34,
            command=self.refresh_devices, fg_color=PANEL_DARK,
            hover_color="#20212D", border_color=BORDER, border_width=1,
        )
        self.refresh_button.grid(row=1, column=1, padx=(0, 8), pady=(0, 14))
        self.test_button = ctk.CTkButton(
            setup, text="●  Grabar prueba de 5 segundos", width=220, height=34,
            command=self.start_test, fg_color=ACCENT, hover_color=ACCENT_HOVER,
        )
        self.test_button.grid(row=1, column=2, padx=(0, 16), pady=(0, 14))

        result_card = ctk.CTkFrame(
            self, fg_color=PANEL, corner_radius=12,
            border_color=BORDER, border_width=1,
        )
        result_card.pack(fill=tk.BOTH, expand=True, padx=24, pady=8)
        self.status_label = ctk.CTkLabel(
            result_card,
            text="Elegí un micrófono y hablá durante toda la prueba.",
            font=("Segoe UI Semibold", 12), text_color=MUTED,
        )
        self.status_label.pack(anchor=tk.W, padx=16, pady=(14, 6))
        self.level_bar = ctk.CTkProgressBar(
            result_card, progress_color=SUCCESS, fg_color=PANEL_DARK,
        )
        self.level_bar.pack(fill=tk.X, padx=16, pady=(0, 14))
        self.level_bar.set(0)
        ctk.CTkLabel(
            result_card, text="TRANSCRIPCIÓN DE PRUEBA",
            font=("Segoe UI Semibold", 9), text_color="#A78BFA",
        ).pack(anchor=tk.W, padx=16)
        self.transcription_box = ctk.CTkTextbox(
            result_card, height=150, fg_color=PANEL_DARK, text_color=TEXT,
            border_width=1, border_color=BORDER, corner_radius=8,
            font=("Segoe UI", 13),
        )
        self.transcription_box.pack(
            fill=tk.BOTH, expand=True, padx=16, pady=(5, 14)
        )
        self.transcription_box.insert(
            "1.0", "Acá aparecerá exactamente lo que Auditext entendió."
        )
        self.transcription_box.configure(state="disabled")

        actions = ctk.CTkFrame(result_card, fg_color="transparent")
        actions.pack(fill=tk.X, padx=16, pady=(0, 16))
        self.play_button = ctk.CTkButton(
            actions, text="▶  Escuchar grabación", width=170,
            command=self.play_result, state="disabled",
            fg_color=PANEL_DARK, hover_color="#20212D",
            border_color=BORDER, border_width=1,
        )
        self.play_button.pack(side=tk.LEFT)
        self.use_button = ctk.CTkButton(
            actions, text="✓  Usar este micrófono", width=180,
            command=self.use_microphone, state="disabled",
            fg_color=SUCCESS, hover_color="#38A169", text_color="#08140E",
        )
        self.use_button.pack(side=tk.RIGHT)

        self.refresh_devices()

    def refresh_devices(self) -> None:
        try:
            names = self.service.list_microphones()
        except Exception as exc:
            names = []
            self.status_label.configure(
                text=f"No se pudieron listar micrófonos: {exc}", text_color=ERROR
            )
        self.microphone_combo.configure(values=names or ["Sin micrófonos"])
        preferred = load_preferred_microphone(config.repository)
        selected = preferred if preferred in names else (names[0] if names else "Sin micrófonos")
        self.microphone_var.set(selected)

    def start_test(self) -> None:
        if self._testing or self.microphone_var.get() == "Sin micrófonos":
            return
        self._testing = True
        self.result = None
        self.test_button.configure(state="disabled", text="●  Grabando… hablá ahora")
        self.refresh_button.configure(state="disabled")
        self.play_button.configure(state="disabled")
        self.use_button.configure(state="disabled")
        self.level_bar.set(0)
        self.status_label.configure(
            text="Grabando 5 segundos. Decí: «Hola, estoy probando mi micrófono».",
            text_color="#F6AD55",
        )
        selected = self.microphone_var.get()

        def work():
            try:
                result = self.service.probe(selected, duration_seconds=5)
            except Exception as exc:
                self.after(0, lambda message=str(exc): self._show_error(message))
                return
            self.after(0, lambda: self._show_result(result))

        threading.Thread(target=work, daemon=True).start()

    def _show_result(self, result) -> None:
        self._testing = False
        self.result = result
        self.test_button.configure(state="normal", text="●  Repetir prueba")
        self.refresh_button.configure(state="normal")
        self.play_button.configure(state="normal")
        self.use_button.configure(state="normal")
        self.level_bar.set(min(1.0, result.peak_level * 4.0))
        understood = result.transcription or "No se reconocieron palabras."
        self.transcription_box.configure(state="normal")
        self.transcription_box.delete("1.0", tk.END)
        self.transcription_box.insert("1.0", understood)
        self.transcription_box.configure(state="disabled")
        color = SUCCESS if result.transcription else ERROR
        self.status_label.configure(
            text=(
                f"Señal recibida · nivel {result.mean_level:.4f} · "
                f"endpoint {result.endpoint_name}"
            ),
            text_color=color,
        )

    def _show_error(self, message: str) -> None:
        self._testing = False
        self.test_button.configure(state="normal", text="●  Reintentar prueba")
        self.refresh_button.configure(state="normal")
        self.status_label.configure(text=message, text_color=ERROR)

    def play_result(self) -> None:
        if self.result is not None:
            winsound.PlaySound(
                str(self.result.playback_path),
                winsound.SND_FILENAME | winsound.SND_ASYNC,
            )

    def use_microphone(self) -> None:
        if self.result is None:
            return
        save_preferred_microphone(config.repository, self.result.microphone_name)
        self.status_label.configure(
            text=(
                f"✓ {self.result.microphone_name} quedó seleccionado para "
                "Resolver y Práctica oral."
            ),
            text_color=SUCCESS,
        )
