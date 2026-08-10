"""Adaptive interview simulation behavior without network access."""

from __future__ import annotations

import unittest

from infrastructure.services.interview_simulation import (
    InterviewSimulationSession,
    SimulationOutputError,
    parse_simulation_turn,
)


class InterviewSimulationParsingTests(unittest.TestCase):
    def test_parses_question_and_feedback_contract(self):
        turn = parse_simulation_turn(
            '{"action":"follow_up","question":"What did you learn?",'
            '"feedback":"Clear example.","strengths":["specific"],'
            '"improvements":["quantify the result"],'
            '"example_answer":"I reduced processing time by 30 percent."}'
        )

        self.assertEqual(turn.action, "follow_up")
        self.assertEqual(turn.question, "What did you learn?")
        self.assertEqual(turn.strengths, ("specific",))
        self.assertEqual(
            turn.example_answer,
            "I reduced processing time by 30 percent.",
        )

    def test_rejects_invalid_action(self):
        with self.assertRaises(SimulationOutputError):
            parse_simulation_turn(
                '{"action":"continue_forever","question":"Next?",'
                '"feedback":"","strengths":[],"improvements":[]}'
            )


class InterviewSimulationSessionTests(unittest.TestCase):
    def test_mastered_question_advances_without_faking_an_answer(self):
        outputs = iter([
            '{"action":"next_topic","question":"¿Qué es homeostasis?",'
            '"feedback":"","strengths":[],"improvements":[]}',
            '{"action":"next_topic","question":"¿Qué es equifinalidad?",'
            '"feedback":"","strengths":[],"improvements":[]}',
        ])
        session = InterviewSimulationSession(
            context="TGS", generate=lambda _prompt: next(outputs),
            simulation_type="academic",
        )
        session.start()

        turn = session.skip_mastered_question()

        self.assertEqual(turn.question, "¿Qué es equifinalidad?")
        self.assertEqual(session.question_count, 2)
        self.assertFalse(any(role == "CANDIDATE" for role, _ in session._history))

    def test_academic_unknown_answer_requests_teaching_feedback(self):
        prompts = []
        outputs = iter([
            '{"action":"next_topic","question":"¿Qué es equifinalidad?",'
            '"feedback":"","strengths":[],"improvements":[],"example_answer":""}',
            '{"action":"follow_up","question":"¿Podés decirlo con tus palabras?",'
            '"feedback":"Es la posibilidad de alcanzar un mismo resultado desde condiciones distintas.",'
            '"strengths":[],"improvements":["Distinguir estado inicial y resultado"],'
            '"example_answer":"Un sistema equifinal llega al mismo resultado desde estados iniciales diferentes."}',
        ])

        def generate(prompt):
            prompts.append(prompt)
            return next(outputs)

        session = InterviewSimulationSession(
            context="Teoría general de sistemas",
            generate=generate,
            simulation_type="academic",
        )
        session.start()
        session.submit_answer(
            "No lo sé. Explicame el concepto y mostrame un ejemplo."
        )

        self.assertIn("teach the concept", prompts[-1].lower())
        self.assertIn("easier follow-up", prompts[-1].lower())

    def test_hint_does_not_advance_the_session_or_reveal_the_answer(self):
        outputs = iter([
            '{"action":"next_topic","question":"What is a class?","feedback":"","strengths":[],"improvements":[]}',
            '{"hint":"Think about the template used to create objects."}',
        ])
        session = InterviewSimulationSession(
            context="UML", generate=lambda _prompt: next(outputs), simulation_type="academic"
        )
        session.start()

        hint = session.hint()

        self.assertIn("template", hint)
        self.assertEqual(session.question_count, 1)
        self.assertEqual(session.state, "waiting_answer")

    def test_duplicate_follow_up_is_regenerated(self):
        outputs = iter([
            '{"action":"next_topic","question":"What is a class?","feedback":"","strengths":[],"improvements":[]}',
            '{"action":"follow_up","question":"What is a class?","feedback":"Good.","strengths":[],"improvements":[]}',
            '{"action":"next_topic","question":"How do classes and objects differ?","feedback":"Good.","strengths":[],"improvements":[]}',
        ])
        session = InterviewSimulationSession(
            context="UML", generate=lambda _prompt: next(outputs), simulation_type="academic"
        )
        session.start()

        turn = session.submit_answer("A class is a template.")

        self.assertIn("differ", turn.question)
        self.assertEqual(session.question_count, 2)

    def test_academic_practice_prompts_as_an_oral_exam_professor(self):
        prompts: list[str] = []

        def generate(prompt: str) -> str:
            prompts.append(prompt)
            return (
                '{"action":"next_topic","question":"¿Qué diferencia hay entre clase y objeto?",'
                '"feedback":"","strengths":[],"improvements":[]}'
            )

        session = InterviewSimulationSession(
            context="UML: clases, objetos y diagramas",
            generate=generate,
            simulation_type="academic",
        )

        session.start()

        self.assertIn("oral exam professor", prompts[0])
        self.assertIn("UML: clases, objetos y diagramas", prompts[0])
        self.assertIn("correctness", prompts[0])

    def test_starts_proactively_with_a_question(self):
        prompts: list[str] = []

        def generate(prompt: str) -> str:
            prompts.append(prompt)
            return (
                '{"action":"next_topic","question":"Tell me about yourself.",'
                '"feedback":"","strengths":[],"improvements":[]}'
            )

        session = InterviewSimulationSession(
            context="Backend developer role", generate=generate, max_questions=4
        )

        turn = session.start()

        self.assertEqual(turn.question, "Tell me about yourself.")
        self.assertEqual(session.state, "waiting_answer")
        self.assertIn("Backend developer role", prompts[0])

    def test_reacts_to_answer_with_a_follow_up(self):
        outputs = iter(
            [
                '{"action":"next_topic","question":"Describe a challenge.",'
                '"feedback":"","strengths":[],"improvements":[]}',
                '{"action":"follow_up","question":"What was the measurable result?",'
                '"feedback":"Good structure.","strengths":["clear context"],'
                '"improvements":["add metrics"]}',
            ]
        )
        session = InterviewSimulationSession(
            context="Engineering", generate=lambda _prompt: next(outputs)
        )
        session.start()

        turn = session.submit_answer("I reduced the processing time.")

        self.assertEqual(turn.action, "follow_up")
        self.assertIn("measurable result", turn.question)
        self.assertEqual(session.question_count, 2)
        self.assertIn("Recomendación:", session.report())

    def test_finishes_at_question_limit_even_if_model_continues(self):
        outputs = iter(
            [
                '{"action":"next_topic","question":"Question one?",'
                '"feedback":"","strengths":[],"improvements":[]}',
                '{"action":"next_topic","question":"Question two?",'
                '"feedback":"Good.","strengths":["clear"],'
                '"improvements":["detail"]}',
            ]
        )
        session = InterviewSimulationSession(
            context="Role", generate=lambda _prompt: next(outputs), max_questions=1
        )
        session.start()

        turn = session.submit_answer("My answer")

        self.assertEqual(turn.action, "finish")
        self.assertEqual(session.state, "completed")
        self.assertFalse(turn.question)

    def test_manual_finish_builds_bounded_feedback_report(self):
        session = InterviewSimulationSession(
            context="Role",
            generate=lambda _prompt: (
                '{"action":"next_topic","question":"Why this role?",'
                '"feedback":"","strengths":[],"improvements":[]}'
            ),
        )
        session.start()

        report = session.finish()

        self.assertEqual(session.state, "completed")
        self.assertIn("Simulacro finalizado", report)

    def test_reading_report_does_not_finish_an_active_session(self):
        session = InterviewSimulationSession(
            context="Role",
            generate=lambda _prompt: (
                '{"action":"next_topic","question":"Why this role?",'
                '"feedback":"","strengths":[],"improvements":[]}'
            ),
        )
        session.start()

        session.report()

        self.assertEqual(session.state, "waiting_answer")


if __name__ == "__main__":
    unittest.main()
