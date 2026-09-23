import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from infrastructure.services import microphone_test
from infrastructure.services.microphone_test import (
    MicrophoneTestService,
    choose_preferred_microphone_label,
    load_preferred_microphone,
    save_preferred_microphone,
)


class _CallbackStop(Exception):
    pass


class _Stream:
    """Endpoint sano, alimentado desde otro hilo como hace PortAudio.

    El callback corre fuera del hilo que llama a ``_capture``, que es el punto
    entero del rediseño: si se entregara todo adentro de ``start()``, el
    ``Event`` ya estaría seteado antes del ``wait()`` y ningún test tocaría la
    concurrencia real.
    """

    def __init__(
        self, callback, frames, level=0.2, blocks=4, deliver=None, block_size=None
    ):
        self._callback = callback
        self._frames = frames
        self._level = level
        self._blocks = blocks
        self._deliver = frames if deliver is None else deliver
        self._block_size = block_size
        self._thread = None
        self.aborted = False
        self.closed = False

    def _feed(self):
        # PortAudio entrega bloques de tamaño fijo, así que el último bloque
        # suele pasarse del pedido. `block_size` permite reproducir ese exceso:
        # sin él, el recorte de `_capture` nunca se ejercita.
        block_size = self._block_size or max(1, self._frames // self._blocks)
        sent = 0
        while sent < self._deliver:
            size = block_size if self._block_size else min(
                block_size, self._deliver - sent
            )
            block = np.full((size, 1), self._level, dtype=np.float32)
            try:
                self._callback(block, size, None, None)
            except _CallbackStop:
                return
            sent += size

    def start(self):
        self._thread = threading.Thread(target=self._feed, daemon=True)
        self._thread.start()

    def abort(self, ignore_errors=True):
        self.aborted = True
        if self._thread is not None:
            self._thread.join(timeout=5)

    def close(self, ignore_errors=True):
        self.closed = True


class _UnstartableStream:
    """Endpoint que abre pero rechaza el start: device ocupado, rate inválido."""

    def __init__(self, *_args, **_kwargs):
        self.aborted = False
        self.closed = False

    def start(self):
        raise RuntimeError("Pa_StartStream: device unavailable")

    def abort(self, ignore_errors=True):
        self.aborted = True

    def close(self, ignore_errors=True):
        self.closed = True


class _StalledStream:
    """Endpoint que abre bien y nunca llama al callback."""

    def __init__(self, *_args, **_kwargs):
        self.aborted = False
        self.closed = False

    def start(self):
        return None

    def abort(self, ignore_errors=True):
        self.aborted = True

    def close(self, ignore_errors=True):
        self.closed = True


def _sounddevice_stub(devices, host_apis, stream_factory):
    module = mock.Mock()
    module.query_devices.return_value = devices
    module.query_hostapis.return_value = host_apis
    module.default.device = (0, 0)
    module.CallbackStop = _CallbackStop
    module.InputStream.side_effect = stream_factory
    return module


class MicrophoneTestServiceTests(unittest.TestCase):
    def test_lists_physical_microphones_without_host_api_duplicates(self):
        soundcard_module = mock.Mock()
        soundcard_module.all_microphones.return_value = [
            SimpleNamespace(name="Micrófono (PD200X Podcast Microphone)"),
            SimpleNamespace(name="Micrófono (C505e HD Webcam)"),
        ]
        sounddevice_module = mock.Mock()

        service = MicrophoneTestService(
            soundcard_module=soundcard_module,
            sounddevice_module=sounddevice_module,
        )

        self.assertEqual(
            service.list_microphones(),
            [
                "Micrófono (PD200X Podcast Microphone)",
                "Micrófono (C505e HD Webcam)",
            ],
        )

    def test_probe_records_and_transcribes_selected_microphone(self):
        def stream_factory(**kwargs):
            return _Stream(kwargs["callback"], frames=16000)

        sounddevice_module = _sounddevice_stub(
            devices=[
                {
                    "name": "Micrófono (PD200X Podcast Microphone)",
                    "max_input_channels": 1,
                    "default_samplerate": 16000,
                    "hostapi": 0,
                }
            ],
            host_apis=[{"name": "Windows WASAPI"}],
            stream_factory=stream_factory,
        )
        transcriber = mock.Mock()
        transcriber.transcribe_array.return_value = (["Probando uno dos tres."], "es")
        service = MicrophoneTestService(
            transcriber=transcriber,
            soundcard_module=mock.Mock(),
            sounddevice_module=sounddevice_module,
        )

        result = service.probe(
            "Micrófono (PD200X Podcast Microphone)", duration_seconds=1
        )

        self.assertEqual(result.transcription, "Probando uno dos tres.")
        self.assertGreater(result.mean_level, 0.1)
        self.assertEqual(result.sample_rate, 16000)
        self.assertTrue(result.audio.size)

    def test_wasapi_endpoint_is_preferred_over_mme_for_the_same_microphone(self):
        """MME va último: hay placas que abren por MME y después no dan frames."""
        devices = [
            {
                "name": "Micrófono (Maono AI Microphone)",
                "max_input_channels": 1,
                "default_samplerate": 44100,
                "hostapi": 0,
            },
            {
                "name": "Micrófono (Maono AI Microphone)",
                "max_input_channels": 1,
                "default_samplerate": 48000,
                "hostapi": 1,
            },
        ]
        sounddevice_module = _sounddevice_stub(
            devices=devices,
            host_apis=[{"name": "MME"}, {"name": "Windows WASAPI"}],
            stream_factory=lambda **kwargs: _Stream(kwargs["callback"], frames=8),
        )
        service = MicrophoneTestService(
            transcriber=None,
            soundcard_module=mock.Mock(),
            sounddevice_module=sounddevice_module,
        )

        ordered = service._candidate_devices("Micrófono (Maono AI Microphone)")

        self.assertEqual([index for index, _device in ordered], [1, 0])

    def test_wdm_ks_endpoints_go_last_instead_of_being_discarded(self):
        """WDM-KS falla al abrir en 0 s, pero descartarlo dejaría sin probar un
        micrófono que solo se exponga por ahí."""
        devices = [
            {
                "name": "Micrófono (PD200X Podcast Microphone)",
                "max_input_channels": 1,
                "default_samplerate": 44100,
                "hostapi": 0,
            },
            {
                "name": "Micrófono (PD200X Podcast Microphone)",
                "max_input_channels": 1,
                "default_samplerate": 48000,
                "hostapi": 1,
            },
            {
                "name": "Micrófono (PD200X Podcast Microphone)",
                "max_input_channels": 1,
                "default_samplerate": 44100,
                "hostapi": 2,
            },
        ]
        sounddevice_module = _sounddevice_stub(
            devices=devices,
            host_apis=[
                {"name": "Windows WDM-KS"},
                {"name": "Windows WASAPI"},
                {"name": "MME"},
            ],
            stream_factory=lambda **kwargs: _Stream(kwargs["callback"], frames=8),
        )
        service = MicrophoneTestService(
            transcriber=None,
            soundcard_module=mock.Mock(),
            sounddevice_module=sounddevice_module,
        )

        ordered = service._candidate_devices("Micrófono (PD200X Podcast Microphone)")

        # Ninguno se descarta, y WDM-KS queda detrás incluso de MME.
        self.assertEqual([index for index, _device in ordered], [1, 2, 0])

    def test_stalled_endpoint_is_abandoned_instead_of_blocking_forever(self):
        """El bug original: un endpoint mudo dejaba el hilo dentro de PortAudio."""
        streams = []

        def stream_factory(**kwargs):
            if len(streams) == 0:
                stream = _StalledStream()
            else:
                stream = _Stream(kwargs["callback"], frames=16000)
            streams.append(stream)
            return stream

        sounddevice_module = _sounddevice_stub(
            devices=[
                {
                    "name": "Micrófono (Maono AI Microphone)",
                    "max_input_channels": 1,
                    "default_samplerate": 16000,
                    "hostapi": 0,
                },
                {
                    "name": "Micrófono (Maono AI Microphone)",
                    "max_input_channels": 1,
                    "default_samplerate": 16000,
                    "hostapi": 1,
                },
            ],
            host_apis=[{"name": "Windows WASAPI"}, {"name": "Windows DirectSound"}],
            stream_factory=stream_factory,
        )
        service = MicrophoneTestService(
            transcriber=None,
            soundcard_module=mock.Mock(),
            sounddevice_module=sounddevice_module,
        )

        with mock.patch.object(microphone_test, "_FIRST_FRAME_SECONDS", 0.05):
            result = service.probe(
                "Micrófono (Maono AI Microphone)", duration_seconds=0.05
            )

        self.assertGreater(result.mean_level, 0.1)
        self.assertTrue(streams[0].aborted)
        self.assertTrue(streams[0].closed)

    def test_stalled_endpoint_is_not_retried_at_a_second_sample_rate(self):
        """Un endpoint estancado no da frames a ningún rate: reintentar gasta 8 s."""
        opened = []

        def stream_factory(**kwargs):
            opened.append(kwargs["samplerate"])
            return _StalledStream()

        sounddevice_module = _sounddevice_stub(
            devices=[
                {
                    "name": "Micrófono (Maono AI Microphone)",
                    "max_input_channels": 1,
                    "default_samplerate": 48000,
                    "hostapi": 0,
                }
            ],
            host_apis=[{"name": "Windows WASAPI"}],
            stream_factory=stream_factory,
        )
        service = MicrophoneTestService(
            transcriber=None,
            soundcard_module=mock.Mock(),
            sounddevice_module=sounddevice_module,
        )

        with mock.patch.object(microphone_test, "_FIRST_FRAME_SECONDS", 0.05):
            with self.assertRaises(microphone_test.EndpointStalled) as caught:
                service.probe(
                    "Micrófono (Maono AI Microphone)", duration_seconds=0.05
                )

        self.assertEqual(opened, [48000])
        self.assertIn("silenciado", str(caught.exception))

    def test_silent_take_comes_back_as_a_result_not_as_a_device_failure(self):
        """Nadie habló: el micrófono anduvo y hay que devolver la toma igual."""
        sounddevice_module = _sounddevice_stub(
            devices=[
                {
                    "name": "Micrófono (PD200X Podcast Microphone)",
                    "max_input_channels": 1,
                    "default_samplerate": 16000,
                    "hostapi": 0,
                }
            ],
            host_apis=[{"name": "Windows WASAPI"}],
            stream_factory=lambda **kwargs: _Stream(
                kwargs["callback"], frames=16000, level=0.0
            ),
        )
        transcriber = mock.Mock()
        service = MicrophoneTestService(
            transcriber=transcriber,
            soundcard_module=mock.Mock(),
            sounddevice_module=sounddevice_module,
        )

        result = service.probe(
            "Micrófono (PD200X Podcast Microphone)", duration_seconds=1
        )

        self.assertEqual(result.mean_level, 0.0)
        self.assertEqual(result.transcription, "")
        self.assertTrue(result.playback_path.exists())
        transcriber.transcribe_array.assert_not_called()

    def test_stream_is_closed_when_start_fails(self):
        """Sin esto se filtra el handle: sounddevice no define __del__ y el
        micrófono queda tomado hasta que se cierra la app."""
        streams = []

        def stream_factory(**kwargs):
            stream = _UnstartableStream() if not streams else _Stream(
                kwargs["callback"], frames=16000
            )
            streams.append(stream)
            return stream

        sounddevice_module = _sounddevice_stub(
            devices=[
                {
                    "name": "Micrófono (PD200X Podcast Microphone)",
                    "max_input_channels": 1,
                    "default_samplerate": 16000,
                    "hostapi": 0,
                },
                {
                    "name": "Micrófono (PD200X Podcast Microphone)",
                    "max_input_channels": 1,
                    "default_samplerate": 16000,
                    "hostapi": 1,
                },
            ],
            host_apis=[{"name": "Windows WASAPI"}, {"name": "Windows DirectSound"}],
            stream_factory=stream_factory,
        )
        service = MicrophoneTestService(
            transcriber=None,
            soundcard_module=mock.Mock(),
            sounddevice_module=sounddevice_module,
        )

        result = service.probe(
            "Micrófono (PD200X Podcast Microphone)", duration_seconds=1
        )

        self.assertGreater(result.mean_level, 0.1)
        self.assertTrue(streams[0].closed)

    def test_partial_take_is_kept_instead_of_reported_as_stalled(self):
        """El callback alcanzó a entregar audio: tirarlo obliga a regrabar."""
        sounddevice_module = _sounddevice_stub(
            devices=[
                {
                    "name": "Micrófono (PD200X Podcast Microphone)",
                    "max_input_channels": 1,
                    "default_samplerate": 16000,
                    "hostapi": 0,
                }
            ],
            host_apis=[{"name": "Windows WASAPI"}],
            stream_factory=lambda **kwargs: _Stream(
                kwargs["callback"], frames=16000, deliver=6000
            ),
        )
        service = MicrophoneTestService(
            transcriber=None,
            soundcard_module=mock.Mock(),
            sounddevice_module=sounddevice_module,
        )

        # Pide 16000 frames y el endpoint muere entregando 6000.
        with mock.patch.object(microphone_test, "_COMPLETION_GRACE_SECONDS", 0.05):
            result = service.probe(
                "Micrófono (PD200X Podcast Microphone)", duration_seconds=1
            )

        self.assertEqual(result.audio.size, 6000)
        self.assertGreater(result.mean_level, 0.1)
        # El audio se conserva, pero la toma queda marcada: un pico corto y
        # fuerte pasa el control de nivel y el micrófono igual está roto.
        self.assertTrue(result.is_partial)
        self.assertAlmostEqual(result.captured_seconds, 0.375, places=3)

    def test_complete_take_is_not_flagged_as_partial(self):
        sounddevice_module = _sounddevice_stub(
            devices=[
                {
                    "name": "Micrófono (PD200X Podcast Microphone)",
                    "max_input_channels": 1,
                    "default_samplerate": 16000,
                    "hostapi": 0,
                }
            ],
            host_apis=[{"name": "Windows WASAPI"}],
            stream_factory=lambda **kwargs: _Stream(kwargs["callback"], frames=16000),
        )
        service = MicrophoneTestService(
            transcriber=None,
            soundcard_module=mock.Mock(),
            sounddevice_module=sounddevice_module,
        )

        result = service.probe(
            "Micrófono (PD200X Podcast Microphone)", duration_seconds=1
        )

        self.assertFalse(result.is_partial)

    def test_overshooting_blocks_are_trimmed_to_the_requested_length(self):
        """PortAudio entrega bloques de tamaño fijo y casi siempre se pasa."""
        sounddevice_module = _sounddevice_stub(
            devices=[
                {
                    "name": "Micrófono (PD200X Podcast Microphone)",
                    "max_input_channels": 1,
                    "default_samplerate": 16000,
                    "hostapi": 0,
                }
            ],
            host_apis=[{"name": "Windows WASAPI"}],
            stream_factory=lambda **kwargs: _Stream(
                kwargs["callback"], frames=16000, block_size=1024
            ),
        )
        service = MicrophoneTestService(
            transcriber=None,
            soundcard_module=mock.Mock(),
            sounddevice_module=sounddevice_module,
        )

        result = service.probe(
            "Micrófono (PD200X Podcast Microphone)", duration_seconds=1
        )

        # 16 bloques de 1024 son 16384: sin recorte la toma sale más larga que
        # lo pedido y quedaría desalineada con requested_seconds.
        self.assertEqual(result.audio.size, 16000)
        self.assertFalse(result.is_partial)

    def test_a_silent_endpoint_does_not_shadow_a_working_one(self):
        """El primer endpoint mudo se guarda, pero la búsqueda sigue."""
        def stream_factory(**kwargs):
            level = 0.0 if kwargs["device"] == 0 else 0.5
            return _Stream(kwargs["callback"], frames=16000, level=level)

        sounddevice_module = _sounddevice_stub(
            devices=[
                {
                    "name": "Micrófono (PD200X Podcast Microphone)",
                    "max_input_channels": 1,
                    "default_samplerate": 16000,
                    "hostapi": 0,
                },
                {
                    "name": "Micrófono (PD200X Podcast Microphone)",
                    "max_input_channels": 1,
                    "default_samplerate": 16000,
                    "hostapi": 1,
                },
            ],
            host_apis=[{"name": "Windows WASAPI"}, {"name": "Windows DirectSound"}],
            stream_factory=stream_factory,
        )
        service = MicrophoneTestService(
            transcriber=None,
            soundcard_module=mock.Mock(),
            sounddevice_module=sounddevice_module,
        )

        result = service.probe(
            "Micrófono (PD200X Podcast Microphone)", duration_seconds=1
        )

        self.assertGreater(result.mean_level, 0.1)

    def test_a_good_take_stops_the_search_instead_of_recording_again(self):
        """Sin el break, la segunda tasa regrababa y pisaba la toma buena."""
        opened = []

        def stream_factory(**kwargs):
            opened.append(kwargs["samplerate"])
            return _Stream(kwargs["callback"], frames=kwargs["samplerate"])

        sounddevice_module = _sounddevice_stub(
            devices=[
                {
                    "name": "Micrófono (PD200X Podcast Microphone)",
                    "max_input_channels": 1,
                    "default_samplerate": 48000,
                    "hostapi": 0,
                }
            ],
            host_apis=[{"name": "Windows WASAPI"}],
            stream_factory=stream_factory,
        )
        service = MicrophoneTestService(
            transcriber=None,
            soundcard_module=mock.Mock(),
            sounddevice_module=sounddevice_module,
        )

        result = service.probe(
            "Micrófono (PD200X Podcast Microphone)", duration_seconds=1
        )

        self.assertEqual(opened, [48000])
        self.assertGreater(result.mean_level, 0.1)

    def test_playback_survives_a_transcriber_failure(self):
        """El wav se escribe antes de transcribir: si el motor muere, la toma
        sigue siendo escuchable en vez de obligar a regrabar."""
        sounddevice_module = _sounddevice_stub(
            devices=[
                {
                    "name": "Micrófono (PD200X Podcast Microphone)",
                    "max_input_channels": 1,
                    "default_samplerate": 16000,
                    "hostapi": 0,
                }
            ],
            host_apis=[{"name": "Windows WASAPI"}],
            stream_factory=lambda **kwargs: _Stream(kwargs["callback"], frames=16000),
        )
        transcriber = mock.Mock()
        transcriber.transcribe_array.side_effect = RuntimeError("modelo caido")
        service = MicrophoneTestService(
            transcriber=transcriber,
            soundcard_module=mock.Mock(),
            sounddevice_module=sounddevice_module,
        )
        written = []
        with mock.patch.object(
            MicrophoneTestService,
            "_write_playback",
            side_effect=lambda audio, rate: written.append(audio.size) or Path("x.wav"),
        ):
            with self.assertRaises(RuntimeError):
                service.probe(
                    "Micrófono (PD200X Podcast Microphone)", duration_seconds=1
                )

        self.assertEqual(written, [16000])

    def test_transcriber_failure_does_not_rerecord_every_endpoint(self):
        """Un fallo del motor no es un micrófono malo: regrabar culpa al equipo
        equivocado y gasta 5 s por candidato."""
        opened = []

        def stream_factory(**kwargs):
            opened.append(kwargs["device"])
            return _Stream(kwargs["callback"], frames=16000)

        sounddevice_module = _sounddevice_stub(
            devices=[
                {
                    "name": "Micrófono (PD200X Podcast Microphone)",
                    "max_input_channels": 1,
                    "default_samplerate": 16000,
                    "hostapi": 0,
                },
                {
                    "name": "Micrófono (PD200X Podcast Microphone)",
                    "max_input_channels": 1,
                    "default_samplerate": 16000,
                    "hostapi": 1,
                },
            ],
            host_apis=[{"name": "Windows WASAPI"}, {"name": "Windows DirectSound"}],
            stream_factory=stream_factory,
        )
        transcriber = mock.Mock()
        transcriber.transcribe_array.side_effect = RuntimeError("modelo caido")
        service = MicrophoneTestService(
            transcriber=transcriber,
            soundcard_module=mock.Mock(),
            sounddevice_module=sounddevice_module,
        )

        with self.assertRaises(RuntimeError):
            service.probe(
                "Micrófono (PD200X Podcast Microphone)", duration_seconds=1
            )

        self.assertEqual(opened, [0])
        transcriber.transcribe_array.assert_called_once()

    def test_dead_endpoint_is_abandoned_on_the_first_frame_deadline(self):
        """Los dos plazos son independientes a propósito.

        Con un plazo único, rendirse ante un endpoint muerto costaba
        ``duración + gracia``: subir la tolerancia de arranque para un
        micrófono Bluetooth castigaba también al muerto. Este test fija esa
        separación — la gracia de finalización es enorme y el abandono tiene
        que seguir ocurriendo rápido.
        """
        sounddevice_module = _sounddevice_stub(
            devices=[
                {
                    "name": "Micrófono (Maono AI Microphone)",
                    "max_input_channels": 1,
                    "default_samplerate": 16000,
                    "hostapi": 0,
                }
            ],
            host_apis=[{"name": "Windows WASAPI"}],
            stream_factory=lambda **kwargs: _StalledStream(),
        )
        service = MicrophoneTestService(
            transcriber=None,
            soundcard_module=mock.Mock(),
            sounddevice_module=sounddevice_module,
        )

        with mock.patch.object(microphone_test, "_FIRST_FRAME_SECONDS", 0.05), \
                mock.patch.object(
                    microphone_test, "_COMPLETION_GRACE_SECONDS", 30.0
                ):
            started = time.monotonic()
            with self.assertRaises(microphone_test.EndpointStalled):
                service.probe(
                    "Micrófono (Maono AI Microphone)", duration_seconds=0.05
                )
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 5.0)

    def test_preferred_microphone_round_trip(self):
        repository = mock.Mock()
        repository.get_setting.return_value = "Micrófono (PD200X Podcast Microphone)"

        save_preferred_microphone(repository, "Micrófono (PD200X Podcast Microphone)")
        loaded = load_preferred_microphone(repository)

        repository.set_setting.assert_called_once_with(
            "preferred_microphone", "Micrófono (PD200X Podcast Microphone)"
        )
        self.assertEqual(loaded, "Micrófono (PD200X Podcast Microphone)")

    def test_resolves_saved_physical_microphone_to_ui_label(self):
        mapping = {
            "🎤  Webcam": "Micrófono (C505e HD Webcam)",
            "🎤  Podcast": "Micrófono (PD200X Podcast Microphone)",
        }

        selected = choose_preferred_microphone_label(
            mapping, "Micrófono (PD200X Podcast Microphone)"
        )

        self.assertEqual(selected, "🎤  Podcast")


if __name__ == "__main__":
    unittest.main()
