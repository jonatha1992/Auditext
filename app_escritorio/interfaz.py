import tkinter as tk
from tkinter import ttk
from funcionalidad import *
from reproductor import *
from config import idiomas
from live_frame import (
    LiveFrame,
    COLOR_BG,
    COLOR_PANEL,
    COLOR_TEXT_BG,
    COLOR_TEXT_FG,
    COLOR_ACCENT,
    COLOR_ACCENT_ACTIVE,
    COLOR_MUTED,
)


def _accent_btn(parent, text, command, **kw):
    # tk.Button (not ttk): the native Windows ttk theme ignores custom button
    # colors, so the classic widget is used for consistent dark styling.
    return tk.Button(
        parent, text=text, command=command, cursor="hand2",
        bg=COLOR_ACCENT, fg="#ffffff",
        activebackground=COLOR_ACCENT_ACTIVE, activeforeground="#ffffff",
        relief=tk.FLAT, borderwidth=0, font=("Segoe UI Semibold", 10),
        padx=14, pady=6, **kw,
    )


def _flat_btn(parent, text, command, **kw):
    return tk.Button(
        parent, text=text, command=command, cursor="hand2",
        bg=COLOR_PANEL, fg=COLOR_TEXT_FG,
        activebackground="#34344a", activeforeground=COLOR_TEXT_FG,
        disabledforeground=COLOR_MUTED,
        relief=tk.FLAT, borderwidth=0, font=("Segoe UI", 10),
        padx=12, pady=6, **kw,
    )


def _setup_theme(ventana):
    ventana.configure(bg=COLOR_BG)
    style = ttk.Style(ventana)
    # The native Windows theme ignores custom tab/combobox colors. "clam" honors
    # configure()/map(), so the dark theme actually applies.
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure("TNotebook", background=COLOR_BG, borderwidth=0)
    style.configure(
        "TNotebook.Tab", background=COLOR_PANEL, foreground=COLOR_MUTED,
        padding=(18, 9), font=("Segoe UI", 10), borderwidth=0,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", COLOR_ACCENT)],
        foreground=[("selected", "#ffffff")],
    )

    style.configure(
        "Archivos.Horizontal.TProgressbar",
        background=COLOR_ACCENT, troughcolor=COLOR_PANEL, borderwidth=0,
    )

    # Dark comboboxes.
    style.configure(
        "TCombobox", fieldbackground=COLOR_TEXT_BG, background=COLOR_PANEL,
        foreground=COLOR_TEXT_FG, arrowcolor=COLOR_TEXT_FG, borderwidth=0,
        padding=4,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", COLOR_TEXT_BG)],
        foreground=[("readonly", COLOR_TEXT_FG)],
        selectbackground=[("readonly", COLOR_TEXT_BG)],
        selectforeground=[("readonly", COLOR_TEXT_FG)],
    )
    ventana.option_add("*TCombobox*Listbox.background", COLOR_TEXT_BG)
    ventana.option_add("*TCombobox*Listbox.foreground", COLOR_TEXT_FG)
    ventana.option_add("*TCombobox*Listbox.selectBackground", COLOR_ACCENT)
    ventana.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")


def crear_interfaz(ventana):
    ventana.geometry("1200x700")
    archivo_procesando = tk.StringVar()
    lista_archivos_paths = {}
    transcripcion_resultado = ""
    diarizar_var = tk.BooleanVar(master=ventana, value=False)

    _setup_theme(ventana)

    # Two tabs: file transcription (existing) and live system-audio transcription.
    notebook = ttk.Notebook(ventana)
    notebook.pack(fill=tk.BOTH, expand=True)

    tab_archivos = tk.Frame(notebook, bg=COLOR_BG)
    notebook.add(tab_archivos, text="Archivos")

    live_frame = LiveFrame(notebook)
    notebook.add(live_frame, text="En vivo")

    # Header (top), then bottom-pinned control bars, then the body fills the
    # middle. Pinning controls to the bottom keeps them visible regardless of
    # window height (otherwise the tall text area pushes them off-screen).
    label_titulo = tk.Label(
        tab_archivos, text="Transcripcion de archivos",
        font=("Segoe UI Semibold", 15), bg=COLOR_BG, fg=COLOR_TEXT_FG,
    )
    label_titulo.pack(side=tk.TOP, anchor=tk.W, padx=16, pady=(14, 8))

    # Bottom container is packed first so it reserves the bottom edge; the body
    # (main_frame) then fills the remaining middle and can never push the
    # controls off-screen.
    bottom = tk.Frame(tab_archivos, bg=COLOR_BG)
    bottom.pack(side=tk.BOTTOM, fill=tk.X)

    main_frame = tk.Frame(tab_archivos, bg=COLOR_BG)
    main_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=16, pady=(0, 6))

    frame_listbox = tk.Frame(main_frame, bg=COLOR_BG)
    frame_listbox.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 12), pady=2)

    label_listbox = tk.Label(
        frame_listbox, text="Lista de archivos",
        bg=COLOR_BG, fg=COLOR_MUTED, font=("Segoe UI", 10),
    )
    label_listbox.pack(side=tk.TOP, anchor=tk.W, pady=(0, 4))

    scrollbar_listbox = tk.Scrollbar(
        frame_listbox, orient=tk.VERTICAL,
        bg=COLOR_PANEL, troughcolor=COLOR_BG, activebackground=COLOR_ACCENT,
        borderwidth=0, highlightthickness=0,
    )
    lista_archivos = tk.Listbox(
        frame_listbox,
        selectmode=tk.EXTENDED,
        width=44,
        height=12,
        yscrollcommand=scrollbar_listbox.set,
        bg=COLOR_TEXT_BG, fg=COLOR_TEXT_FG,
        selectbackground=COLOR_ACCENT, selectforeground="#ffffff",
        relief=tk.FLAT, borderwidth=0, highlightthickness=0,
        font=("Segoe UI", 10), activestyle="none",
    )

    lista_archivos.pack(side=tk.LEFT, fill=tk.BOTH)
    scrollbar_listbox.pack(side=tk.RIGHT, fill=tk.Y)
    scrollbar_listbox.config(command=lista_archivos.yview)

    frame_text = tk.Frame(main_frame, bg=COLOR_BG)
    frame_text.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(12, 0), pady=2)

    label_text_area = tk.Label(
        frame_text, text="Transcripcion",
        bg=COLOR_BG, fg=COLOR_MUTED, font=("Segoe UI", 10),
    )
    label_text_area.pack(side=tk.TOP, anchor=tk.W, pady=(0, 4))

    scrollbar_text = tk.Scrollbar(
        frame_text, orient=tk.VERTICAL,
        bg=COLOR_PANEL, troughcolor=COLOR_BG, activebackground=COLOR_ACCENT,
        borderwidth=0, highlightthickness=0,
    )
    text_area = tk.Text(
        frame_text, height=12, width=81, yscrollcommand=scrollbar_text.set,
        bg=COLOR_TEXT_BG, fg=COLOR_TEXT_FG, insertbackground=COLOR_TEXT_FG,
        relief=tk.FLAT, borderwidth=0, highlightthickness=0,
        font=("Segoe UI", 12), padx=12, pady=10, spacing3=4,
    )
    text_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar_text.pack(side=tk.RIGHT, fill=tk.Y)
    scrollbar_text.config(command=text_area.yview)

    frame_botones = tk.Frame(bottom, bg=COLOR_BG)
    frame_botones.pack(side=tk.TOP, pady=8, padx=16, fill=tk.X)

    boton_seleccionar = _flat_btn(
        frame_botones, "\U0001F4C2  Seleccionar",
        lambda: seleccionar_archivos(lista_archivos, lista_archivos_paths),
    )
    boton_seleccionar.pack(side=tk.LEFT)

    boton_borrar = _flat_btn(
        frame_botones, "\U0001F5D1  Borrar",
        lambda: borrar_y_actualizar(lista_archivos, lista_archivos_paths, boton_borrar),
        state="disabled",
    )
    boton_borrar.pack(side=tk.LEFT, padx=(8, 0))

    boton_transcribir = _accent_btn(
        frame_botones, "\U0001F4DD  Transcribir",
        lambda: iniciar_transcripcion_thread(
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
        ),
    )
    boton_transcribir.pack(side=tk.LEFT, padx=(8, 0))

    boton_exportar = _flat_btn(
        frame_botones, "\U0001F4BE  Exportar",
        lambda: exportar_transcripcion(text_area.get("1.0", tk.END)),
    )
    boton_exportar.pack(side=tk.RIGHT)

    boton_limpiar = _flat_btn(
        frame_botones, "\U0001F9F9  Limpiar",
        lambda: limpiar(text_area),
    )
    boton_limpiar.pack(side=tk.RIGHT, padx=(0, 8))

    frame_progress = tk.Frame(bottom, bg=COLOR_BG)
    frame_progress.pack(side=tk.TOP, fill=tk.X, padx=16, pady=0)

    progress_label = tk.Label(
        frame_progress, textvariable=archivo_procesando,
        bg=COLOR_BG, fg=COLOR_MUTED, font=("Segoe UI", 10),
    )
    progress_label.pack(pady=4)

    progress_bar = ttk.Progressbar(
        frame_progress,
        orient="horizontal",
        mode="determinate",
        style="Archivos.Horizontal.TProgressbar",
    )
    progress_bar.pack_forget()

    frame_opciones = tk.Frame(bottom, bg=COLOR_BG)
    frame_opciones.pack(side=tk.TOP, pady=6, padx=16, fill=tk.X)

    tk.Label(
        frame_opciones, text="Idioma de entrada",
        bg=COLOR_BG, fg=COLOR_MUTED, font=("Segoe UI", 10),
    ).pack(side=tk.LEFT, padx=(0, 6))
    combobox_idioma_entrada = ttk.Combobox(
        frame_opciones, values=list(idiomas.keys()), state="readonly", width=14
    )
    combobox_idioma_entrada.set("Spanish")
    combobox_idioma_entrada.pack(side=tk.LEFT, padx=(0, 20))

    tk.Label(
        frame_opciones, text="Idioma de salida",
        bg=COLOR_BG, fg=COLOR_MUTED, font=("Segoe UI", 10),
    ).pack(side=tk.LEFT, padx=(0, 6))
    combobox_idioma_salida = ttk.Combobox(
        frame_opciones, values=list(idiomas.keys()), state="readonly", width=14
    )
    combobox_idioma_salida.set("Spanish")
    combobox_idioma_salida.pack(side=tk.LEFT)

    tk.Checkbutton(
        frame_opciones, text="Diferenciar hablantes", variable=diarizar_var,
        bg=COLOR_BG, fg=COLOR_TEXT_FG, selectcolor=COLOR_PANEL,
        activebackground=COLOR_BG, activeforeground=COLOR_TEXT_FG,
        font=("Segoe UI", 10), borderwidth=0, highlightthickness=0,
        cursor="hand2",
    ).pack(side=tk.LEFT, padx=(20, 0))

    frame_reproduccion = tk.Frame(bottom, bg=COLOR_BG)
    frame_reproduccion.pack(side=tk.TOP, pady=8, padx=16, fill=tk.X)

    boton_reproducir = _flat_btn(
        frame_reproduccion, "▶  Reproducir",
        lambda: reproducir(
            lista_archivos,
            lista_archivos_paths,
            boton_pausar_reanudar,
            label_reproduccion,
            label_tiempo,
            boton_adelantar,
            boton_retroceder,
        ),
    )
    boton_reproducir.pack(side=tk.LEFT)

    boton_retroceder = _flat_btn(
        frame_reproduccion, "⏪  -5s",
        lambda: retroceder(label_tiempo), state="disabled",
    )
    boton_retroceder.pack(side=tk.LEFT, padx=(8, 0))

    lista_archivos.bind(
        "<<ListboxSelect>>", lambda event: activar_boton_borrar(event, boton_borrar)
    )
    boton_pausar_reanudar = _flat_btn(
        frame_reproduccion, "⏸  Pausar",
        lambda: pausar_reanudar(boton_pausar_reanudar, label_reproduccion, label_tiempo),
        state="disabled",
    )
    boton_pausar_reanudar.pack(side=tk.LEFT, padx=(8, 0))

    boton_adelantar = _flat_btn(
        frame_reproduccion, "⏩  +5s",
        lambda: adelantar(label_tiempo), state="disabled",
    )
    boton_adelantar.pack(side=tk.LEFT, padx=(8, 0))

    boton_detener = _flat_btn(
        frame_reproduccion, "⏹  Detener",
        lambda: detener_reproduccion(
            boton_pausar_reanudar,
            label_reproduccion,
            label_tiempo,
            boton_adelantar,
            boton_retroceder,
        ),
    )
    boton_detener.pack(side=tk.LEFT, padx=(8, 0))

    label_reproduccion = tk.Label(
        frame_reproduccion, text="", bg=COLOR_BG, fg=COLOR_MUTED, font=("Segoe UI", 10)
    )
    label_reproduccion.pack(side=tk.LEFT, padx=(16, 6))

    label_tiempo = tk.Label(
        frame_reproduccion, text="00:00 / 00:00",
        bg=COLOR_BG, fg=COLOR_TEXT_FG, font=("Segoe UI", 10),
    )
    label_tiempo.pack(side=tk.LEFT)

    frame_creditos = tk.Frame(bottom, bg=COLOR_BG)
    frame_creditos.pack(side=tk.TOP, pady=6)

    label_creditos = tk.Label(
        frame_creditos,
        text="@Copyright 2024 Version 1.0 Produced by Correa Jonathan",
        font=("Segoe UI", 9), bg=COLOR_BG, fg=COLOR_MUTED,
    )
    label_creditos.pack()

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
    }


def centrar_ventana(ventana):
    ventana.update_idletasks()
    ancho_ventana = ventana.winfo_width()
    alto_ventana = ventana.winfo_height()
    ancho_pantalla = ventana.winfo_screenwidth()
    alto_pantalla = ventana.winfo_screenheight()
    x = (ancho_pantalla // 2) - (ancho_ventana // 2)
    y = (alto_pantalla // 2) - (alto_ventana // 2)
    ventana.geometry(f"{ancho_ventana}x{alto_ventana}+{x}+{y}")


def activar_boton_borrar(event, boton_borrar):
    if event.widget.curselection():
        boton_borrar.config(state="normal")
    else:
        boton_borrar.config(state="disabled")


def borrar_y_actualizar(lista_archivos, lista_archivos_paths, boton_borrar):
    if borrar_archivo(lista_archivos, lista_archivos_paths):
        boton_borrar.config(state="disabled")
