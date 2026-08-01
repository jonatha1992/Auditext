import os
import sys
import time
from typing import Callable, Any
from core.interfaces.interfaces import TranscriptionService
from faster_whisper import WhisperModel
import config
from infrastructure.services import latency_log

class OfflineTranscriptionService(TranscriptionService):
    def __init__(self, model_size: str = "small", logger=config.logger):
        self.model_size = model_size
        self.logger = logger
        self._model = None

    def get_model(self) -> WhisperModel:
        if self._model is None:
            self.logger.info(f"Cargando modelo local de Whisper ({self.model_size})...")
            
            # Prevent potential lazy torch imports from raising errors
            if "torch" not in sys.modules:
                sys.modules["torch"] = None

            model_path_or_size = self.model_size
            if getattr(sys, "frozen", False):
                # When running from frozen EXE, look in the bundled models directory
                base_dir = os.path.dirname(sys.executable)
                possible_path = os.path.join(base_dir, "models", f"whisper-{self.model_size}")
                if os.path.exists(possible_path):
                    model_path_or_size = possible_path
                    self.logger.info(f"Usando modelo empaquetado local desde: {model_path_or_size}")

            # Lazy one-time load: this cost lands on whoever transcribes first,
            # so it must be visible separately from the transcription itself.
            with latency_log.timed("model", "load", size=self.model_size):
                self._model = WhisperModel(
                    model_path_or_size,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=int(os.environ.get("OMP_NUM_THREADS", 4))
                )
            self.logger.info("Modelo de transcripción cargado exitosamente.")
        return self._model

    def transcribe_file(
        self,
        file_path: str,
        language: str | None,
        translate: bool,
        progress_cb: Callable[[float], None] | None = None,
        should_continue: Callable[[], bool] | None = None,
    ) -> str:
        try:
            model = self.get_model()
            task = "translate" if translate else "transcribe"
            
            self.logger.info(f"Iniciando transcripción local offline: {file_path} (idioma={language}, tarea={task})")
            if progress_cb:
                progress_cb(0.1)

            _t0 = time.perf_counter()
            # Perform transcription using voice activity detection (VAD) filter
            segments, info = model.transcribe(
                file_path,
                language=language,
                task=task,
                beam_size=5,
                vad_filter=True
            )

            results = []
            duration = info.duration if info else 1.0
            aborted = False

            for segment in segments:
                if should_continue and not should_continue():
                    self.logger.warning("Transcripción interrumpida por el usuario.")
                    aborted = True
                    break

                results.append(segment.text)

                # Report progressive status
                if progress_cb and duration > 0:
                    fraction = min(1.0, segment.end / duration)
                    # Scale progress between 10% and 90%
                    progress_cb(0.1 + fraction * 0.8)

            if progress_cb:
                progress_cb(1.0)

            # faster-whisper decodes lazily, so the real work happens in the loop
            # above -- timing only the transcribe() call would report ~0 ms.
            _elapsed = (time.perf_counter() - _t0) * 1000.0
            _audio_ms = duration * 1000.0
            latency_log.log_stage(
                "file",
                "transcribe_file",
                _elapsed,
                audio_ms=f"{_audio_ms:.0f}",
                rtf=latency_log.rtf(_elapsed, _audio_ms),
                segments=len(results),
                aborted="yes" if aborted else None,
            )

            return "".join(results).strip()
        except Exception as e:
            self.logger.error(f"Fallo en la transcripción local: {e}")
            raise e

    def transcribe_file_segments(
        self,
        file_path: str,
        language: str | None,
        translate: bool,
        progress_cb: Callable[[float], None] | None = None,
        should_continue: Callable[[], bool] | None = None,
    ) -> list[tuple[float, float, str]]:
        try:
            model = self.get_model()
            task = "translate" if translate else "transcribe"
            
            self.logger.info(f"Iniciando transcripción por segmentos local offline: {file_path} (idioma={language}, tarea={task})")
            if progress_cb:
                progress_cb(0.1)

            _t0 = time.perf_counter()
            segments, info = model.transcribe(
                file_path,
                language=language,
                task=task,
                beam_size=5,
                vad_filter=True
            )

            results = []
            duration = info.duration if info else 1.0
            aborted = False

            for segment in segments:
                if should_continue and not should_continue():
                    self.logger.warning("Transcripción interrumpida por el usuario.")
                    aborted = True
                    break

                text = segment.text.strip()
                if text:
                    results.append((segment.start, segment.end, text))

                if progress_cb and duration > 0:
                    fraction = min(1.0, segment.end / duration)
                    progress_cb(0.1 + fraction * 0.8)

            if progress_cb:
                progress_cb(1.0)

            _elapsed = (time.perf_counter() - _t0) * 1000.0
            _audio_ms = duration * 1000.0
            latency_log.log_stage(
                "file",
                "transcribe_file_segments",
                _elapsed,
                audio_ms=f"{_audio_ms:.0f}",
                rtf=latency_log.rtf(_elapsed, _audio_ms),
                segments=len(results),
                aborted="yes" if aborted else None,
            )

            return results
        except Exception as e:
            self.logger.error(f"Fallo en la transcripción por segmentos local: {e}")
            raise e

    def transcribe_array(
        self,
        audio: Any,
        language: str | None = None,
        translate: bool = False,
    ) -> tuple[list[str], str | None]:
        try:
            model = self.get_model()
            task = "translate" if translate else "transcribe"
            segments, info = model.transcribe(
                audio,
                language=language,
                task=task,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            texts = [seg.text.strip() for seg in segments if seg.text.strip()]
            return texts, info.language
        except Exception as e:
            self.logger.error(f"Fallo en la transcripción de array: {e}")
            raise e
