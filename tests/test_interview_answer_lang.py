"""Answer-language selection reaches the coach prompt (no network)."""

from __future__ import annotations

import unittest
from unittest import mock

from infrastructure.services import interview_live
from infrastructure.services.interview_live import (
    ANSWER_LANGS,
    DEFAULT_ANSWER_LANG,
    coach_assist,
    resolve_answer_lang,
)


class _Recorder:
    """Stands in for genai, capturing the prompt instead of calling the API."""

    def __init__(self):
        self.prompt = ""

    def __call__(self, *, model, contents, config):
        self.prompt = contents
        raise RuntimeError("stop after capturing the prompt")


def _prompt_for(lang: str | None, *, mode: str = "entrevista", utterance: str = "explique la herencia") -> str:
    """Capture the prompt coach_assist would send, without touching the network.

    _get_client caches clients per API key, so patching it (rather than the
    genai module) is what keeps one test's recorder out of the next one.
    """
    recorder = _Recorder()
    client = mock.MagicMock()
    # The coach streams now, so this is the call that carries the prompt.
    client.models.generate_content_stream.side_effect = recorder

    kwargs = {} if lang is None else {"answer_lang": lang}
    with mock.patch.object(interview_live, "_get_client", return_value=client):
        coach_assist("temario", utterance, api_key="k", mode=mode, **kwargs)
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


    def test_oral_modes_force_spanish(self):
        self.assertEqual(resolve_answer_lang("examen_oral", "en"), "es")
        self.assertEqual(resolve_answer_lang("prueba_oral", "auto"), "es")
        self.assertIn(ANSWER_LANGS["es"], _prompt_for("en", mode="examen_oral"))
        self.assertEqual(resolve_answer_lang("resolver", "en"), "es")

    def test_non_oral_modes_keep_the_selected_language(self):
        self.assertEqual(resolve_answer_lang("entrevista", "en"), "en")
        self.assertEqual(resolve_answer_lang("general", "auto"), "auto")

    def test_live_transcription_is_not_told_to_translate(self):
        self.assertIn("never translate", interview_live._LIVE_SYSTEM)

    def test_one_word_question_reaches_the_coach(self):
        self.assertIn("¿Cuándo?", _prompt_for("es", mode="examen_oral", utterance="¿Cuándo?"))

    def test_resolver_prompt_asks_for_a_complete_direct_answer(self):
        prompt = _prompt_for("en", mode="resolver", utterance="¿Qué es la fotosíntesis?")
        self.assertIn("Contestá exactamente lo preguntado", prompt)
        self.assertIn("COMPLETA, directa, correcta", prompt)
        # Dos respuestas por turno: corta para contestar ya, ejemplos por si repreguntan.
        self.assertIn("R: la respuesta directa en 1 o 2 oraciones", prompt)
        self.assertIn("máximo 40 palabras", prompt)
        # La caja derecha no reformula: son ejemplos, y cortos.
        self.assertIn("NO vuelve a explicar ni reformula", prompt)
        self.assertIn("DOS ejemplos y nada más", prompt)
        self.assertIn("un caso límite", prompt)
        self.assertIn("Máximo 25 palabras por ejemplo y 60 en total", prompt)
        # Nada del prompt puede volver a pedirle que reformule R.
        self.assertNotIn("la misma respuesta desarrollada", prompt)
        self.assertNotIn("la misma respuesta explayada", prompt)
        self.assertIn("Sin introducciones", prompt)
        self.assertIn(ANSWER_LANGS["es"], prompt)
        self.assertIn("No inventes términos", prompt)
        self.assertIn("debe repetirse", prompt)

    def test_prompt_bans_notation_without_banning_maths(self):
        # Prohibir LaTeX no puede leerse como "no resuelvas la cuenta".
        prompt = _prompt_for("es", mode="examen_oral", utterance="¿Cuánto es un medio más un cuarto?")
        self.assertIn("nada de LaTeX", prompt)
        self.assertIn("NO significa evitar la matemática", prompt)
        self.assertIn("resolvela y dá el", prompt)
        self.assertIn("tres cuartos", prompt)

    def test_practice_mode_never_gets_worked_examples(self):
        # Su regla central es que el usuario formule solo: un ejemplo resuelto
        # le entregaría la respuesta hecha.
        prompt = _prompt_for("es", mode="prueba_oral", utterance="¿Qué es una clase abstracta?")
        self.assertIn("esqueleto de la respuesta en 3 pasos", prompt)
        self.assertIn("Ningún ejemplo resuelto", prompt)
        self.assertNotIn("DOS ejemplos concretos", prompt)

    def test_prompt_forbids_mixing_languages(self):
        # Caso real: devolvió "equação" (portugués) en una respuesta en español.
        prompt = _prompt_for("es", mode="examen_oral", utterance="¿Qué es una ecuación diofántica?")
        self.assertIn("No mezcles idiomas", prompt)
        self.assertIn("equação", prompt)

    def test_oral_question_waits_through_natural_pauses(self):
        self.assertGreaterEqual(
            interview_live.InterviewLiveSession._FLUSH_IDLE_SECONDS, 2.5
        )


if __name__ == "__main__":
    unittest.main()
