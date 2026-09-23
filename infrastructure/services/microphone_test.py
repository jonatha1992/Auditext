"""Microphone diagnostics shared by live and oral-practice workflows."""

from __future__ import annotations

import re
import tempfile
import threading
import unicodedata
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

import config


PREFERRED_MICROPHONE_SETTING = "preferred_microphone"

# Windows expone el mismo micrófono físico a través de varias host APIs y no
# son intercambiables. WASAPI es la moderna, DirectSound una capa encima, y MME
# la heredada: hay placas que aceptan abrir el stream por MME y después no
# entregan un solo frame. Una host API sin ranking queda en el medio.
_HOST_API_RANK = {
    "windows wasapi": 0,
    "windows directsound": 1,
    "mme": 3,
    # WDM-KS va último, no excluido. Medido acá: rechaza la lectura bloqueante
    # con -9999 "Blocking API not supported yet" y ni siquiera abre en modo
    # callback (-9996 "Invalid device"). Descartarlo de plano dejaría sin probar
    # un micrófono que solo se exponga por ahí, y como falla al abrir en 0 s,
    # tenerlo de último recurso no cuesta nada.
    "windows wdm-ks": 4,
}
_DEFAULT_HOST_API_RANK = 2

# Dos plazos en vez de uno. El audio no puede llegar más rápido que el tiempo
# real, así que un plazo único de `duración + gracia` deja para el arranque del
# device exactamente la gracia — y ese mismo número decide cuánto tarda en
# rendirse un endpoint muerto. Los dos usos piden valores opuestos.
#
# Separados, cada uno se elige por su cuenta. Antes del primer frame no hay
# forma de distinguir un endpoint muerto de uno que tarda en despertar, así que
# el plazo de arranque se calibra por el lento: 6 s cubren la negociación HFP
# de un micrófono Bluetooth, que con los 3 s previos quedaba descartado. El
# muerto ahora cuesta esos 6 s y no `duración + gracia`, que con la misma
# tolerancia habrían sido 11.
_FIRST_FRAME_SECONDS = 6.0

# Ya llegando audio, esto solo absorbe jitter: no necesita ser generoso.
_COMPLETION_GRACE_SECONDS = 2.0

# Debajo de este nivel medio no hay nada que transcribir. No es una falla del
# endpoint: casi siempre es alguien que no habló. La prueba devuelve igual la
# toma para que se vea el medidor y se pueda escuchar la grabación.
AUDIBLE_LEVEL = 0.0001

# Una toma que llegó cortada no alcanza para aprobar un micrófono. Sin este
# umbral, un endpoint que muere entregando 0,2 s de un chasquido fuerte pasa
# el control de nivel y queda a un clic de ser el preferido de Resolver.
MIN_CAPTURE_RATIO = 0.8


class EndpointStalled(RuntimeError):
    """El stream abrió y no entregó ni un frame.

    Se distingue del silencio porque significan cosas distintas: un endpoint
    mudo entrega bloques en cero y a veces anda a otro sample rate, pero uno
    estancado no llama al callback nunca y reintentarlo solo gasta la ventana
    de captura de nuevo.

    Una toma corta no entra acá: si el callback alcanzó a entregar algo, ese
    audio sirve y se devuelve, porque tirarlo obliga a grabar todo de nuevo.
    """


@dataclass(frozen=True)
class MicrophoneTestResult:
    microphone_name: str
    endpoint_name: str
    sample_rate: int
    audio: np.ndarray
    mean_level: float
    peak_level: float
    transcription: str
    playback_path: Path
    requested_seconds: float = 0.0

    @property
    def captured_seconds(self) -> float:
        return self.audio.size / self.sample_rate if self.sample_rate else 0.0

    @property
    def is_partial(self) -> bool:
        """El endpoint cortó antes de entregar la toma que se le pidió.

        Se mira aparte del nivel porque un pico corto y fuerte —un chasquido,
        un transitorio de desconexión— promedia muy por encima de
        ``AUDIBLE_LEVEL`` aunque el micrófono esté roto.
        """
        if not self.requested_seconds:
            return False
        return self.captured_seconds < self.requested_seconds * MIN_CAPTURE_RATIO


def save_preferred_microphone(repository, microphone_name: str) -> None:
    if repository is not None:
        repository.set_setting(PREFERRED_MICROPHONE_SETTING, microphone_name)


def load_preferred_microphone(repository) -> str | None:
    if repository is None:
        return None
    value = repository.get_setting(PREFERRED_MICROPHONE_SETTING)
    return value.strip() if value and value.strip() else None


def choose_preferred_microphone_label(
    microphone_map: dict[str, str | None], preferred_name: str | None
) -> str | None:
    if not preferred_name:
        return None
    return next(
        (
            label for label, physical_name in microphone_map.items()
            if physical_name == preferred_name
        ),
        None,
    )


def _tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    plain = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return {
        token
        for token in re.findall(r"[a-z0-9]+", plain)
        if len(token) >= 4 and token not in {"microfono", "microphone"}
    }


def _resample(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    mono = np.asarray(audio, dtype=np.float32)
    if not mono.size or source_rate == target_rate:
        return mono
    output_size = max(1, round(len(mono) * target_rate / source_rate))
    return np.interp(
        np.linspace(0.0, 1.0, output_size, endpoint=False),
        np.linspace(0.0, 1.0, len(mono), endpoint=False),
        mono,
    ).astype(np.float32)


class MicrophoneTestService:
    def __init__(
        self,
        transcriber=None,
        soundcard_module=None,
        sounddevice_module=None,
    ):
        if soundcard_module is None:
            import soundcard as soundcard_module
        if sounddevice_module is None:
            import sounddevice as sounddevice_module
        self.transcriber = transcriber or config.transcription_service
        self.soundcard = soundcard_module
        self.sounddevice = sounddevice_module

    def list_microphones(self) -> list[str]:
        names = []
        for microphone in self.soundcard.all_microphones():
            name = str(microphone.name).strip()
            if name and name not in names:
                names.append(name)
        return names

    def _host_api_name(self, device) -> str:
        """Host API del device, o cadena vacía si el backend no la informa."""
        try:
            return str(
                self.sounddevice.query_hostapis()[int(device["hostapi"])]["name"]
            ).strip().casefold()
        except Exception:
            return ""

    def _candidate_devices(self, microphone_name: str):
        devices = [
            (index, device)
            for index, device in enumerate(self.sounddevice.query_devices())
            if int(device.get("max_input_channels", 0)) > 0
        ]
        wanted = _tokens(microphone_name)
        matches = [
            item for item in devices
            if wanted and wanted & _tokens(str(item[1].get("name", "")))
        ]
        usable = list(matches)
        if not usable:
            raise RuntimeError(
                f"No se encontró un endpoint compatible para {microphone_name}."
            )
        try:
            default_input = getattr(self.sounddevice.default, "device", (-1, -1))[0]
        except Exception:
            default_input = -1
        usable.sort(
            key=lambda item: (
                _HOST_API_RANK.get(
                    self._host_api_name(item[1]), _DEFAULT_HOST_API_RANK
                ),
                item[0] != default_input,
                item[0],
            )
        )
        return usable

    def _capture(
        self, device_index: int, capture_rate: int, duration_seconds: float
    ) -> np.ndarray:
        """Graba una toma y abandona al endpoint que deja de alimentar el stream.

        El ``stream.read()`` bloqueante no se puede acotar: en un endpoint que
        abre y después se queda mudo se estaciona adentro de PortAudio para
        siempre, y se lleva puesto el hilo de la prueba junto con el handle del
        micrófono.
        """
        frames_needed = max(1, round(capture_rate * duration_seconds))
        chunks: list[np.ndarray] = []
        collected = 0
        first_frame = threading.Event()
        done = threading.Event()

        def callback(indata, frame_count, _time_info, status):
            nonlocal collected
            if status:
                config.logger.debug("Microphone test stream status: %s", status)
            chunks.append(np.asarray(indata[:, 0], dtype=np.float32).copy())
            collected += int(frame_count)
            first_frame.set()
            if collected >= frames_needed:
                done.set()
                raise self.sounddevice.CallbackStop

        stream = self.sounddevice.InputStream(
            samplerate=capture_rate,
            device=device_index,
            channels=1,
            dtype="float32",
            callback=callback,
        )
        # El constructor ya hizo Pa_OpenStream, así que el handle existe antes
        # del start(). Si start() falla — device ocupado, WASAPI en exclusivo —
        # dejarlo afuera del try filtraba el stream: sounddevice no define
        # __del__, y el micrófono quedaba tomado hasta cerrar la app.
        try:
            stream.start()
            if first_frame.wait(timeout=_FIRST_FRAME_SECONDS):
                done.wait(timeout=duration_seconds + _COMPLETION_GRACE_SECONDS)
        finally:
            # La barrera es el close, no el abort: Pa_CloseStream descarta los
            # buffers pendientes, así que después de esto no puede quedar un
            # callback en vuelo tocando `chunks`.
            try:
                stream.abort(ignore_errors=True)
            finally:
                stream.close(ignore_errors=True)
        if not chunks:
            raise EndpointStalled(
                "el endpoint aceptó la conexión pero no entregó audio"
            )
        # El callback agrega bloques enteros y recién corta cuando ya juntó de
        # más, así que PortAudio real casi siempre se pasa del pedido: el
        # recorte no es decorativo.
        return np.concatenate(chunks)[:frames_needed]

    def probe(
        self,
        microphone_name: str,
        duration_seconds: float = 5.0,
        language: str = "es",
    ) -> MicrophoneTestResult:
        target_rate = config.SAMPLE_RATE
        last_error = None
        take = None
        quiet_take = None
        for device_index, device in self._candidate_devices(microphone_name):
            if take is not None:
                break
            rates = list(dict.fromkeys(
                rate for rate in (
                    int(float(device.get("default_samplerate", 0) or 0)),
                    target_rate,
                ) if rate > 0
            ))
            for capture_rate in rates:
                try:
                    raw = self._capture(device_index, capture_rate, duration_seconds)
                    mono = _resample(raw, capture_rate, target_rate)
                    mean_level = float(np.abs(mono).mean()) if mono.size else 0.0
                    peak_level = float(np.abs(mono).max()) if mono.size else 0.0
                    if mean_level < AUDIBLE_LEVEL:
                        # Se guarda la primera toma muda y se sigue buscando un
                        # endpoint con señal: si no aparece ninguno, esta es la
                        # respuesta honesta, no un error de dispositivo.
                        if quiet_take is None:
                            quiet_take = (device, mono, mean_level, peak_level)
                        raise RuntimeError("el endpoint no entregó señal audible")
                    take = (device, mono, mean_level, peak_level)
                    break
                except Exception as exc:
                    last_error = exc
                    stalled = isinstance(exc, EndpointStalled)
                    config.logger.warning(
                        "Microphone probe failed device=%s rate=%s: %s",
                        device.get("name"), capture_rate, exc,
                    )
                    if stalled:
                        break
        # La transcripción va fuera del bucle a propósito: adentro, un fallo del
        # motor (carga del modelo, OOM) se contaba como endpoint malo y mandaba
        # a regrabar 5 s en cada candidato para terminar culpando al micrófono.
        if take is None:
            take = quiet_take
        if take is not None:
            device, mono, mean_level, peak_level = take
            # El wav se escribe antes de transcribir: si el motor falla, la
            # toma ya está en disco y el usuario puede escucharla en vez de
            # tener que volver a grabar los 5 segundos.
            playback_path = self._write_playback(mono, target_rate)
            transcription = ""
            if mean_level >= AUDIBLE_LEVEL and self.transcriber is not None:
                texts, _detected = self.transcriber.transcribe_array(
                    mono, language=language, translate=False
                )
                transcription = " ".join(texts).strip()
            return MicrophoneTestResult(
                microphone_name=microphone_name,
                endpoint_name=str(device["name"]),
                sample_rate=target_rate,
                audio=mono,
                mean_level=mean_level,
                peak_level=peak_level,
                transcription=transcription,
                playback_path=playback_path,
                requested_seconds=duration_seconds,
            )
        if isinstance(last_error, EndpointStalled):
            raise EndpointStalled(
                f"{microphone_name} aceptó la conexión pero no entregó audio en "
                "ningún endpoint. Revisá que no esté silenciado y que Windows "
                "le permita el acceso al micrófono."
            )
        raise RuntimeError(
            f"No se pudo probar {microphone_name}: {last_error or 'sin señal'}"
        )

    @staticmethod
    def _write_playback(audio: np.ndarray, sample_rate: int) -> Path:
        path = Path(tempfile.gettempdir()) / "auditext_microphone_test.wav"
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm.tobytes())
        return path
