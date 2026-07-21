import tkinter as tk
from tkinter import ttk, messagebox
import customtkinter as ctk
import threading
from infrastructure.services import summarizer
from presentation.controllers.funcionalidad import *
from infrastructure.audio.reproductor import *
from config import idiomas
from .live_frame import LiveFrame
from .interview_frame import InterviewFrame
from .spinner import Spinner
from .ajustes_frame import AjustesFrame
from .historial_frame import HistorialFrame
from .tooltip import Tooltip

# Design System Colors matching the mockup
COLOR_BG = "#0B0C10"          # Deep dark window background
COLOR_SIDEBAR = "#08090C"     # Even darker sidebar background
COLOR_PANEL = "#15161E"       # Cards and panels background
COLOR_PANEL_LIGHT = "#1A1B26" # Hover and sub-panels background
COLOR_TEXT_FG = "#FFFFFF"     # Primary text
COLOR_MUTED = "#8A8F9E"       # Muted/secondary text
COLOR_ACCENT = "#7000FF"      # Purple accent
COLOR_ACCENT_HOVER = "#5900CC"# Darker purple hover
COLOR_BORDER = "#2A2B36"      # Card border outline


def _setup_theme(ventana):
    try:
        ventana.configure(fg_color=COLOR_BG)  # ctk.CTk (production window)
    except tk.TclError:
        ventana.configure(bg=COLOR_BG)  # plain tk.Tk (headless build check)
    style = ttk.Style(ventana)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    # Style the listbox scrollbars and progressbar to match
    style.configure(
        "Archivos.Horizontal.TProgressbar",
        background=COLOR_ACCENT, troughcolor=COLOR_PANEL_LIGHT, borderwidth=0,
    )
    style.configure(
        "ProgressScale.Horizontal.TScale",
        background=COLOR_PANEL,
        troughcolor="#11121A",
        slidercolor=COLOR_ACCENT,
        borderwidth=0,
    )


def crear_interfaz(ventana):
    # Defensive fallback initialization for testing/standalone runs (SOLID)
    import config
    from infrastructure.repositories.sqlite_repository import SQLiteTranscriptionRepository
    from infrastructure.services.onnx_transcriber import OfflineTranscriptionService

    if config.repository is None:
        config.repository = SQLiteTranscriptionRepository(config.DB_PATH)
        config.repository.init_db()
    if config.transcription_service is None:
        config.transcription_service = OfflineTranscriptionService(model_size=config.MODEL_SIZE)

    ventana.geometry("1200x700")
    archivo_procesando = tk.StringVar()
    lista_archivos_paths = {}
    transcripcion_resultado = ""
    diarizar_var = tk.BooleanVar(master=ventana, value=False)
    timestamps_var = tk.BooleanVar(master=ventana, value=True)
    _last_file_path: str | None = None

    def _on_file_done(fp: str) -> None:
        nonlocal _last_file_path
        _last_file_path = fp


    _setup_theme(ventana)

    # ----------------------------------------------------
    # APP SHELL: LEFT SIDEBAR & RIGHT CONTENT AREA
    # ----------------------------------------------------
    
    # Left Sidebar Frame
    sidebar_frame = ctk.CTkFrame(ventana, width=200, corner_radius=0, fg_color=COLOR_SIDEBAR, border_width=0)
    sidebar_frame.pack(side=tk.LEFT, fill=tk.Y)
    sidebar_frame.pack_propagate(False)

    # Logo/Brand / Toggle Frame
    logo_frame = ctk.CTkFrame(sidebar_frame, fg_color="transparent")
    logo_frame.pack(fill=tk.X, pady=(24, 28), padx=12)

    label_logo = ctk.CTkLabel(
        logo_frame, text="AudioText", font=("Segoe UI Semibold", 20), text_color="#A78BFA"
    )
    label_logo.pack(side=tk.LEFT, padx=(8, 0))

    btn_toggle_sidebar = ctk.CTkButton(
        logo_frame, text="☰", font=("Segoe UI", 15),
        fg_color="transparent", text_color=COLOR_MUTED, hover_color=COLOR_PANEL_LIGHT,
        width=32, height=32, corner_radius=6
    )
    btn_toggle_sidebar.pack(side=tk.RIGHT)

    # Navigation logic
    def switch_view(view_name):
        view_archivos.pack_forget()
        view_envivo.pack_forget()
        view_entrevista.pack_forget()
        view_historial.pack_forget()
        view_ajustes.pack_forget()

        # Stop history playback when leaving the view
        if hasattr(view_historial, "_stop_history_playback"):
            view_historial._stop_history_playback()
        if view_name != "entrevista" and (
            view_entrevista.worker.is_running()
            or view_entrevista.candidate_listener.is_running()
        ):
            view_entrevista.finish_session()

        # Reset button colors — keep tinted icons; brighten the active destination
        nav_colors = {
            "archivos": "#A78BFA",
            "envivo": "#4DA3FF",
            "entrevista": "#48BB78",
            "historial": "#F6AD55",
            "ajustes": "#8A8F9E",
        }
        btn_archivos.configure(
            fg_color="transparent" if view_name != "archivos" else COLOR_PANEL,
            text_color=COLOR_TEXT_FG if view_name == "archivos" else nav_colors["archivos"],
        )
        btn_envivo.configure(
            fg_color="transparent" if view_name != "envivo" else COLOR_PANEL,
            text_color=COLOR_TEXT_FG if view_name == "envivo" else nav_colors["envivo"],
        )
        btn_entrevista.configure(
            fg_color="transparent" if view_name != "entrevista" else COLOR_PANEL,
            text_color=COLOR_TEXT_FG if view_name == "entrevista" else nav_colors["entrevista"],
        )
        btn_historial.configure(
            fg_color="transparent" if view_name != "historial" else COLOR_PANEL,
            text_color=COLOR_TEXT_FG if view_name == "historial" else nav_colors["historial"],
        )
        btn_ajustes.configure(
            fg_color="transparent" if view_name != "ajustes" else COLOR_PANEL,
            text_color=COLOR_TEXT_FG if view_name == "ajustes" else nav_colors["ajustes"],
        )

        if view_name == "archivos":
            view_archivos.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        elif view_name == "envivo":
            view_envivo.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            # Refresh devices or parameters if needed
            if hasattr(view_envivo, "refresh_devices"):
                view_envivo.refresh_devices()
        elif view_name == "entrevista":
            view_entrevista.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            view_entrevista.refresh_devices()
        elif view_name == "historial":
            view_historial.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            view_historial.load_history()
        elif view_name == "ajustes":
            view_ajustes.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            # Refresh DB stats
            view_ajustes.refresh_db_stats()

    # Sidebar Buttons — tinted text so each destination reads as a distinct color cue
    btn_archivos = ctk.CTkButton(
        sidebar_frame, text="📁   Archivos", font=("Segoe UI Semibold", 13),
        fg_color="transparent", text_color="#A78BFA", hover_color=COLOR_PANEL_LIGHT,
        anchor=tk.W, width=176, height=40, corner_radius=8, command=lambda: switch_view("archivos")
    )
    btn_archivos.pack(fill=tk.X, padx=12, pady=4)

    btn_envivo = ctk.CTkButton(
        sidebar_frame, text="🎙   En vivo", font=("Segoe UI Semibold", 13),
        fg_color="transparent", text_color="#4DA3FF", hover_color=COLOR_PANEL_LIGHT,
        anchor=tk.W, width=176, height=40, corner_radius=8, command=lambda: switch_view("envivo")
    )
    btn_envivo.pack(fill=tk.X, padx=12, pady=4)

    btn_entrevista = ctk.CTkButton(
        sidebar_frame, text="🎯   Entrevista", font=("Segoe UI Semibold", 13),
        fg_color="transparent", text_color="#48BB78", hover_color=COLOR_PANEL_LIGHT,
        anchor=tk.W, width=176, height=40, corner_radius=8,
        command=lambda: switch_view("entrevista")
    )
    btn_entrevista.pack(fill=tk.X, padx=12, pady=4)

    btn_historial = ctk.CTkButton(
        sidebar_frame, text="📜   Historial", font=("Segoe UI Semibold", 13),
        fg_color="transparent", text_color="#F6AD55", hover_color=COLOR_PANEL_LIGHT,
        anchor=tk.W, width=176, height=40, corner_radius=8, command=lambda: switch_view("historial")
    )
    btn_historial.pack(fill=tk.X, padx=12, pady=4)

    btn_ajustes = ctk.CTkButton(
        sidebar_frame, text="⚙   Ajustes", font=("Segoe UI Semibold", 13),
        fg_color="transparent", text_color="#8A8F9E", hover_color=COLOR_PANEL_LIGHT,
        anchor=tk.W, width=176, height=40, corner_radius=8, command=lambda: switch_view("ajustes")
    )
    btn_ajustes.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=20)

    Tooltip(btn_archivos, "Transcribí archivos de audio (MP3, WAV, M4A, FLAC) a texto de forma offline.")
    Tooltip(btn_envivo, "Transcribí en tiempo real lo que reproduce el sistema o el micrófono.")
    Tooltip(btn_entrevista, "Seguí la conversación y recibí ayuda para responder con fluidez.")
    Tooltip(btn_historial, "Revisá, buscá y exportá transcripciones guardadas anteriormente.")
    Tooltip(btn_ajustes, "Configuración de la app: base de datos, modelo, carpetas.")

    # Sidebar Collapse Logic
    sidebar_collapsed = [False]

    def toggle_sidebar():
        if not sidebar_collapsed[0]:
            # Collapse sidebar to 60px
            sidebar_frame.configure(width=60)
            label_logo.pack_forget()
            
            btn_toggle_sidebar.pack_forget()
            btn_toggle_sidebar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
            btn_toggle_sidebar.configure(text="☰", width=36)
            
            btn_archivos.configure(text="📁", anchor=tk.CENTER, width=44)
            btn_envivo.configure(text="🎙", anchor=tk.CENTER, width=44)
            btn_entrevista.configure(text="🎯", anchor=tk.CENTER, width=44)
            btn_historial.configure(text="📜", anchor=tk.CENTER, width=44)
            btn_ajustes.configure(text="⚙", anchor=tk.CENTER, width=44)
            
            btn_archivos.pack_configure(padx=8)
            btn_envivo.pack_configure(padx=8)
            btn_entrevista.pack_configure(padx=8)
            btn_historial.pack_configure(padx=8)
            btn_ajustes.pack_configure(padx=8)
            
            sidebar_collapsed[0] = True
        else:
            # Expand sidebar back to 200px
            sidebar_frame.configure(width=200)
            
            btn_toggle_sidebar.pack_forget()
            label_logo.pack(side=tk.LEFT, padx=(8, 0))
            btn_toggle_sidebar.pack(side=tk.RIGHT)
            btn_toggle_sidebar.configure(text="☰", width=32)
            
            btn_archivos.configure(text="📁   Archivos", anchor=tk.W, width=176)
            btn_envivo.configure(text="🎙   En vivo", anchor=tk.W, width=176)
            btn_entrevista.configure(text="🎯   Entrevista", anchor=tk.W, width=176)
            btn_historial.configure(text="📜   Historial", anchor=tk.W, width=176)
            btn_ajustes.configure(text="⚙   Ajustes", anchor=tk.W, width=176)
            
            btn_archivos.pack_configure(padx=12)
            btn_envivo.pack_configure(padx=12)
            btn_entrevista.pack_configure(padx=12)
            btn_historial.pack_configure(padx=12)
            btn_ajustes.pack_configure(padx=12)
            
            sidebar_collapsed[0] = False

    btn_toggle_sidebar.configure(command=toggle_sidebar)

    # Right Content Frame
    right_content = ctk.CTkFrame(ventana, corner_radius=0, fg_color=COLOR_BG, border_width=0)
    right_content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    # ----------------------------------------------------
    # VIEW 1: ARCHIVOS TAB
    # ----------------------------------------------------
    view_archivos = ctk.CTkFrame(right_content, corner_radius=0, fg_color="transparent")
    view_archivos.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    # Header
    header_frame = ctk.CTkFrame(view_archivos, fg_color="transparent")
    header_frame.pack(fill=tk.X, padx=24, pady=(20, 10))

    label_titulo = ctk.CTkLabel(
        header_frame, text="📁  Transcripción de archivos",
        font=("Segoe UI Semibold", 22), text_color=COLOR_TEXT_FG
    )
    label_titulo.pack(anchor=tk.W)

    label_subtitulo = ctk.CTkLabel(
        header_frame, text="🎧  Seleccioná uno o más audios y convertilos en texto offline.",
        font=("Segoe UI", 12), text_color=COLOR_MUTED
    )
    label_subtitulo.pack(anchor=tk.W, pady=(2, 0))

    # Main Body Columns
    columns_frame = ctk.CTkFrame(view_archivos, fg_color="transparent")
    columns_frame.pack(fill=tk.BOTH, expand=True, padx=24, pady=8)

    # Left Column: Listbox — fixed width so transcript area gets the space
    left_col = ctk.CTkFrame(columns_frame, fg_color="transparent", width=340)
    left_col.pack_propagate(False)
    left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=(0, 12))

    label_left_title = ctk.CTkLabel(
        left_col, text="📂  LISTA DE ARCHIVOS", font=("Segoe UI Semibold", 10), text_color="#A78BFA"
    )
    label_left_title.pack(anchor=tk.W, pady=(0, 6))

    card_listbox = ctk.CTkFrame(left_col, fg_color=COLOR_PANEL, corner_radius=12, border_color=COLOR_BORDER, border_width=1)
    card_listbox.pack(fill=tk.BOTH, expand=True)

    # Clickable Upload Zone
    dnd_frame = ctk.CTkFrame(
        card_listbox, fg_color=COLOR_PANEL_LIGHT, corner_radius=10,
        border_width=1, border_color=COLOR_BORDER, cursor="hand2"
    )
    
    # Sub-frame to center DND content vertically when expanded
    dnd_content = ctk.CTkFrame(dnd_frame, fg_color="transparent")
    dnd_content.pack(expand=True)

    # Files Listbox & Scrollbar
    lista_archivos = tk.Listbox(
        card_listbox,
        selectmode=tk.EXTENDED,
        bg="#11121A", fg=COLOR_TEXT_FG,
        selectbackground=COLOR_ACCENT, selectforeground="#ffffff",
        relief=tk.FLAT, borderwidth=0, highlightthickness=0,
        font=("Segoe UI", 10), activestyle="none",
    )
    scrollbar_listbox = ctk.CTkScrollbar(
        card_listbox, orientation="vertical",
        command=lista_archivos.yview
    )
    lista_archivos.config(yscrollcommand=scrollbar_listbox.set)

    def actualizar_vista_lista():
        if lista_archivos.size() == 0:
            lista_archivos.pack_forget()
            scrollbar_listbox.pack_forget()
            dnd_frame.pack_forget()
            dnd_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        else:
            dnd_frame.pack_forget()
            lista_archivos.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0), pady=12)
            scrollbar_listbox.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 12), pady=12)

    seleccionar_lock = [False]

    def on_dnd_click(event=None):
        if seleccionar_lock[0]:
            return "break"
        seleccionar_lock[0] = True
        seleccionar_archivos(lista_archivos, lista_archivos_paths)
        actualizar_vista_lista()
        ventana.after(600, lambda: seleccionar_lock.__setitem__(0, False))
        return "break"

    dnd_frame.bind("<Button-1>", on_dnd_click)
    dnd_content.bind("<Button-1>", on_dnd_click)

    icon_label = ctk.CTkLabel(dnd_content, text="📤", font=("Segoe UI", 22), text_color="#A78BFA")
    icon_label.pack(pady=(12, 2))
    icon_label.bind("<Button-1>", on_dnd_click)

    dnd_text1 = ctk.CTkLabel(dnd_content, text="Arrastra tus audios aquí", font=("Segoe UI Semibold", 12), text_color=COLOR_TEXT_FG)
    dnd_text1.pack(pady=1)
    dnd_text1.bind("<Button-1>", on_dnd_click)

    dnd_text2 = ctk.CTkLabel(dnd_content, text="o usa Seleccionar", font=("Segoe UI Semibold", 12), text_color="#A78BFA")
    dnd_text2.pack(pady=1)
    dnd_text2.bind("<Button-1>", on_dnd_click)

    dnd_text3 = ctk.CTkLabel(dnd_content, text="MP3  •  WAV  •  M4A  •  FLAC", font=("Segoe UI", 9), text_color=COLOR_MUTED)
    dnd_text3.pack(pady=(2, 12))
    dnd_text3.bind("<Button-1>", on_dnd_click)

    # Configure initial empty layout visibility
    actualizar_vista_lista()

    # Right Column: Transcript area
    right_col = ctk.CTkFrame(columns_frame, fg_color="transparent")
    right_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0))

    right_col_header = ctk.CTkFrame(right_col, fg_color="transparent")
    right_col_header.pack(fill=tk.X, pady=(0, 6))

    label_right_title = ctk.CTkLabel(
        right_col_header, text="📝  TRANSCRIPCIÓN", font=("Segoe UI Semibold", 10), text_color="#63B3ED"
    )
    label_right_title.pack(side=tk.LEFT)

    label_palabras = ctk.CTkLabel(
        right_col_header, text="0 palabras", font=("Segoe UI", 10), text_color=COLOR_MUTED
    )
    label_palabras.pack(side=tk.RIGHT)

    card_text = ctk.CTkFrame(right_col, fg_color=COLOR_PANEL, corner_radius=12, border_color=COLOR_BORDER, border_width=1)
    card_text.pack(fill=tk.BOTH, expand=True)

    text_area = ctk.CTkTextbox(
        card_text, fg_color="#11121A", text_color=COLOR_TEXT_FG,
        font=("Segoe UI", 12), wrap="word",
        corner_radius=8, border_width=0,
    )
    text_area.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

    # Update word count on any text change (keyboard or programmatic insert)
    def update_word_count(event=None):
        try:
            content = text_area.get("1.0", "end-1c").strip()
            words = len(content.split()) if content else 0
            label_palabras.configure(text=f"{words} palabras")
            if event:
                text_area._textbox.edit_modified(False)
        except Exception:
            pass

    text_area.bind("<KeyRelease>", update_word_count)
    try:
        text_area._textbox.bind("<<Modified>>", update_word_count)
    except Exception:
        pass

    # Options Card
    card_options = ctk.CTkFrame(view_archivos, fg_color=COLOR_PANEL, corner_radius=12, border_color=COLOR_BORDER, border_width=1)
    card_options.pack(fill=tk.X, padx=24, pady=6)

    # Options Grid Layout
    label_entrada = ctk.CTkLabel(card_options, text="🌐  IDIOMA DE ENTRADA", font=("Segoe UI Semibold", 9), text_color="#A78BFA")
    label_entrada.grid(row=0, column=0, padx=(16, 4), pady=(8, 2), sticky=tk.W)

    combobox_idioma_entrada = ctk.CTkComboBox(
        card_options, values=list(idiomas.keys()), state="readonly", width=140,
        fg_color=COLOR_PANEL_LIGHT, border_color=COLOR_BORDER, button_color=COLOR_BORDER,
        button_hover_color=COLOR_PANEL_LIGHT, dropdown_fg_color=COLOR_PANEL, dropdown_text_color=COLOR_TEXT_FG,
        dropdown_hover_color=COLOR_ACCENT
    )
    combobox_idioma_entrada.set("Spanish")
    combobox_idioma_entrada.grid(row=1, column=0, padx=(16, 12), pady=(0, 12), sticky=tk.W)

    label_salida = ctk.CTkLabel(card_options, text="🗣  IDIOMA DE SALIDA", font=("Segoe UI Semibold", 9), text_color="#4DA3FF")
    label_salida.grid(row=0, column=1, padx=(4, 4), pady=(8, 2), sticky=tk.W)

    combobox_idioma_salida = ctk.CTkComboBox(
        card_options, values=list(idiomas.keys()), state="readonly", width=140,
        fg_color=COLOR_PANEL_LIGHT, border_color=COLOR_BORDER, button_color=COLOR_BORDER,
        button_hover_color=COLOR_PANEL_LIGHT, dropdown_fg_color=COLOR_PANEL, dropdown_text_color=COLOR_TEXT_FG,
        dropdown_hover_color=COLOR_ACCENT
    )
    combobox_idioma_salida.set("Spanish")
    combobox_idioma_salida.grid(row=1, column=1, padx=(4, 12), pady=(0, 12), sticky=tk.W)



    check_timestamps = ctk.CTkCheckBox(
        card_options, text="⏱  Incluir marcas de tiempo", variable=timestamps_var,
        font=("Segoe UI", 12), text_color="#F6AD55",
        fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, border_color=COLOR_BORDER
    )
    check_timestamps.grid(row=1, column=2, padx=(32, 16), pady=(4, 12), sticky=tk.E)
    card_options.grid_columnconfigure(2, weight=1)


    # Action Buttons Row
    buttons_frame = ctk.CTkFrame(view_archivos, fg_color="transparent")
    buttons_frame.pack(fill=tk.X, padx=24, pady=6)

    def on_seleccionar():
        seleccionar_archivos(lista_archivos, lista_archivos_paths)
        actualizar_vista_lista()

    def on_borrar():
        borrar_y_actualizar(lista_archivos, lista_archivos_paths, boton_borrar)
        actualizar_vista_lista()

    boton_seleccionar = ctk.CTkButton(
        buttons_frame, text="📁   Seleccionar", font=("Segoe UI Semibold", 12),
        fg_color=COLOR_PANEL_LIGHT, text_color="#A78BFA", hover_color=COLOR_BORDER,
        width=120, height=36, corner_radius=8,
        command=on_seleccionar
    )
    boton_seleccionar.pack(side=tk.LEFT, padx=(0, 8))

    boton_borrar = ctk.CTkButton(
        buttons_frame, text="🗑️   Borrar", font=("Segoe UI Semibold", 12),
        fg_color=COLOR_PANEL_LIGHT, text_color="#E53E3E", hover_color="#3D1D1D",
        width=90, height=36, corner_radius=8, state="disabled",
        command=on_borrar
    )
    boton_borrar.pack(side=tk.LEFT, padx=(0, 8))

    # Dynamic Progress container for files transcription (initially unpacked)
    frame_progress = ctk.CTkFrame(view_archivos, fg_color="transparent")

    frame_status_info = ctk.CTkFrame(frame_progress, fg_color="transparent")
    frame_status_info.pack(pady=4)

    spinner = Spinner(
        frame_status_info, size=20, bg=COLOR_BG,
        accent_color=COLOR_ACCENT, muted_color=COLOR_MUTED
    )

    progress_label = ctk.CTkLabel(
        frame_status_info, textvariable=archivo_procesando,
        font=("Segoe UI", 12), text_color=COLOR_MUTED
    )
    progress_label.pack(side=tk.LEFT)

    progress_bar = ttk.Progressbar(
        frame_progress,
        orient="horizontal",
        mode="determinate",
        style="Archivos.Horizontal.TProgressbar",
    )
    progress_bar.pack_forget()

    def resumir_transcripcion_files():
        texto = text_area.get("1.0", tk.END).strip()
        if not texto:
            messagebox.showwarning("Advertencia", "No hay texto para resumir.")
            return
            
        if not summarizer.is_configured():
            messagebox.showinfo(
                "Resumen no configurado",
                "Falta GEMINI_API_KEY. Crea un archivo .env en la raíz del proyecto "
                "con tu clave para habilitar el resumen.",
            )
            return

        # Check if a summary already exists in the database
        if _last_file_path:
            record = config.repository.get(_last_file_path)
            if record and record.summary and record.summary.strip():
                use_cached = messagebox.askyesno(
                    "Resumen guardado",
                    "Ya existe un resumen guardado para este archivo.\n\n"
                    "¿Querés usar el resumen guardado? (sin costo)\n\n"
                    "Seleccioná NO para regenerarlo con IA (consume API).",
                )
                if use_cached:
                    mostrar_resumen_modal(record.summary)
                    return
            
        boton_resumir.configure(state="disabled")
        archivo_procesando.set("Resumiendo con IA usando Gemini...")
        frame_progress.pack(side=tk.TOP, fill=tk.X, pady=(10, 0))
        spinner.start()
        spinner.pack(side=tk.LEFT, padx=5)
        
        def run():
            try:
                resumen = summarizer.summarize(texto)
                # Guardar el resumen en la BD si hay un archivo asociado
                if _last_file_path:
                    config.repository.update_summary(_last_file_path, resumen)
                ventana.after(0, lambda r=resumen: mostrar_resumen_modal(r))
            except Exception as e:
                err_msg = str(e)
                ventana.after(0, lambda msg=err_msg: messagebox.showerror("Error de Resumen IA", f"No se pudo completar el resumen:\n\n{msg}"))
            finally:
                ventana.after(0, clean_up_resumir)
                
        def clean_up_resumir():
            boton_resumir.configure(state="normal")
            archivo_procesando.set("")
            spinner.stop()
            spinner.pack_forget()
            frame_progress.pack_forget()
            
        def mostrar_resumen_modal(summary):
            win = ctk.CTkToplevel(ventana)
            win.title("Resumen de IA")
            win.geometry("560x520")
            win.configure(fg_color=COLOR_BG)
            
            # Make modal stay on top
            win.transient(ventana)
            win.grab_set()
            
            ctk.CTkLabel(
                win, text="Resumen de la transcripción",
                font=("Segoe UI Semibold", 18), text_color="#FFFFFF"
            ).pack(anchor=tk.W, padx=20, pady=(16, 8))
            
            card_box = ctk.CTkFrame(win, fg_color=COLOR_PANEL, corner_radius=12, border_color=COLOR_BORDER, border_width=1)
            card_box.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 16))
            
            txt_resumen = ctk.CTkTextbox(
                card_box, fg_color="#11121A", text_color=COLOR_TEXT_FG,
                font=("Segoe UI", 12), corner_radius=8, border_width=0
            )
            txt_resumen.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
            txt_resumen.insert("1.0", summary)
            txt_resumen.configure(state="disabled") # read-only
            
            # Buttons row
            btn_row = ctk.CTkFrame(win, fg_color="transparent")
            btn_row.pack(fill=tk.X, padx=20, pady=(0, 16))
            
            def copiar_resumen():
                win.clipboard_clear()
                win.clipboard_append(summary)
                messagebox.showinfo("Copiado", "El resumen ha sido copiado al portapapeles.", parent=win)
                
            btn_copy = ctk.CTkButton(
                btn_row, text="📋   Copiar", font=("Segoe UI Semibold", 12),
                fg_color=COLOR_ACCENT, text_color="#FFFFFF", hover_color=COLOR_ACCENT_HOVER,
                width=100, height=36, corner_radius=8, command=copiar_resumen
            )
            btn_copy.pack(side=tk.LEFT)
            
            btn_close = ctk.CTkButton(
                btn_row, text="Cerrar", font=("Segoe UI Semibold", 12),
                fg_color=COLOR_PANEL_LIGHT, text_color=COLOR_TEXT_FG, hover_color=COLOR_BORDER,
                width=80, height=36, corner_radius=8, command=win.destroy
            )
            btn_close.pack(side=tk.RIGHT)
            
        threading.Thread(target=run, daemon=True).start()

    boton_transcribir = ctk.CTkButton(
        buttons_frame, text="🎙️   Transcribir", font=("Segoe UI Semibold", 12),
        fg_color=COLOR_ACCENT, text_color="#FFFFFF", hover_color=COLOR_ACCENT_HOVER,
        width=120, height=36, corner_radius=8,
        command=lambda: iniciar_transcripcion_thread(
            lista_archivos,
            text_area,
            archivo_procesando,
            lista_archivos_paths,
            transcripcion_resultado,
            progress_bar,
            ventana,
            boton_transcribir,
            combobox_idioma_entrada,
            combobox_idioma_salida,
            diarizar_var,
            marcas_tiempo_var=timestamps_var,
            spinner=spinner,
            frame_progress=frame_progress,
            on_file_done=_on_file_done,
        ),
    )
    boton_transcribir.pack(side=tk.LEFT, padx=(0, 8))

    boton_resumir = ctk.CTkButton(
        buttons_frame, text="✨   Resumir", font=("Segoe UI Semibold", 12),
        fg_color=COLOR_PANEL_LIGHT, text_color="#F6E05E", hover_color=COLOR_BORDER,
        width=100, height=36, corner_radius=8,
        command=resumir_transcripcion_files
    )
    boton_resumir.pack(side=tk.LEFT, padx=(0, 8))

    boton_exportar = ctk.CTkButton(
        buttons_frame, text="📥   Exportar", font=("Segoe UI Semibold", 12),
        fg_color=COLOR_PANEL_LIGHT, text_color="#48BB78", hover_color=COLOR_BORDER,
        width=100, height=36, corner_radius=8,
        command=lambda: exportar_transcripcion(text_area.get("1.0", tk.END))
    )
    boton_exportar.pack(side=tk.RIGHT, padx=(8, 0))

    boton_limpiar = ctk.CTkButton(
        buttons_frame, text="🧹   Limpiar", font=("Segoe UI Semibold", 12),
        fg_color=COLOR_PANEL_LIGHT, text_color="#F6AD55", hover_color=COLOR_BORDER,
        width=90, height=36, corner_radius=8,
        command=lambda: limpiar(text_area)
    )
    boton_limpiar.pack(side=tk.RIGHT)

    # Tooltips — shown on hover after 600 ms
    Tooltip(boton_seleccionar, "Abrí el explorador de archivos para agregar audios MP3, WAV, M4A o FLAC a la cola de transcripción.")
    Tooltip(boton_borrar, "Eliminá el archivo seleccionado de la lista (no borra el archivo del disco).")
    Tooltip(boton_transcribir, "Iniciá la transcripción del archivo seleccionado usando el modelo Whisper offline. Podés detenerlo en cualquier momento.")
    Tooltip(boton_resumir, "Generá un resumen con IA (Gemini) del texto transcrito que aparece en el área de texto. Necesita conexión a internet.")
    Tooltip(boton_exportar, "Guardá el texto transcrito como archivo .txt, documento de Word (.docx) o subtítulos (.srt, .vtt).")
    Tooltip(boton_limpiar, "Limpiá el área de texto de transcripciones anteriores.")
    Tooltip(check_timestamps, "Inserta marcas de tiempo [MM:SS - MM:SS] al inicio de cada segmento transcrito.")


    # ----------------------------------------------------
    # PLAYER CARD (Card B)
    # ----------------------------------------------------
    card_reproductor = ctk.CTkFrame(view_archivos, fg_color=COLOR_PANEL, corner_radius=12, border_color=COLOR_BORDER, border_width=1)
    card_reproductor.pack(side=tk.TOP, fill=tk.X, padx=24, pady=6)

    # Fila de la barra de progreso (Scale)
    frame_slider = ctk.CTkFrame(card_reproductor, fg_color="transparent")
    # Not packed at startup, packed dynamically on playback

    slider_progreso = ttk.Scale(
        frame_slider,
        from_=0,
        to=100,
        orient=tk.HORIZONTAL,
        style="ProgressScale.Horizontal.TScale",
    )
    slider_arrastrando = [False]

    # Controls Row
    frame_reproduccion = ctk.CTkFrame(card_reproductor, fg_color="transparent")
    frame_reproduccion.pack(side=tk.TOP, fill=tk.X, padx=16, pady=12)

    # Circular Player buttons
    boton_reproducir = ctk.CTkButton(
        frame_reproduccion, text="▶", font=("Segoe UI", 13),
        fg_color=COLOR_ACCENT, text_color="#FFFFFF", hover_color=COLOR_ACCENT_HOVER,
        width=36, height=36, corner_radius=18,
        command=lambda: reproducir(
            lista_archivos,
            lista_archivos_paths,
            boton_pausar_reanudar,
            label_reproduccion,
            label_tiempo,
            boton_adelantar,
            boton_retroceder,
            slider_progreso=slider_progreso,
            slider_arrastrando=slider_arrastrando,
            frame_slider=frame_slider,
        ),
    )
    boton_reproducir.pack(side=tk.LEFT)

    boton_retroceder = ctk.CTkButton(
        frame_reproduccion, text="⏪", font=("Segoe UI", 12),
        fg_color="transparent", text_color=COLOR_TEXT_FG, hover_color=COLOR_PANEL_LIGHT,
        width=36, height=36, corner_radius=18, state="disabled",
        command=lambda: retroceder(label_tiempo)
    )
    boton_retroceder.pack(side=tk.LEFT, padx=(6, 0))

    boton_pausar_reanudar = ctk.CTkButton(
        frame_reproduccion, text="⏸", font=("Segoe UI", 12),
        fg_color="transparent", text_color=COLOR_TEXT_FG, hover_color=COLOR_PANEL_LIGHT,
        width=36, height=36, corner_radius=18, state="disabled",
        command=lambda: pausar_reanudar(
            boton_pausar_reanudar,
            label_reproduccion,
            label_tiempo,
            boton_adelantar,
            boton_retroceder,
            slider_progreso=slider_progreso,
            slider_arrastrando=slider_arrastrando,
        ),
    )
    boton_pausar_reanudar.pack(side=tk.LEFT, padx=(6, 0))

    boton_adelantar = ctk.CTkButton(
        frame_reproduccion, text="⏩", font=("Segoe UI", 12),
        fg_color="transparent", text_color=COLOR_TEXT_FG, hover_color=COLOR_PANEL_LIGHT,
        width=36, height=36, corner_radius=18, state="disabled",
        command=lambda: adelantar(label_tiempo)
    )
    boton_adelantar.pack(side=tk.LEFT, padx=(6, 0))

    boton_detener = ctk.CTkButton(
        frame_reproduccion, text="⏹", font=("Segoe UI", 12),
        fg_color="transparent", text_color=COLOR_TEXT_FG, hover_color=COLOR_PANEL_LIGHT,
        width=36, height=36, corner_radius=18,
        command=lambda: detener_reproduccion(
            boton_pausar_reanudar,
            label_reproduccion,
            label_tiempo,
            boton_adelantar,
            boton_retroceder,
            slider_progreso=slider_progreso,
            frame_slider=frame_slider,
        ),
    )
    boton_detener.pack(side=tk.LEFT, padx=(6, 0))

    label_reproduccion = ctk.CTkLabel(
        frame_reproduccion, text="", font=("Segoe UI", 11), text_color=COLOR_MUTED
    )
    label_reproduccion.pack(side=tk.LEFT, padx=(16, 6))

    label_tiempo = ctk.CTkLabel(
        frame_reproduccion, text="00:00 / 00:00", font=("Segoe UI", 11), text_color=COLOR_TEXT_FG
    )
    label_tiempo.pack(side=tk.LEFT)

    Tooltip(boton_reproducir, "Reproducí el archivo de audio seleccionado en la lista.")
    Tooltip(boton_retroceder, "Retrocedé 5 segundos en la reproducción.")
    Tooltip(boton_pausar_reanudar, "Pausá o reanudá la reproducción.")
    Tooltip(boton_adelantar, "Avanzá 5 segundos en la reproducción.")
    Tooltip(boton_detener, "Detené la reproducción y volvé al inicio.")
    Tooltip(label_tiempo, "Tiempo actual / duración total del audio.")

    # Bind lists selection
    lista_archivos.bind(
        "<<ListboxSelect>>", lambda event: activar_boton_borrar(event, boton_borrar)
    )

    # Slider Drag events
    def iniciar_arrastre(event):
        slider_arrastrando[0] = True

    def finalizar_arrastre(event):
        slider_arrastrando[0] = False
        if reproductor.audio_actual:
            nuevo_tiempo = slider_progreso.get()
            reproductor.posicion_actual = nuevo_tiempo
            if reproductor.reproduciendo:
                if pygame.mixer.get_init():
                    try:
                        pygame.mixer.music.play(start=nuevo_tiempo)
                    except pygame.error:
                        pass
                reproductor.tiempo_inicio = time.time() - nuevo_tiempo
            else:
                label_tiempo.configure(text=reproductor.obtener_tiempo_formateado())

    slider_progreso.bind("<ButtonPress-1>", iniciar_arrastre)
    slider_progreso.bind("<ButtonRelease-1>", finalizar_arrastre)

    # Playback Hotkeys
    def hotkey_play_pause(event=None):
        if boton_pausar_reanudar.cget("state") == "normal":
            boton_pausar_reanudar.invoke()
        elif boton_reproducir.cget("state") == "normal":
            boton_reproducir.invoke()

    def hotkey_retroceder(event=None):
        if boton_retroceder.cget("state") == "normal":
            boton_retroceder.invoke()

    def hotkey_adelantar(event=None):
        if boton_adelantar.cget("state") == "normal":
            boton_adelantar.invoke()

    def toggle_fullscreen(event=None):
        is_fs = ventana.attributes("-fullscreen")
        ventana.attributes("-fullscreen", not is_fs)
        return "break"

    # Keyboard bindings
    ventana.bind("<Alt-p>", hotkey_play_pause)
    ventana.bind("<Alt-P>", hotkey_play_pause)
    ventana.bind("<Alt-Left>", hotkey_retroceder)
    ventana.bind("<Alt-Right>", hotkey_adelantar)
    ventana.bind("<F11>", toggle_fullscreen)

    # ----------------------------------------------------
    # VIEW 2: LIVE TRANSCRIPTION (EN VIVO)
    # ----------------------------------------------------
    view_envivo = LiveFrame(right_content)
    # Initially hidden, packed dynamically via switch_view

    # ----------------------------------------------------
    # VIEW 3: INTERVIEW COACH
    # ----------------------------------------------------
    view_entrevista = InterviewFrame(right_content)

    # ----------------------------------------------------
    # VIEW 4: SETTINGS (AJUSTES)
    # ----------------------------------------------------
    view_ajustes = AjustesFrame(right_content)
    # Initially hidden, packed dynamically via switch_view

    # ----------------------------------------------------
    # VIEW 5: HISTORY (HISTORIAL)
    # ----------------------------------------------------
    view_historial = HistorialFrame(right_content)
    # Initially hidden, packed dynamically via switch_view

    # Credits footer (Bottom of view_archivos)
    frame_creditos = ctk.CTkFrame(view_archivos, fg_color="transparent")
    frame_creditos.pack(side=tk.BOTTOM, pady=8)

    import webbrowser
    def abrir_web(event=None):
        try:
            webbrowser.open_new("https://tecnofuision-it.web.app/")
        except Exception:
            pass

    label_creditos = ctk.CTkLabel(
        frame_creditos,
        text="© 2026 - AudioText v2.0 - tecnofusion.it",
        font=("Segoe UI", 10, "underline"), text_color=COLOR_MUTED,
        cursor="hand2"
    )
    label_creditos.bind("<Button-1>", abrir_web)
    label_creditos.bind("<Enter>", lambda e: label_creditos.configure(text_color=COLOR_ACCENT))
    label_creditos.bind("<Leave>", lambda e: label_creditos.configure(text_color=COLOR_MUTED))
    label_creditos.pack()

    # Responsive layout adjustment
    last_width = [0]
    _resize_after_id = [None]

    def _apply_layout(new_width):
        """Actually perform the layout changes (called after debounce)."""
        _resize_after_id[0] = None

        if new_width == last_width[0]:
            return
        last_width[0] = new_width

        # Threshold at 850px width
        if new_width < 850:
            # 1-Column Layout: Stack Listbox and Transcript vertically
            left_col.pack_forget()
            right_col.pack_forget()
            left_col.configure(width=new_width - 48, height=180)
            left_col.pack(side=tk.TOP, fill=tk.BOTH, expand=False, pady=(0, 10))
            right_col.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(10, 0))

            # Vertical Option Card Grid Layout
            label_entrada.grid_forget()
            combobox_idioma_entrada.grid_forget()
            label_salida.grid_forget()
            combobox_idioma_salida.grid_forget()
            check_timestamps.grid_forget()

            label_entrada.grid(row=0, column=0, padx=16, pady=(8, 2), sticky=tk.W)
            combobox_idioma_entrada.grid(row=1, column=0, padx=16, pady=(0, 6), sticky=tk.W)
            label_salida.grid(row=2, column=0, padx=16, pady=(6, 2), sticky=tk.W)
            combobox_idioma_salida.grid(row=3, column=0, padx=16, pady=(0, 6), sticky=tk.W)
            check_timestamps.grid(row=5, column=0, padx=16, pady=(4, 12), sticky=tk.W)

            card_options.grid_columnconfigure(0, weight=1)
            card_options.grid_columnconfigure(1, weight=0)
            card_options.grid_columnconfigure(2, weight=0)

            # Wrapped Action Buttons Frame (Grid)
            boton_seleccionar.pack_forget()
            boton_borrar.pack_forget()
            boton_transcribir.pack_forget()
            boton_resumir.pack_forget()
            boton_exportar.pack_forget()
            boton_limpiar.pack_forget()

            boton_seleccionar.grid(row=0, column=1, padx=6, pady=4)
            boton_borrar.grid(row=0, column=2, padx=6, pady=4)
            boton_transcribir.grid(row=0, column=3, padx=6, pady=4)
            boton_resumir.grid(row=1, column=1, padx=6, pady=4)
            boton_limpiar.grid(row=1, column=2, padx=6, pady=4)
            boton_exportar.grid(row=1, column=3, padx=6, pady=4)

            buttons_frame.grid_columnconfigure(0, weight=1)
            buttons_frame.grid_columnconfigure(1, weight=0)
            buttons_frame.grid_columnconfigure(2, weight=0)
            buttons_frame.grid_columnconfigure(3, weight=0)
            buttons_frame.grid_columnconfigure(4, weight=1)
        else:
            # 2-Column Layout: fixed left, expanding right
            left_col.pack_forget()
            right_col.pack_forget()
            left_col.configure(width=340, height=columns_frame.winfo_height())
            left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=(0, 12))
            right_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0))

            # Horizontal Option Card Grid Layout
            label_entrada.grid_forget()
            combobox_idioma_entrada.grid_forget()
            label_salida.grid_forget()
            combobox_idioma_salida.grid_forget()
            check_timestamps.grid_forget()

            label_entrada.grid(row=0, column=0, padx=(16, 4), pady=(8, 2), sticky=tk.W)
            combobox_idioma_entrada.grid(row=1, column=0, padx=(16, 12), pady=(0, 12), sticky=tk.W)
            label_salida.grid(row=0, column=1, padx=(4, 4), pady=(8, 2), sticky=tk.W)
            combobox_idioma_salida.grid(row=1, column=1, padx=(4, 12), pady=(0, 12), sticky=tk.W)
            check_timestamps.grid(row=1, column=2, padx=(32, 16), pady=(4, 12), sticky=tk.E)

            card_options.grid_columnconfigure(0, weight=0)
            card_options.grid_columnconfigure(1, weight=0)
            card_options.grid_columnconfigure(2, weight=1)

            # Single Horizontal Row for Buttons
            boton_seleccionar.grid_forget()
            boton_borrar.grid_forget()
            boton_transcribir.grid_forget()
            boton_resumir.grid_forget()
            boton_exportar.grid_forget()
            boton_limpiar.grid_forget()

            boton_seleccionar.pack(side=tk.LEFT, padx=(0, 8))
            boton_borrar.pack(side=tk.LEFT, padx=(0, 8))
            boton_transcribir.pack(side=tk.LEFT, padx=(0, 8))
            boton_resumir.pack(side=tk.LEFT, padx=(0, 8))
            boton_exportar.pack(side=tk.RIGHT, padx=(8, 0))
            boton_limpiar.pack(side=tk.RIGHT)

            buttons_frame.grid_columnconfigure(0, weight=0)
            buttons_frame.grid_columnconfigure(1, weight=0)
            buttons_frame.grid_columnconfigure(2, weight=0)
            buttons_frame.grid_columnconfigure(3, weight=0)
            buttons_frame.grid_columnconfigure(4, weight=0)

    def on_view_configure(event):
        if event.widget != view_archivos:
            return
        new_width = event.width
        if new_width < 400:
            return
        # Cancel any pending layout update (debounce at 150ms)
        if _resize_after_id[0] is not None:
            ventana.after_cancel(_resize_after_id[0])
        _resize_after_id[0] = ventana.after(150, lambda w=new_width: _apply_layout(w))

    view_archivos.bind("<Configure>", on_view_configure)

    # Initialize view
    switch_view("archivos")

    return {
        "lista_archivos": lista_archivos,
        "text_area": text_area,
        "progress_bar": progress_bar,
        "combobox_idioma_entrada": combobox_idioma_entrada,
        "combobox_idioma_salida": combobox_idioma_salida,
        "boton_transcribir": boton_transcribir,
        "archivo_procesando": archivo_procesando,
        "lista_archivos_paths": lista_archivos_paths,
        "transcripcion_resultado": transcripcion_resultado,
        "boton_reproducir": boton_reproducir,
        "boton_pausar_renaudar": boton_pausar_reanudar,
        "boton_detener": boton_detener,
        "label_reproduccion": label_reproduccion,
        "label_tiempo": label_tiempo,
        "live_frame": view_envivo,
        "interview_frame": view_entrevista,
        "historial_frame": view_historial,
        "spinner": spinner,
        "frame_progress": frame_progress,
        "frame_slider": frame_slider,
    }


def centrar_ventana(ventana, ancho=1200, alto=700):
    ventana.update_idletasks()
    ancho_pantalla = ventana.winfo_screenwidth()
    alto_pantalla = ventana.winfo_screenheight()
    x = (ancho_pantalla // 2) - (ancho // 2)
    y = (alto_pantalla // 2) - (alto // 2)
    ventana.geometry(f"{ancho}x{alto}+{x}+{y}")


def activar_boton_borrar(event, boton_borrar):
    if event.widget.curselection():
        boton_borrar.configure(state="normal")
    else:
        boton_borrar.configure(state="disabled")


def borrar_y_actualizar(lista_archivos, lista_archivos_paths, boton_borrar):
    if borrar_archivo(lista_archivos, lista_archivos_paths):
        boton_borrar.configure(state="disabled")
