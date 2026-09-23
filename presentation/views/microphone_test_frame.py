"""Interactive microphone diagnostics before starting an oral workflow."""

from __future__ import annotations

import threading
import tkinter as tk
import winsound

import customtkinter as ctk

import config
from core.errors import record
from infrastructure.services.microphone_test import (
    AUDIBLE_LEVEL,
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

# Techo de pantalla para una prueba entera. El servicio ya acota cada endpoint,
# así que esto solo cubre lo que queda afuera: la carga del modelo de Whisper y
# la transcripción. Sin este segundo cerrojo, cualquier bloqueo nuevo vuelve a
# dejar el botón deshabilitado y la app aparenta estar colgada.
_TEST_TIMEOUT_MS = 90_000


class MicrophoneTestFrame(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color=BG)
        self.service = MicrophoneTestService()
        self.result = None
        self._testing = False
        # Una prueba que venció ya devolvió el control al usuario: lo que llegue
        # tarde de ese intento no puede volver a pisar la pantalla.
        self._test_token = 0
        self._watchdog = None

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
        self._test_token += 1
        token = self._test_token
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
                self.after(
                    0,
                    lambda error=exc: self._show_error(
                        # Sin user_msg, record() devuelve el texto genérico de
                        # la bitácora y el diagnóstico del servicio — qué
                        # endpoint falló y por qué — no llega nunca a pantalla.
                        record(
                            "microfono.prueba",
                            error,
                            microfono=selected,
                            user_msg=str(error),
                        ),
                        token,
                    ),
                )
                return
            self.after(0, lambda: self._show_result(result, token))

        self._watchdog = self.after(
            _TEST_TIMEOUT_MS, lambda: self._on_timeout(token, selected)
        )
        threading.Thread(target=work, daemon=True).start()

    def _cancel_watchdog(self) -> None:
        if self._watchdog is not None:
            self.after_cancel(self._watchdog)
            self._watchdog = None

    def _on_timeout(self, token: int, microphone_name: str) -> None:
        if token != self._test_token or not self._testing:
            return
        self._watchdog = None
        # El intento vencido queda invalidado acá mismo: si el hilo revive más
        # tarde, su resultado ya no puede pisar la pantalla del usuario.
        self._test_token += 1
        # Un endpoint que se cuelga no levanta excepción, así que sin esto la
        # bitácora no registra nada y la falla queda invisible en Ajustes.
        self._show_error(
            record(
                "microfono.prueba",
                TimeoutError(
                    f"{microphone_name} no respondió en "
                    f"{_TEST_TIMEOUT_MS // 1000} segundos"
                ),
                microfono=microphone_name,
                user_msg=(
                    f"{microphone_name} no respondió. Probá con otro micrófono "
                    "o revisá Ajustes → Bitácora de errores."
                ),
            )
        )

    def _show_result(self, result, token: int | None = None) -> None:
        if token is not None and token != self._test_token:
            return
        self._cancel_watchdog()
        self._testing = False
        self.result = result
        self.test_button.configure(state="normal", text="●  Repetir prueba")
        self.refresh_button.configure(state="normal")
        self.play_button.configure(state="normal")
        self.level_bar.set(min(1.0, result.peak_level * 4.0))
        # Una toma muda no es lo mismo que una que no se entendió: el micrófono
        # captó bien y casi siempre significa que nadie habló. Decirlo como
        # "no se reconocieron palabras" manda a revisar el equipo equivocado.
        silent = result.mean_level < AUDIBLE_LEVEL
        # Una toma cortada engaña al control de nivel: un chasquido de 0,2 s
        # promedia muy por encima del umbral y el micrófono igual está roto.
        partial = result.is_partial
        if silent:
            understood = "No se captó audio: hablá más fuerte o subí el volumen de entrada."
        elif partial:
            understood = (
                "El micrófono cortó la grabación antes de tiempo. "
                "Revisá el cable o el puerto USB y probá de nuevo."
            )
        else:
            understood = result.transcription or "No se reconocieron palabras."
        self.transcription_box.configure(state="normal")
        self.transcription_box.delete("1.0", tk.END)
        self.transcription_box.insert("1.0", understood)
        self.transcription_box.configure(state="disabled")
        # Aprobar un micrófono que no captó nada es exactamente lo que esta
        # pestaña existe para evitar: quedaría de preferido en Resolver y
        # Práctica oral, y la falla reaparecería en medio de un examen.
        usable = not silent and not partial
        self.use_button.configure(state="normal" if usable else "disabled")
        if silent:
            headline = "Micrófono abierto, pero no entró señal"
            color = "#F6AD55"
        elif partial:
            headline = (
                f"Grabación incompleta · {result.captured_seconds:.1f} s de "
                f"{result.requested_seconds:.0f} s"
            )
            color = ERROR
        else:
            headline = "Señal recibida"
            color = SUCCESS if result.transcription else ERROR
        self.status_label.configure(
            text=(
                f"{headline} · nivel {result.mean_level:.4f} · "
                f"endpoint {result.endpoint_name}"
            ),
            text_color=color,
        )

    def _show_error(self, message: str, token: int | None = None) -> None:
        # Un intento viejo que falla tarde no puede tocar nada: cancelaría el
        # watchdog del intento en curso, lo marcaría como terminado y dejaría
        # que el usuario largue una tercera prueba sobre el mismo micrófono.
        if token is not None and token != self._test_token:
            return
        self._cancel_watchdog()
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
