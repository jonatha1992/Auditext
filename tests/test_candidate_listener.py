"""Regression tests for the microphone fallback pacing."""

from __future__ import annotations

import threading
import unittest
from unittest import mock

import numpy as np

from presentation.views.interview_frame import (
    CandidateListener,
    InterviewFrame,
    _candidate_sample_rates,
    _new_transcript_suffix,
    _resample_mono,
)


class _ImmediateStream:
    def __init__(self, stop: threading.Event):
        self.stop = stop
        self.reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, block):
        self.reads += 1
        self.stop.set()
        return np.zeros((block, 1), dtype=np.float32), False


class CandidateListenerPacingTests(unittest.TestCase):
    def test_overlapping_transcription_only_emits_new_words(self):
        self.assertEqual(
            _new_transcript_suffix(
                "un modelo de caja blanca",
                "de caja blanca permite ver el proceso",
            ),
            "permite ver el proceso",
        )

    def test_audio_is_queued_instead_of_transcribed_on_capture_thread(self):
        listener = CandidateListener(mock.Mock(), mock.Mock())
        listener._audio_queue = mock.Mock()

        listener._queue_audio(np.ones(48000, dtype=np.float32))

        listener._audio_queue.put_nowait.assert_called_once()

    def test_simulation_submit_drains_pending_transcription_first(self):
        frame = mock.Mock()
        frame._simulation_busy = False
        frame._simulation_session = mock.Mock()
        frame._simulation_answer_parts = []

        def drain_pending(*_args):
            frame._simulation_answer_parts.append("Una clase define objetos.")

        frame._append_batch.side_effect = drain_pending
        frame._submit_drained_simulation_answer.side_effect = lambda: (
            InterviewFrame._submit_drained_simulation_answer(frame)
        )

        test_case = self

        class ImmediateThread:
            def __init__(self, *, target, daemon):
                test_case.assertTrue(daemon)
                self.target = target

            def start(self):
                self.target()

        frame.after.side_effect = lambda _delay, callback: callback()
        with mock.patch(
            "presentation.views.interview_frame.threading.Thread",
            side_effect=ImmediateThread,
        ):
            InterviewFrame._submit_simulation_answer(frame)

        frame._append_batch.assert_called_once_with(
            frame.candidate_box, "candidate", frame.candidate_queue
        )
        frame.candidate_listener.pause.assert_called_once_with()
        frame.candidate_listener.wait_until_idle.assert_called_once_with(timeout=10.0)

    def test_pyaudio_fallback_captures_selected_usb_microphone(self):
        listener = CandidateListener(mock.Mock(), mock.Mock())
        consumed = []
        stream = mock.Mock()

        def read(_block, exception_on_overflow=False):
            listener._stop.set()
            return (np.ones(8000, dtype=np.int16) * 1200).tobytes()

        stream.read.side_effect = read
        engine = mock.Mock()
        engine.get_device_count.return_value = 1
        engine.get_device_info_by_index.return_value = {
            "name": "Microphone (PD200X Podcast Microphone)",
            "maxInputChannels": 1,
            "defaultSampleRate": 44100,
        }
        engine.open.return_value = stream
        fake_pyaudio = mock.MagicMock()
        fake_pyaudio.paInt16 = 8
        fake_pyaudio.PyAudio.return_value = engine

        with mock.patch.dict("sys.modules", {"pyaudio": fake_pyaudio}):
            listener._capture_with_pyaudio(
                "PD200X Podcast Microphone", 16000, 8000, consumed.append
            )

        self.assertEqual(len(consumed), 1)
        self.assertGreater(float(np.abs(consumed[0]).mean()), 0.01)
        self.assertEqual(engine.open.call_args.kwargs["input_device_index"], 0)
        stream.close.assert_called_once_with()
        engine.terminate.assert_called_once_with()

    def test_device_default_rate_is_tried_before_target_rate(self):
        self.assertEqual(
            _candidate_sample_rates({"default_samplerate": 48000}, 16000),
            [48000, 16000],
        )

    def test_captured_audio_is_resampled_for_transcription(self):
        audio = np.linspace(-1, 1, 48000, dtype=np.float32)

        converted = _resample_mono(audio, 48000, 16000)

        self.assertEqual(converted.dtype, np.float32)
        self.assertEqual(len(converted), 16000)

    def test_sounddevice_prefers_matching_default_input_over_duplicate_endpoint(self):
        listener = CandidateListener(mock.Mock(), mock.Mock())
        stream = _ImmediateStream(listener._stop)
        devices = [
            {
                "name": "Micrófono (PD200X Podcast Microphone)",
                "max_input_channels": 1,
                "default_samplerate": 48000,
            },
            {
                "name": "Micrófono (PD200X Podcast Micro",
                "max_input_channels": 1,
                "default_samplerate": 44100,
            },
        ]
        fake_sd = mock.MagicMock()
        fake_sd.query_devices.return_value = devices
        fake_sd.default.device = (1, 0)
        fake_sd.InputStream.return_value = stream

        with mock.patch.dict("sys.modules", {"sounddevice": fake_sd}):
            listener._capture_with_sounddevice(
                "Micrófono (PD200X Podcast Microphone)",
                16000,
                8000,
                mock.Mock(),
            )

        self.assertEqual(fake_sd.InputStream.call_args.kwargs["device"], 1)

    def test_immediate_empty_read_is_paced(self):
        listener = CandidateListener(mock.Mock(), mock.Mock())
        stream = _ImmediateStream(listener._stop)
        device = {"name": "Mic", "max_input_channels": 1}
        fake_sd = mock.MagicMock()
        fake_sd.query_devices.return_value = [device]
        fake_sd.InputStream.return_value = stream

        with (
            mock.patch.dict("sys.modules", {"sounddevice": fake_sd}),
            mock.patch.object(listener._stop, "wait", wraps=listener._stop.wait) as wait,
        ):
            listener._capture_with_sounddevice(
                "Mic", 16000, 8000, mock.Mock()
            )

        wait.assert_called_once()
        self.assertAlmostEqual(wait.call_args.args[0], 0.5, delta=0.05)


if __name__ == "__main__":
    unittest.main()
