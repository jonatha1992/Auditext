# AudioText — Transcripción de Escritorio (Offline)

**AudioText** es una aplicación de escritorio para transcripción de audio
totalmente offline usando [faster-whisper](https://github.com/SYSTRAN/faster-whisper).
Incluye modo de captura en vivo (loopback), historial local, resumen con Gemini
(opcional, online) y modo entrevista con IA.

> Consulta el [PRD del producto](docs/PRD.md) y la
> [decisión de separar Entrevista de En vivo](docs/decisions/001-separate-interview-experience.md).

## 🚀 Características

- ✅ **Transcripción offline** con Whisper modelo `small` en CPU (int8)
- ✅ **Captura en vivo** de audio del sistema (loopback WASAPI)
- ✅ **Módulo Entrevista** con captura separada del entrevistador y tu micrófono, contexto conversacional y coach de fluidez
- ✅ **Resumen opcional** con Gemini (online, bajo demanda)
- ✅ **Historial local** en SQLite con búsqueda y exportación
- ✅ **Reproductor** de audio incorporado
- ✅ **Mejoramiento de audio** (reducción de ruido, VAD, filtros)
- ✅ **Ajuste de idioma** con detección automática y lockeo

## 📦 Requisitos

- **Windows** (loopback WASAPI requiere Windows 10+)
- **Python 3.11+**
- **FFmpeg** incluido en `ffmpeg/bin/`

## 🔧 Instalación

```bash
git clone https://github.com/jonatha1992/AudioText.git
cd AudioText
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

### Gemini (opcional)

Para usar resumen o modo entrevista, creá `.env` en la raíz:

```
GEMINI_API_KEY=<tu-api-key>
GEMINI_MODEL=gemini-2.5-flash
```

## 📁 Estructura

```
AudioText/
├── main.py                     # Entry point
├── config.py                   # Logging, ffmpeg, DI container
├── requirements.txt            # Dependencias pip
├── AudioText.spec              # PyInstaller spec
├── .env.example                # Template para Gemini keys
│
├── core/                       # Dominio (entidades, interfaces)
├── presentation/               # GUI (views, controllers)
├── infrastructure/             # Implementaciones (servicios, repos, audio)
├── tests/                      # Tests unitarios y smoke
├── hooks/                      # PyInstaller runtime hook
├── icons/                      # Icono de la app
├── data/                       # Audios de sesiones
├── ffmpeg/                     # Binarios FFmpeg
├── Audios/                     # Samples de prueba
└── pretrained_models/          # Modelo speaker recognition
```

## 🛠️ Desarrollo

```bash
# Headless smoke test
.venv\Scripts\python.exe -c "import tkinter as tk; from presentation.views.interfaz import crear_interfaz; r=tk.Tk(); crear_interfaz(r); r.update(); print('OK'); r.destroy()"

# Tests
.venv\Scripts\python.exe -m pytest tests/

# Smoke test Gemini Live
.venv\Scripts\python.exe tests\smoke_live_connect.py

# Build con PyInstaller
.venv\Scripts\pyinstaller AudioText.spec
```

## 📄 Licencia

Desarrollado por **Correa Jonathan** © 2025.
