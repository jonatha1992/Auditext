"""Answer-language selection reaches the coach prompt (no network)."""

from __future__ import annotations

import unittest
from unittest import mock

from infrastructure.services import interview_live
from infrastructure.services.interview_live import (
    ANSWER_LANGS,
    DEFAULT_ANSWER_LANG,
    coach_assist,
)


class _Recorder:
    """Stands in for genai, capturing the prompt instead of calling the API."""

    def __init__(self):
        self.prompt = ""

    def __call__(self, *, model, contents, config):
        self.prompt = contents
        raise RuntimeError("stop after capturing the prompt")


def _prompt_for(lang: str | None) -> str:
    """Capture the prompt coach_assist would send, without touching the network.

    _get_client caches clients per API key, so patching it (rather than the
    genai module) is what keeps one test's recorder out of the next one.
    """
    recorder = _Recorder()
    client = mock.MagicMock()
    client.models.generate_content.side_effect = recorder

    kwargs = {} if lang is None else {"answer_lang": lang}
    with mock.patch.object(interview_live, "_get_client", return_value=client):
        coach_assist("temario", "explique la herencia", api_key="k", **kwargs)
    return recorder.prompt


class AnswerLangTests(unittest.TestCase):
    def test_catalog_covers_the_three_choices(self):
        self.assertEqual(set(ANSWER_LANGS), {"es", "en", "auto"})

    def test_default_is_english_for_backwards_compatibility(self):
        self.assertEqual(DEFAULT_ANSWER_LANG, "en")

    def test_spanish_instruction_reaches_the_prompt(self):
        self.assertIn(ANSWER_LANGS["es"], _prompt_for("es"))

    def test_english_instruction_reaches_the_prompt(self):
        self.assertIn(ANSWER_LANGS["en"], _prompt_for("en"))

    def test_auto_instruction_reaches_the_prompt(self):
        self.assertIn(ANSWER_LANGS["auto"], _prompt_for("auto"))

    def test_unknown_language_falls_back_to_the_default(self):
        self.assertIn(ANSWER_LANGS[DEFAULT_ANSWER_LANG], _prompt_for("klingon"))

    def test_omitting_the_argument_keeps_previous_behaviour(self):
        self.assertIn(ANSWER_LANGS["en"], _prompt_for(None))

    def test_prompt_no_longer_hardcodes_english_answers(self):
        self.assertNotIn("respuesta recomendada corta en inglés", _prompt_for("es"))


if __name__ == "__main__":
    unittest.main()
