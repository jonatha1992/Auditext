"""AUD-2 RED: the interview coach must survive a Gemini-wide 503 outage.

Today (see .agents/HANDOFF.md "Root causes") a saturated/timed-out Gemini
never reaches the Groq/NVIDIA fallback, `GEMINI_MODEL=gemini-flash-latest`
crowds out the only healthy model `gemini-3.6-flash`, a 503 on one model gets
retried on every key instead of being skipped for the turn, and neither
`_coach_config` nor `_stream_coach` enforce a request deadline. These tests
pin the contract the GREEN phase must satisfy. NEVER hit real Gemini/Groq/
NVIDIA: everything below is mocked.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock

from infrastructure.services import interview_live
from infrastructure.services.interview_live import CoachUnavailable, InterviewAssist

FALLBACK_ASSIST = InterviewAssist(
    pregunta_es="¿Todo bien?",
    respuestas=["Respuesta de respaldo"],
    provider="Groq",
)


def _ok_client(text: str = "R: Todo bien, gracias.\n"):
    client = mock.MagicMock()
    client.models.generate_content_stream.return_value = [SimpleNamespace(text=text)]
    return client


def _clock(*values):
    """Returns `values` in order, then repeats the last one forever."""
    remaining = list(values)

    def _tick():
        if len(remaining) > 1:
            return remaining.pop(0)
        return remaining[0]

    return _tick


class CoachModelCandidatesTests(unittest.TestCase):
    """Cause 1: GEMINI_MODEL=gemini-flash-latest must never crowd out 3.6-flash."""

    def test_always_includes_the_healthy_default_even_with_a_custom_env_model(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GEMINI_COACH_MODEL", None)
            os.environ["GEMINI_MODEL"] = "gemini-flash-latest"
            candidates = interview_live._coach_model_candidates()
        self.assertIn("gemini-3.6-flash", candidates)

    def test_dedupes_when_env_model_equals_the_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GEMINI_COACH_MODEL", None)
            os.environ["GEMINI_MODEL"] = "gemini-3.6-flash"
            candidates = interview_live._coach_model_candidates()
        self.assertEqual(candidates.count("gemini-3.6-flash"), 1)

    def test_appears_once_when_env_model_is_unset(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GEMINI_COACH_MODEL", None)
            os.environ.pop("GEMINI_MODEL", None)
            candidates = interview_live._coach_model_candidates()
        self.assertEqual(candidates.count("gemini-3.6-flash"), 1)

    def test_override_env_wins_and_stays_first(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ["GEMINI_COACH_MODEL"] = "custom-override-model"
            candidates = interview_live._coach_model_candidates()
        self.assertEqual(candidates[0], "custom-override-model")

    def test_preserves_the_documented_order(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GEMINI_COACH_MODEL", None)
            os.environ["GEMINI_MODEL"] = "custom-model-x"
            candidates = interview_live._coach_model_candidates()
        self.assertEqual(
            candidates,
            [
                "gemini-3.1-flash-lite",
                "gemini-3.5-flash-lite",
                "gemini-flash-lite-latest",
                "custom-model-x",
                "gemini-3.6-flash",
                "gemini-2.0-flash-lite",
            ],
        )


class CoachOutageFallbackTests(unittest.TestCase):
    """Cause 2: a 503/timeout must reach Groq/NVIDIA, not just quota errors."""

    def setUp(self):
        interview_live._broken_models.clear()
        self.addCleanup(interview_live._broken_models.clear)

    def test_503_on_every_gemini_attempt_falls_back_to_configured_provider(self):
        client = mock.MagicMock()
        client.models.generate_content_stream.side_effect = RuntimeError(
            "503 UNAVAILABLE. The model is overloaded"
        )
        with mock.patch.object(interview_live, "COACH_MODELS", ["model-a", "model-b"]), \
             mock.patch.object(interview_live, "_get_client", return_value=client), \
             mock.patch.object(
                 interview_live.gemini_keys.pool, "available", return_value=["k1"]
             ), \
             mock.patch.object(interview_live, "has_fallback_provider", return_value=True), \
             mock.patch.object(
                 interview_live, "_fallback_assist", return_value=FALLBACK_ASSIST
             ) as fallback:
            result = interview_live.coach_assist("ctx", "¿Qué es UML?", api_key="k1")

        fallback.assert_called_once()
        self.assertIs(result, FALLBACK_ASSIST)

    def test_timeout_on_every_gemini_attempt_falls_back_to_configured_provider(self):
        client = mock.MagicMock()
        client.models.generate_content_stream.side_effect = RuntimeError(
            "The read operation timed out"
        )
        with mock.patch.object(interview_live, "COACH_MODELS", ["model-a", "model-b"]), \
             mock.patch.object(interview_live, "_get_client", return_value=client), \
             mock.patch.object(
                 interview_live.gemini_keys.pool, "available", return_value=["k1"]
             ), \
             mock.patch.object(interview_live, "has_fallback_provider", return_value=True), \
             mock.patch.object(
                 interview_live, "_fallback_assist", return_value=FALLBACK_ASSIST
             ) as fallback:
            result = interview_live.coach_assist("ctx", "¿Qué es UML?", api_key="k1")

        fallback.assert_called_once()
        self.assertIs(result, FALLBACK_ASSIST)

    def test_without_a_fallback_configured_still_raises_coach_unavailable(self):
        client = mock.MagicMock()
        client.models.generate_content_stream.side_effect = RuntimeError(
            "503 UNAVAILABLE. The model is overloaded"
        )
        with mock.patch.object(interview_live, "COACH_MODELS", ["model-a"]), \
             mock.patch.object(interview_live, "_get_client", return_value=client), \
             mock.patch.object(
                 interview_live.gemini_keys.pool, "available", return_value=["k1"]
             ), \
             mock.patch.object(interview_live, "has_fallback_provider", return_value=False):
            with self.assertRaises(CoachUnavailable):
                interview_live.coach_assist("ctx", "¿Qué es UML?", api_key="k1")

    def test_quota_exhaustion_still_falls_back(self):
        # The pre-existing path (root cause 2 only added the 503/timeout ones).
        client = mock.MagicMock()
        client.models.generate_content_stream.side_effect = RuntimeError(
            "429 RESOURCE_EXHAUSTED quota"
        )
        with mock.patch.object(interview_live, "COACH_MODELS", ["model-a"]), \
             mock.patch.object(interview_live, "_get_client", return_value=client), \
             mock.patch.object(
                 interview_live.gemini_keys.pool, "available", return_value=["k1"]
             ), \
             mock.patch.object(interview_live.gemini_keys.pool, "mark_exhausted"), \
             mock.patch.object(interview_live, "has_fallback_provider", return_value=True), \
             mock.patch.object(
                 interview_live, "_fallback_assist", return_value=FALLBACK_ASSIST
             ) as fallback:
            result = interview_live.coach_assist("ctx", "¿Qué es UML?", api_key="k1")

        fallback.assert_called_once()
        self.assertIs(result, FALLBACK_ASSIST)


class ModelWideOutageSkipTests(unittest.TestCase):
    """Cause 3: a 503 is model-wide, not per-key, and stays transient."""

    def setUp(self):
        interview_live._broken_models.clear()
        self.addCleanup(interview_live._broken_models.clear)

    def test_saturated_model_is_tried_once_per_turn_not_once_per_key(self):
        calls = {"model-a": 0, "model-b": 0}

        def stream(*, model, contents, config):
            calls[model] += 1
            if model == "model-a":
                raise RuntimeError("503 UNAVAILABLE. The model is overloaded")
            # model-b: quota on key 1 (exhausts it), answers on key 2.
            if calls[model] == 1:
                raise RuntimeError("429 RESOURCE_EXHAUSTED quota")
            return iter([SimpleNamespace(text="R: Todo bien, gracias.\n")])

        client = mock.MagicMock()
        client.models.generate_content_stream.side_effect = stream

        with mock.patch.object(interview_live, "COACH_MODELS", ["model-a", "model-b"]), \
             mock.patch.object(interview_live, "_get_client", return_value=client), \
             mock.patch.object(
                 interview_live.gemini_keys.pool, "available", return_value=["k1", "k2"]
             ), \
             mock.patch.object(interview_live.gemini_keys.pool, "mark_exhausted"):
            result = interview_live.coach_assist("ctx", "¿Qué es UML?", api_key="k1")

        self.assertIsNotNone(result)
        self.assertEqual(calls["model-a"], 1)
        self.assertNotIn("model-a", interview_live._broken_models)

    def test_saturated_model_is_retried_on_the_next_turn(self):
        client = mock.MagicMock()
        client.models.generate_content_stream.side_effect = RuntimeError(
            "503 UNAVAILABLE. The model is overloaded"
        )
        with mock.patch.object(interview_live, "COACH_MODELS", ["model-a"]), \
             mock.patch.object(interview_live, "_get_client", return_value=client), \
             mock.patch.object(
                 interview_live.gemini_keys.pool, "available", return_value=["k1"]
             ), \
             mock.patch.object(interview_live, "has_fallback_provider", return_value=False):
            with self.assertRaises(CoachUnavailable):
                interview_live.coach_assist("ctx", "primera pregunta", api_key="k1")
            first_turn_calls = client.models.generate_content_stream.call_count
            self.assertNotIn("model-a", interview_live._broken_models)

            client.models.generate_content_stream.reset_mock()
            with self.assertRaises(CoachUnavailable):
                interview_live.coach_assist("ctx", "segunda pregunta", api_key="k1")

        self.assertGreaterEqual(first_turn_calls, 1)
        # A blacklisted model would make the second turn skip the Gemini call
        # entirely (empty `models` list) instead of trying it again.
        self.assertGreaterEqual(client.models.generate_content_stream.call_count, 1)


class AuthKeySkipTests(unittest.TestCase):
    """Cause 2/3 sibling: a 403 on one key must not burn the other models on it."""

    def setUp(self):
        interview_live._broken_models.clear()
        self.addCleanup(interview_live._broken_models.clear)

    def test_permission_denied_skips_the_key_for_the_rest_of_the_turn(self):
        client_k1 = mock.MagicMock()
        client_k1.models.generate_content_stream.side_effect = RuntimeError(
            "403 PERMISSION_DENIED project denied access"
        )
        client_k2 = _ok_client()

        def get_client(genai, api_key):
            return {"k1": client_k1, "k2": client_k2}[api_key]

        with mock.patch.object(interview_live, "COACH_MODELS", ["model-a", "model-b"]), \
             mock.patch.object(interview_live, "_get_client", side_effect=get_client), \
             mock.patch.object(
                 interview_live.gemini_keys.pool, "available", return_value=["k1", "k2"]
             ), \
             mock.patch.object(interview_live.gemini_keys.pool, "mark_exhausted"):
            result = interview_live.coach_assist("ctx", "¿Qué es UML?", api_key="k1")

        self.assertIsNotNone(result)
        self.assertEqual(client_k1.models.generate_content_stream.call_count, 1)


class CoachConfigTimeoutTests(unittest.TestCase):
    """Cause 3, bound (a): the socket timeout, in milliseconds."""

    def test_coach_config_accepts_timeout_seconds_in_milliseconds(self):
        config = interview_live._coach_config(
            "gemini-3.6-flash", "entrevista", timeout_seconds=12.5
        )
        self.assertEqual(config.http_options.timeout, 12500)

    def test_request_timeout_caps_large_budgets_and_respects_the_floor(self):
        for remaining, expected_ms in ((60.0, 20000), (12.5, 12500), (1.0, 10000)):
            with self.subTest(remaining=remaining):
                config = interview_live._coach_config(
                    "model-a", timeout_seconds=remaining
                )
                self.assertEqual(config.http_options.timeout, expected_ms)


class CoachBudgetTests(unittest.TestCase):
    """Cause 3, bounds (b) and (c): the per-request timeout tracks the budget."""

    def setUp(self):
        interview_live._broken_models.clear()
        self.addCleanup(interview_live._broken_models.clear)

    def _call_and_capture_timeout(self, budget_seconds: float) -> int | None:
        captured: list[int | None] = []

        def stream(*, model, contents, config):
            http_options = getattr(config, "http_options", None)
            captured.append(getattr(http_options, "timeout", None))
            return iter([SimpleNamespace(text="R: Todo bien, gracias.\n")])

        client = mock.MagicMock()
        client.models.generate_content_stream.side_effect = stream

        with mock.patch.object(interview_live, "COACH_MODELS", ["model-a"]), \
             mock.patch.object(interview_live, "_get_client", return_value=client), \
             mock.patch.object(
                 interview_live.gemini_keys.pool, "available", return_value=["k1"]
             ), \
             mock.patch.object(
                 interview_live, "COACH_GEMINI_BUDGET_SECONDS", budget_seconds
             ):
            interview_live.coach_assist("ctx", "¿Qué es UML?", api_key="k1")

        return captured[0]

    def test_timeout_passed_to_gemini_is_derived_from_the_remaining_budget(self):
        timeout_a = self._call_and_capture_timeout(20.0)
        timeout_b = self._call_and_capture_timeout(12.0)

        self.assertGreaterEqual(timeout_a, 10000)
        self.assertGreaterEqual(timeout_b, 10000)
        self.assertNotEqual(timeout_a, timeout_b)

    def test_budget_below_the_floor_skips_gemini_and_goes_to_fallback(self):
        clock = _clock(1000.0, 1000.0 + 999.0)
        calls = {"model-a": 0, "model-b": 0}

        def stream(*, model, contents, config):
            calls[model] += 1
            raise RuntimeError("503 UNAVAILABLE. The model is overloaded")

        client = mock.MagicMock()
        client.models.generate_content_stream.side_effect = stream

        with mock.patch.object(interview_live, "COACH_MODELS", ["model-a", "model-b"]), \
             mock.patch.object(interview_live, "_get_client", return_value=client), \
             mock.patch.object(
                 interview_live.gemini_keys.pool, "available", return_value=["k1"]
             ), \
             mock.patch.object(interview_live.time, "monotonic", side_effect=clock), \
             mock.patch.object(
                 interview_live, "COACH_GEMINI_BUDGET_SECONDS", 20.0
             ), \
             mock.patch.object(interview_live, "has_fallback_provider", return_value=True), \
             mock.patch.object(
                 interview_live, "_fallback_assist", return_value=FALLBACK_ASSIST
             ) as fallback:
            result = interview_live.coach_assist("ctx", "¿Qué es UML?", api_key="k1")

        self.assertEqual(calls["model-a"], 1)
        self.assertEqual(calls["model-b"], 0)
        fallback.assert_called_once()
        self.assertIs(result, FALLBACK_ASSIST)


class StreamCoachDeadlineTests(unittest.TestCase):
    """Cause 3, bound (d): a hung stream must not block the next question forever."""

    def test_raises_timeout_error_once_the_deadline_passes_between_chunks(self):
        client = mock.MagicMock()
        client.models.generate_content_stream.return_value = [
            SimpleNamespace(text="R: a "),
            SimpleNamespace(text="b b b\n"),
        ]
        with mock.patch.object(
            interview_live.time, "monotonic", side_effect=_clock(100.0, 200.0, 300.0)
        ):
            with self.assertRaises(TimeoutError):
                interview_live._stream_coach(
                    client, "model-a", "prompt", None, None, deadline=150.0
                )


class CoachReviewRegressionTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.object(interview_live, "_broken_models", set()))
        self.enterContext(mock.patch.object(interview_live, "_no_thinking_models", set()))
        self.enterContext(mock.patch.object(interview_live, "COACH_MODELS", ["model-a", "model-b"]))
        self.enterContext(mock.patch.object(interview_live, "COACH_GEMINI_BUDGET_SECONDS", 20.0))
        self.now = 100.0
        self.enterContext(mock.patch.object(interview_live.time, "monotonic", side_effect=lambda: self.now))
        self.enterContext(mock.patch.object(interview_live.gemini_keys.pool, "available", return_value=["k1", "k2"]))
        self.exhausted = self.enterContext(mock.patch.object(interview_live.gemini_keys.pool, "mark_exhausted"))
        self.client = mock.MagicMock()
        self.enterContext(mock.patch.object(interview_live, "_get_client", return_value=self.client))
        self.enterContext(mock.patch.object(interview_live, "has_fallback_provider", return_value=True))

    def test_fallback_providers_get_full_budget_after_gemini_overspends(self):
        def stalled_stream(**kwargs):
            self.now = 119.0
            yield SimpleNamespace(text="not an assist")
            self.now = 139.0
            raise TimeoutError("The read operation timed out")

        self.client.models.generate_content_stream.side_effect = stalled_stream
        groq = mock.Mock()
        groq.generate.side_effect = RuntimeError("503 UNAVAILABLE")
        nvidia = mock.Mock()
        nvidia.generate.return_value = "R: The fallback still has its full budget.\n"
        with mock.patch.object(interview_live, "_fallback_providers", return_value=[("Groq", groq), ("NVIDIA", nvidia)]), \
             mock.patch("core.errors.record"):
            result = interview_live.coach_assist("ctx", "question", api_key="k1")

        self.assertEqual(self.now, 139.0)
        self.client.models.generate_content_stream.assert_called_once()
        for provider in (groq, nvidia):
            provider.generate.assert_called_once()
            self.assertEqual(
                provider.generate.call_args.kwargs["budget_seconds"],
                interview_live._NVIDIA_TURN_BUDGET_SECONDS,
            )
        self.assertEqual(result.provider, "NVIDIA")

    def test_transient_model_and_quota_model_do_not_exhaust_any_keys(self):
        for transient in ("503 UNAVAILABLE", "The read operation timed out"):
            with self.subTest(transient=transient):
                self.exhausted.reset_mock()

                def stream(*, model, **kwargs):
                    raise RuntimeError(transient if model == "model-a" else "429 RESOURCE_EXHAUSTED quota")

                self.client.models.generate_content_stream.side_effect = stream
                with mock.patch.object(interview_live, "_fallback_assist", return_value=FALLBACK_ASSIST):
                    result = interview_live.coach_assist("ctx", "question", api_key="k1")
                self.assertIs(result, FALLBACK_ASSIST)
                self.exhausted.assert_not_called()

    def test_all_attempted_models_returning_quota_still_exhausts_keys(self):
        self.client.models.generate_content_stream.side_effect = RuntimeError("429 RESOURCE_EXHAUSTED quota")
        with mock.patch.object(interview_live, "_fallback_assist", return_value=FALLBACK_ASSIST):
            interview_live.coach_assist("ctx", "question", api_key="k1")
        self.assertEqual(self.exhausted.call_args_list, [mock.call("k1"), mock.call("k2")])

    def _interrupted_result(self, text, *, on_partial=None, mode="entrevista", fallback=FALLBACK_ASSIST):
        def stream(**kwargs):
            if text:
                yield SimpleNamespace(text=text)
            raise TimeoutError("The read operation timed out")

        self.client.models.generate_content_stream.side_effect = stream
        with mock.patch.object(interview_live, "_fallback_assist") as backup:
            if isinstance(fallback, Exception):
                backup.side_effect = fallback
            else:
                backup.return_value = fallback
            result = interview_live.coach_assist(
                "ctx", "question", api_key="k1", on_partial=on_partial, mode=mode
            )
        return result, backup

    def test_complete_emitted_answer_survives_stream_error_without_fallback_overwrite(self):
        emitted = []
        result, _ = self._interrupted_result("R: Keep the answer already on screen.\n", on_partial=emitted.append)
        self.assertTrue(emitted)
        self.assertEqual(result.respuestas, emitted[-1].respuestas)
        self.assertEqual(result.provider, "Gemini")

    def test_interrupted_incomplete_assist_survives_unavailable_fallback(self):
        emitted = []
        result, backup = self._interrupted_result(
            "R: A short answer.\n", on_partial=emitted.append, mode="resolver",
            fallback=CoachUnavailable("No fallback available"),
        )
        backup.assert_called_once()
        self.assertEqual(result.respuestas, ["A short answer."])

    def test_interrupted_unrendered_buffer_is_preserved_without_a_callback(self):
        result, backup = self._interrupted_result(
            "R: Useful text without a trailing newline",
            fallback=CoachUnavailable("No fallback available"),
        )
        backup.assert_called_once()
        self.assertEqual(result.respuestas, ["Useful text without a trailing newline"])

    def test_no_streamed_answer_still_uses_fallback(self):
        emitted = []
        result, backup = self._interrupted_result("", on_partial=emitted.append)
        self.assertEqual(emitted, [])
        backup.assert_called_once()
        self.assertIs(result, FALLBACK_ASSIST)

    def test_incomplete_emitted_answer_still_uses_successful_fallback(self):
        result, backup = self._interrupted_result(
            "R: Too short.\n", on_partial=lambda assist: None, mode="resolver"
        )
        backup.assert_called_once()
        self.assertIs(result, FALLBACK_ASSIST)

    def test_unclosed_word_boundary_preview_still_uses_fallback(self):
        emitted = []
        result, backup = self._interrupted_result("R: An unfinished answer ", on_partial=emitted.append)
        self.assertTrue(emitted)
        backup.assert_called_once()
        self.assertIs(result, FALLBACK_ASSIST)

    def test_complete_unemitted_answer_still_uses_fallback(self):
        result, backup = self._interrupted_result("R: Complete but never displayed.\n")
        backup.assert_called_once()
        self.assertIs(result, FALLBACK_ASSIST)

    def test_complete_emitted_resolver_answer_survives_stream_error(self):
        emitted = []
        answer = " ".join(["answer"] * interview_live._MIN_ANSWER_WORDS)
        expansion = " ".join(["expansion"] * interview_live._MIN_EXPANSION_WORDS)
        result, _ = self._interrupted_result(
            f"R: {answer}\nA: {expansion}\n", on_partial=emitted.append, mode="resolver"
        )
        self.assertEqual(result.respuestas, [answer, expansion])
        self.assertEqual(result.respuestas, emitted[-1].respuestas)

    def test_complete_emitted_answer_is_not_overwritten_by_a_config_retry(self):
        emitted = []

        def interrupted_stream():
            yield SimpleNamespace(text="R: Keep this completed answer.\n")
            raise RuntimeError("400 INVALID_ARGUMENT")

        self.client.models.generate_content_stream.side_effect = [
            interrupted_stream(),
            iter([SimpleNamespace(text="R: An unwanted replacement.\n")]),
        ]
        result = interview_live.coach_assist("ctx", "question", api_key="k1", on_partial=emitted.append)
        self.assertEqual(result.respuestas, ["Keep this completed answer."])
        self.client.models.generate_content_stream.assert_called_once()

    def test_deadline_interruption_preserves_the_answer_before_the_late_chunk(self):
        def stream(**kwargs):
            yield SimpleNamespace(text="R: Keep this partial answer\n")
            self.now = 121.0
            yield SimpleNamespace(text="A: Too late\n")

        self.client.models.generate_content_stream.side_effect = stream
        with mock.patch.object(interview_live, "_fallback_assist", side_effect=CoachUnavailable("No fallback available")):
            result = interview_live.coach_assist("ctx", "question", api_key="k1", mode="resolver")
        self.assertEqual(result.respuestas, ["Keep this partial answer"])
        self.client.models.generate_content_stream.assert_called_once()


if __name__ == "__main__":
    unittest.main()
