"""Themed modal dialogs for Auditext — replaces native Windows messageboxes."""

from __future__ import annotations

import tkinter as tk
from typing import Literal

import customtkinter as ctk

BG = "#0B0C10"
PANEL = "#15161E"
PANEL_LIGHT = "#1A1B26"
PANEL_DARK = "#11121A"
TEXT = "#FFFFFF"
MUTED = "#8A8F9E"
ACCENT = "#7000FF"
ACCENT_HOVER = "#5900CC"
BORDER = "#2A2B36"
WARNING = "#F6AD55"
ERROR = "#E0506A"
ERROR_SOFT = "#2A1518"
INFO = "#63B3ED"

Kind = Literal["info", "warning", "error", "confirm"]


def _resolve_parent(parent: tk.Misc | None) -> tk.Misc:
    if parent is None:
        return tk._default_root  # type: ignore[attr-defined]
    return parent.winfo_toplevel()


def _center(win: ctk.CTkToplevel, parent: tk.Misc, width: int, height: int) -> None:
    win.update_idletasks()
    try:
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        x = px + max(0, (pw - width) // 2)
        y = py + max(0, (ph - height) // 2)
        win.geometry(f"{width}x{height}+{x}+{y}")
    except Exception:
        win.geometry(f"{width}x{height}")


def _accent_for(kind: Kind) -> tuple[str, str]:
    if kind == "warning":
        return WARNING, PANEL_LIGHT
    if kind == "error":
        return ERROR, ERROR_SOFT
    if kind == "confirm":
        return ACCENT, ACCENT_HOVER
    return INFO, PANEL_LIGHT


class _MessageDialog(ctk.CTkToplevel):
    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        message: str,
        *,
        kind: Kind = "info",
        buttons: Literal["ok", "yes_no", "yes_no_cancel"] = "ok",
    ):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.configure(fg_color=BG)
        self.result: bool | None = None

        accent, _ = _accent_for(kind)
        icon = {"info": "ℹ️", "warning": "⚠️", "error": "❌", "confirm": "❓"}.get(kind, "ℹ️")

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill=tk.X, padx=24, pady=(20, 8))
        ctk.CTkLabel(
            header, text=f"{icon}  {title}",
            font=("Segoe UI Semibold", 15), text_color=TEXT, anchor=tk.W,
        ).pack(fill=tk.X)

        card = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=10, border_color=BORDER, border_width=1)
        card.pack(fill=tk.BOTH, expand=True, padx=24, pady=(0, 8))
        ctk.CTkLabel(
            card, text=message, font=("Segoe UI", 12), text_color=MUTED,
            justify=tk.LEFT, wraplength=360, anchor=tk.NW,
        ).pack(fill=tk.BOTH, expand=True, padx=16, pady=14)

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill=tk.X, padx=24, pady=(8, 20))

        def close(value: bool | None = True):
            self.result = value
            self.destroy()

        if buttons == "ok":
            ctk.CTkButton(
                btn_row, text="Aceptar", font=("Segoe UI Semibold", 12),
                fg_color=ACCENT, text_color=TEXT, hover_color=ACCENT_HOVER,
                width=100, height=36, corner_radius=8, command=lambda: close(True),
            ).pack(side=tk.RIGHT)
            self.bind("<Return>", lambda _e: close(True))
            self.bind("<Escape>", lambda _e: close(True))
        elif buttons == "yes_no":
            ctk.CTkButton(
                btn_row, text="No", font=("Segoe UI Semibold", 12),
                fg_color=PANEL_LIGHT, text_color=TEXT, hover_color=BORDER,
                width=90, height=36, corner_radius=8, command=lambda: close(False),
            ).pack(side=tk.RIGHT)
            ctk.CTkButton(
                btn_row, text="Sí", font=("Segoe UI Semibold", 12),
                fg_color=ACCENT, text_color=TEXT, hover_color=ACCENT_HOVER,
                width=90, height=36, corner_radius=8, command=lambda: close(True),
            ).pack(side=tk.RIGHT, padx=(0, 8))
            self.bind("<Return>", lambda _e: close(True))
            self.bind("<Escape>", lambda _e: close(False))
        else:
            ctk.CTkButton(
                btn_row, text="Cancelar", font=("Segoe UI Semibold", 12),
                fg_color=PANEL_LIGHT, text_color=TEXT, hover_color=BORDER,
                width=100, height=36, corner_radius=8, command=lambda: close(None),
            ).pack(side=tk.RIGHT)
            ctk.CTkButton(
                btn_row, text="No", font=("Segoe UI Semibold", 12),
                fg_color=PANEL_LIGHT, text_color=TEXT, hover_color=BORDER,
                width=80, height=36, corner_radius=8, command=lambda: close(False),
            ).pack(side=tk.RIGHT, padx=(0, 8))
            ctk.CTkButton(
                btn_row, text="Sí", font=("Segoe UI Semibold", 12),
                fg_color=ACCENT, text_color=TEXT, hover_color=ACCENT_HOVER,
                width=80, height=36, corner_radius=8, command=lambda: close(True),
            ).pack(side=tk.RIGHT, padx=(0, 8))
            self.bind("<Escape>", lambda _e: close(None))

        lines = max(2, message.count("\n") + 1, len(message) // 48 + 1)
        height = min(360, 168 + lines * 18)
        _center(self, parent, 420, height)
        self.transient(parent)
        self.grab_set()
        self.focus_force()


class _InputDialog(ctk.CTkToplevel):
    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        prompt: str,
        initial: str = "",
        ok_text: str = "Aceptar",
        cancel_text: str = "Cancelar",
    ):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.configure(fg_color=BG)
        self.result: str | None = None

        ctk.CTkLabel(
            self, text=title, font=("Segoe UI Semibold", 15), text_color=TEXT,
        ).pack(anchor=tk.W, padx=24, pady=(20, 6))
        ctk.CTkLabel(
            self, text=prompt, font=("Segoe UI", 12), text_color=MUTED,
            wraplength=360, justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=24, pady=(0, 10))

        self.entry = ctk.CTkEntry(
            self, fg_color=PANEL_DARK, border_color=BORDER, text_color=TEXT,
            width=360, height=36, corner_radius=8,
        )
        self.entry.pack(padx=24, pady=(0, 8))
        if initial:
            self.entry.insert(0, initial)
            self.entry.select_range(0, tk.END)
        self.entry.focus()

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill=tk.X, padx=24, pady=(16, 20))

        def on_ok(_event=None):
            value = self.entry.get().strip()
            self.result = value if value else None
            self.destroy()

        def on_cancel(_event=None):
            self.destroy()

        ctk.CTkButton(
            btn_row, text=ok_text, font=("Segoe UI Semibold", 12),
            fg_color=ACCENT, text_color=TEXT, hover_color=ACCENT_HOVER,
            width=100, height=36, corner_radius=8, command=on_ok,
        ).pack(side=tk.LEFT)
        ctk.CTkButton(
            btn_row, text=cancel_text, font=("Segoe UI Semibold", 12),
            fg_color=PANEL_LIGHT, text_color=TEXT, hover_color=BORDER,
            width=100, height=36, corner_radius=8, command=on_cancel,
        ).pack(side=tk.RIGHT)

        self.bind("<Return>", on_ok)
        self.bind("<Escape>", on_cancel)

        _center(self, parent, 420, 220)
        self.transient(parent)
        self.grab_set()
        self.focus_force()


class _ExportChoiceDialog(ctk.CTkToplevel):
    def __init__(
        self,
        parent: tk.Misc,
        options: list[tuple[str, str, str, str]],
    ):
        super().__init__(parent)
        self.title("Exportar")
        self.resizable(False, False)
        self.configure(fg_color=BG)
        self.result: str | None = None

        ctk.CTkLabel(
            self, text="📥  Exportar",
            font=("Segoe UI Semibold", 15), text_color=TEXT,
        ).pack(anchor=tk.W, padx=24, pady=(20, 4))

        ctk.CTkLabel(
            self, text="¿Qué querés exportar del registro seleccionado?",
            font=("Segoe UI", 12), text_color=MUTED,
        ).pack(anchor=tk.W, padx=24, pady=(0, 14))

        def choose(value: str | None):
            self.result = value
            self.destroy()

        for key, label, icon, color in options:
            ctk.CTkButton(
                self, text=f"{icon}  {label}",
                font=("Segoe UI Semibold", 12),
                fg_color=PANEL_LIGHT, text_color=color,
                hover_color=BORDER, anchor=tk.W,
                height=42, corner_radius=8,
                command=lambda v=key: choose(v),
            ).pack(fill=tk.X, padx=24, pady=4)

        cancel_frame = ctk.CTkFrame(self, fg_color="transparent")
        cancel_frame.pack(fill=tk.X, padx=24, pady=(12, 20))

        ctk.CTkButton(
            cancel_frame, text="Cancelar", font=("Segoe UI Semibold", 12),
            fg_color=PANEL_LIGHT, text_color=TEXT, hover_color=BORDER,
            height=36, corner_radius=8, command=lambda: choose(None),
        ).pack(side=tk.RIGHT)

        self.bind("<Escape>", lambda _e: choose(None))
        self.protocol("WM_DELETE_WINDOW", lambda: choose(None))

        height = 140 + len(options) * 54
        _center(self, parent, 380, min(height, 360))
        self.transient(parent)
        self.grab_set()
        self.focus_force()


def ask_export_choice(
    parent: tk.Misc | None,
    options: list[tuple[str, str, str, str]],
) -> str | None:
    dlg = _ExportChoiceDialog(_resolve_parent(parent), options)
    root = _resolve_parent(parent)
    root.wait_window(dlg)
    return dlg.result


def _show(parent: tk.Misc | None, dialog: ctk.CTkToplevel):
    root = _resolve_parent(parent)
    root.wait_window(dialog)
    return dialog.result


def show_info(parent: tk.Misc | None, title: str, message: str) -> None:
    dlg = _MessageDialog(_resolve_parent(parent), title, message, kind="info")
    _show(parent, dlg)


def show_warning(parent: tk.Misc | None, title: str, message: str) -> None:
    dlg = _MessageDialog(_resolve_parent(parent), title, message, kind="warning")
    _show(parent, dlg)


def show_error(parent: tk.Misc | None, title: str, message: str) -> None:
    dlg = _MessageDialog(_resolve_parent(parent), title, message, kind="error")
    _show(parent, dlg)


def ask_yes_no(parent: tk.Misc | None, title: str, message: str) -> bool:
    dlg = _MessageDialog(
        _resolve_parent(parent), title, message, kind="confirm", buttons="yes_no",
    )
    return bool(_show(parent, dlg))


def ask_yes_no_cancel(parent: tk.Misc | None, title: str, message: str) -> bool | None:
    dlg = _MessageDialog(
        _resolve_parent(parent), title, message, kind="confirm", buttons="yes_no_cancel",
    )
    return _show(parent, dlg)


def ask_input(
    parent: tk.Misc | None,
    title: str,
    prompt: str,
    initial: str = "",
    *,
    ok_text: str = "Aceptar",
    cancel_text: str = "Cancelar",
) -> str | None:
    dlg = _InputDialog(
        _resolve_parent(parent), title, prompt, initial,
        ok_text=ok_text, cancel_text=cancel_text,
    )
    return _show(parent, dlg)
