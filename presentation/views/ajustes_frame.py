import os
from pathlib import Path
import tkinter as tk
from tkinter import filedialog
import customtkinter as ctk
import config
from . import live_frame
from .app_dialog import ask_yes_no, show_error, show_info

class AjustesFrame(ctk.CTkFrame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)

        # Header
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill=tk.X, padx=24, pady=(20, 10))

        label_titulo = ctk.CTkLabel(
            header_frame, text="⚙  Ajustes",
            font=("Segoe UI Semibold", 22), text_color="#FFFFFF"
        )
        label_titulo.pack(anchor=tk.W)

        label_subtitulo = ctk.CTkLabel(
            header_frame, text="🛠  Configurá el modelo, las carpetas y el historial.",
            font=("Segoe UI", 12), text_color="#8A8F9E"
        )
        label_subtitulo.pack(anchor=tk.W, pady=(2, 0))

        # Main Scrollable Area
        scroll_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll_frame.pack(fill=tk.BOTH, expand=True, padx=24, pady=10)

        # ----------------------------------------------------
        # CARD 1: MODELO DE TRANSCRIPCIÓN
        # ----------------------------------------------------
        card_model = ctk.CTkFrame(scroll_frame, fg_color="#15161E", corner_radius=12, border_color="#2A2B36", border_width=1)
        card_model.pack(fill=tk.X, pady=(0, 16))

        ctk.CTkLabel(
            card_model, text="🧠  MODELO DE TRANSCRIPCIÓN (WHISPER)",
            font=("Segoe UI Semibold", 11), text_color="#A78BFA"
        ).pack(anchor=tk.W, padx=16, pady=(16, 12))

        # Model options combo
        models = {
            "tiny (Rápido, menor precisión)": "tiny",
            "base (Equilibrado, velocidad rápida)": "base",
            "small (Recomendado, buena precisión)": "small",
            "medium (Alta precisión, más pesado)": "medium",
            "large-v3 (Máxima precisión, muy pesado)": "large-v3"
        }

        self.model_combo = ctk.CTkComboBox(
            card_model, values=list(models.keys()), state="readonly", width=300,
            fg_color="#1A1B26", border_color="#2A2B36", button_color="#2A2B36",
            dropdown_fg_color="#15161E", dropdown_text_color="#FFFFFF",
            dropdown_hover_color="#7000FF", command=self.on_model_change
        )
        self.model_combo.pack(anchor=tk.W, padx=16, pady=(0, 12))
        
        # Set current model value
        current_model = config.MODEL_SIZE
        for label, val in models.items():
            if val == current_model:
                self.model_combo.set(label)
                break

        self.label_model_status = ctk.CTkLabel(
            card_model, text=f"Modelo actual en memoria: {current_model} (se recargará al transcribir)",
            font=("Segoe UI", 11), text_color="#8A8F9E"
        )
        self.label_model_status.pack(anchor=tk.W, padx=16, pady=(0, 16))

        # ----------------------------------------------------
        # CARD 2: GRABACIONES EN VIVO
        # ----------------------------------------------------
        card_live = ctk.CTkFrame(scroll_frame, fg_color="#15161E", corner_radius=12, border_color="#2A2B36", border_width=1)
        card_live.pack(fill=tk.X, pady=(0, 16))

        ctk.CTkLabel(
            card_live, text="🎙  CONFIGURACIÓN DE GRABACIONES EN VIVO",
            font=("Segoe UI Semibold", 11), text_color="#4DA3FF"
        ).pack(anchor=tk.W, padx=16, pady=(16, 12))

        # Path browser row
        path_row = ctk.CTkFrame(card_live, fg_color="transparent")
        path_row.pack(fill=tk.X, padx=16, pady=(0, 16))

        self.path_entry = ctk.CTkEntry(
            path_row, placeholder_text="Ruta de guardado...",
            fg_color="#1A1B26", border_color="#2A2B36", text_color="#FFFFFF",
            height=36, corner_radius=8
        )
        self.path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self.path_entry.insert(0, str(live_frame.DEFAULT_DIR))
        self.path_entry.configure(state="readonly")

        btn_browse = ctk.CTkButton(
            path_row, text="📂  Examinar…", font=("Segoe UI Semibold", 12),
            fg_color="#1A1B26", text_color="#63B3ED", hover_color="#2A2B36",
            width=110, height=36, corner_radius=8, command=self.on_browse_path
        )
        btn_browse.pack(side=tk.RIGHT)

        # ----------------------------------------------------
        # CARD 3: BASE DE DATOS Y CACHÉ
        # ----------------------------------------------------
        card_db = ctk.CTkFrame(scroll_frame, fg_color="#15161E", corner_radius=12, border_color="#2A2B36", border_width=1)
        card_db.pack(fill=tk.X, pady=(0, 16))

        ctk.CTkLabel(
            card_db, text="🗄  HISTORIAL Y BASE DE DATOS",
            font=("Segoe UI Semibold", 11), text_color="#F6AD55"
        ).pack(anchor=tk.W, padx=16, pady=(16, 12))

        self.label_db_stats = ctk.CTkLabel(
            card_db, text="Cargando estadísticas...",
            font=("Segoe UI", 12), text_color="#FFFFFF"
        )
        self.label_db_stats.pack(anchor=tk.W, padx=16, pady=(0, 12))

        btn_clear_db = ctk.CTkButton(
            card_db, text="🗑  Limpiar Historial", font=("Segoe UI Semibold", 12),
            fg_color="#1A1B26", text_color="#E53E3E", hover_color="#3D1D1D",
            width=160, height=36, corner_radius=8, command=self.on_clear_db
        )
        btn_clear_db.pack(anchor=tk.W, padx=16, pady=(0, 16))

        # ----------------------------------------------------
        # CARD 4: BITACORA DE ERRORES
        # ----------------------------------------------------
        # Los fallos de proveedor (saturacion, cuota, timeout) terminaban solo
        # en un archivo que nadie abre, asi que la app parecia colgada sin
        # motivo. Aca se ven los ultimos, sin salir del programa.
        card_log = ctk.CTkFrame(
            scroll_frame, fg_color="#15161E", corner_radius=12,
            border_color="#2A2B36", border_width=1,
        )
        card_log.pack(fill=tk.X, pady=(0, 16))

        ctk.CTkLabel(
            card_log, text="🧾  BITÁCORA DE ERRORES",
            font=("Segoe UI Semibold", 11), text_color="#E53E3E"
        ).pack(anchor=tk.W, padx=16, pady=(16, 12))

        self.log_box = ctk.CTkTextbox(
            card_log, height=150, font=("Consolas", 11),
            fg_color="#0F1017", text_color="#C9CBD4", wrap="none",
        )
        self.log_box.pack(fill=tk.X, padx=16, pady=(0, 12))
        self.log_box.configure(state="disabled")

        row_log = ctk.CTkFrame(card_log, fg_color="transparent")
        row_log.pack(fill=tk.X, padx=16, pady=(0, 16))

        ctk.CTkButton(
            row_log, text="🔄  Actualizar", font=("Segoe UI Semibold", 12),
            fg_color="#1A1B26", text_color="#4DA3FF", hover_color="#1D2A3D",
            width=130, height=36, corner_radius=8, command=self.refresh_error_log,
        ).pack(side=tk.LEFT)

        ctk.CTkButton(
            row_log, text="📂  Abrir carpeta", font=("Segoe UI Semibold", 12),
            fg_color="#1A1B26", text_color="#48BB78", hover_color="#1D3D2A",
            width=140, height=36, corner_radius=8, command=self.on_open_logs,
        ).pack(side=tk.LEFT, padx=(8, 0))

        ctk.CTkButton(
            row_log, text="🗑  Vaciar", font=("Segoe UI Semibold", 12),
            fg_color="#1A1B26", text_color="#E53E3E", hover_color="#3D1D1D",
            width=110, height=36, corner_radius=8, command=self.on_clear_log,
        ).pack(side=tk.LEFT, padx=(8, 0))

        # Refresh stats immediately
        self.refresh_db_stats()
        self.refresh_error_log()

    def refresh_error_log(self):
        """Show the newest journal entries, newest first."""
        from core.errors import recent

        try:
            entries = recent(limit=40)
        except Exception as exc:
            text = f"No se pudo leer la bitácora: {exc}"
        else:
            text = "\n".join(
                f"{e.get('time', '')}  [{e.get('severity', 'error')}]  "
                f"{e.get('context', '')}: {e.get('error_type', '')}: "
                f"{(e.get('message') or '')[:160]}"
                for e in entries
            ) or "Sin errores registrados."
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", tk.END)
        self.log_box.insert("1.0", text)
        self.log_box.configure(state="disabled")

    def on_open_logs(self):
        try:
            os.startfile(config.log_directory)
        except Exception as exc:
            show_error(self, "Bitácora", f"No se pudo abrir la carpeta: {exc}")

    def on_clear_log(self):
        if not ask_yes_no(self, "Bitácora", "¿Vaciar la bitácora de errores?"):
            return
        from core.errors import clear

        clear()
        self.refresh_error_log()

    def on_model_change(self, selected_label):
        models = {
            "tiny (Rápido, menor precisión)": "tiny",
            "base (Equilibrado, velocidad rápida)": "base",
            "small (Recomendado, buena precisión)": "small",
            "medium (Alta precisión, más pesado)": "medium",
            "large-v3 (Máxima precisión, muy pesado)": "large-v3"
        }
        val = models.get(selected_label, "small")
        if config.MODEL_SIZE != val:
            config.MODEL_SIZE = val
            config.transcription_service.model_size = val
            config.transcription_service._model = None  # Force reload on next run
            self.label_model_status.configure(text=f"Modelo actual en memoria: {val} (se recargará al transcribir)")

    def on_browse_path(self):
        new_dir = filedialog.askdirectory(initialdir=str(live_frame.DEFAULT_DIR))
        if new_dir:
            live_frame.DEFAULT_DIR = Path(new_dir)
            self.path_entry.configure(state="normal")
            self.path_entry.delete(0, tk.END)
            self.path_entry.insert(0, str(new_dir))
            self.path_entry.configure(state="readonly")
            show_info(self, "Configuración", "Ruta de grabaciones actualizada correctamente.")

    def refresh_db_stats(self):
        try:
            import config
            stats = config.repository.get_stats()
            count = stats.get("total_records", 0)
            size_kb = stats.get("size_kb", 0.0)
            
            self.label_db_stats.configure(
                text=f"• Total de archivos en caché: {count}\n• Tamaño de la base de datos: {size_kb:.1f} KB"
            )
        except Exception as e:
            self.label_db_stats.configure(text=f"No se pudieron cargar las estadísticas: {e}")

    def on_clear_db(self):
        confirm = ask_yes_no(
            self,
            "Confirmar acción",
            "¿Estás seguro de que querés borrar todo el historial de transcripciones guardadas?\n\nEsta acción no se puede deshacer.",
        )
        if confirm:
            try:
                import config
                config.repository.clear()
                self.refresh_db_stats()
                show_info(self, "Éxito", "El historial fue eliminado por completo.")
                self.refresh_db_stats()
            except Exception as e:
                show_error(self, "Error", f"No se pudo limpiar la base de datos: {e}")
