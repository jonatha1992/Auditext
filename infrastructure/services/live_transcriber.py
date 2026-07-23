"""Shared coordinator for real-time audio capture and transcription."""

import datetime as dt
import queue
import threading
import wave
from pathlib import Path

import numpy as np
import soundcard as sc

import config
from config import logger
from infrastructure.services import interview_live

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


def mix_mono_tracks(
    system: np.ndarray,
    mic: np.ndarray,
    *,
    system_gain: float = 0.85,
    mic_gain: float = 0.85,
) -> np.ndarray:
    """Mix interviewer (system) and candidate (mic) into one mono track for WAV export."""
    n = len(system)
    mic_aligned = np.zeros(n, dtype=np.float32)
    take = min(n, len(mic))
    if take:
        mic_aligned[:take] = mic[:take].astype(np.float32)
    return np.clip(
        system.astype(np.float32) * system_gain + mic_aligned * mic_gain,
        -1.0,
        1.0,
    )


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
        self._interview_mode = False
        self._interview_context = ""
        self._interview_assist_mode = interview_live.DEFAULT_ASSIST_MODE
        self._live_session: interview_live.InterviewLiveSession | None = None
        self._assist_queue: "queue.Queue | None" = None
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
        self._mix_lock = threading.Lock()
        self._mic_mix_buf = np.zeros(0, dtype=np.float32)

    def start(
        self,
        language=None,
        out_dir: Path = DEFAULT_DIR,
        source_type="loopback",
        source_val=None,
        translate=False,
        save_audio=True,
        interview_mode=False,
        interview_context="",
        interview_assist_mode=interview_live.DEFAULT_ASSIST_MODE,
        assist_queue=None,
    ):
        if self._thread and self._thread.is_alive():
            return
        self._language = language
        self._locked_language = None
        self._out_dir = out_dir
        self._source_type = source_type
        self._source_val = source_val
        self._translate = translate
        self._save_audio = save_audio
        self._interview_mode = bool(interview_mode)
        self._interview_context = interview_context or ""
        self._interview_assist_mode = interview_assist_mode
        self._assist_queue = assist_queue
        self._live_session = None
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
        with self._mix_lock:
            self._mic_mix_buf = np.zeros(0, dtype=np.float32)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        live = self._live_session
        if live is not None:
            try:
                live.stop()
            except Exception:
                logger.exception("Failed stopping interview Live session")
        import time
        if getattr(self, "start_time", None):
            self.duration_seconds = time.time() - self.start_time

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def add_candidate_turn(self, text: str) -> None:
        live = self._live_session
        if live is not None:
            live.add_candidate_turn(text)

    def push_candidate_audio(self, mono: np.ndarray) -> None:
        """Append candidate microphone samples for WAV mixing (not sent to Gemini)."""
        if not (self._save_audio and self._interview_mode):
            return
        chunk = mono.astype(np.float32).flatten()
        if chunk.size == 0:
            return
        max_samples = SAMPLE_RATE * 30
        with self._mix_lock:
            if self._mic_mix_buf.size == 0:
                self._mic_mix_buf = chunk
            else:
                self._mic_mix_buf = np.concatenate([self._mic_mix_buf, chunk])
            if self._mic_mix_buf.size > max_samples:
                self._mic_mix_buf = self._mic_mix_buf[-max_samples:]

    def _take_mic_for_mix(self, n: int) -> np.ndarray:
        with self._mix_lock:
            if self._mic_mix_buf.size >= n:
                out = self._mic_mix_buf[:n].copy()
                self._mic_mix_buf = self._mic_mix_buf[n:]
            elif self._mic_mix_buf.size > 0:
                out = np.zeros(n, dtype=np.float32)
                out[: self._mic_mix_buf.size] = self._mic_mix_buf
                self._mic_mix_buf = np.zeros(0, dtype=np.float32)
            else:
                out = np.zeros(n, dtype=np.float32)
        return out

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
            if self._interview_mode and self._live_session is not None:
                pcm = interview_live.float32_to_pcm16(mono)
                self._live_session.send_audio(pcm)
                if self._wav_file:
                    mic_part = self._take_mic_for_mix(len(mono))
                    mixed = mix_mono_tracks(mono, mic_part)
                    self._wav_file.writeframes(interview_live.float32_to_pcm16(mixed))
            else:
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
                if config.transcription_service is not None:
                    texts, detected = config.transcription_service.transcribe_array(
                        audio, language=lang, translate=self._translate
                    )
                else:
                    raise RuntimeError("El servicio de transcripción no está inicializado en config.")
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
            if self._interview_mode:
                if self._source_type in _cloud_modes:
                    self._status_queue.put(
                        "Modo entrevista usa audio del sistema; cambiá la fuente."
                    )
                    return
                self._open_log()
                self._start_live_session()
                producer = threading.Thread(target=self._producer, daemon=True)
                producer.start()
                producer.join()
                if self._live_session is not None:
                    self._live_session.stop()
                    self._live_session = None
                self._status_queue.put("Inactivo")
                return

            if self._source_type in _cloud_modes:
                mode_label = "Google STT" if self._source_type == "google_stt" else "Windows STT"
                self._status_queue.put(f"Conectando con {mode_label}...")
            else:
                self._status_queue.put("Cargando modelo...")
                if config.transcription_service is not None:
                    config.transcription_service.get_model()
                else:
                    raise RuntimeError("El servicio de transcripción no está inicializado en config.")
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
            if self._live_session is not None:
                try:
                    self._live_session.stop()
                except Exception:
                    pass
                self._live_session = None
            if self._log_file:
                self._log_file.close()
                self._log_file = None
            if self._wav_file:
                try:
                    self._wav_file.close()
                except Exception:
                    pass
                self._wav_file = None

    def _start_live_session(self):
        def on_transcript(text: str):
            self._emit(text)

        def on_assist(assist: interview_live.InterviewAssist):
            if self._assist_queue is not None:
                self._assist_queue.put(assist)

        def on_status(msg: str):
            self._status_queue.put(msg)

        session = interview_live.InterviewLiveSession(
            context=self._interview_context,
            on_transcript=on_transcript,
            on_assist=on_assist,
            on_status=on_status,
            mode=self._interview_assist_mode,
        )
        self._live_session = session
        session.start()
        # Brief wait so asyncio loop is up before producer floods PCM
        import time
        for _ in range(50):
            if self._stop.is_set():
                return
            if session._loop is not None and session._audio_q is not None:
                break
            time.sleep(0.05)


