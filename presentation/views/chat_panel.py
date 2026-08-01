"""Ask follow-up questions about a transcription, inside the summary window.

A summary always leaves something out. This panel exists so recovering it does
not mean pasting a two-hour transcript into another tool: questions are answered
here, against the full transcription.

Every turn is written to SQLite as it happens — same as the transcription and
the summary — so the conversation survives closing the window.
"""

from __future__ import annotations

import threading
import tkinter as tk

import customtkinter as ctk

import config
from config import logger
from infrastructure.services import transcript_chat

COLOR_PANEL = "#15161E"
COLOR_PANEL_LIGHT = "#1A1B26"
COLOR_TEXT_FG = "#FFFFFF"
COLOR_MUTED = "#8A8F9E"
COLOR_ACCENT = "#7000FF"
COLOR_ACCENT_HOVER = "#5900CC"
COLOR_BORDER = "#2A2B36"
COLOR_INPUT_BG = "#11121A"

_PLACEHOLDER = (
    "Preguntá lo que el resumen no cubrió. Se responde con la transcripción "
    "completa, no con el resumen."
)


class TranscriptChatPanel(ctk.CTkFrame):
    def __init__(self, master, transcription: str, file_path: str | None):
        super().__init__(master, fg_color="transparent")
        self._transcription = transcription or ""
        self._file_path = file_path or ""
        self._busy = False
        # In-memory source of truth. SQLite mirrors it, but a transcription that
        # was never saved has no file_path to store under and would otherwise
        # lose every turn as soon as it was rendered.
        self._messages: list[dict] = []

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill=tk.X, pady=(0, 6))
        ctk.CTkLabel(
            header,
            text="Preguntas sobre la transcripción",
            font=("Segoe UI Semibold", 14),
            text_color=COLOR_TEXT_FG,
        ).pack(side=tk.LEFT)
        self._status = ctk.CTkLabel(
            header, text="", font=("Segoe UI", 11), text_color=COLOR_MUTED
        )
        self._status.pack(side=tk.RIGHT)

        self._history_box = ctk.CTkTextbox(
            self,
            fg_color=COLOR_INPUT_BG,
            text_color=COLOR_TEXT_FG,
            font=("Segoe UI", 12),
            corner_radius=8,
            border_width=0,
            height=180,
        )
        self._history_box.pack(fill=tk.BOTH, expand=True)
        self._history_box.configure(state="disabled")

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill=tk.X, pady=(8, 0))

        self._entry = ctk.CTkEntry(
            row,
            placeholder_text="Escribí tu pregunta...",
            fg_color=COLOR_INPUT_BG,
            text_color=COLOR_TEXT_FG,
            border_color=COLOR_BORDER,
            height=36,
            corner_radius=8,
        )
        self._entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._entry.bind("<Return>", lambda _e: self._send())

        self._send_btn = ctk.CTkButton(
            row,
            text="Preguntar",
            font=("Segoe UI Semibold", 12),
            fg_color=COLOR_ACCENT,
            text_color=COLOR_TEXT_FG,
            hover_color=COLOR_ACCENT_HOVER,
            width=100,
            height=36,
            corner_radius=8,
            command=self._send,
        )
        self._send_btn.pack(side=tk.LEFT, padx=(8, 0))

        self._clear_btn = ctk.CTkButton(
            row,
            text="Limpiar",
            font=("Segoe UI Semibold", 12),
            fg_color=COLOR_PANEL_LIGHT,
            text_color=COLOR_TEXT_FG,
            hover_color=COLOR_BORDER,
            width=80,
            height=36,
            corner_radius=8,
            command=self._clear,
        )
        self._clear_btn.pack(side=tk.LEFT, padx=(8, 0))

        self._load_history()

    # ---------- persistence ----------

    def _stored_messages(self) -> list[dict]:
        if not (self._file_path and config.repository):
            return []
        try:
            return config.repository.get_chat_messages(self._file_path)
        except Exception as exc:
            logger.error("No se pudo leer el chat guardado: %s", exc)
            return []

    def _append(self, role: str, content: str) -> None:
        """Record a turn in memory and mirror it to SQLite when possible."""
        self._messages.append({"role": role, "content": content})
        if not (self._file_path and config.repository):
            return
        try:
            config.repository.add_chat_message(self._file_path, role, content)
        except Exception as exc:
            logger.error("No se pudo guardar el mensaje de chat: %s", exc)

    def _load_history(self) -> None:
        self._messages = self._stored_messages()
        if not self._messages:
            self._set_history_text(_PLACEHOLDER, muted=True)
            return
        self._render()

    # ---------- rendering ----------

    def _set_history_text(self, text: str, muted: bool = False) -> None:
        self._history_box.configure(state="normal")
        self._history_box.delete("1.0", tk.END)
        self._history_box.insert("1.0", text)
        self._history_box.configure(
            state="disabled", text_color=COLOR_MUTED if muted else COLOR_TEXT_FG
        )

    def _render(self) -> None:
        blocks = [
            f"{'Vos' if m.get('role') == 'user' else 'AudioText'}:\n{m.get('content', '')}"
            for m in self._messages
        ]
        self._set_history_text("\n\n".join(blocks))
        self._history_box.see(tk.END)

    def _set_busy(self, busy: bool, status: str = "") -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        self._send_btn.configure(state=state)
        self._clear_btn.configure(state=state)
        self._status.configure(text=status)

    # ---------- actions ----------

    def _clear(self) -> None:
        if self._file_path and config.repository:
            try:
                config.repository.clear_chat(self._file_path)
            except Exception as exc:
                logger.error("No se pudo borrar el chat: %s", exc)
        self._messages = []
        self._set_history_text(_PLACEHOLDER, muted=True)

    def _send(self) -> None:
        if self._busy:
            return
        question = self._entry.get().strip()
        if not question:
            return

        # History is captured before the new question is appended: the provider
        # takes prior turns separately from the question being asked.
        history = transcript_chat.build_history(self._messages)
        self._entry.delete(0, tk.END)
        self._append("user", question)
        self._render()
        self._set_busy(True, "Pensando...")

        def work():
            try:
                answer = transcript_chat.ask(self._transcription, question, history)
            except Exception as exc:
                answer = f"[Error] {exc}"
            self.after(0, lambda a=answer: self._finish(a))

        threading.Thread(target=work, daemon=True).start()

    def _finish(self, answer: str) -> None:
        self._append("assistant", answer)
        self._render()
        self._set_busy(False)
