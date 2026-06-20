import os
import threading
import tkinter as tk
from tkinter import messagebox, filedialog
import customtkinter as ctk
import db
import summarizer
from spinner import Spinner

COLOR_BG = "#0B0C10"          # Deep dark window background
COLOR_PANEL = "#15161E"       # Cards and panels background
COLOR_PANEL_LIGHT = "#1A1B26" # Hover and sub-panels background
COLOR_TEXT_FG = "#FFFFFF"     # Primary text
COLOR_MUTED = "#8A8F9E"       # Muted/secondary text
COLOR_ACCENT = "#7000FF"      # Purple accent
COLOR_ACCENT_HOVER = "#5900CC"# Darker purple hover
COLOR_BORDER = "#2A2B36"      # Card border outline

class HistorialFrame(ctk.CTkFrame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        
        self.records = []
        self.selected_record = None

        # Main layout structure: Split into Header and Body Columns
        # Header
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill=tk.X, padx=24, pady=(20, 10))

        label_titulo = ctk.CTkLabel(
            header_frame, text="Historial de transcripciones",
            font=("Segoe UI Semibold", 22), text_color=COLOR_TEXT_FG
        )
        label_titulo.pack(side=tk.LEFT, anchor=tk.W)

        self.btn_refresh = ctk.CTkButton(
            header_frame, text="↻   Actualizar", font=("Segoe UI Semibold", 12),
            fg_color=COLOR_PANEL_LIGHT, text_color=COLOR_TEXT_FG, hover_color=COLOR_BORDER,
            width=100, height=32, corner_radius=8, command=self.load_history
        )
        self.btn_refresh.pack(side=tk.RIGHT)

        label_subtitulo = ctk.CTkLabel(
            self, text="Revisá, exportá y generá resúmenes de tus transcripciones pasadas.",
            font=("Segoe UI", 12), text_color=COLOR_MUTED
        )
        label_subtitulo.pack(anchor=tk.W, padx=24, pady=(0, 10))

        # Columns container
        self.columns_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.columns_frame.pack(fill=tk.BOTH, expand=True, padx=24, pady=8)

        # Left Column: List of recordings (transcriptions)
        self.left_col = ctk.CTkFrame(self.columns_frame, fg_color="transparent", width=320)
        self.left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=(0, 12))
        self.left_col.pack_propagate(False)

        label_left_title = ctk.CTkLabel(
            self.left_col, text="TRANSCRIPCIONES GUARDADAS", font=("Segoe UI Semibold", 10), text_color=COLOR_MUTED
        )
        label_left_title.pack(anchor=tk.W, pady=(0, 6))

        self.card_listbox = ctk.CTkFrame(self.left_col, fg_color=COLOR_PANEL, corner_radius=12, border_color=COLOR_BORDER, border_width=1)
        self.card_listbox.pack(fill=tk.BOTH, expand=True)

        self.scrollbar_listbox = tk.Scrollbar(
            self.card_listbox, orient=tk.VERTICAL,
            bg=COLOR_PANEL, troughcolor=COLOR_PANEL, activebackground=COLOR_ACCENT,
            borderwidth=0, highlightthickness=0,
        )
        self.lista_historial = tk.Listbox(
            self.card_listbox,
            selectmode=tk.SINGLE,
            yscrollcommand=self.scrollbar_listbox.set,
            bg="#11121A", fg=COLOR_TEXT_FG,
            selectbackground=COLOR_ACCENT, selectforeground="#ffffff",
            relief=tk.FLAT, borderwidth=0, highlightthickness=0,
            font=("Segoe UI", 10), activestyle="none",
        )
        self.lista_historial.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0), pady=12)
        self.scrollbar_listbox.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 12), pady=12)
        self.scrollbar_listbox.config(command=self.lista_historial.yview)
        
        self.lista_historial.bind("<<ListboxSelect>>", self.on_record_select)

        # Right Column: Details view
        self.right_col = ctk.CTkFrame(self.columns_frame, fg_color="transparent")
        self.right_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0))

        # Placeholder inside right column (initially shown)
        self.placeholder_frame = ctk.CTkFrame(self.right_col, fg_color=COLOR_PANEL, corner_radius=12, border_color=COLOR_BORDER, border_width=1)
        self.placeholder_frame.pack(fill=tk.BOTH, expand=True)
        
        self.placeholder_label = ctk.CTkLabel(
            self.placeholder_frame, text="📜\n\nSeleccioná una transcripción para ver sus detalles.",
            font=("Segoe UI Semibold", 13), text_color=COLOR_MUTED, justify=tk.CENTER
        )
        self.placeholder_label.pack(expand=True)

        # Details Panel container (packed when a record is selected)
        self.details_frame = ctk.CTkFrame(self.right_col, fg_color="transparent")
        # Initially not packed

        # Header of details: Title, Date, Duration, and Top action buttons
        self.details_header = ctk.CTkFrame(self.details_frame, fg_color="transparent")
        self.details_header.pack(fill=tk.X, pady=(0, 8))

        self.label_details_title = ctk.CTkLabel(
            self.details_header, text="nombre_archivo.mp3", font=("Segoe UI Semibold", 14), text_color=COLOR_TEXT_FG,
            anchor=tk.W, justify=tk.LEFT
        )
        self.label_details_title.pack(anchor=tk.W)

        self.label_details_meta = ctk.CTkLabel(
            self.details_header, text="Duración: 00:00  •  Fecha: 2026-06-20", font=("Segoe UI", 11), text_color=COLOR_MUTED,
            anchor=tk.W
        )
        self.label_details_meta.pack(anchor=tk.W, pady=(2, 0))

        # Split Card view for details (Transcription on left/top, Summary on right/bottom)
        self.split_details = ctk.CTkFrame(self.details_frame, fg_color="transparent")
        self.split_details.pack(fill=tk.BOTH, expand=True)

        # Left Detail Panel: Transcription Text
        self.pane_text = ctk.CTkFrame(self.split_details, fg_color=COLOR_PANEL, corner_radius=12, border_color=COLOR_BORDER, border_width=1)
        self.pane_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        ctk.CTkLabel(
            self.pane_text, text="TEXTO TRANSCRITO", font=("Segoe UI Semibold", 10), text_color=COLOR_MUTED
        ).pack(anchor=tk.W, padx=14, pady=(12, 4))

        self.txt_transcription = ctk.CTkTextbox(
            self.pane_text, fg_color="#11121A", text_color=COLOR_TEXT_FG,
            font=("Segoe UI", 12), corner_radius=8, border_width=0
        )
        self.txt_transcription.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

        # Right Detail Panel: AI Summary
        self.pane_summary = ctk.CTkFrame(self.split_details, fg_color=COLOR_PANEL, corner_radius=12, border_color=COLOR_BORDER, border_width=1)
        self.pane_summary.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))

        ctk.CTkLabel(
            self.pane_summary, text="RESUMEN CON IA (GEMINI)", font=("Segoe UI Semibold", 10), text_color=COLOR_MUTED
        ).pack(anchor=tk.W, padx=14, pady=(12, 4))

        # Summary Sub-container (can either show summary text or the AI prompt placeholder)
        self.summary_content_frame = ctk.CTkFrame(self.pane_summary, fg_color="transparent")
        self.summary_content_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

        self.txt_summary = ctk.CTkTextbox(
            self.summary_content_frame, fg_color="#11121A", text_color=COLOR_TEXT_FG,
            font=("Segoe UI", 12), corner_radius=8, border_width=0
        )
        # Packed dynamically based on summary status

        self.no_summary_frame = ctk.CTkFrame(self.summary_content_frame, fg_color="#11121A", corner_radius=8)
        # Packed dynamically

        self.spinner = Spinner(
            self.no_summary_frame, size=24, bg="#11121A",
            accent_color=COLOR_ACCENT, muted_color=COLOR_PANEL
        )
        
        self.label_no_summary = ctk.CTkLabel(
            self.no_summary_frame, text="Esta transcripción aún no tiene un resumen generado.",
            font=("Segoe UI", 12), text_color=COLOR_MUTED, justify=tk.CENTER
        )
        self.label_no_summary.pack(pady=(20, 8), padx=20)

        self.btn_gen_summary_inline = ctk.CTkButton(
            self.no_summary_frame, text="✨   Generar Resumen con IA", font=("Segoe UI Semibold", 12),
            fg_color=COLOR_ACCENT, text_color="#FFFFFF", hover_color=COLOR_ACCENT_HOVER,
            height=36, corner_radius=8, command=self.generate_summary
        )
        self.btn_gen_summary_inline.pack(pady=(0, 20))

        # Action Buttons Row below cards
        self.action_row = ctk.CTkFrame(self.details_frame, fg_color="transparent")
        self.action_row.pack(fill=tk.X, pady=(10, 0))

        self.btn_copy_text = ctk.CTkButton(
            self.action_row, text="📋   Copiar Transcripción", font=("Segoe UI Semibold", 12),
            fg_color=COLOR_PANEL_LIGHT, text_color=COLOR_TEXT_FG, hover_color=COLOR_BORDER,
            height=36, corner_radius=8, command=self.copy_transcription
        )
        self.btn_copy_text.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_copy_summary = ctk.CTkButton(
            self.action_row, text="✨   Copiar Resumen", font=("Segoe UI Semibold", 12),
            fg_color=COLOR_PANEL_LIGHT, text_color=COLOR_TEXT_FG, hover_color=COLOR_BORDER,
            height=36, corner_radius=8, command=self.copy_summary
        )
        self.btn_copy_summary.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_export = ctk.CTkButton(
            self.action_row, text="📥   Exportar", font=("Segoe UI Semibold", 12),
            fg_color=COLOR_PANEL_LIGHT, text_color=COLOR_TEXT_FG, hover_color=COLOR_BORDER,
            height=36, corner_radius=8, command=self.export_transcription
        )
        self.btn_export.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_delete = ctk.CTkButton(
            self.action_row, text="🗑️   Eliminar Registro", font=("Segoe UI Semibold", 12),
            fg_color=COLOR_PANEL_LIGHT, text_color=COLOR_TEXT_FG, hover_color="#E53E3E",
            height=36, corner_radius=8, command=self.delete_record
        )
        self.btn_delete.pack(side=tk.RIGHT)

        self.label_status = ctk.CTkLabel(
            self.action_row, text="", font=("Segoe UI", 11), text_color=COLOR_MUTED
        )
        self.label_status.pack(side=tk.RIGHT, padx=12)

        # Responsive layout adjustments
        self.last_hist_width = [0]
        self.bind("<Configure>", self.on_hist_configure)

        # Load data initially
        self.load_history()

    def load_history(self):
        self.records = db.get_all_transcriptions()
        self.lista_historial.delete(0, tk.END)
        for r in self.records:
            # Format: "Filename (duration) - timestamp"
            filename = r["file_name"]
            duration = r["duration"] or "00:00"
            date_str = r["created_at"].split(" ")[0] if r["created_at"] else ""
            item_text = f"{filename} ({duration}) - {date_str}"
            self.lista_historial.insert(tk.END, item_text)
        
        # Hide details panel and show placeholder
        self.details_frame.pack_forget()
        self.placeholder_frame.pack(fill=tk.BOTH, expand=True)
        self.selected_record = None

    def on_record_select(self, event):
        selection = self.lista_historial.curselection()
        if not selection:
            return
        
        idx = selection[0]
        if idx >= len(self.records):
            return
            
        record = self.records[idx]
        self.selected_record = record
        
        # Hide placeholder and show details panel
        self.placeholder_frame.pack_forget()
        self.details_frame.pack(fill=tk.BOTH, expand=True)

        # Update labels
        self.label_details_title.configure(text=record["file_name"])
        date_str = record["created_at"] or "Desconocida"
        meta_text = f"Archivo: {record['file_path']}  •  Duración: {record['duration'] or '00:00'}  •  Fecha: {date_str}"
        self.label_details_meta.configure(text=meta_text)

        # Set text fields
        self.txt_transcription.configure(state="normal")
        self.txt_transcription.delete("1.0", tk.END)
        self.txt_transcription.insert("1.0", record["transcription"] or "")
        self.txt_transcription.configure(state="normal") # Allow user editing if they want, or keep it read-only? Keep normal but user can modify.

        self.update_summary_ui()

    def update_summary_ui(self):
        if not self.selected_record:
            return
            
        summary = self.selected_record.get("summary", "").strip()
        
        self.txt_summary.pack_forget()
        self.no_summary_frame.pack_forget()
        
        if summary:
            self.txt_summary.pack(fill=tk.BOTH, expand=True)
            self.txt_summary.configure(state="normal")
            self.txt_summary.delete("1.0", tk.END)
            self.txt_summary.insert("1.0", summary)
            self.btn_copy_summary.configure(state="normal")
        else:
            self.no_summary_frame.pack(fill=tk.BOTH, expand=True)
            self.btn_copy_summary.configure(state="disabled")

    def generate_summary(self):
        if not self.selected_record:
            return
            
        text = self.txt_transcription.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning("Advertencia", "No hay texto transcrito para resumir.")
            return

        if not summarizer.is_configured():
            messagebox.showinfo(
                "Resumen no configurado",
                "Falta GEMINI_API_KEY. Crea un archivo .env en app_escritorio "
                "con tu clave para habilitar el resumen.",
            )
            return

        # Show spinner, hide labels and button
        self.label_no_summary.pack_forget()
        self.btn_gen_summary_inline.pack_forget()
        self.spinner.pack(pady=40)
        self.spinner.start()
        
        self.set_status("Resumiendo con IA...")

        threading.Thread(target=self._run_summary_thread, args=(text,), daemon=True).start()

    def _run_summary_thread(self, text):
        try:
            summary = summarizer.summarize(text)
            self.after(0, lambda: self._summary_success(summary))
        except Exception as e:
            self.after(0, lambda: self._summary_failed(str(e)))

    def _summary_success(self, summary):
        self.spinner.stop()
        self.spinner.pack_forget()
        
        # Restore labels for future use if it gets cleared
        self.label_no_summary.pack(pady=(20, 8), padx=20)
        self.btn_gen_summary_inline.pack(pady=(0, 20))

        if self.selected_record:
            file_path = self.selected_record["file_path"]
            db.update_summary(file_path, summary)
            self.selected_record["summary"] = summary
            
            # Refresh list item reference
            for r in self.records:
                if r["file_path"] == file_path:
                    r["summary"] = summary
                    break
                    
            self.update_summary_ui()
            self.set_status("¡Resumen listo!")
            messagebox.showinfo("Éxito", "El resumen de IA fue generado e incorporado correctamente.")
        else:
            self.set_status("")

    def _summary_failed(self, err_msg):
        self.spinner.stop()
        self.spinner.pack_forget()
        self.label_no_summary.pack(pady=(20, 8), padx=20)
        self.btn_gen_summary_inline.pack(pady=(0, 20))
        self.set_status("Error al resumir")
        messagebox.showerror("Error de Resumen IA", f"No se pudo completar el resumen:\n\n{err_msg}")

    def copy_transcription(self):
        text = self.txt_transcription.get("1.0", tk.END).strip()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.show_toast("Transcripción copiada")

    def copy_summary(self):
        text = self.txt_summary.get("1.0", tk.END).strip()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.show_toast("Resumen copiado")

    def export_transcription(self):
        if not self.selected_record:
            return
        text = self.txt_transcription.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning("Advertencia", "No hay transcripción para exportar.")
            return

        initial_name = os.path.splitext(self.selected_record["file_name"])[0] + "_transcripcion.txt"
        output_file = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Archivo de texto", "*.txt")],
            title="Guardar transcripción como",
            initialfile=initial_name
        )
        if output_file:
            try:
                with open(output_file, "w", encoding="utf-8") as f:
                    f.write(text)
                messagebox.showinfo("Información", f"Transcripción guardada en {output_file}.")
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo guardar el archivo: {e}")

    def delete_record(self):
        if not self.selected_record:
            return
            
        confirm = messagebox.askyesno(
            "Confirmar eliminación",
            f"¿Estás seguro de que querés borrar la transcripción de '{self.selected_record['file_name']}' del historial?\n\nEsta acción no eliminará tu archivo de audio, solo el registro guardado.",
            parent=self
        )
        if confirm:
            file_path = self.selected_record["file_path"]
            if db.delete_transcription(file_path):
                self.show_toast("Registro eliminado")
                self.load_history()
            else:
                messagebox.showerror("Error", "No se pudo eliminar el registro de la base de datos.")

    def set_status(self, text):
        self.label_status.configure(text=text)

    def show_toast(self, text):
        self.set_status(f"✓ {text}")
        self.after(2500, lambda: self.set_status(""))

    def on_hist_configure(self, event):
        if event.widget != self:
            return
            
        new_w = event.width
        if new_w < 400: # Ignorar anchos de inicialización
            return
            
        if new_w == self.last_hist_width[0]:
            return
        self.last_hist_width[0] = new_w

        # Threshold at 800px width
        if new_w < 800:
            # 1-Column Layout: Left Column full width above details
            self.left_col.pack_forget()
            self.right_col.pack_forget()
            
            self.left_col.configure(height=180)
            self.left_col.pack(side=tk.TOP, fill=tk.X, expand=False, pady=(0, 10))
            self.right_col.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(10, 0))
            
            # Stack cards inside right column details vertically
            self.pane_text.pack_forget()
            self.pane_summary.pack_forget()
            self.pane_text.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(0, 6))
            self.pane_summary.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(6, 0))
            
            # Wrap action buttons
            self.btn_copy_text.pack_forget()
            self.btn_copy_summary.pack_forget()
            self.btn_export.pack_forget()
            self.btn_delete.pack_forget()
            
            self.btn_copy_text.pack(side=tk.LEFT, padx=(0, 4), pady=2)
            self.btn_copy_summary.pack(side=tk.LEFT, padx=(0, 4), pady=2)
            self.btn_export.pack(side=tk.LEFT, padx=(0, 4), pady=2)
            self.btn_delete.pack(side=tk.RIGHT, pady=2)
        else:
            # 2-Column Layout: Side-by-side
            self.left_col.pack_forget()
            self.right_col.pack_forget()
            
            self.left_col.configure(width=320)
            self.left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=(0, 12))
            self.right_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0))
            
            # Side-by-side cards inside details
            self.pane_text.pack_forget()
            self.pane_summary.pack_forget()
            self.pane_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
            self.pane_summary.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
            
            # Single row buttons
            self.btn_copy_text.pack_forget()
            self.btn_copy_summary.pack_forget()
            self.btn_export.pack_forget()
            self.btn_delete.pack_forget()
            
            self.btn_copy_text.pack(side=tk.LEFT, padx=(0, 6))
            self.btn_copy_summary.pack(side=tk.LEFT, padx=(0, 6))
            self.btn_export.pack(side=tk.LEFT, padx=(0, 6))
            self.btn_delete.pack(side=tk.RIGHT)
