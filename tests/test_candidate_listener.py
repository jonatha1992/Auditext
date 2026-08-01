"""Regression tests for the microphone fallback pacing."""

from __future__ import annotations

import threading
import unittest
from unittest import mock

import numpy as np

from presentation.views.interview_frame import CandidateListener


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
