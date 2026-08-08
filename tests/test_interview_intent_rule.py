"""The classified question intent reaches the coach prompt (no network).

`_infer_question_intent` already told us what the examiner asked for — compare,
enumerate, solve — and the answer never used it: the coach kept defining terms
when the question asked to contrast them. These tests guard the wiring.
"""

from __future__ import annotations

import contextlib
import unittest
from unittest import mock

from infrastructure.services import interview_live
from infrastructure.services.interview_live import (
    _INTENT_RULES,
    coach_assist,
    intent_rule,
    interpret_question_candidate,
)


class _Recorder:
    def __init__(self):
        self.prompt = ""

    def __call__(self, *, model, contents, config):
        self.prompt = contents
        raise RuntimeError("stop after capturing the prompt")


def _prompt_for(**kwargs) -> str:
    recorder = _Recorder()
    client = mock.MagicMock()
    client.models.generate_content_stream.side_effect = recorder
    with mock.patch.object(interview_live, "_get_client", return_value=client):
        with contextlib.suppress(interview_live.CoachUnavailable):
            coach_assist(
                "temario",
                "¿Cuál es la diferencia entre include y extend?",
                api_key="k",
                mode="examen_oral",
                **kwargs,
            )
    return recorder.prompt


class IntentRuleTests(unittest.TestCase):
    def test_every_intent_the_classifier_emits_has_a_rule(self):
        # 'question' is the deliberate no-op: an unclassified question keeps
        # the previous behaviour instead of getting an invented shape.
        classified = {
            "compare",
            "define",
            "explain",
            "enumerate",
            "justify",
            "exemplify",
            "solve",
        }
        self.assertEqual(set(_INTENT_RULES), classified)

    def test_unknown_and_generic_intents_add_nothing(self):
        self.assertEqual(intent_rule(""), "")
        self.assertEqual(intent_rule("question"), "")
        self.assertEqual(intent_rule("no-existe"), "")


class IntentReachesThePromptTests(unittest.TestCase):
    def test_compare_rule_is_injected(self):
        self.assertIn(_INTENT_RULES["compare"], _prompt_for(intent="compare"))

    def test_enumerate_rule_is_injected(self):
        self.assertIn(_INTENT_RULES["enumerate"], _prompt_for(intent="enumerate"))

    def test_omitting_the_intent_keeps_previous_behaviour(self):
        prompt = _prompt_for()
        for rule in _INTENT_RULES.values():
            self.assertNotIn(rule, prompt)

    def test_generic_intent_injects_nothing(self):
        prompt = _prompt_for(intent="question")
        for rule in _INTENT_RULES.values():
            self.assertNotIn(rule, prompt)

    def test_the_classifier_output_is_a_valid_prompt_input(self):
        # End to end on the real path: what the interpreter returns must be a
        # key the prompt builder recognises, not a label nobody consumes.
        result = interpret_question_candidate(
            "¿Cuál es la diferencia entre include y extend?"
        )
        self.assertEqual(result.intent, "compare")
        self.assertIn(_INTENT_RULES[result.intent], _prompt_for(intent=result.intent))


if __name__ == "__main__":
    unittest.main()
