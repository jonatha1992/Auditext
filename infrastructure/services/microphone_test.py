"""Microphone diagnostics shared by live and oral-practice workflows."""

from __future__ import annotations

import re
import tempfile
import unicodedata
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

import config


PREFERRED_MICROPHONE_SETTING = "preferred_microphone"


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
        if not matches:
            raise RuntimeError(
                f"No se encontró un endpoint compatible para {microphone_name}."
            )
        default_input = getattr(self.sounddevice.default, "device", (-1, -1))[0]
        matches.sort(key=lambda item: item[0] != default_input)
        return matches

    def probe(
        self,
        microphone_name: str,
        duration_seconds: float = 5.0,
        language: str = "es",
    ) -> MicrophoneTestResult:
        target_rate = config.SAMPLE_RATE
        last_error = None
        for device_index, device in self._candidate_devices(microphone_name):
            rates = list(dict.fromkeys(
                rate for rate in (
                    int(float(device.get("default_samplerate", 0) or 0)),
                    target_rate,
                ) if rate > 0
            ))
            for capture_rate in rates:
                try:
                    frames = max(1, round(capture_rate * duration_seconds))
                    with self.sounddevice.InputStream(
                        samplerate=capture_rate,
                        device=device_index,
                        channels=1,
                        dtype="float32",
                    ) as stream:
                        data, overflowed = stream.read(frames)
                    if overflowed:
                        config.logger.warning(
                            "Microphone test overflow device=%s", device["name"]
                        )
                    mono = _resample(data[:, 0], capture_rate, target_rate)
                    mean_level = float(np.abs(mono).mean()) if mono.size else 0.0
                    peak_level = float(np.abs(mono).max()) if mono.size else 0.0
                    if mean_level < 0.0001:
                        raise RuntimeError("el endpoint no entregó señal audible")
                    transcription = ""
                    if self.transcriber is not None:
                        texts, _detected = self.transcriber.transcribe_array(
                            mono, language=language, translate=False
                        )
                        transcription = " ".join(texts).strip()
                    playback_path = self._write_playback(mono, target_rate)
                    return MicrophoneTestResult(
                        microphone_name=microphone_name,
                        endpoint_name=str(device["name"]),
                        sample_rate=target_rate,
                        audio=mono,
                        mean_level=mean_level,
                        peak_level=peak_level,
                        transcription=transcription,
                        playback_path=playback_path,
                    )
                except Exception as exc:
                    last_error = exc
                    config.logger.warning(
                        "Microphone probe failed device=%s rate=%s: %s",
                        device.get("name"), capture_rate, exc,
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
