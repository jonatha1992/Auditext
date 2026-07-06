import queue
import threading
import datetime as dt
import wave
from pathlib import Path
import random

import numpy as np
import soundcard as sc

import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk

from infrastructure.services import summarizer
import config
from config import logger
from .spinner import Spinner

SAMPLE_RATE = config.SAMPLE_RATE
CHUNK_SECONDS = 5
CAPTURE_BLOCK_SECONDS = 0.5
SILENCE_THRESHOLD = 1e-4

LANGUAGES = {
    "Auto": None,
    "Español": "es",
    "English": "en",
    "Português": "pt",
    "Français": "fr",
    "Deutsch": "de",
    "Italiano": "it",
}

def detect_system_language() -> str | None:
    import locale
    try:
        loc = locale.getdefaultlocale()[0] or ""
    except Exception:
        loc = ""
    code = loc.split("_")[0].lower()[:2] if loc else ""
    return code if code in LANGUAGES.values() else None

# BCP-47 tags for Google Web Speech API (same engine as Google Docs voice typing)
GOOGLE_STT_LANG = {
    "es": "es-AR",
    "en": "en-US",
    "pt": "pt-BR",
    "fr": "fr-FR",
    "de": "de-DE",
    "it": "it-IT",
    None: "es-AR",
}

GOOGLE_STT_LABEL = "🌐  Google STT (nube)"
WINDOWS_STT_LABEL = "🖥️  Windows STT (offline)"

# BCP-47 tags for Windows System.Speech engine
WINDOWS_STT_LANG = {
    "es": "es-ES", "en": "en-US", "pt": "pt-BR",
    "fr": "fr-FR", "de": "de-DE", "it": "it-IT", None: "es-ES",
}

# PowerShell script that drives System.Speech recognition.
# Writes "READY:<culture>" to stderr when ready, then recognized phrases to stdout.
# Writes "ERROR_NO_RECOGNIZER" or "ERROR:<msg>" to stderr on failure.
_WINDOWS_STT_PS_SCRIPT = r"""
param([string]$lang = "es-ES")
Add-Type -AssemblyName System.Speech

$installed = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()
if ($installed.Count -eq 0) {
    [Console]::Error.WriteLine("ERROR_NO_RECOGNIZER")
    [Console]::Error.Flush()
    exit 1
}

$culture = $null
$prefix = $lang.Split('-')[0]
foreach ($r in $installed) {
    if ($r.Culture.Name -like ($prefix + '*')) { $culture = $r.Culture; break }
}
if (-not $culture) { $culture = $installed[0].Culture }

try {
    $engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine($culture)
    $engine.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
    $engine.SetInputToDefaultAudioDevice()
    [Console]::Error.WriteLine("READY:" + $culture.Name)
    [Console]::Error.Flush()
    while ($true) {
        $result = $engine.Recognize([TimeSpan]::FromSeconds(2))
        if ($result -and $result.Text.Trim()) {
            [Console]::Out.WriteLine($result.Text)
            [Console]::Out.Flush()
        }
    }
} catch {
    [Console]::Error.WriteLine("ERROR:" + $_.Exception.Message)
    [Console]::Error.Flush()
    exit 1
}
"""

DEFAULT_DIR = Path.home() / "Documents" / "LiveTranscribe"
WHOLE_SYSTEM_LABEL = "🔊  Todo el sistema"


def to_mono(data: np.ndarray) -> np.ndarray:
    return data.mean(axis=1).astype(np.float32)


def is_silent(audio: np.ndarray, threshold: float = SILENCE_THRESHOLD) -> bool:
    return float(np.abs(audio).mean()) < threshold


class Transcriber:
    def __init__(self, text_queue, status_queue):
        self._text_queue = text_queue
        self._status_queue = status_queue
        self._stop = threading.Event()
        self._thread = None
        self._log_file = None
        self._wav_file = None
        self._language = None
        self._locked_language = None
        self._out_dir = DEFAULT_DIR
        self._source_type = "loopback"
        self._source_val = None
        self._translate = False
        self._save_audio = True
        self._audio_q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=200)
        self.num_bars = 60
        self.last_bands = [0.0] * 60
        self.playhead_index = 0
        self.update_count = 0
        self.peak_accumulator = []
        self.start_time = None
        self.duration_seconds = 0
        self.current_transcript_path = None
        self.current_wav_path = None

    def start(self, language=None, out_dir: Path = DEFAULT_DIR, source_type="loopback", source_val=None, translate=False, save_audio=True):
        if self._thread and self._thread.is_alive():
            return
        self._language = language
        self._locked_language = None
        self._out_dir = out_dir
        self._source_type = source_type
        self._source_val = source_val
        self._translate = translate
        self._save_audio = save_audio
        self._stop.clear()
        import time
        self.start_time = time.time()
        self.duration_seconds = 0
        self.current_transcript_path = None
        self.current_wav_path = None
        self.last_bands = [0.0] * getattr(self, "num_bars", 60)
        self.playhead_index = 0
        self.update_count = 0
        self.peak_accumulator = []
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        import time
        if getattr(self, "start_time", None):
            self.duration_seconds = time.time() - self.start_time

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _open_log(self):
        self._out_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.current_transcript_path = self._out_dir / f"transcript_{stamp}.txt"
        self._log_file = open(
            self.current_transcript_path, "a", encoding="utf-8"
        )
        if self._save_audio:
            self.current_wav_path = self._out_dir / f"audio_{stamp}.wav"
            self._wav_file = wave.open(
                str(self.current_wav_path), "wb"
            )
            self._wav_file.setnchannels(1)
            self._wav_file.setsampwidth(2)
            self._wav_file.setframerate(SAMPLE_RATE)
        else:
            self.current_wav_path = None

    def _emit(self, text: str):
        self._text_queue.put(text)
        if self._log_file:
            self._log_file.write(text + "\n")
            self._log_file.flush()

    def _producer(self):
        import sys
        com_initialized = False
        if sys.platform == "win32":
            import ctypes
            try:
                # Inicializar COM como COINIT_MULTITHREADED (0x0)
                hr = ctypes.windll.ole32.CoInitializeEx(None, 0)
                if hr >= 0:
                    com_initialized = True
            except Exception as exc:
                logger.exception("CoInitializeEx falló: %s", exc)

        try:
            block = int(SAMPLE_RATE * CAPTURE_BLOCK_SECONDS)
            if self._source_type == "google_stt":
                self._capture_google_stt()
                return
            if self._source_type == "windows_stt":
                self._capture_windows_stt()
                return
            if self._source_type == "app":
                try:
                    self._capture_process(block)
                    return
                except Exception as exc:
                    logger.exception("Process loopback failed: %s", exc)
                    self._status_queue.put(
                        "Error al capturar la app; usando micrófono."
                    )
                    self._source_type = "mic"
                    self._source_val = None

            if self._source_type == "mic":
                self._capture_mic(block)
            else:
                self._capture_system(block)
        finally:
            if com_initialized:
                import ctypes
                try:
                    ctypes.windll.ole32.CoUninitialize()
                except Exception:
                    pass

    def _capture_google_stt(self):
        """Use Google's free Web Speech API (same engine as Google Docs voice typing).

        Runs entirely via speech_recognition + pyaudio. No faster-whisper needed.
        Audio is captured from the default microphone and sent to Google's cloud.
        """
        try:
            import speech_recognition as sr
        except ImportError:
            self._status_queue.put("Falta speech_recognition (pip install SpeechRecognition pyaudio)")
            return

        bcp47 = GOOGLE_STT_LANG.get(self._language, "es-AR")
        self._status_queue.put(f"Google STT activo — idioma: {bcp47}")

        r_engine = sr.Recognizer()
        r_engine.dynamic_energy_threshold = True
        r_engine.pause_threshold = 0.8

        def on_phrase(recognizer, audio):
            try:
                text = recognizer.recognize_google(audio, language=bcp47)
                if text.strip():
                    self._emit(text)
            except sr.UnknownValueError:
                pass
            except sr.RequestError as exc:
                self._status_queue.put(f"Google STT error: {exc}")
                self._stop.set()

        try:
            mic = sr.Microphone()
        except OSError as exc:
            self._status_queue.put(f"No se encontró micrófono: {exc}")
            return

        with mic as source:
            r_engine.adjust_for_ambient_noise(source, duration=0.5)

        stop_bg = r_engine.listen_in_background(mic, on_phrase, phrase_time_limit=20)
        self._status_queue.put(f"Escuchando: Google STT ({bcp47})")
        self._stop.wait()
        stop_bg(wait_for_stop=False)

    def _capture_windows_stt(self):
        """Use Windows System.Speech (offline) via a PowerShell subprocess.

        Requires a Windows speech recognition language pack installed via
        Settings → Time & Language → Speech → Add a speech language.
        Shows a setup dialog when the language pack is missing.
        """
        import subprocess
        import tempfile
        import os
        import threading as _threading

        lang = WINDOWS_STT_LANG.get(self._language, "es-ES")

        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.ps1', delete=False, encoding='utf-8'
        ) as f:
            f.write(_WINDOWS_STT_PS_SCRIPT)
            ps_path = f.name

        proc = None
        try:
            proc = subprocess.Popen(
                ["powershell", "-ExecutionPolicy", "Bypass", "-File", ps_path, "-lang", lang],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

            # Kill PS process when stop flag fires
            def _killer():
                self._stop.wait()
                try:
                    proc.terminate()
                except Exception:
                    pass
            _threading.Thread(target=_killer, daemon=True).start()

            # First stderr line: READY:<culture> | ERROR_NO_RECOGNIZER | ERROR:<msg>
            first = proc.stderr.readline().strip()

            if first == "ERROR_NO_RECOGNIZER":
                self._status_queue.put(
                    "WINSTT_SETUP:Windows Speech Recognition no está disponible. "
                    "Para usarlo, instalá un paquete de voz en:\n"
                    "Configuración → Hora e idioma → Voz → Agregar idioma de voz"
                )
                return

            if first.startswith("ERROR:"):
                self._status_queue.put(f"Error Windows STT: {first[6:]}")
                return

            if first.startswith("READY:"):
                active_lang = first[6:]
                self._status_queue.put(f"Escuchando: Windows STT ({active_lang})")

            # Stream recognized phrases until the process exits
            for line in proc.stdout:
                text = line.strip()
                if text:
                    self._emit(text)

        except FileNotFoundError:
            self._status_queue.put("Error: PowerShell no encontrado en el sistema.")
        except Exception as exc:
            logger.exception("Windows STT failed: %s", exc)
            self._status_queue.put(f"Error Windows STT: {exc}")
        finally:
            if proc and proc.poll() is None:
                proc.terminate()
            try:
                os.unlink(ps_path)
            except Exception:
                pass

    def _push(self, mono):
        try:
            self._audio_q.put_nowait(mono)
            if self._wav_file:
                pcm_data = (mono * 32767).clip(-32768, 32767).astype(np.int16)
                self._wav_file.writeframes(pcm_data.tobytes())
        except queue.Full:
            pass

    def _update_equalizer(self, chunk):
        if len(chunk) == 0:
            return
        try:
            # Calcular la amplitud pico en este bloque de 50 ms
            peak = float(np.max(np.abs(chunk)))
            self.peak_accumulator.append(peak)
            
            # Cada 3 bloques (150 ms) procesamos y desplazamos el playhead
            if len(self.peak_accumulator) >= 3:
                max_peak = max(self.peak_accumulator)
                self.peak_accumulator = []
                
                # Escalar para llenar el alto de las barras
                val = min(1.0, max_peak * 4.5)
                if max_peak < 0.001:
                    val = 0.0
                
                num_bars = getattr(self, "num_bars", 60)
                # Asegurar longitud correcta
                if len(self.last_bands) != num_bars:
                    if len(self.last_bands) < num_bars:
                        self.last_bands += [0.0] * (num_bars - len(self.last_bands))
                    else:
                        self.last_bands = self.last_bands[:num_bars]
                
                idx = self.update_count
                if idx < num_bars:
                    self.last_bands[idx] = val
                    self.playhead_index = idx
                    self.update_count += 1
                else:
                    # Si llega al final, desplaza el historial hacia la izquierda
                    # y mantiene el playhead en el último índice
                    self.last_bands = self.last_bands[1:] + [val]
                    self.playhead_index = num_bars - 1
        except Exception:
            pass

    def _capture_system(self, block):
        sub_block = int(SAMPLE_RATE * 0.05)
        accumulated = []
        try:
            speaker = sc.default_speaker()
            if self._source_val and self._source_type == "loopback":
                speaker = next(
                    (s for s in sc.all_speakers() if s.name == self._source_val), speaker
                )
            loopback = sc.get_microphone(id=str(speaker.name), include_loopback=True)
            self._status_queue.put(f"Escuchando: Sistema ({speaker.name[:18]}...)")
            with loopback.recorder(samplerate=SAMPLE_RATE) as rec:
                while not self._stop.is_set():
                    chunk_data = rec.record(numframes=sub_block)
                    mono_chunk = to_mono(chunk_data)
                    self._update_equalizer(mono_chunk)
                    accumulated.append(mono_chunk)
                    if len(accumulated) >= 10:
                        self._push(np.concatenate(accumulated))
                        accumulated = []
        except Exception as exc:
            logger.exception("System loopback capture failed: %s", exc)
            self._status_queue.put(f"Error sistema: {exc}")

    def _capture_mic(self, block):
        sub_block = int(SAMPLE_RATE * 0.05)
        accumulated = []
        try:
            mic = sc.default_microphone()
            if self._source_val and self._source_type == "mic":
                mic = next(
                    (m for m in sc.all_microphones() if m.name == self._source_val), mic
                )
            self._status_queue.put(f"Escuchando: Micrófono ({mic.name[:18]}...)")
            with mic.recorder(samplerate=SAMPLE_RATE) as rec:
                while not self._stop.is_set():
                    chunk_data = rec.record(numframes=sub_block)
                    mono_chunk = to_mono(chunk_data)
                    self._update_equalizer(mono_chunk)
                    accumulated.append(mono_chunk)
                    if len(accumulated) >= 10:
                        self._push(np.concatenate(accumulated))
                        accumulated = []
        except Exception as exc:
            logger.exception("Microphone capture failed: %s", exc)
            self._status_queue.put(f"Error micrófono: {exc}")

    def _capture_process(self, block):
        from infrastructure.audio import process_loopback
        self._status_queue.put(f"Escuchando App (PID {self._source_val})")
        sub_block = int(SAMPLE_RATE * 0.05)
        accumulated = []
        with process_loopback.ProcessLoopbackRecorder(
            self._source_val, samplerate=SAMPLE_RATE
        ) as rec:
            while not self._stop.is_set():
                audio_data = rec.record(sub_block, stop_event=self._stop)
                if len(audio_data) > 0:
                    mono_chunk = to_mono(audio_data)
                    self._update_equalizer(mono_chunk)
                    accumulated.append(mono_chunk)
                    if len(accumulated) >= 10:
                        self._push(np.concatenate(accumulated))
                        accumulated = []

    def _consumer(self):
        target = int(SAMPLE_RATE * CHUNK_SECONDS)
        buf: list = []
        have = 0
        while not self._stop.is_set():
            try:
                chunk = self._audio_q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                buf.append(chunk)
                have += len(chunk)
                if have < target:
                    continue

                audio = np.concatenate(buf)
                buf, have = [], 0
                if is_silent(audio):
                    continue

                lang = self._language if self._language is not None else self._locked_language
                texts, detected = transcriber.transcribe_array(audio, language=lang, translate=self._translate)
                if self._language is None and self._locked_language is None and detected:
                    self._locked_language = detected
                    self._status_queue.put(f"Idioma detectado: {detected}")
                for text in texts:
                    self._emit(text)
            except Exception as exc:
                logger.exception("Consumer loop error: %s", exc)
                self._status_queue.put(f"Error transcripción: {exc}")
                buf, have = [], 0

    def _run(self):
        _cloud_modes = {"google_stt", "windows_stt"}
        try:
            if self._source_type in _cloud_modes:
                mode_label = "Google STT" if self._source_type == "google_stt" else "Windows STT"
                self._status_queue.put(f"Conectando con {mode_label}...")
            else:
                self._status_queue.put("Cargando modelo...")
                transcriber.get_model()
            self._open_log()

            producer = threading.Thread(target=self._producer, daemon=True)
            producer.start()

            if self._source_type not in _cloud_modes:
                consumer = threading.Thread(target=self._consumer, daemon=True)
                consumer.start()
                producer.join()
                consumer.join()
            else:
                producer.join()

            self._status_queue.put("Inactivo")
        except Exception as exc:
            logger.exception("Fallo la transcripcion en vivo: %s", exc)
            self._status_queue.put(f"Error: {exc}")
        finally:
            if self._log_file:
                self._log_file.close()
                self._log_file = None
            if self._wav_file:
                try:
                    self._wav_file.close()
                except Exception:
                    pass
                self._wav_file = None


class LiveFrame(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color="transparent")

        self.text_queue: "queue.Queue[str]" = queue.Queue()
        self.status_queue: "queue.Queue[str]" = queue.Queue()
        self.translate_var = tk.BooleanVar(value=False)
        self.record_var = tk.BooleanVar(value=True)
        self.worker = Transcriber(self.text_queue, self.status_queue)
        self.out_dir = DEFAULT_DIR
        self._controls_disabled = False

        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill=tk.X, padx=24, pady=(20, 10))

        label_titulo = ctk.CTkLabel(
            header, text="Transcripción en vivo",
            font=("Segoe UI Semibold", 22), text_color="#FFFFFF"
        )
        label_titulo.pack(side=tk.LEFT)

        # Status badge capsule (like Screenshot 2)
        self.status_badge = ctk.CTkFrame(header, fg_color="#1E1F29", corner_radius=16, height=28, border_width=1, border_color="#2A2B36")
        self.status_badge.pack(side=tk.RIGHT, padx=5)
        self.status_badge.pack_propagate(False)

        self.spinner = Spinner(
            self.status_badge, size=14, bg="#1E1F29",
            accent_color="#7000FF", muted_color="#2A2B36"
        )

        self.status_label = ctk.CTkLabel(
            self.status_badge, text="●  Inactivo", font=("Segoe UI Semibold", 11), text_color="#8A8F9E"
        )
        self.status_label.pack(side=tk.LEFT, padx=(12, 12), pady=0)

        # Subtitle
        subtitle_frame = ctk.CTkFrame(self, fg_color="transparent")
        subtitle_frame.pack(fill=tk.X, padx=24)
        label_subtitulo = ctk.CTkLabel(
            subtitle_frame, text="Capturá el audio del sistema o micrófono en tiempo real.",
            font=("Segoe UI", 12), text_color="#8A8F9E"
        )
        label_subtitulo.pack(anchor=tk.W)

        # Toolbar
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(fill=tk.X, padx=24, pady=(12, 8))

        self.toggle_btn = ctk.CTkButton(
            toolbar, text="▶   Iniciar", font=("Segoe UI Semibold", 12),
            fg_color="#7000FF", text_color="#FFFFFF", hover_color="#5900CC",
            width=110, height=36, corner_radius=8, command=self._toggle
        )
        self.toggle_btn.pack(side=tk.LEFT)

        self.btn_save = ctk.CTkButton(
            toolbar, text="💾   Guardar", font=("Segoe UI Semibold", 12),
            fg_color="#15161E", text_color="#FFFFFF", hover_color="#1A1B26",
            width=90, height=36, corner_radius=8, command=self._save
        )
        self.btn_save.pack(side=tk.LEFT, padx=(8, 0))

        self.btn_clear = ctk.CTkButton(
            toolbar, text="🧹   Limpiar", font=("Segoe UI Semibold", 12),
            fg_color="#15161E", text_color="#FFFFFF", hover_color="#1A1B26",
            width=90, height=36, corner_radius=8, command=self._clear
        )
        self.btn_clear.pack(side=tk.LEFT, padx=(8, 0))

        self.summary_btn = ctk.CTkButton(
            toolbar, text="✨   Resumir", font=("Segoe UI Semibold", 12),
            fg_color="#15161E", text_color="#FFFFFF", hover_color="#1A1B26",
            width=100, height=36, corner_radius=8, command=self._summarize
        )
        self.summary_btn.pack(side=tk.LEFT, padx=(8, 0))

        self.btn_open_folder = ctk.CTkButton(
            toolbar, text="📁   Grabaciones", font=("Segoe UI Semibold", 12),
            fg_color="#15161E", text_color="#FFFFFF", hover_color="#1A1B26",
            width=120, height=36, corner_radius=8, command=self._open_recordings_folder
        )
        self.btn_open_folder.pack(side=tk.LEFT, padx=(8, 0))

        # Main Layout split: Config options on left, equalizers and text area below
        card_config = ctk.CTkFrame(self, fg_color="#15161E", corner_radius=12, border_color="#2A2B36", border_width=1)
        card_config.pack(fill=tk.X, padx=24, pady=6)

        # Options layout inside config card
        self.label_fuente = ctk.CTkLabel(
            card_config, text="FUENTE", font=("Segoe UI Semibold", 9), text_color="#8A8F9E"
        )
        self.label_fuente.grid(row=0, column=0, padx=(16, 4), pady=(8, 2), sticky=tk.W)

        try:
            default_dev = sc.default_speaker().name
        except Exception:
            default_dev = ""
        self._device_name = default_dev
        self._source_map: dict = {}
        self.source_var = tk.StringVar()
        
        self.source_combo = ctk.CTkComboBox(
            card_config, values=[WHOLE_SYSTEM_LABEL], state="readonly", width=320,
            fg_color="#1A1B26", border_color="#2A2B36", button_color="#2A2B36",
            dropdown_fg_color="#15161E", dropdown_text_color="#FFFFFF",
            dropdown_hover_color="#7000FF", variable=self.source_var
        )
        self.source_combo.grid(row=1, column=0, padx=(16, 8), pady=(0, 12), sticky=tk.W)

        self.btn_refresh = ctk.CTkButton(
            card_config, text="↻", font=("Segoe UI Semibold", 13),
            fg_color="#1A1B26", text_color="#FFFFFF", hover_color="#2A2B36",
            width=36, height=28, corner_radius=8, command=self._refresh_sources
        )
        self.btn_refresh.grid(row=1, column=1, padx=(0, 12), pady=(0, 12), sticky=tk.W)

        self.label_idioma = ctk.CTkLabel(
            card_config, text="IDIOMA", font=("Segoe UI Semibold", 9), text_color="#8A8F9E"
        )
        self.label_idioma.grid(row=0, column=2, padx=(8, 4), pady=(8, 2), sticky=tk.W)

        _sys = detect_system_language()
        _default_lang = next(
            (label for label, code in LANGUAGES.items() if code == _sys), "Auto"
        )
        self.lang_var = tk.StringVar(value=_default_lang)

        self.lang_combo = ctk.CTkComboBox(
            card_config, values=list(LANGUAGES.keys()), state="readonly", width=110,
            fg_color="#1A1B26", border_color="#2A2B36", button_color="#2A2B36",
            dropdown_fg_color="#15161E", dropdown_text_color="#FFFFFF",
            dropdown_hover_color="#7000FF", variable=self.lang_var
        )
        self.lang_combo.grid(row=1, column=2, padx=(8, 12), pady=(0, 12), sticky=tk.W)

        self.translate_check = ctk.CTkCheckBox(
            card_config, text="Traducir a Inglés", variable=self.translate_var,
            font=("Segoe UI", 12), text_color="#FFFFFF",
            fg_color="#7000FF", hover_color="#5900CC", border_color="#2A2B36"
        )
        self.translate_check.grid(row=0, column=3, rowspan=2, padx=(24, 16), pady=12, sticky=tk.E)

        # Recording controls — explicit toggle + folder picker
        rec_frame = ctk.CTkFrame(card_config, fg_color="#15161E")
        rec_frame.grid(row=0, column=4, rowspan=2, padx=(0, 16), pady=12, sticky=tk.E)

        self.record_check = ctk.CTkCheckBox(
            rec_frame, text="⏺ Grabar audio", variable=self.record_var,
            font=("Segoe UI", 12), text_color="#FFFFFF",
            fg_color="#E53E3E", hover_color="#C53030", border_color="#2A2B36"
        )
        self.record_check.pack(anchor=tk.W)

        self.btn_change_folder = ctk.CTkButton(
            rec_frame, text="📂 Cambiar carpeta", font=("Segoe UI", 11),
            fg_color="transparent", text_color="#8A8F9E", hover_color="#1A1B26",
            width=130, height=20, corner_radius=6, command=self._choose_folder
        )
        self.btn_change_folder.pack(anchor=tk.W, pady=(4, 0))

        card_config.grid_columnconfigure(3, weight=0)
        card_config.grid_columnconfigure(4, weight=1)

        # Responsive layout adjustment logic
        self.last_live_width = [0]
        self._resize_after_id = None

        def _apply_live_layout(new_w):
            self._resize_after_id = None
            if new_w == self.last_live_width[0]:
                return
            self.last_live_width[0] = new_w

            # Responsive config cards grid rearrange
            _all_controls = [
                self.label_fuente, self.source_combo, self.btn_refresh,
                self.label_idioma, self.lang_combo, self.translate_check, rec_frame,
            ]
            for w in _all_controls:
                w.grid_forget()

            if new_w < 800:
                self.label_fuente.grid(row=0, column=0, padx=16, pady=(8, 2), sticky=tk.W)
                self.source_combo.grid(row=1, column=0, padx=(16, 8), pady=(0, 6), sticky=tk.W)
                self.btn_refresh.grid(row=1, column=1, padx=(0, 16), pady=(0, 6), sticky=tk.W)
                self.label_idioma.grid(row=2, column=0, padx=16, pady=(6, 2), sticky=tk.W)
                self.lang_combo.grid(row=3, column=0, padx=16, pady=(0, 6), sticky=tk.W)
                self.translate_check.grid(row=4, column=0, columnspan=2, padx=16, pady=(4, 4), sticky=tk.W)
                rec_frame.grid(row=5, column=0, columnspan=2, padx=16, pady=(4, 12), sticky=tk.W)

                card_config.grid_columnconfigure(0, weight=1)
                card_config.grid_columnconfigure(1, weight=0)
                card_config.grid_columnconfigure(2, weight=0)
                card_config.grid_columnconfigure(3, weight=0)
                card_config.grid_columnconfigure(4, weight=0)

                # Responsive toolbar buttons (Grid)
                self.toggle_btn.pack_forget()
                self.btn_save.pack_forget()
                self.btn_clear.pack_forget()
                self.summary_btn.pack_forget()
                self.btn_open_folder.pack_forget()

                self.toggle_btn.grid(row=0, column=1, padx=6, pady=4)
                self.btn_save.grid(row=0, column=2, padx=6, pady=4)
                self.btn_clear.grid(row=0, column=3, padx=6, pady=4)
                self.summary_btn.grid(row=1, column=1, padx=6, pady=4)
                self.btn_open_folder.grid(row=1, column=2, columnspan=2, padx=6, pady=4)

                toolbar.grid_columnconfigure(0, weight=1)
                toolbar.grid_columnconfigure(1, weight=0)
                toolbar.grid_columnconfigure(2, weight=0)
                toolbar.grid_columnconfigure(3, weight=0)
                toolbar.grid_columnconfigure(4, weight=1)
            else:
                self.label_fuente.grid(row=0, column=0, padx=(16, 4), pady=(8, 2), sticky=tk.W)
                self.source_combo.grid(row=1, column=0, padx=(16, 8), pady=(0, 12), sticky=tk.W)
                self.btn_refresh.grid(row=1, column=1, padx=(0, 12), pady=(0, 12), sticky=tk.W)
                self.label_idioma.grid(row=0, column=2, padx=(8, 4), pady=(8, 2), sticky=tk.W)
                self.lang_combo.grid(row=1, column=2, padx=(8, 12), pady=(0, 12), sticky=tk.W)
                self.translate_check.grid(row=0, column=3, rowspan=2, padx=(24, 16), pady=12, sticky=tk.E)
                rec_frame.grid(row=0, column=4, rowspan=2, padx=(0, 16), pady=12, sticky=tk.E)

                card_config.grid_columnconfigure(0, weight=0)
                card_config.grid_columnconfigure(1, weight=0)
                card_config.grid_columnconfigure(2, weight=0)
                card_config.grid_columnconfigure(3, weight=0)
                card_config.grid_columnconfigure(4, weight=1)

                # Reset toolbar buttons (Pack)
                self.toggle_btn.grid_forget()
                self.btn_save.grid_forget()
                self.btn_clear.grid_forget()
                self.summary_btn.grid_forget()
                self.btn_open_folder.grid_forget()

                self.toggle_btn.pack(side=tk.LEFT)
                self.btn_save.pack(side=tk.LEFT, padx=(8, 0))
                self.btn_clear.pack(side=tk.LEFT, padx=(8, 0))
                self.summary_btn.pack(side=tk.LEFT, padx=(8, 0))
                self.btn_open_folder.pack(side=tk.LEFT, padx=(8, 0))

                toolbar.grid_columnconfigure(0, weight=0)
                toolbar.grid_columnconfigure(1, weight=0)
                toolbar.grid_columnconfigure(2, weight=0)
                toolbar.grid_columnconfigure(3, weight=0)
                toolbar.grid_columnconfigure(4, weight=0)

        def on_live_configure(event):
            if event.widget != self:
                return
            new_w = event.width
            if new_w < 400: # Ignorar anchos de inicialización pequeños
                return
            if self._resize_after_id is not None:
                self.after_cancel(self._resize_after_id)
            self._resize_after_id = self.after(150, lambda w=new_w: _apply_live_layout(w))

        self.bind("<Configure>", on_live_configure)

        # Equalizer Wave Card (like Screenshot 2)
        self.wave_card = ctk.CTkFrame(self, fg_color="#15161E", corner_radius=12, border_color="#2A2B36", border_width=1)
        self.wave_card.pack(fill=tk.X, padx=24, pady=6)

        self.wave_canvas = tk.Canvas(
            self.wave_card, bg="#11121A", highlightthickness=0, height=70
        )
        self.wave_canvas.pack(fill=tk.X, padx=12, pady=(12, 6))

        # Setup equalizer bars variables
        self.bar_ids = []
        self.num_bars = 60
        self.bar_width = 5
        self.bar_gap = 3
        self.is_animating = False

        self.path_label = ctk.CTkLabel(
            self.wave_card, text=f"Pulsa Iniciar para comenzar\n{self.out_dir}",
            font=("Segoe UI", 11), text_color="#8A8F9E", justify=tk.CENTER
        )
        self.path_label.pack(pady=(4, 10))

        # Output text box
        text_card = ctk.CTkFrame(self, fg_color="#15161E", corner_radius=12, border_color="#2A2B36", border_width=1)
        text_card.pack(fill=tk.BOTH, expand=True, padx=24, pady=(6, 16))

        self.output = ctk.CTkTextbox(
            text_card, fg_color="#11121A", text_color="#FFFFFF",
            font=("Segoe UI", 12),
            corner_radius=8, border_width=0
        )
        self.output.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        self._refresh_sources()
        self.wave_canvas.bind("<Configure>", lambda event: self._init_wave_bars(event.width))
        self.after(150, self._drain_queues)

    def _init_wave_bars(self, canvas_width=None):
        self.wave_canvas.delete("all")
        self.bar_ids = []
        w = canvas_width if canvas_width is not None else self.wave_canvas.winfo_width()
        if w < 10:
            w = 500  # Default fallback width before render
        
        self.bar_width = 6
        self.bar_gap = 3
        
        # Calcular cantidad de barras dinámicamente para ocupar el ancho disponible de la tarjeta
        # Dejamos 24px de margen a los costados
        visible_w = w - 48
        self.num_bars = max(20, visible_w // (self.bar_width + self.bar_gap))
        
        # Sincronizar la cantidad de barras con el worker
        self.worker.num_bars = self.num_bars
        
        h = 70
        center_y = 45  # waveform centered in bottom 50px

        total_width = self.num_bars * self.bar_width + (self.num_bars - 1) * self.bar_gap
        start_x = (w - total_width) / 2

        # Draw the baseline (horizontal dotted line)
        x_start = start_x
        x_end = start_x + total_width
        self.wave_canvas.create_line(
            x_start, center_y, x_end, center_y, fill="#1A1B26", width=1, dash=(2, 2)
        )

        for i in range(self.num_bars):
            x0 = start_x + i * (self.bar_width + self.bar_gap)
            x1 = x0 + self.bar_width
            y0 = center_y - 2
            y1 = center_y + 2
            bar_id = self.wave_canvas.create_rectangle(
                x0, y0, x1, y1, fill="#7000FF", outline="", tags=f"bar_{i}"
            )
            self.bar_ids.append(bar_id)
            
        # Draw the initial red playhead line (make it width 3 for better visibility)
        self.wave_canvas.create_line(
            start_x, 22, start_x, 68, fill="#E53E3E", width=3, tags="playhead"
        )
        
        # Initial draw of timeline
        self._draw_timeline(0.0)

    def _draw_timeline(self, elapsed):
        self.wave_canvas.delete("timeline")
        
        w = self.wave_canvas.winfo_width()
        if w < 10:
            w = 500
        total_width = self.num_bars * self.bar_width + (self.num_bars - 1) * self.bar_gap
        start_x = (w - total_width) / 2

        # Duración total que cabe en pantalla según la cantidad de barras
        duration_on_screen = self.num_bars * 0.15
        if elapsed < duration_on_screen:
            base_time = 0.0
        else:
            base_time = elapsed - duration_on_screen

        # Generar marcas de tiempo (ticks) dinámicamente cada 2 segundos
        ticks = []
        start_second = int(base_time // 2) * 2
        if start_second < base_time:
            start_second += 2.0
            
        t_val = start_second
        while True:
            idx = int((t_val - base_time) / 0.15)
            if idx >= self.num_bars:
                break
            if idx >= 0:
                ticks.append((idx, t_val))
            t_val += 2.0

        def format_time(t):
            mins = int(t // 60)
            secs = int(t % 60)
            hundredths = int((t * 100) % 100)
            return f"{mins:02d}:{secs:02d}.{hundredths:02d}"

        for idx, t_val in ticks:
            if idx < self.num_bars:
                x = start_x + idx * (self.bar_width + self.bar_gap) + self.bar_width / 2
                # Draw small tick mark
                self.wave_canvas.create_line(x, 2, x, 8, fill="#2A2B36", width=1, tags="timeline")
                # Draw text label
                self.wave_canvas.create_text(
                    x, 15, text=format_time(t_val), font=("Segoe UI Semibold", 8), fill="#8A8F9E", tags="timeline"
                )

    def _animate_wave(self):
        if not self.is_animating:
            # Flatten all bars when stopped and reset playhead/timeline to start
            h = 70
            center_y = 45
            for bar_id in self.bar_ids:
                coords = self.wave_canvas.coords(bar_id)
                if coords:
                    x0, _, x1, _ = coords
                    self.wave_canvas.coords(bar_id, x0, center_y - 2, x1, center_y + 2)
            
            # Reset playhead to start
            w = self.wave_canvas.winfo_width()
            if w < 10:
                w = 500
            total_width = self.num_bars * self.bar_width + (self.num_bars - 1) * self.bar_gap
            start_x = (w - total_width) / 2
            self.wave_canvas.coords("playhead", start_x, 22, start_x, 68)
            
            # Reset timeline
            self._draw_timeline(0.0)
            return

        h = 70
        center_y = 45

        # Check if we are running in cloud or subprocess modes (simulate playhead moving)
        if getattr(self.worker, "_source_type", None) in {"google_stt", "windows_stt"}:
            if not hasattr(self, "_fake_history") or len(self._fake_history) != self.num_bars:
                self._fake_history = [0.0] * self.num_bars
                self._fake_playhead = min(getattr(self, "_fake_playhead", 0), self.num_bars - 1)
                self._fake_counter = 0
                self._fake_elapsed = getattr(self, "_fake_elapsed", 0.0)
            
            # Update fake wave every 3 animation frames (150ms)
            self._fake_counter += 1
            if self._fake_counter >= 3:
                self._fake_counter = 0
                self._fake_elapsed += 0.15
                if random.random() < 0.15:
                    sim_val = random.uniform(0.3, 0.9)
                else:
                    sim_val = 0.0
                
                idx = self._fake_playhead
                if idx < self.num_bars - 1:
                    self._fake_history[idx] = sim_val
                    self._fake_playhead += 1
                else:
                    self._fake_history = self._fake_history[1:] + [sim_val]
                    self._fake_playhead = self.num_bars - 1
            
            bands = self._fake_history
            playhead_idx = self._fake_playhead
            elapsed = self._fake_elapsed
        else:
            # Get latest rolling waveform history from real-time worker
            bands = getattr(self.worker, "last_bands", None)
            if not bands:
                bands = [0.0] * self.num_bars
            playhead_idx = getattr(self.worker, "playhead_index", 0) or 0
            
            # Real elapsed time
            elapsed = 0.0
            if self.is_animating and getattr(self.worker, "start_time", None):
                import time
                elapsed = time.time() - self.worker.start_time

        for i, bar_id in enumerate(self.bar_ids):
            band_val = bands[i] if i < len(bands) else 0.0
            
            # Tiny random jitter on silence to keep the UI feeling "alive"
            if band_val < 0.02:
                bar_h = 4 + random.randint(0, 1)
            else:
                bar_h = 4 + int(band_val * 40)  # max height 40px

            y0 = center_y - (bar_h / 2)
            y1 = center_y + (bar_h / 2)
            coords = self.wave_canvas.coords(bar_id)
            if coords:
                x0, _, x1, _ = coords
                self.wave_canvas.coords(bar_id, x0, y0, x1, y1)

        # Update the playhead red line position
        w = self.wave_canvas.winfo_width()
        if w < 10:
            w = 500
        total_width = self.num_bars * self.bar_width + (self.num_bars - 1) * self.bar_gap
        start_x = (w - total_width) / 2
        x_playhead = start_x + playhead_idx * (self.bar_width + self.bar_gap) + self.bar_width / 2
        self.wave_canvas.coords("playhead", x_playhead, 22, x_playhead, 68)

        # Draw the dynamic timeline
        self._draw_timeline(elapsed)

        self.after(50, self._animate_wave)

    def _toggle(self):
        if self.worker.is_running():
            self.worker.stop()
        else:
            source_info = self._source_map.get(self.source_var.get(), ("loopback", None))
            self.worker.start(
                language=LANGUAGES[self.lang_var.get()],
                out_dir=self.out_dir,
                source_type=source_info[0],
                source_val=source_info[1],
                translate=self.translate_var.get(),
                save_audio=self.record_var.get(),
            )
            self.toggle_btn.configure(text="⏹   Detener")

    def refresh_devices(self):
        self._refresh_sources()

    def _refresh_sources(self):
        try:
            from infrastructure.audio import process_loopback
            apps = process_loopback.list_audio_apps()
        except Exception:
            apps = []

        self._source_map = {WHOLE_SYSTEM_LABEL: ("loopback", None)}

        # Google STT — same engine as Google Docs voice typing (needs internet)
        self._source_map[GOOGLE_STT_LABEL] = ("google_stt", None)

        # Windows STT — System.Speech offline (needs Windows speech language pack)
        self._source_map[WINDOWS_STT_LABEL] = ("windows_stt", None)

        try:
            mics = sc.all_microphones()
            for mic in mics:
                label = f"🎤  Micrófono: {mic.name}"
                self._source_map[label] = ("mic", mic.name)
        except Exception as exc:
            logger.exception("Failed to list microphones: %s", exc)

        for name, pid in apps:
            self._source_map[f"💻  App: {name} (PID {pid})"] = ("app", pid)

        labels = list(self._source_map.keys())
        self.source_combo.configure(values=labels)
        if self.source_var.get() not in self._source_map:
            self.source_var.set(WHOLE_SYSTEM_LABEL)

    def _open_recordings_folder(self):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        import subprocess as _sp
        _sp.Popen(["explorer", str(self.out_dir)])

    def _choose_folder(self):
        initial = self.out_dir if self.out_dir.exists() else Path.home()
        chosen = filedialog.askdirectory(
            parent=self, initialdir=str(initial), title="Carpeta de grabaciones"
        )
        if chosen:
            self.out_dir = Path(chosen)
            self.path_label.configure(
                text=f"Pulsa Iniciar para comenzar\n{self.out_dir}",
                text_color="#8A8F9E"
            )

    def _clear(self):
        self.output.delete("1.0", tk.END)

    def _summarize(self):
        text = self.output.get("1.0", tk.END).strip()
        if not text:
            self._set_status("Nada para resumir")
            return
        if not summarizer.is_configured():
            messagebox.showinfo(
                "Resumen no configurado",
                "Falta GEMINI_API_KEY. Crea un archivo .env en app_escritorio "
                "con tu clave para habilitar el resumen.",
            )
            return
        self.summary_btn.configure(state="disabled")
        self._set_status("Resumiendo...")
        threading.Thread(target=self._run_summary, args=(text,), daemon=True).start()

    def _run_summary(self, text):
        try:
            summary = summarizer.summarize(text)
            self.after(0, lambda: self._show_summary(summary))
        except summarizer.SummaryError as exc:
            msg = str(exc)
            self.after(0, lambda: self._summary_failed(msg))

    def _summary_failed(self, msg):
        self.summary_btn.configure(state="normal")
        self._set_status("Error en el resumen")
        messagebox.showerror("Resumen", msg)

    def _show_summary(self, summary):
        self.summary_btn.configure(state="normal")
        self._set_status("Resumen listo")

        win = ctk.CTkToplevel(self)
        win.title("Resumen")
        win.geometry("560x520")
        win.configure(fg_color="#0B0C10")

        ctk.CTkLabel(
            win, text="Resumen de la transcripción",
            font=("Segoe UI Semibold", 18), text_color="#FFFFFF"
        ).pack(anchor=tk.W, padx=20, pady=(16, 8))

        card_box = ctk.CTkFrame(win, fg_color="#15161E", corner_radius=12, border_color="#2A2B36", border_width=1)
        card_box.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 16))

        box = ctk.CTkTextbox(
            card_box, fg_color="#11121A", text_color="#FFFFFF",
            font=("Segoe UI", 12), corner_radius=8
        )
        box.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        box.insert("1.0", summary)

        bar = ctk.CTkFrame(win, fg_color="transparent")
        bar.pack(fill=tk.X, padx=20, pady=(0, 20))

        def copy():
            self.clipboard_clear()
            self.clipboard_append(summary)
            self._set_status("Resumen copiado")

        ctk.CTkButton(
            bar, text="Copiar", font=("Segoe UI Semibold", 12),
            fg_color="#15161E", text_color="#FFFFFF", hover_color="#1A1B26",
            width=90, height=36, corner_radius=8, command=copy
        ).pack(side=tk.LEFT)

        ctk.CTkButton(
            bar, text="Cerrar", font=("Segoe UI Semibold", 12),
            fg_color="#7000FF", text_color="#FFFFFF", hover_color="#5900CC",
            width=90, height=36, corner_radius=8, command=win.destroy
        ).pack(side=tk.RIGHT)

    def _save(self):
        text = self.output.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning("Advertencia", "No hay texto para guardar.")
            return

        # Pedir nombre de la grabación al usuario usando CTkInputDialog
        dialog = ctk.CTkInputDialog(
            text="Ingresá el nombre para la grabación:",
            title="Guardar grabación"
        )
        custom_name = dialog.get_input()
        if not custom_name:
            return
        custom_name = custom_name.strip()
        if not custom_name:
            return

        # Sanitizar nombre para ser compatible con Windows
        import re
        safe_name = re.sub(r'[\\/*?:"<>|]', "_", custom_name)

        self.out_dir.mkdir(parents=True, exist_ok=True)
        target_txt = self.out_dir / f"{safe_name}.txt"
        target_wav = self.out_dir / f"{safe_name}.wav"

        # Verificar si ya existe para confirmar sobreescritura
        if target_txt.exists() or (getattr(self.worker, "current_wav_path", None) and target_wav.exists()):
            overwrite = messagebox.askyesno(
                "Confirmar sobrescritura",
                f"Ya existe una grabación con el nombre '{safe_name}'.\n\n¿Querés sobrescribirla?",
                parent=self
            )
            if not overwrite:
                return

        # Guardar archivo de texto editado
        try:
            target_txt.write_text(text + "\n", encoding="utf-8")
        except Exception as exc:
            logger.exception("Failed to write transcript file: %s", exc)
            messagebox.showerror("Error", f"No se pudo guardar el archivo de texto: {exc}")
            return

        # Si hay un audio asociado, renombrarlo/moverlo al nuevo nombre
        import os
        import shutil
        wav_moved = False
        if getattr(self.worker, "current_wav_path", None) and os.path.exists(self.worker.current_wav_path):
            try:
                shutil.move(str(self.worker.current_wav_path), str(target_wav))
                self.worker.current_wav_path = target_wav
                wav_moved = True
            except Exception as exc:
                logger.exception("Failed to move WAV file: %s", exc)
                messagebox.showwarning(
                    "Advertencia",
                    f"Se guardó la transcripción pero no se pudo renombrar el archivo de audio:\n{exc}"
                )

        # Borrar el archivo de transcripción temporal original
        if getattr(self.worker, "current_transcript_path", None) and os.path.exists(self.worker.current_transcript_path):
            try:
                if target_txt != self.worker.current_transcript_path:
                    os.remove(self.worker.current_transcript_path)
            except Exception:
                pass
            self.worker.current_transcript_path = target_txt

        # Calcular duración
        duration_str = "00:00"
        if getattr(self.worker, "duration_seconds", 0) > 0:
            mins = int(self.worker.duration_seconds // 60)
            secs = int(self.worker.duration_seconds % 60)
            duration_str = f"{mins:02d}:{secs:02d}"

        # Guardar en base de datos para integrarlo con la pestaña "Historial"
        db_path = str(target_wav) if wav_moved else str(target_txt)
        db_friendly_name = safe_name
        db_lang = self.worker._locked_language or self.worker._language or ""

        from core.domain.entities import TranscriptionRecord
        try:
            config.repository.save(
                TranscriptionRecord(
                    file_path=db_path,
                    file_name=db_friendly_name,
                    duration=duration_str,
                    transcription=text,
                    summary="",
                    language=db_lang
                )
            )
            self._set_status(f"Guardado: {safe_name}")
            messagebox.showinfo("Éxito", f"Grabación guardada como '{safe_name}' y añadida al historial.")
        except Exception as exc:
            logger.exception("Failed to save to database: %s", exc)
            messagebox.showerror("Error", f"Se guardó el archivo en disco pero no se pudo indexar en el historial: {exc}")

    def _append(self, text: str):
        at_bottom = self.output.yview()[1] >= 0.999
        val = self.output.get("1.0", "end-1c")
        if val and not val.endswith(("\n", " ", "\t")):
            self.output.insert(tk.END, " " + text)
        else:
            self.output.insert(tk.END, text)
        if at_bottom:
            self.output.see(tk.END)

    def _set_status(self, text: str):
        low = text.lower()
        if "escuchando" in low:
            color = "#4ec98a"  # green
        elif "error" in low:
            color = "#e0506a"  # red
        elif "cargando" in low:
            color = "#7000FF"  # purple
        else:
            color = "#8A8F9E"  # gray
        self.status_label.configure(text=f"●  {text}", text_color=color)

    def _drain_queues(self):
        while not self.status_queue.empty():
            msg = self.status_queue.get_nowait()
            if msg.startswith("WINSTT_SETUP:"):
                detail = msg[13:]
                self.after(
                    10,
                    lambda d=detail: messagebox.showinfo(
                        "Windows STT — Configuración necesaria",
                        f"{d}\n\nMás info: busca 'Speech' en Configuración de Windows.",
                    ),
                )
                self._set_status("Windows STT: requiere instalación")
            else:
                self._set_status(msg)
        while not self.text_queue.empty():
            self._append(self.text_queue.get_nowait())

        # Reactive sync of toggle button and spinner based on worker state
        if self.worker.is_running():
            if not getattr(self, "_controls_disabled", False):
                self._controls_disabled = True
                self.source_combo.configure(state="disabled")
                self.lang_combo.configure(state="disabled")
                self.translate_check.configure(state="disabled")
                self.record_check.configure(state="disabled")
                self.btn_change_folder.configure(state="disabled")
                self.btn_refresh.configure(state="disabled")
                self.btn_save.configure(state="disabled")
                self.summary_btn.configure(state="disabled")
                self.btn_clear.configure(state="disabled")

            if self.worker._stop.is_set():
                self.is_animating = False
                if self.spinner.is_spinning:
                    self.spinner.stop()
                    self.spinner.pack_forget()
                self.toggle_btn.configure(text="⌛ Deteniendo...", state="disabled")
                self.path_label.configure(text=f"Guardando en: {self.out_dir}")
            else:
                if not self.is_animating:
                    self.is_animating = True
                    self._init_wave_bars()
                    self._animate_wave()
                if not self.spinner.is_spinning:
                    self.spinner.start()
                    self.spinner.pack(side=tk.LEFT, padx=(12, 0))
                self.toggle_btn.configure(text="⏹   Detener", state="normal")
                # Show recording indicator
                source = self.source_var.get()
                is_rec = self.record_var.get()
                rec_tag = "  ⏺ REC" if is_rec else ""
                if GOOGLE_STT_LABEL in source:
                    self.path_label.configure(
                        text=f"🌐 Google STT activo{rec_tag}  →  {self.out_dir}",
                        text_color="#4ec98a"
                    )
                elif WINDOWS_STT_LABEL in source:
                    self.path_label.configure(
                        text=f"🖥️ Windows STT activo{rec_tag}  →  {self.out_dir}",
                        text_color="#4ec98a"
                    )
                else:
                    color = "#e0506a" if is_rec else "#4ec98a"
                    prefix = "🔴 Grabando audio + transcripción" if is_rec else "🎙️ Transcribiendo (sin grabar audio)"
                    self.path_label.configure(
                        text=f"{prefix}  →  {self.out_dir}",
                        text_color=color
                    )
        else:
            if getattr(self, "_controls_disabled", False):
                self._controls_disabled = False
                self.source_combo.configure(state="readonly")
                self.lang_combo.configure(state="readonly")
                self.translate_check.configure(state="normal")
                self.record_check.configure(state="normal")
                self.btn_change_folder.configure(state="normal")
                self.btn_refresh.configure(state="normal")
                self.btn_save.configure(state="normal")
                self.summary_btn.configure(state="normal")
                self.btn_clear.configure(state="normal")

            self.is_animating = False
            if self.spinner.is_spinning:
                self.spinner.stop()
                self.spinner.pack_forget()
            self.toggle_btn.configure(text="▶   Iniciar", state="normal")
            self.path_label.configure(
                text=f"Pulsa Iniciar para comenzar\n{self.out_dir}",
                text_color="#8A8F9E"
            )

        self.after(100, self._drain_queues)

    def stop_worker(self):
        self.worker.stop()
