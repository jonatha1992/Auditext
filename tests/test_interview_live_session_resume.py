"""Session survival across Gemini Live's own duration limit (no network).

The regression these guard: a live session ended after ~10 minutes with a
`go_away` warning the client ignored, the socket died with 1008, and the
connect loop read that as "this model is broken" and fell to the next, slower
entry of LIVE_MODELS. An oral exam degraded silently and then stopped.
"""

from __future__ import annotations

import asyncio
import contextlib
import unittest
from unittest import mock

from infrastructure.services import interview_live
from infrastructure.services.interview_live import InterviewLiveSession


def _session(mode: str = "examen_oral") -> InterviewLiveSession:
    return InterviewLiveSession(
        context="temario",
        on_transcript=lambda _t: None,
        on_assist=lambda _a: None,
        on_status=lambda _s: None,
        mode=mode,
    )


class GoAwayHandlingTests(unittest.TestCase):
    def test_resumption_handle_is_kept(self):
        live = _session()
        msg = mock.Mock(
            session_resumption_update=mock.Mock(
                resumable=True, new_handle="handle-abc"
            ),
            go_away=None,
            server_content=None,
        )

        asyncio.run(live._handle_message(msg))

        self.assertEqual(live._resume_handle, "handle-abc")

    def test_non_resumable_update_is_ignored(self):
        live = _session()
        msg = mock.Mock(
            session_resumption_update=mock.Mock(resumable=False, new_handle="x"),
            go_away=None,
            server_content=None,
        )

        asyncio.run(live._handle_message(msg))

        self.assertIsNone(live._resume_handle)

    def test_go_away_requests_reconnect_and_ends_the_session_cleanly(self):
        live = _session()
        msg = mock.Mock(
            session_resumption_update=None,
            go_away=mock.Mock(time_left="10s"),
            server_content=None,
        )

        async def scenario():
            live._audio_q = asyncio.Queue(maxsize=8)
            await live._handle_message(msg)
            return live._audio_q.get_nowait()

        sentinel = asyncio.run(scenario())

        self.assertTrue(live._reconnect_requested)
        # The sentinel is what ends `_send_loop`, which is what `_session_loop`
        # waits on: nothing is killed mid-await and the websocket closes the way
        # the server asked, so no 1008 is raised at all.
        self.assertIsNone(sentinel)

    def test_go_away_on_a_full_queue_still_gets_through(self):
        live = _session()
        msg = mock.Mock(
            session_resumption_update=None,
            go_away=mock.Mock(time_left=None),
            server_content=None,
        )

        async def scenario():
            live._audio_q = asyncio.Queue(maxsize=1)
            live._audio_q.put_nowait(b"pcm")
            await live._handle_message(msg)
            return live._audio_q.get_nowait()

        self.assertIsNone(asyncio.run(scenario()))

    def test_transcription_still_reaches_the_buffer(self):
        # The new early reads must not shadow the message that matters most.
        seen: list[str] = []
        live = InterviewLiveSession(
            context="temario",
            on_transcript=seen.append,
            on_assist=lambda _a: None,
            on_status=lambda _s: None,
            mode="examen_oral",
        )
        msg = mock.Mock(
            session_resumption_update=None,
            go_away=None,
            server_content=mock.Mock(
                input_transcription=mock.Mock(text="¿Qué es un caso de uso?")
            ),
        )

        asyncio.run(live._handle_message(msg))

        self.assertEqual(seen, ["¿Qué es un caso de uso?"])
        self.assertFalse(live._reconnect_requested)


class LiveConfigTests(unittest.TestCase):
    def test_config_carries_the_handle_and_compression(self):
        from google.genai import types

        live = _session()
        live._resume_handle = "handle-xyz"

        config = live._live_config(types)

        self.assertEqual(config.session_resumption.handle, "handle-xyz")
        self.assertEqual(
            config.context_window_compression.trigger_tokens,
            live._COMPRESSION_TRIGGER_TOKENS,
        )
        self.assertEqual(
            config.context_window_compression.sliding_window.target_tokens,
            live._COMPRESSION_TARGET_TOKENS,
        )

    def test_first_connect_has_no_handle(self):
        from google.genai import types

        config = _session()._live_config(types)

        self.assertIsNone(config.session_resumption.handle)


class _FakeConnect:
    """Stands in for `client.aio.live.connect` as an async context manager."""

    def __init__(self, models, configs):
        self._models = models
        self._configs = configs

    def __call__(self, *, model, config):
        self._models.append(model)
        self._configs.append(config)
        return self

    async def __aenter__(self):
        return mock.Mock()

    async def __aexit__(self, *exc_info):
        return False


class ConnectLoopTests(unittest.TestCase):
    """What the session does when a connection ends, per reason."""

    MODELS = ["modelo-bueno", "modelo-viejo", "modelo-peor"]

    def _run_connect(self, session_outcomes, healthy_seconds=None):
        """Drive `_run` with a scripted sequence of session endings.

        Each outcome is either an exception to raise from `_session_loop` or
        the string "goaway" (a clean end that asked for a reconnect).
        """
        live = _session()
        if healthy_seconds is not None:
            live._HEALTHY_SESSION_SECONDS = healthy_seconds
        models_tried: list[str] = []
        configs: list[object] = []
        outcomes = list(session_outcomes)

        async def fake_session_loop(_session, _types):
            outcome = outcomes.pop(0) if outcomes else None
            if outcome is None:  # the user stopped: a clean, final end
                return
            if outcome == "goaway":
                live._reconnect_requested = True
                live._resume_handle = f"handle-{len(models_tried)}"
                return
            raise outcome

        client = mock.MagicMock()
        client.aio.live.connect = _FakeConnect(models_tried, configs)

        pool = mock.Mock()
        pool.available.return_value = ["key-1"]
        pool.current_label.return_value = "API 1"
        pool.is_quota_error.return_value = False

        with (
            mock.patch.object(interview_live, "LIVE_MODELS", self.MODELS),
            mock.patch.object(interview_live.gemini_keys, "pool", pool),
            mock.patch.object(interview_live.nvidia_provider, "pool", mock.Mock()),
            mock.patch.object(interview_live, "_fallback_providers", return_value=[]),
            mock.patch("google.genai.Client", return_value=client),
            mock.patch.object(live, "_session_loop", fake_session_loop),
        ):
            with contextlib.suppress(interview_live.InterviewLiveError):
                asyncio.run(live._run())
        return models_tried, configs, live

    def test_goaway_reconnects_to_the_same_model(self):
        models, configs, live = self._run_connect(["goaway", "goaway", None])

        # This is the regression: three sessions, one model. Before the fix the
        # second connect landed on modelo-viejo and the third on modelo-peor.
        self.assertEqual(models, ["modelo-bueno"] * 3)
        # And the reconnects carried the handle, so the conversation continued.
        self.assertIsNone(configs[0].session_resumption.handle)
        self.assertEqual(configs[1].session_resumption.handle, "handle-1")

    def test_1008_retries_the_same_model_instead_of_blaming_it(self):
        # 1008 is how the duration limit surfaces when the GoAway was missed.
        error = RuntimeError(
            "1008 None. Connection aborted because the client failed to close "
            "the connection after receiving a GoAway signal once the session "
            "duration limit was reached"
        )
        models, _configs, _live = self._run_connect([error, None])

        self.assertEqual(models, ["modelo-bueno", "modelo-bueno"])

    def test_1007_still_falls_to_the_next_model(self):
        # A genuinely unavailable model must keep using the fallback list.
        models, _configs, _live = self._run_connect([RuntimeError("1007 not found"), None])

        self.assertEqual(models, ["modelo-bueno", "modelo-viejo"])

    def test_instant_reconnect_flapping_gives_up_and_changes_model(self):
        # Every session here ends immediately, which is a broken model, not the
        # server's rollover schedule.
        outcomes = ["goaway"] * (InterviewLiveSession._MAX_RECONNECTS + 1) + [None]
        models, _configs, _live = self._run_connect(outcomes)

        self.assertEqual(
            models.count("modelo-bueno"), InterviewLiveSession._MAX_RECONNECTS + 1
        )
        self.assertIn("modelo-viejo", models)

    def test_a_long_exam_rolls_over_indefinitely_on_the_same_model(self):
        # healthy_seconds=0 makes every session count as one that actually ran,
        # which is what a real 10-minute rollover looks like. The cap must not
        # apply: an oral exam longer than an hour still never changes model.
        outcomes = ["goaway"] * (InterviewLiveSession._MAX_RECONNECTS * 3) + [None]
        models, _configs, _live = self._run_connect(outcomes, healthy_seconds=0.0)

        self.assertEqual(set(models), {"modelo-bueno"})
        self.assertEqual(len(models), InterviewLiveSession._MAX_RECONNECTS * 3 + 1)


class RecvLoopTests(unittest.TestCase):
    def test_recv_loop_returns_when_the_server_closes(self):
        # Looping back would call receive() on a dead session forever.
        calls = 0

        class FakeSession:
            def receive(self):
                nonlocal calls
                calls += 1

                async def gen():
                    return
                    yield  # pragma: no cover - generator shape only

                return gen()

        live = _session()
        asyncio.run(live._recv_loop(FakeSession()))

        self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()
