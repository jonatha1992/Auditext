"""Summary depth scales with how much was said, and long audio is not truncated."""

from __future__ import annotations

import unittest
from unittest import mock

from infrastructure.services import summarizer
from infrastructure.services.summarizer import (
    _LONG_MAX_WORDS,
    _MEDIUM_MAX_WORDS,
    _SHORT_MAX_WORDS,
    build_prompt,
    plan_for,
    split_into_chunks,
)


def words(n: int) -> str:
    return " ".join(f"p{i}" for i in range(n))


class PlanTests(unittest.TestCase):
    def test_short_note_keeps_the_original_brief_shape(self):
        plan = plan_for(words(200))
        self.assertEqual(plan.max_tokens, 700)
        self.assertFalse(plan.chunked)
        self.assertIn("3 a 6 viñetas", plan.instructions)

    def test_long_recording_gets_room_to_cover_everything(self):
        # The reported bug: a 20 533-word class summarised into ~20 lines
        # because the prompt demanded 3-6 bullets and capped output at 700.
        plan = plan_for(words(20533))
        self.assertGreaterEqual(plan.max_tokens, 6000)
        self.assertIn("NO OMITAS", plan.instructions)
        self.assertNotIn("3 a 6 viñetas", plan.instructions)

    def test_depth_never_decreases_as_the_transcript_grows(self):
        sizes = [100, _SHORT_MAX_WORDS + 1, _MEDIUM_MAX_WORDS + 1, _LONG_MAX_WORDS + 1]
        budgets = [plan_for(words(n)).max_tokens for n in sizes]
        self.assertEqual(budgets, sorted(budgets))

    def test_only_the_longest_tier_is_chunked(self):
        self.assertFalse(plan_for(words(_LONG_MAX_WORDS)).chunked)
        self.assertTrue(plan_for(words(_LONG_MAX_WORDS + 1)).chunked)

    def test_long_prompt_asks_for_the_closing_stretch(self):
        # Single-pass summaries tend to cover the opening and skim the end.
        self.assertIn("tramos finales", plan_for(words(20000)).instructions)


class ChunkTests(unittest.TestCase):
    def test_chunks_cover_every_word_in_order(self):
        text = words(1000)
        chunks = split_into_chunks(text, chunk_words=300)
        self.assertEqual(len(chunks), 4)
        self.assertEqual(" ".join(chunks), text)

    def test_empty_text_has_no_chunks(self):
        self.assertEqual(split_into_chunks(""), [])

    def test_build_prompt_carries_instructions_and_text(self):
        prompt = build_prompt("hola", "- regla propia\n")
        self.assertIn("- regla propia", prompt)
        self.assertIn("hola", prompt)


class SummarizeRoutingTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(summarizer.gemini_keys, "pool")
        self.addCleanup(patcher.stop)
        patcher.start()

    def _run(self, text: str, generate):
        with mock.patch.object(summarizer, "nvidia_provider") as nv:
            nv.is_configured.return_value = True
            nv.generate.side_effect = generate
            summary = summarizer.summarize(text)
        return summary

    def test_short_transcript_uses_a_single_call(self):
        calls = []

        def generate(prompt, **kwargs):
            calls.append(kwargs.get("max_tokens"))
            return "resumen corto"

        self.assertEqual(self._run(words(100), generate), "resumen corto")
        self.assertEqual(calls, [700])

    def test_very_long_transcript_summarises_every_chunk(self):
        prompts = []

        def generate(prompt, **kwargs):
            prompts.append(prompt)
            return f"parcial {len(prompts)}"

        text = words(_LONG_MAX_WORDS + 12000)
        summary = self._run(text, generate)

        expected_chunks = len(split_into_chunks(text))
        # Exactly one call per chunk: no extra merge pass to re-compress it.
        self.assertEqual(len(prompts), expected_chunks)
        self.assertIn("UN TRAMO", prompts[0])
        # Every chunk summary survives into the result.
        for i in range(1, expected_chunks + 1):
            self.assertIn(f"parcial {i}", summary)

    def test_chunked_summary_is_labelled_by_tramo(self):
        text = words(_LONG_MAX_WORDS + 12000)
        summary = self._run(text, lambda prompt, **kw: "contenido")
        self.assertIn("## Tramo 1 de", summary)

    def test_progress_is_reported_while_chunking(self):
        seen: list[str] = []
        with mock.patch.object(summarizer, "nvidia_provider") as nv:
            nv.is_configured.return_value = True
            nv.generate.return_value = "parcial"
            summarizer.summarize(words(_LONG_MAX_WORDS + 12000), progress_cb=seen.append)

        self.assertTrue(any("tramo 1" in s for s in seen))
        self.assertTrue(any("tramo 2" in s for s in seen))

    def test_empty_text_is_rejected(self):
        with self.assertRaises(summarizer.SummaryError):
            summarizer.summarize("   ")


if __name__ == "__main__":
    unittest.main()
