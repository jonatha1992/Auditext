# CLAUDE.md — AudioText Desktop App

GUI de escritorio offline para transcripción de audio con faster-whisper.

## Architecture

- **`main.py`** — entry point. PyInstaller bootstrap + dependency injection.
- **`config.py`** — logging, ffmpeg path, proxy probe, idiomas dict, global DI container.
- **`core/`** — dominio: `domain/entities.py`, `interfaces/interfaces.py`.
- **`presentation/`** — GUI en 3 capas:
  - `views/` — `interfaz.py` (Notebook con tabs), `live_frame.py` (captura loopback), `historial_frame.py`, `ajustes_frame.py`, `tooltip.py`, `spinner.py`
  - `controllers/` — `funcionalidad.py` (lógica de transcripción de archivos)
- **`infrastructure/`** — implementaciones:
  - `services/` — `onnx_transcriber.py` (faster-whisper), `summarizer.py` (Gemini), `interview_live.py` (Gemini Live), `gemini_keys.py` (rotación de APIs)
  - `repositories/` — `sqlite_repository.py`
  - `audio/` — `reproductor.py` (pygame), `process_loopback.py`, `audio_preprocessor.py`
- **`tests/`** — tests unitarios + smoke test de conectividad Live.
- **`hooks/`** — `rthook_frozen.py` (PyInstaller runtime hook).

## Critical conventions

- **Transcripción offline-first.** Solo el resumen con Gemini es online.
- **faster-whisper** modelo `small` en CPU con int8. Se carga lazy una sola vez.
- **No usar `ttk.Button` para botones con color** — el tema `clam` lo soporta con `tk.Button`.
- **Live tab:** nunca default a "Auto". El idioma se detecta una vez y se lockea (`Transcriber._locked_language`).
- **ffmpeg** en `ffmpeg/bin/` — `config.py` lo agrega al PATH automáticamente.

## Commands

```bash
# Run desde la raíz del repo
.venv\Scripts\python.exe main.py

# Install deps
.venv\Scripts\python.exe -m pip install -r requirements.txt

# Headless build check
.venv\Scripts\python.exe -c "import tkinter as tk; from presentation.views.interfaz import crear_interfaz; r=tk.Tk(); crear_interfaz(r); r.update(); print('OK'); r.destroy()"

# Tests
.venv\Scripts\python.exe -m pytest tests/

# Smoke test Live connectivity
.venv\Scripts\python.exe tests\smoke_live_connect.py
```

## Gemini setup

Crear `.env` en la raíz del proyecto (gitignorado):

```
GEMINI_API_KEY=<key from https://aistudio.google.com/apikey>
GEMINI_MODEL=gemini-2.5-flash
```

`.env.example` tiene la plantilla con todas las vars documentadas.
