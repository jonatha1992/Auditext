"""Lightweight tooltip for CustomTkinter widgets.

Usage:
    from tooltip import Tooltip
    Tooltip(widget, "Explanation text")
"""
import tkinter as tk
import customtkinter as ctk


class Tooltip:
    """Shows a dark themed tooltip on hover after a short delay."""

    _BG = "#1E1F29"
    _FG = "#C5C7D4"
    _BORDER = "#3A3B4A"
    _DELAY_MS = 600
    _WRAP = 260

    def __init__(self, widget: tk.Widget, text: str):
        self._widget = widget
        self._text = text
        self._tip_win: tk.Toplevel | None = None
        self._after_id: str | None = None

        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._cancel, add="+")
        widget.bind("<ButtonPress>", self._cancel, add="+")
        widget.bind("<Destroy>", self._cancel, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self._after_id = self._widget.after(self._DELAY_MS, self._show)

    def _cancel(self, _event=None):
        if self._after_id:
            try:
                self._widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        self._hide()

    def _show(self):
        if self._tip_win:
            return
        try:
            x = self._widget.winfo_rootx() + 10
            y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        except Exception:
            return

        self._tip_win = tw = tk.Toplevel(self._widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.attributes("-topmost", True)

        outer = tk.Frame(tw, bg=self._BORDER, bd=1)
        outer.pack()

        inner = tk.Frame(outer, bg=self._BG, padx=10, pady=6)
        inner.pack(fill=tk.BOTH)

        tk.Label(
            inner,
            text=self._text,
            bg=self._BG,
            fg=self._FG,
            font=("Segoe UI", 10),
            wraplength=self._WRAP,
            justify=tk.LEFT,
        ).pack()

    def _hide(self):
        if self._tip_win:
            try:
                self._tip_win.destroy()
            except Exception:
                pass
            self._tip_win = None
