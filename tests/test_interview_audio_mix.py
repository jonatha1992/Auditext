import numpy as np
from unittest import mock

from infrastructure.services.live_transcriber import Transcriber, mix_mono_tracks


def test_pause_and_resume_are_forwarded_to_live_question_session():
    import queue

    worker = Transcriber(queue.Queue(), queue.Queue())
    live = mock.Mock()
    worker._live_session = live
    worker._thread = mock.Mock()
    worker._thread.is_alive.return_value = True

    worker.pause()
    worker.resume()

    live.pause.assert_called_once_with()
    live.resume.assert_called_once_with()


def test_mix_mono_tracks_combines_both_sides():
    system = np.array([1.0, 0.0, -1.0], dtype=np.float32)
    mic = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    mixed = mix_mono_tracks(system, mic, system_gain=1.0, mic_gain=1.0)
    assert mixed[0] == 1.0
    assert mixed[1] == 1.0
    assert mixed[2] == -1.0


def test_mix_mono_tracks_clips_to_unit_range():
    system = np.array([1.0], dtype=np.float32)
    mic = np.array([1.0], dtype=np.float32)
    mixed = mix_mono_tracks(system, mic, system_gain=1.0, mic_gain=1.0)
    assert mixed[0] == 1.0


def test_push_candidate_audio_fifo_alignment():
    import queue

    worker = Transcriber(queue.Queue(), queue.Queue())
    worker._save_audio = True
    worker._interview_mode = True

    worker.push_candidate_audio(np.ones(4, dtype=np.float32))
    taken = worker._take_mic_for_mix(4)
    assert np.allclose(taken, np.ones(4))

    worker.push_candidate_audio(np.full(6, 0.5, dtype=np.float32))
    taken = worker._take_mic_for_mix(4)
    assert np.allclose(taken, np.full(4, 0.5))
    taken = worker._take_mic_for_mix(4)
    assert np.allclose(taken[:2], np.full(2, 0.5))
    assert np.allclose(taken[2:], np.zeros(2))


def test_push_candidate_audio_ignored_when_not_interview_mode():
    import queue

    worker = Transcriber(queue.Queue(), queue.Queue())
    worker._save_audio = True
    worker._interview_mode = False

    worker.push_candidate_audio(np.ones(8, dtype=np.float32))
    taken = worker._take_mic_for_mix(8)
    assert np.allclose(taken, np.zeros(8))
