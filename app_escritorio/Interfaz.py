import tkinter as tk
from tkinter import ttk
from Funcionalidad import *
from Reproductor import *
from Config import idiomas


def crear_interfaz(ventana):
    ventana.geometry("1200x700")
    archivo_procesando = tk.StringVar()
    lista_archivos_paths = {}
    transcripcion_resultado = ""

    main_frame = tk.Frame(ventana)
    main_frame.pack(pady=10, padx=10, fill=tk.BOTH, expand=True)

    label_titulo = tk.Label(main_frame, text="AudioText", font=("Helvetica", 16))
    label_titulo.pack(side=tk.TOP, pady=5)

    frame_listbox = tk.Frame(main_frame)
    frame_listbox.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=2)

    label_listbox = tk.Label(frame_listbox, text="Lista de archivos")
    label_listbox.pack(side=tk.TOP)

    scrollbar_listbox = tk.Scrollbar(frame_listbox, orient=tk.VERTICAL)
    lista_archivos = tk.Listbox(
        frame_listbox,
        selectmode=tk.EXTENDED,
        width=50,
        height=20,
        yscrollcommand=scrollbar_listbox.set,
    )

    lista_archivos.pack(side=tk.LEFT, fill=tk.BOTH)
    scrollbar_listbox.pack(side=tk.RIGHT, fill=tk.Y)
    scrollbar_listbox.config(command=lista_archivos.yview)

    frame_text = tk.Frame(main_frame)
    frame_text.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=10, pady=2)

    label_text_area = tk.Label(frame_text, text="Transcripción")
    label_text_area.pack(side=tk.TOP)

    scrollbar_text = tk.Scrollbar(frame_text, orient=tk.VERTICAL)
    text_area = tk.Text(
        frame_text, height=25, width=81, yscrollcommand=scrollbar_text.set
    )
    text_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar_text.pack(side=tk.RIGHT, fill=tk.Y)
    scrollbar_text.config(command=text_area.yview)

    frame_botones = tk.Frame(ventana)
    frame_botones.pack(side=tk.TOP, pady=10, padx=20, fill=tk.X)

    boton_seleccionar = tk.Button(
        frame_botones,
        text="Seleccionar Archivos",
        command=lambda: seleccionar_archivos(lista_archivos, lista_archivos_paths),
    )
    boton_seleccionar.pack(side=tk.LEFT, padx=10)

    boton_borrar = tk.Button(
        frame_botones,
        text="Borrar",
        state="disabled",
        command=lambda: borrar_y_actualizar(
            lista_archivos, lista_archivos_paths, boton_borrar
        ),
    )
    boton_borrar.pack(side=tk.LEFT, padx=5)

    boton_transcribir = tk.Button(
        frame_botones,
        text="Transcribir",
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
        ),
    )
    boton_transcribir.pack(side=tk.LEFT, padx=5)

    boton_exportar = tk.Button(
        frame_botones,
        text="Exportar",
        command=lambda: exportar_transcripcion(text_area.get("1.0", tk.END)),
    )
    boton_exportar.pack(side=tk.RIGHT, padx=10)

    boton_limpiar = tk.Button(
        frame_botones,
        text="Limpiar",
        command=lambda: limpiar(text_area),
    )
    boton_limpiar.pack(side=tk.RIGHT, padx=10)

    frame_progress = tk.Frame(ventana)
    frame_progress.pack(fill=tk.X, padx=30, pady=0)

    progress_label = tk.Label(frame_progress, textvariable=archivo_procesando)
    progress_label.pack(pady=5)

    progress_bar = ttk.Progressbar(
        frame_progress,
        orient="horizontal",
        mode="determinate",
    )
    progress_bar.pack_forget()

    frame_idioma_entrada = tk.Frame(ventana)
    frame_idioma_entrada.pack(side=tk.TOP, pady=5)

    label_idioma_entrada = tk.Label(frame_idioma_entrada, text=" Idioma de Entrada:")
    label_idioma_entrada.pack(side=tk.LEFT, padx=10)

    combobox_idioma_entrada = ttk.Combobox(
        frame_idioma_entrada, values=list(idiomas.keys()), state="readonly"
    )
    combobox_idioma_entrada.set("Spanish")
    combobox_idioma_entrada.pack(side=tk.LEFT, padx=10)

    frame_idioma_salida = tk.Frame(ventana)
    frame_idioma_salida.pack(side=tk.TOP, pady=5)

    label_idioma_salida = tk.Label(frame_idioma_salida, text="Idioma de Salida:")
    label_idioma_salida.pack(side=tk.LEFT, padx=10)

    combobox_idioma_salida = ttk.Combobox(
        frame_idioma_salida, values=list(idiomas.keys()), state="readonly"
    )
    combobox_idioma_salida.set("Spanish")
    combobox_idioma_salida.pack(side=tk.LEFT, padx=10)

    frame_reproduccion = tk.Frame(ventana)
    frame_reproduccion.pack(side=tk.TOP, pady=5)

    boton_reproducir = tk.Button(
        frame_reproduccion,
        text="Reproducir",
        command=lambda: reproducir(
            lista_archivos,
            lista_archivos_paths,
            boton_pausar_reanudar,
            label_reproduccion,
            label_tiempo,
            boton_adelantar,
            boton_retroceder,
        ),
    )
    boton_reproducir.pack(side=tk.LEFT, padx=5)

    boton_retroceder = tk.Button(
        frame_reproduccion,
        text="Retroceder 5s",
        state="disabled",
        command=lambda: retroceder(label_tiempo),
    )
    boton_retroceder.pack(side=tk.LEFT, padx=5)

    lista_archivos.bind(
        "<<ListboxSelect>>", lambda event: activar_boton_borrar(event, boton_borrar)
    )
    boton_pausar_reanudar = tk.Button(
        frame_reproduccion,
        text="Pausar",
        state="disabled",
        command=lambda: pausar_reanudar(
            boton_pausar_reanudar, label_reproduccion, label_tiempo
        ),
    )
    boton_pausar_reanudar.pack(side=tk.LEFT, padx=5)

    boton_adelantar = tk.Button(
        frame_reproduccion,
        text="Adelantar 5s",
        state="disabled",
        command=lambda: adelantar(label_tiempo),
    )
    boton_adelantar.pack(side=tk.LEFT, padx=5)
    boton_detener = tk.Button(
        frame_reproduccion,
        text="Detener",
        command=lambda: detener_reproduccion(
            boton_pausar_reanudar,
            label_reproduccion,
            label_tiempo,
            boton_adelantar,
            boton_retroceder,
        ),
    )
    boton_detener.pack(side=tk.LEFT, padx=5)

    label_reproduccion = tk.Label(frame_reproduccion, text="")
    label_reproduccion.pack(side=tk.LEFT, padx=5)

    label_tiempo = tk.Label(frame_reproduccion, text="00:00 / 00:00")
    label_tiempo.pack(side=tk.LEFT, padx=5)

    frame_creditos = tk.Frame(ventana)
    frame_creditos.pack(side=tk.BOTTOM, pady=5)

    label_creditos = tk.Label(
        frame_creditos,
        text="@Copyright 2024 Version 1.0 Produced by Correa Jonathan",
        font=("Arial", 10, "bold"),
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
