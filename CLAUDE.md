# CLAUDE.md — AudioText Desktop App

GUI de escritorio offline para transcripción de audio con faster-whisper.

## Architecture

- **`main.py`** — entry point. PyInstaller bootstrap + dependency injection.
- **`config.py`** — logging, ffmpeg path, proxy probe, idiomas dict, global DI container.
- **`core/`** — dominio: `domain/entities.py`, `interfaces/interfaces.py`.
- **`presentation/`** — GUI en 3 capas:
  - `views/` — `interfaz.py` (Notebook con tabs), `live_frame.py` (captura loopback), `historial_frame.py`, `ajustes_frame.py`, `tooltip.py`, `spinner.py`
  - `controllers/` — `funcionalidad.py` (lógica de transcripción de archivos)
- **`core/errors.py`** — bitácora de errores (`logs/bitacora.jsonl`) + captura global.
- **`infrastructure/`** — implementaciones:
  - `services/` — `onnx_transcriber.py` (faster-whisper), `summarizer.py` (Gemini), `interview_live.py` (Gemini Live), `gemini_keys.py` (rotación de APIs), `groq_provider.py` y `nvidia_provider.py` (respaldos del coach)
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
GEMINI_API_KEY1=<key from https://aistudio.google.com/apikey>
GEMINI_API_KEY2=<opcional: el pool rota hasta la 20>
GEMINI_MODEL=gemini-flash-latest
```

**No poner `gemini-2.5-flash` acá.** Está en `RETIRED_MODELS` y el pool lo
descarta con un warning: responde 404 "no longer available" con cualquier key.

`.env.example` tiene la plantilla con todas las vars documentadas.

## Cadena de proveedores del coach (Entrevista / Resolver)

Orden: **Gemini** (streaming, primero) → **Groq** → **NVIDIA**. Los tres fallan por
motivos independientes, y ese es el punto: Gemini agota su cuota free-tier,
NVIDIA devuelve `503 Worker local total request limit reached` cuando su endpoint
compartido se satura, y Groq corre en LPU con cuota aparte.

```
GROQ_API_KEY=<key de https://console.groq.com/keys>
GROQ_MODEL=llama-3.3-70b-versatile      # opcional
NVIDIA_API_KEY=<key de https://build.nvidia.com>
GROQ_TOTAL_BUDGET_SECONDS=40            # opcional, techo por llamada
NVIDIA_TOTAL_BUDGET_SECONDS=45          # opcional
```

### Modelos Gemini (verificado 2026-08-06)

Google retira modelos sin aviso y devuelve **404 "no longer available"**.
`gemini_keys.RETIRED_MODELS` los filtra en un solo lugar, porque `GEMINI_MODEL`
del `.env` alimenta coach, resumen y transcripción a la vez. Retirados:
`gemini-2.5-flash`, `gemini-2.5-flash-lite`, y toda la familia 1.5/1.0.

**`thinking_budget=0` no es universal.** Los Gemini 3.x lite/flash lo rechazan
con 400, y como un 400 también parece "modelo muerto", el código los
blacklisteaba por sesión entera (93 de esos en los logs). Ahora un 400 reintenta
el mismo modelo sin el campo antes de descartarlo (`_no_thinking_models`).
`gemini-3.1-flash-lite` encabeza la lista por ser el único lite actual que sí lo
acepta: medido 4.3 s end-to-end contra 6.7 s de `gemini-3.5-flash-lite`, que
tiene que pensar antes de contestar.

### Sesión Live: el corte a los ~10 minutos (verificado 2026-08-07)

Gemini Live termina la sesión por **límite de duración**, no por error. Avisa
antes con un mensaje `go_away` y reparte handles en `session_resumption_update`.
Ignorar los dos costaba esto, medido en `logs/error_log.txt`:

```
19:47:00  connected model=gemini-3.1-flash-live-preview
19:56:52  1008 ... "failed to close the connection after receiving a GoAway"
19:56:53  connected model=gemini-2.5-flash-native-audio-latest   <- degradado
```

9 min 52 s de vida útil, y el `1008` clasificado como "modelo caído" hacía bajar
por `LIVE_MODELS` hasta agotarla. Ese es el "empieza bien y después falla".

- `_handle_message` lee `go_away` y `session_resumption_update` **antes** del
  early-return de `server_content`: llegan en mensajes sin contenido.
- Un `go_away` cierra la sesión con el centinela `None` del `_audio_q` y
  `_run` reconecta **al mismo modelo** con el handle. `1008` ya no descarta nada.
- `_MAX_RECONNECTS` frena un reconnect que rebota, no un examen largo: una sesión
  que duró más de `_HEALTHY_SESSION_SECONDS` resetea el contador.
- El handle se borra al cambiar de key o de modelo: pertenece a su sesión.

Reglas que no se rompen:

- **Todo proveedor va acotado dos veces**: timeout de socket por request (escala
  con `max_tokens`) y presupuesto total por llamada. Sin el segundo, una rotación
  de claves con timeouts encadenados congela la app.
- **El cooldown depende de la clase de error.** Saturación (503) se libera en
  segundos; cuota y auth no. Un cooldown plano de 30 s convertía un 503
  transitorio en medio minuto sin respuesta.
- **Groq va detrás de Cloudflare**: sin un `User-Agent` propio contesta
  `403 error 1010` al default de `urllib`.

## Bitácora de errores

- `logs/bitacora.jsonl` — solo fallas, estructuradas (contexto, tipo, traceback).
- `logs/errores.log` — WARNING+ en texto plano.
- `logs/error_log.txt` — todo, incluido INFO.

`core/errors.py` expone `record()`, `capturing()`, `guard()` y `recent()`.
`install()` engancha `sys.excepthook`, `threading.excepthook` y
`Tk.report_callback_exception`; `install_asyncio(loop)` hace lo propio con las
tareas del Live. Sin eso, un fallo en un hilo o en un callback de Tk terminaba en
un stderr que la build windowed descarta: la app parecía colgada y el log salía
limpio. Se ve desde **Ajustes → Bitácora de errores**.
