import os
import threading
import time
import tkinter as tk
import customtkinter as ctk
from infrastructure.services import summarizer
from .spinner import Spinner
from infrastructure.audio.reproductor import reproductor
from presentation.controllers.funcionalidad import (
    es_archivo_multimedia_exportable,
    exportar_audio,
    exportar_resumen,
    exportar_transcripcion,
    obtener_duracion_audio,
)
from .tooltip import Tooltip
from .app_dialog import (
    ask_export_choice,
    ask_input,
    ask_yes_no,
    show_error,
    show_info,
    show_warning,
)


COLOR_BG          = "#0B0C10"
COLOR_PANEL       = "#15161E"
COLOR_PANEL_LIGHT = "#1A1B26"
COLOR_PANEL_DARK  = "#11121A"
COLOR_TEXT_FG     = "#FFFFFF"
COLOR_MUTED       = "#8A8F9E"
COLOR_ACCENT      = "#7000FF"
COLOR_ACCENT_HOVER= "#5900CC"
COLOR_BORDER      = "#2A2B36"
COLOR_CARD_HOVER  = "#23243A"
COLOR_CARD_SEL    = "#2D1569"  # dark purple for selected card bg
COLOR_CHIP        = "#252633"
COLOR_ICON_HOVER  = "#20212D"
COLOR_DANGER      = "#E0506A"   # soft rose (matches interview ERROR)
COLOR_DANGER_SOFT = "#2A1518"  # quiet delete hover — never solid alarm red
COLOR_GREEN       = "#48BB78"

class HistorialFrame(ctk.CTkFrame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        self.records       = []
        self.selected_record = None
        self._card_widgets = []
        self._selected_idx = -1

        self._slider_dragging = False
        self._build_ui()
        self.load_history()
        
        # Responsive layout adjustment logic
        self.last_historial_width = [0]
        self._resize_after_id = None
        self.bind("<Configure>", self._on_historial_configure)
        
        self.after(100, self._tick_history_player)

    def _on_historial_configure(self, event):
        if event.widget != self:
            return
        new_w = event.width
        if new_w < 400:
            return
        if self._resize_after_id is not None:
            self.after_cancel(self._resize_after_id)
        self._resize_after_id = self.after(150, lambda w=new_w: self._apply_layout(w))

    def _apply_layout(self, new_w):
        self._resize_after_id = None
        if new_w == self.last_historial_width[0]:
            return
        self.last_historial_width[0] = new_w

        if new_w < 850:
            # 1-Column Layout: Stack left_col (list) and right_col (details) vertically
            self.left_col.pack_forget()
            self.right_col.pack_forget()
            
            # Constrain height of left_col to 180 so details gets vertical space
            self.left_col.configure(width=new_w - 48, height=180)
            self.left_col.pack(side=tk.TOP, fill=tk.BOTH, expand=False, pady=(0, 10))
            self.right_col.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(10, 0))

            # Stack transcript panel and summary panel vertically
            self.pane_text.pack_forget()
            self.pane_summary.pack_forget()
            self.pane_text.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(0, 8))
            self.pane_summary.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(8, 0))

            # Wrapped Action Buttons Frame (Grid)
            self.btn_rename.pack_forget()
            self.btn_copy_text.pack_forget()
            self.btn_copy_summary.pack_forget()
            self.btn_export.pack_forget()
            self.btn_delete.pack_forget()

            self.btn_rename.grid(row=0, column=1, padx=6, pady=4)
            self.btn_copy_text.grid(row=0, column=2, padx=6, pady=4)
            self.btn_copy_summary.grid(row=0, column=3, padx=6, pady=4)
            self.btn_export.grid(row=1, column=1, padx=6, pady=4)
            self.btn_delete.grid(row=1, column=2, columnspan=2, padx=6, pady=4)

            self.action_row.grid_columnconfigure(0, weight=1)
            self.action_row.grid_columnconfigure(1, weight=0)
            self.action_row.grid_columnconfigure(2, weight=0)
            self.action_row.grid_columnconfigure(3, weight=0)
            self.action_row.grid_columnconfigure(4, weight=1)
        else:
            # 2-Column Layout: fixed left, expanding right
            self.left_col.pack_forget()
            self.right_col.pack_forget()
            
            # Body height is not fixed, let left_col stretch vertically
            master_height = self.left_col.master.winfo_height() if self.left_col.master else 500
            if master_height < 50:
                master_height = 500
            self.left_col.configure(width=260, height=master_height)
            self.left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=(0, 14))
            self.right_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            # Side-by-side transcript and summary panels
            self.pane_text.pack_forget()
            self.pane_summary.pack_forget()
            self.pane_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
            self.pane_summary.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))

            # Single Horizontal Row for Buttons
            self.btn_rename.grid_forget()
            self.btn_copy_text.grid_forget()
            self.btn_copy_summary.grid_forget()
            self.btn_export.grid_forget()
            self.btn_delete.grid_forget()

            self.btn_rename.pack(side=tk.LEFT, padx=(0, 4))
            self.btn_copy_text.pack(side=tk.LEFT, padx=(0, 4))
            self.btn_copy_summary.pack(side=tk.LEFT, padx=(0, 4))
            self.btn_export.pack(side=tk.LEFT, padx=(0, 4))
            self.btn_delete.pack(side=tk.RIGHT)

            self.action_row.grid_columnconfigure(0, weight=0)
            self.action_row.grid_columnconfigure(1, weight=0)
            self.action_row.grid_columnconfigure(2, weight=0)
            self.action_row.grid_columnconfigure(3, weight=0)
            self.action_row.grid_columnconfigure(4, weight=0)

    # ------------------------------------------------------------------
    # UI BUILD
    # ------------------------------------------------------------------

    def _build_ui(self):
        # Header row
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill=tk.X, padx=24, pady=(20, 4))

        ctk.CTkLabel(
            hdr, text="📜  Historial de transcripciones",
            font=("Segoe UI Semibold", 22), text_color=COLOR_TEXT_FG
        ).pack(side=tk.LEFT, anchor=tk.W)

        self.btn_refresh = ctk.CTkButton(
            hdr, text="↻   Actualizar", font=("Segoe UI Semibold", 12),
            fg_color=COLOR_PANEL_LIGHT, text_color="#63B3ED",
            hover_color=COLOR_BORDER, width=120, height=32, corner_radius=8,
            command=self.load_history
        )
        self.btn_refresh.pack(side=tk.RIGHT)

        ctk.CTkLabel(
            self, text="🔎  Revisá, exportá y generá resúmenes de tus transcripciones pasadas.",
            font=("Segoe UI", 12), text_color=COLOR_MUTED
        ).pack(anchor=tk.W, padx=24, pady=(0, 14))

        # Body
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill=tk.BOTH, expand=True, padx=24, pady=(0, 12))

        # Left column — fixed width card list
        self.left_col = ctk.CTkFrame(body, fg_color="transparent", width=260)
        self.left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=(0, 14))
        self.left_col.pack_propagate(False)

        ctk.CTkLabel(
            self.left_col, text="📂  TRANSCRIPCIONES GUARDADAS",
            font=("Segoe UI Semibold", 10), text_color="#A78BFA"
        ).pack(anchor=tk.W, pady=(0, 6))

        # Search Bar
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self._on_search_change)
        
        self.search_entry = ctk.CTkEntry(
            self.left_col, placeholder_text="🔍 Buscar...", textvariable=self.search_var,
            fg_color="#11121A", border_color=COLOR_BORDER, text_color="#FFFFFF",
            height=32, corner_radius=8
        )
        self.search_entry.pack(fill=tk.X, pady=(0, 10))

        self.card_scroll = ctk.CTkScrollableFrame(
            self.left_col, fg_color=COLOR_PANEL, corner_radius=12,
            border_color=COLOR_BORDER, border_width=1,
            scrollbar_button_color=COLOR_BORDER,
            scrollbar_button_hover_color=COLOR_ACCENT
        )
        self.card_scroll.pack(fill=tk.BOTH, expand=True)

        # Right column
        self.right_col = ctk.CTkFrame(body, fg_color="transparent")
        self.right_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Placeholder (shown when no record selected)
        self.placeholder_frame = ctk.CTkFrame(
            self.right_col, fg_color=COLOR_PANEL, corner_radius=12,
            border_color=COLOR_BORDER, border_width=1
        )
        self.placeholder_frame.pack(fill=tk.BOTH, expand=True)

        ctk.CTkLabel(
            self.placeholder_frame,
            text="📜\n\nSeleccioná una transcripción para ver sus detalles.",
            font=("Segoe UI Semibold", 13), text_color="#A78BFA",
            justify=tk.CENTER
        ).pack(expand=True)

        # Detail frame (hidden until a card is selected)
        self.details_frame = ctk.CTkFrame(self.right_col, fg_color="transparent")
        self._build_detail_ui()

    def _build_detail_ui(self):
        df = self.details_frame

        # Title + metadata (2 rows)
        dh = ctk.CTkFrame(df, fg_color="transparent")
        dh.pack(fill=tk.X, pady=(0, 10))

        self.label_detail_title = ctk.CTkLabel(
            dh, text="", font=("Segoe UI Semibold", 16),
            text_color=COLOR_TEXT_FG, anchor=tk.W
        )
        self.label_detail_title.pack(anchor=tk.W)

        self.label_detail_meta1 = ctk.CTkLabel(
            dh, text="", font=("Segoe UI", 11),
            text_color=COLOR_MUTED, anchor=tk.W
        )
        self.label_detail_meta1.pack(anchor=tk.W, pady=(2, 0))

        self.label_detail_meta2 = ctk.CTkLabel(
            dh, text="", font=("Segoe UI", 11),
            text_color=COLOR_MUTED, anchor=tk.W
        )
        self.label_detail_meta2.pack(anchor=tk.W)

        # Player Frame for audio files
        self.player_frame = ctk.CTkFrame(
            df, fg_color=COLOR_PANEL, corner_radius=12,
            border_color=COLOR_BORDER, border_width=1
        )
        
        play_btn_frame = ctk.CTkFrame(self.player_frame, fg_color="transparent")
        play_btn_frame.pack(side=tk.LEFT, padx=(12, 6), pady=8)
        
        self.btn_play_history = ctk.CTkButton(
            play_btn_frame, text="▶", font=("Segoe UI Semibold", 12),
            fg_color=COLOR_ACCENT, text_color="#FFFFFF", hover_color=COLOR_ACCENT_HOVER,
            width=32, height=32, corner_radius=16,
            command=self._play_pause_history
        )
        self.btn_play_history.pack()

        self.btn_rewind_history = ctk.CTkButton(
            self.player_frame, text="⏪", font=("Segoe UI", 12),
            fg_color="transparent", text_color=COLOR_TEXT_FG, hover_color=COLOR_PANEL_LIGHT,
            width=32, height=32, corner_radius=16,
            command=lambda: self._seek_history(-5)
        )
        self.btn_rewind_history.pack(side=tk.LEFT, padx=4)

        self.btn_forward_history = ctk.CTkButton(
            self.player_frame, text="⏩", font=("Segoe UI", 12),
            fg_color="transparent", text_color=COLOR_TEXT_FG, hover_color=COLOR_PANEL_LIGHT,
            width=32, height=32, corner_radius=16,
            command=lambda: self._seek_history(5)
        )
        self.btn_forward_history.pack(side=tk.LEFT, padx=4)

        self.lbl_time_history = ctk.CTkLabel(
            self.player_frame, text="00:00 / 00:00",
            font=("Segoe UI", 11), text_color=COLOR_TEXT_FG
        )
        self.lbl_time_history.pack(side=tk.LEFT, padx=(12, 12))

        self.slider_history = ctk.CTkSlider(
            self.player_frame, from_=0, to=100,
            fg_color="#11121A", progress_color=COLOR_ACCENT, button_color=COLOR_ACCENT,
            button_hover_color=COLOR_ACCENT_HOVER, height=14,
            command=self._on_slider_change
        )
        self.slider_history.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 16))
        
        def on_slider_press(event):
            self._slider_dragging = True
            
        self.slider_history.bind("<ButtonPress-1>", on_slider_press)
        self.slider_history.bind("<ButtonRelease-1>", self._on_slider_release)

        # Two side-by-side panels
        self.split_details = ctk.CTkFrame(df, fg_color="transparent")
        self.split_details.pack(fill=tk.BOTH, expand=True)

        # — Transcript panel —
        self.pane_text = ctk.CTkFrame(
            self.split_details, fg_color=COLOR_PANEL, corner_radius=12,
            border_color=COLOR_BORDER, border_width=1
        )
        self.pane_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        txt_hdr = ctk.CTkFrame(self.pane_text, fg_color="transparent")
        txt_hdr.pack(fill=tk.X, padx=14, pady=(12, 4))

        ctk.CTkLabel(
            txt_hdr, text="📝  TEXTO TRANSCRITO",
            font=("Segoe UI Semibold", 10), text_color="#63B3ED"
        ).pack(side=tk.LEFT)

        self.label_word_count = ctk.CTkLabel(
            txt_hdr, text="",
            font=("Segoe UI", 10), text_color=COLOR_MUTED
        )
        self.label_word_count.pack(side=tk.RIGHT)

        self.txt_transcription = ctk.CTkTextbox(
            self.pane_text, fg_color="#11121A", text_color=COLOR_TEXT_FG,
            font=("Segoe UI", 12), corner_radius=8, border_width=0
        )
        self.txt_transcription.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

        # — Summary panel —
        self.pane_summary = ctk.CTkFrame(
            self.split_details, fg_color=COLOR_PANEL, corner_radius=12,
            border_color=COLOR_BORDER, border_width=1
        )
        self.pane_summary.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))

        sum_hdr = ctk.CTkFrame(self.pane_summary, fg_color="transparent")
        sum_hdr.pack(fill=tk.X, padx=14, pady=(12, 4))

        ctk.CTkLabel(
            sum_hdr, text="✨  RESUMEN IA · GEMINI",
            font=("Segoe UI Semibold", 10), text_color="#F6E05E"
        ).pack(side=tk.LEFT)

        # Summary content area (either textbox or no-summary placeholder)
        self.summary_content_frame = ctk.CTkFrame(self.pane_summary, fg_color="transparent")
        self.summary_content_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 0))

        self.txt_summary = ctk.CTkTextbox(
            self.summary_content_frame, fg_color="#11121A", text_color=COLOR_TEXT_FG,
            font=("Segoe UI", 12), corner_radius=8, border_width=0
        )

        self.no_summary_frame = ctk.CTkFrame(
            self.summary_content_frame, fg_color="#11121A", corner_radius=8
        )

        self.spinner = Spinner(
            self.no_summary_frame, size=24, bg="#11121A",
            accent_color=COLOR_ACCENT, muted_color=COLOR_PANEL
        )

        self.label_no_summary = ctk.CTkLabel(
            self.no_summary_frame,
            text="Esta transcripción aún no tiene un resumen generado.",
            font=("Segoe UI", 12), text_color=COLOR_MUTED, justify=tk.CENTER
        )
        self.label_no_summary.pack(pady=(20, 8), padx=20)

        self.btn_gen_summary_inline = ctk.CTkButton(
            self.no_summary_frame, text="✨   Generar Resumen con IA",
            font=("Segoe UI Semibold", 12),
            fg_color=COLOR_ACCENT, text_color="#FFFFFF", hover_color=COLOR_ACCENT_HOVER,
            height=36, corner_radius=8, command=self.generate_summary
        )
        self.btn_gen_summary_inline.pack(pady=(0, 20))

        # "● Generado - N puntos clave" indicator at bottom of summary panel
        self.summary_indicator = ctk.CTkFrame(self.pane_summary, fg_color="transparent")

        self.dot_label = ctk.CTkLabel(
            self.summary_indicator, text="●",
            font=("Segoe UI", 9), text_color=COLOR_GREEN
        )
        self.dot_label.pack(side=tk.LEFT, padx=(14, 4))

        self.label_indicator_text = ctk.CTkLabel(
            self.summary_indicator, text="",
            font=("Segoe UI", 10), text_color=COLOR_MUTED
        )
        self.label_indicator_text.pack(side=tk.LEFT)

        # Action bar — single row, delete on the right
        self.action_row = ctk.CTkFrame(df, fg_color="transparent")
        self.action_row.pack(fill=tk.X, pady=(10, 0))

        self.btn_rename = ctk.CTkButton(
            self.action_row, text="✏️  Renombrar",
            font=("Segoe UI Semibold", 11),
            fg_color=COLOR_PANEL_LIGHT, text_color="#F6AD55", hover_color=COLOR_BORDER,
            width=110, height=34, corner_radius=8, command=self.rename_record
        )
        self.btn_rename.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_copy_text = ctk.CTkButton(
            self.action_row, text="📋  Copiar",
            font=("Segoe UI Semibold", 11),
            fg_color=COLOR_PANEL_LIGHT, text_color="#63B3ED", hover_color=COLOR_BORDER,
            width=90, height=34, corner_radius=8, command=self.copy_transcription
        )
        self.btn_copy_text.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_copy_summary = ctk.CTkButton(
            self.action_row, text="✨  Resumen",
            font=("Segoe UI Semibold", 11),
            fg_color=COLOR_PANEL_LIGHT, text_color="#F6E05E", hover_color=COLOR_BORDER,
            width=100, height=34, corner_radius=8, command=self.copy_summary
        )
        self.btn_copy_summary.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_export = ctk.CTkButton(
            self.action_row, text="📥  Exportar",
            font=("Segoe UI Semibold", 11),
            fg_color=COLOR_PANEL_LIGHT, text_color="#48BB78", hover_color=COLOR_BORDER,
            width=100, height=34, corner_radius=8, command=self.export_transcription
        )
        self.btn_export.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_delete = ctk.CTkButton(
            self.action_row, text="🗑️  Eliminar",
            font=("Segoe UI Semibold", 11),
            fg_color="transparent", text_color=COLOR_DANGER,
            hover_color=COLOR_DANGER_SOFT, border_width=1, border_color=COLOR_BORDER,
            width=110, height=34, corner_radius=8, command=self.delete_record
        )
        self.btn_delete.pack(side=tk.RIGHT)

        self.label_status = ctk.CTkLabel(
            self.action_row, text="", font=("Segoe UI", 11), text_color=COLOR_MUTED
        )
        self.label_status.pack(side=tk.RIGHT, padx=(0, 8))

    # ------------------------------------------------------------------
    # CARD LIST
    # ------------------------------------------------------------------

    def _make_card(self, record, index):
        card = ctk.CTkFrame(
            self.card_scroll, fg_color=COLOR_PANEL_LIGHT,
            corner_radius=10, cursor="hand2",
            border_width=1, border_color=COLOR_BORDER,
        )
        card.pack(fill=tk.X, padx=6, pady=(4, 0))

        name = record.get("file_name", "")
        dur  = record.get("duration") or "00:00"
        date = (record.get("created_at") or "").split(" ")[0]
        path = (record.get("file_path") or "").lower()
        audio_exts = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".mp4", ".aac", ".opus"}
        ext = os.path.splitext(path)[1]
        kind_icon = "🎙" if ext in audio_exts else "📄"

        lbl_name = ctk.CTkLabel(
            card, text=f"{kind_icon}  {name}", font=("Segoe UI Semibold", 12),
            text_color=COLOR_TEXT_FG, anchor=tk.W
        )
        lbl_name.pack(fill=tk.X, padx=12, pady=(10, 4))

        row2 = ctk.CTkFrame(card, fg_color="transparent")
        row2.pack(fill=tk.X, padx=12, pady=(0, 10))

        # Quiet duration chip — accent reserved for primary actions
        badge = ctk.CTkLabel(
            row2, text=dur, font=("Segoe UI Semibold", 9),
            fg_color=COLOR_CHIP, text_color=COLOR_MUTED,
            corner_radius=6, width=48, height=20
        )
        badge.pack(side=tk.LEFT, padx=(0, 8))

        lbl_date = ctk.CTkLabel(
            row2, text=date, font=("Segoe UI", 10), text_color=COLOR_MUTED
        )
        lbl_date.pack(side=tk.LEFT)

        actions = ctk.CTkFrame(row2, fg_color="transparent")
        # Hidden until hover/selection — cleaner default list

        def on_delete_card(ev=None):
            self._select_card(index)
            self.after(50, self.delete_record)

        def on_rename_card(ev=None):
            self._select_card(index)
            self.after(50, self.rename_record)

        def _bind_icon_hover(btn, idle_fg, hover_fg, idle_tc=COLOR_MUTED, hover_tc=COLOR_TEXT_FG):
            def enter(_e=None):
                btn.configure(fg_color=hover_fg, text_color=hover_tc)
            def leave(_e=None):
                btn.configure(fg_color=idle_fg, text_color=idle_tc)
            btn.bind("<Enter>", enter, add="+")
            btn.bind("<Leave>", leave, add="+")

        btn_delete_card = ctk.CTkButton(
            actions, text="🗑", font=("Segoe UI", 12),
            fg_color=COLOR_PANEL_DARK, text_color=COLOR_MUTED,
            hover_color=COLOR_DANGER_SOFT,
            width=28, height=28, corner_radius=6,
            command=on_delete_card
        )
        btn_delete_card.pack(side=tk.RIGHT, padx=(4, 0))
        _bind_icon_hover(
            btn_delete_card, COLOR_PANEL_DARK, COLOR_DANGER_SOFT,
            COLOR_MUTED, COLOR_DANGER,
        )

        btn_rename_card = ctk.CTkButton(
            actions, text="✎", font=("Segoe UI", 12),
            fg_color=COLOR_PANEL_DARK, text_color=COLOR_MUTED,
            hover_color=COLOR_ICON_HOVER,
            width=28, height=28, corner_radius=6,
            command=on_rename_card
        )
        btn_rename_card.pack(side=tk.RIGHT)
        _bind_icon_hover(btn_rename_card, COLOR_PANEL_DARK, COLOR_ICON_HOVER)

        Tooltip(btn_rename_card, "Renombrar grabación")
        Tooltip(btn_delete_card, "Eliminar grabación")

        card._actions = actions
        card._index = index

        def _show_actions():
            if not actions.winfo_ismapped():
                actions.pack(side=tk.RIGHT)

        def _hide_actions():
            if self._selected_idx == index:
                return
            if actions.winfo_ismapped():
                actions.pack_forget()

        def on_click(ev, idx=index):
            self._select_card(idx)

        def on_enter(ev):
            if self._selected_idx != index:
                card.configure(fg_color=COLOR_CARD_HOVER, border_color=COLOR_BORDER)
            _show_actions()

        def on_leave(ev):
            x, y = ev.x, ev.y
            w, h = card.winfo_width(), card.winfo_height()
            if 0 <= x < w and 0 <= y < h:
                return
            _hide_actions()
            if self._selected_idx == index:
                return
            card.configure(fg_color=COLOR_PANEL_LIGHT, border_color=COLOR_BORDER)

        for w in [card, lbl_name, row2, badge, lbl_date]:
            w.bind("<Button-1>", on_click)

        card.bind("<Enter>", on_enter)
        card.bind("<Leave>", on_leave)

        return card

    def _select_card(self, index):
        if index >= len(self.records):
            return

        # Reset all cards to default; hide actions on non-selected
        for c in self._card_widgets:
            c.configure(fg_color=COLOR_PANEL_LIGHT, border_color=COLOR_BORDER)
            actions = getattr(c, "_actions", None)
            if actions is not None and actions.winfo_ismapped():
                actions.pack_forget()

        self._selected_idx = index
        sel = self._card_widgets[index]
        sel.configure(fg_color=COLOR_CARD_SEL, border_color=COLOR_ACCENT)
        actions = getattr(sel, "_actions", None)
        if actions is not None and not actions.winfo_ismapped():
            actions.pack(side=tk.RIGHT)

        record = self.records[index]
        self.selected_record = record

        self.placeholder_frame.pack_forget()
        self.details_frame.pack(fill=tk.BOTH, expand=True)

        # Stop any active history playback
        self._stop_history_playback()

        # Header labels
        self.label_detail_title.configure(text=record.get("file_name", ""))

        file_path = record.get("file_path", "")
        duration  = record.get("duration") or "00:00"
        created   = record.get("created_at", "")

        # Truncate long paths
        display_path = file_path
        if len(display_path) > 65:
            display_path = "..." + display_path[-62:]

        self.label_detail_meta1.configure(
            text=f"Archivo  -  {display_path}    Duración  -  {duration}"
        )
        self.label_detail_meta2.configure(text=f"Fecha  -  {created}")

        # Check if the record has a playable audio file (any supported format)
        _, ext = os.path.splitext(file_path.lower())
        audio_extensions = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".mp4", ".aac", ".opus"}
        has_audio = ext in audio_extensions and os.path.exists(file_path)
        
        self.player_frame.pack_forget()
        self.split_details.pack_forget()
        
        # Always pack the player frame to keep the UI layout identical and stable
        if has_audio:
            dur_secs = obtener_duracion_audio(file_path)
            self.slider_history.configure(to=dur_secs if dur_secs > 0 else 100, state="normal")
            self.btn_play_history.configure(state="normal")
            self.btn_rewind_history.configure(state="normal")
            self.btn_forward_history.configure(state="normal")
            self.lbl_time_history.configure(text="00:00 / 00:00")
            self.player_frame.pack(fill=tk.X, pady=(12, 14))
            self.update_history_player_ui()
        else:
            self.slider_history.configure(to=100, state="disabled")
            self.slider_history.set(0)
            self.btn_play_history.configure(state="disabled", text="▶")
            self.btn_rewind_history.configure(state="disabled")
            self.btn_forward_history.configure(state="disabled")
            self.lbl_time_history.configure(text="Audio no disponible")
            self.player_frame.pack(fill=tk.X, pady=(12, 14))
            
        self.split_details.pack(fill=tk.BOTH, expand=True)

        # Fill transcript
        self.txt_transcription.configure(state="normal")
        self.txt_transcription.delete("1.0", tk.END)
        text = record.get("transcription") or ""
        self.txt_transcription.insert("1.0", text)

        # Word count
        words = len(text.split()) if text.strip() else 0
        self.label_word_count.configure(text=f"{words} palabras")

        self.update_summary_ui()

    # ------------------------------------------------------------------
    # DATA
    # ------------------------------------------------------------------

    def load_history(self):
        # Reset search bar text
        self.search_var.set("")
        
        import config
        records_entities = config.repository.get_all()
        self.records = [
            {
                "file_path": rec.file_path,
                "file_name": rec.file_name,
                "duration": rec.duration,
                "transcription": rec.transcription,
                "summary": rec.summary,
                "language": rec.language,
                "created_at": rec.created_at
            }
            for rec in records_entities
        ]

        for c in self._card_widgets:
            c.destroy()
        self._card_widgets.clear()
        self._selected_idx = -1

        for i, rec in enumerate(self.records):
            card = self._make_card(rec, i)
            self._card_widgets.append(card)

        self.details_frame.pack_forget()
        self.placeholder_frame.pack(fill=tk.BOTH, expand=True)
        self.selected_record = None

    def _on_search_change(self, *args):
        query = self.search_var.get().strip().lower()
        
        for card in self._card_widgets:
            card.pack_forget()
            
        for i, rec in enumerate(self.records):
            card = self._card_widgets[i]
            
            name = rec.get("file_name", "").lower()
            trans = rec.get("transcription", "").lower()
            summ = rec.get("summary", "").lower()
            
            match = (not query) or (query in name) or (query in trans) or (query in summ)
            
            if match:
                card.pack(fill=tk.X, padx=6, pady=(4, 0))


    def update_summary_ui(self):
        if not self.selected_record:
            return

        summary = (self.selected_record.get("summary") or "").strip()

        self.txt_summary.pack_forget()
        self.no_summary_frame.pack_forget()
        self.summary_indicator.pack_forget()

        if summary:
            self.txt_summary.pack(fill=tk.BOTH, expand=True)
            self.txt_summary.configure(state="normal")
            self.txt_summary.delete("1.0", tk.END)
            self.txt_summary.insert("1.0", summary)
            self.txt_summary.configure(state="disabled")
            self.btn_copy_summary.configure(state="normal")

            # Count key points (non-empty paragraphs / bullet lines)
            lines = [ln.strip() for ln in summary.split("\n") if ln.strip() and
                     (ln.strip().startswith("-") or ln.strip().startswith("•") or ln.strip().startswith("**"))]
            n = len(lines) if lines else max(1, len([p for p in summary.split("\n\n") if p.strip()]))
            plural = "s" if n != 1 else ""
            self.label_indicator_text.configure(text=f"Generado  -  {n} punto{plural} clave")
            self.summary_indicator.pack(fill=tk.X, pady=(0, 10))
        else:
            self.no_summary_frame.pack(fill=tk.BOTH, expand=True)
            self.btn_copy_summary.configure(state="disabled")

    # ------------------------------------------------------------------
    # SUMMARY GENERATION
    # ------------------------------------------------------------------

    def generate_summary(self):
        if not self.selected_record:
            return

        text = self.txt_transcription.get("1.0", tk.END).strip()
        if not text:
            show_warning(self, "Advertencia", "No hay texto transcrito para resumir.")
            return

        if not summarizer.is_configured():
            show_info(
                self,
                "Resumen no configurado",
                "Falta GEMINI_API_KEY. Crea un archivo .env en la raíz del proyecto "
                "con tu clave para habilitar el resumen.",
            )
            return

        self.label_no_summary.pack_forget()
        self.btn_gen_summary_inline.pack_forget()
        self.spinner.pack(pady=40)
        self.spinner.start()
        self.set_status("Resumiendo con IA...")

        threading.Thread(target=self._run_summary_thread, args=(text,), daemon=True).start()

    def _run_summary_thread(self, text):
        try:
            summary = summarizer.summarize(text)
            self.after(0, lambda s=summary: self._summary_success(s))
        except Exception as e:
            err_msg = str(e)
            self.after(0, lambda msg=err_msg: self._summary_failed(msg))

    def _summary_success(self, summary):
        self.spinner.stop()
        self.spinner.pack_forget()
        self.label_no_summary.pack(pady=(20, 8), padx=20)
        self.btn_gen_summary_inline.pack(pady=(0, 20))

        if self.selected_record:
            file_path = self.selected_record["file_path"]
            import config
            record = config.repository.get(file_path)
            if record:
                record.summary = summary
                config.repository.save(record)
            self.selected_record["summary"] = summary
            for r in self.records:
                if r["file_path"] == file_path:
                    r["summary"] = summary
                    break
            self.update_summary_ui()
            self.set_status("¡Resumen listo!")
            show_info(self, "Éxito", "El resumen de IA fue generado correctamente.")
        else:
            self.set_status("")

    def _summary_failed(self, err_msg):
        self.spinner.stop()
        self.spinner.pack_forget()
        self.label_no_summary.pack(pady=(20, 8), padx=20)
        self.btn_gen_summary_inline.pack(pady=(0, 20))
        self.set_status("Error al resumir")
        show_error(self, "Error de Resumen IA", f"No se pudo completar el resumen:\n\n{err_msg}")

    # ------------------------------------------------------------------
    # ACTIONS
    # ------------------------------------------------------------------

    def copy_transcription(self):
        text = self.txt_transcription.get("1.0", tk.END).strip()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.show_toast("Transcripción copiada")

    def copy_summary(self):
        try:
            text = self.txt_summary.get("1.0", tk.END).strip()
        except Exception:
            text = ""
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.show_toast("Resumen copiado")

    def export_transcription(self):
        if not self.selected_record:
            return

        record = self.selected_record
        file_path = record.get("file_path", "")
        text = self.txt_transcription.get("1.0", tk.END).strip()
        summary = (record.get("summary") or "").strip()

        available = []

        if text:
            available.append(
                ("transcript", "Transcripción / subtítulos", "📄", "#63B3ED")
            )

        if summary:
            available.append(("summary", "Resumen", "✨", "#F6E05E"))

        if es_archivo_multimedia_exportable(file_path):
            available.append(("audio", "Audio", "🎙", "#48BB78"))

        if not available:
            show_warning(self, "Exportar", "No hay contenido disponible para exportar en este registro.")
            return

        choice = ask_export_choice(self, available)
        if not choice:
            return

        if choice == "transcript":
            exportar_transcripcion(text)
        elif choice == "summary":
            exportar_resumen(summary, parent=self)
        elif choice == "audio":
            exportar_audio(file_path, parent=self)


    def rename_record(self):
        if not self.selected_record:
            return
        current_name = self.selected_record.get("file_name", "")
        new_name = ask_input(
            self,
            "Renombrar transcripción",
            "Nuevo nombre:",
            current_name,
        )
        if not new_name or new_name == current_name:
            return
        import config
        file_path = self.selected_record["file_path"]
        if config.repository.rename(file_path, new_name):
            self.selected_record["file_name"] = new_name
            for r in self.records:
                if r["file_path"] == file_path:
                    r["file_name"] = new_name
                    break
            # Refresh the card label in-place (keep type icon)
            card = self._card_widgets[self._selected_idx]
            path = (file_path or "").lower()
            audio_exts = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".mp4", ".aac", ".opus"}
            ext = os.path.splitext(path)[1]
            kind_icon = "🎙" if ext in audio_exts else "📄"
            for child in card.winfo_children():
                if isinstance(child, ctk.CTkLabel):
                    child.configure(text=f"{kind_icon}  {new_name}")
                    break
            self.label_detail_title.configure(text=new_name)
            self.show_toast("Nombre actualizado")
        else:
            show_error(self, "Error", "No se pudo renombrar el registro.")

    def delete_record(self):
        if not self.selected_record:
            return
        confirm = ask_yes_no(
            self,
            "Confirmar eliminación",
            f"¿Estás seguro de que querés borrar la transcripción de "
            f"'{self.selected_record['file_name']}' del historial?\n\n"
            "Esta acción no eliminará tu archivo de audio, solo el registro guardado.",
        )
        if confirm:
            try:
                import config
                config.repository.delete(self.selected_record["file_path"])
                self.show_toast("Registro eliminado")
                self.load_history()
            except Exception as e:
                show_error(self, "Error", f"No se pudo eliminar el registro: {e}")

    def set_status(self, text):
        self.label_status.configure(text=text)

    def show_toast(self, text):
        self.set_status(f"✓ {text}")
        self.after(2500, lambda: self.set_status(""))

    def _play_pause_history(self):
        if not self.selected_record:
            return
        
        file_path = self.selected_record["file_path"]
        
        if reproductor.ruta_solicitada != file_path:
            self._stop_history_playback()
            try:
                reproductor.iniciar(file_path)
            except Exception as e:
                show_error(self, "Error", f"No se pudo reproducir el audio: {e}")
                return
        else:
            if reproductor.reproduciendo:
                reproductor.pausar()
            else:
                reproductor.reanudar()
                
        self.update_history_player_ui()

    def _stop_history_playback(self):
        reproductor.detener()
        self.update_history_player_ui()

    def _seek_history(self, seconds):
        if not self.selected_record:
            return
        file_path = self.selected_record["file_path"]
        if reproductor.ruta_solicitada == file_path:
            if seconds > 0:
                reproductor.adelantar(seconds)
            else:
                reproductor.retroceder(abs(seconds))
            self.update_history_player_ui()

    def _on_slider_change(self, value):
        if self.selected_record and reproductor.ruta_solicitada == self.selected_record["file_path"]:
            curr = int(value)
            total = int(reproductor.duracion_total)
            def _fmt(s):
                h = s // 3600
                m = (s % 3600) // 60
                sec = s % 60
                if h > 0:
                    return f"{h}:{m:02d}:{sec:02d}"
                return f"{m:02d}:{sec:02d}"
            self.lbl_time_history.configure(
                text=f"{_fmt(curr)} / {_fmt(total)}"
            )

    def _on_slider_release(self, event):
        self._slider_dragging = False
        if self.selected_record and reproductor.ruta_solicitada == self.selected_record["file_path"]:
            new_pos = self.slider_history.get()
            reproductor.posicion_actual = new_pos
            import pygame
            if pygame.mixer.get_init():
                try:
                    if reproductor.reproduciendo:
                        pygame.mixer.music.play(start=new_pos)
                    else:
                        pygame.mixer.music.set_pos(new_pos)
                except pygame.error:
                    pass
            if reproductor.reproduciendo:
                reproductor.tiempo_inicio = time.time() - new_pos

    def update_history_player_ui(self):
        if not self.selected_record:
            return
            
        file_path = self.selected_record["file_path"]
        is_current = (reproductor.ruta_solicitada == file_path)
        
        if is_current:
            if reproductor.reproduciendo:
                self.btn_play_history.configure(text="⏸")
            else:
                self.btn_play_history.configure(text="▶")
            
            curr = reproductor.obtener_tiempo_actual()
            self.lbl_time_history.configure(text=reproductor.obtener_tiempo_formateado())
            
            if not self._slider_dragging:
                self.slider_history.set(curr)
        else:
            self.btn_play_history.configure(text="▶")
            self.lbl_time_history.configure(text="00:00 / 00:00")
            self.slider_history.set(0)

    def _tick_history_player(self):
        if not self.winfo_exists():
            return
            
        if self.selected_record:
            file_path = self.selected_record["file_path"]
            if reproductor.ruta_solicitada == file_path:
                import pygame
                mixer_busy = False
                if pygame.mixer.get_init():
                    try:
                        mixer_busy = pygame.mixer.music.get_busy()
                    except pygame.error:
                        pass
                
                should_stop = False
                if pygame.mixer.get_init():
                    should_stop = not mixer_busy
                else:
                    should_stop = reproductor.obtener_tiempo_actual() >= reproductor.duracion_total

                if reproductor.reproduciendo and should_stop:
                    reproductor.detener()
                self.update_history_player_ui()
                
        self.after(100, self._tick_history_player)
