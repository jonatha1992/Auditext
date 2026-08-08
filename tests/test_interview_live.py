"""Tests for Gemini key pool and interview Live helpers (no network)."""

from __future__ import annotations

import os
import unittest
from unittest import mock

import numpy as np

from infrastructure.services import interview_live
from infrastructure.services.gemini_keys import KeyPool
from infrastructure.services.interview_live import (
    InterviewAssist,
    _MAX_CONTEXT_CHARS,
    _session_status,
    _looks_like_unsupported_definition,
    _coach_config,
    _has_required_answers,
    _truncate_context,
    extract_question_candidate,
    interpret_question_candidate,
    float32_to_pcm16,
    merge_transcript_delta,
    parse_assist_text,
)


class TestKeyPool(unittest.TestCase):
    def test_collect_and_rotate(self):
        # clear=True, no una lista de vars en blanco: el colector canonico lee
        # hasta la key 20, asi que enumerar las que se limpian dejaba entrar las
        # del .env real del desarrollador y el conteo dependia de su maquina.
        env = {
            "GEMINI_API_KEY": "key-a",
            "GEMINI_API_KEY_2": "key-b",
            "GEMINI_API_KEY_3": "key-c",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            pool = KeyPool()
            pool.reload()
            self.assertEqual(pool.count(), 3)
            self.assertEqual(pool.current(), "key-a")
            self.assertTrue(pool.current_label().startswith("API "))
            nxt = pool.mark_exhausted("key-a")
            self.assertEqual(nxt, "key-b")
            pool.mark_exhausted("key-b")
            pool.mark_exhausted("key-c")
            self.assertIsNone(pool.current())

    def test_csv_keys(self):
        env = {"GEMINI_API_KEYS": "aaa, bbb"}
        with mock.patch.dict(os.environ, env, clear=True):
            pool = KeyPool()
            pool.reload()
            self.assertEqual(pool.count(), 2)

    def test_key1_variant(self):
        env = {"GEMINI_API_KEY1": "solo1", "GEMINI_API_KEY2": "solo2"}
        with mock.patch.dict(os.environ, env, clear=True):
            pool = KeyPool()
            pool.reload()
            self.assertEqual(pool.count(), 2)
            self.assertEqual(pool.current(), "solo1")

    def test_is_quota_error(self):
        pool = KeyPool()
        self.assertTrue(pool.is_quota_error(Exception("429 resource_exhausted")))
        self.assertFalse(pool.is_quota_error(Exception("network timeout")))


class TestInterviewHelpers(unittest.TestCase):
    def test_interprets_fragmented_stt_question_with_common_errors(self):
        result = interpret_question_candidate(
            "Vale. En un día rama de clases eh representa una asociación con multiplicidad."
        )
        self.assertEqual(
            result.question,
            "¿En un diagrama de clases qué representa una asociación con multiplicidad?",
        )
        self.assertGreaterEqual(result.confidence, 0.85)

    def test_does_not_turn_an_explanation_into_a_question(self):
        result = interpret_question_candidate(
            "En un diagrama de clases una asociación representa una relación."
        )
        self.assertEqual(result.question, "")

    def test_corrects_fork_when_join_makes_the_intent_clear(self):
        result = interpret_question_candidate(
            "En un diagrama de actividades para qué sirven los nodos de for o join"
        )
        self.assertIn("fork o join", result.question.lower())

    def test_expands_abbreviated_difference_prompt(self):
        result = interpret_question_candidate(
            "Ahora, diferencia entre nodo de decisión y nodo fork."
        )
        self.assertEqual(
            result.question,
            "¿Cuál es la diferencia entre nodo de decisión y nodo fork?",
        )

    def test_understands_common_elliptical_academic_prompts(self):
        cases = {
            "Ahora, función del nodo fork.": "¿Cuál es la función del nodo fork?",
            "Elementos de un diagrama de clases.": "¿Cuáles son los elementos de un diagrama de clases?",
            "Ventajas del modelo M M 1.": "¿Cuáles son las ventajas del modelo M M 1?",
            "Un ejemplo de nodo de decisión.": "¿Podés dar un ejemplo de nodo de decisión?",
            "Relación entre caso de uso y secuencia.": "¿Qué relación hay entre caso de uso y secuencia?",
        }
        for spoken, expected in cases.items():
            with self.subTest(spoken=spoken):
                self.assertEqual(
                    interpret_question_candidate(spoken).question,
                    expected,
                )

    def test_does_not_rewrite_explanatory_sentences_as_elliptical_prompts(self):
        statements = (
            "La función del nodo fork permite abrir caminos paralelos.",
            "Las ventajas del modelo quedaron explicadas antes.",
            "La relación entre los diagramas fue analizada en clase.",
        )
        for statement in statements:
            with self.subTest(statement=statement):
                self.assertEqual(
                    interpret_question_candidate(statement).question,
                    "",
                )

    def test_reconstructs_context_prompt_and_constraint_across_sentences(self):
        result = interpret_question_candidate(
            "En un cas del uso de comprar pasaje. actores y relaciones incluirías. "
            "considerando restricciones como menores que deben tener un adulto responsable."
        )
        self.assertEqual(
            result.question,
            "¿En un caso de uso de comprar pasaje, qué actores y relaciones incluirías, "
            "considerando restricciones como menores que deben tener un adulto responsable?",
        )

    def test_classifies_imperative_comparison_variants(self):
        prompts = (
            "Diferenciame el nodo de decisión del nodo fork.",
            "Diferenciá el nodo de decisión del nodo fork.",
            "Diferencie el nodo de decisión del nodo fork.",
            "Comparame el nodo de decisión con el nodo fork.",
        )
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                result = interpret_question_candidate(prompt)
                self.assertTrue(result.question)
                self.assertEqual(result.intent, "compare")
                self.assertGreaterEqual(result.confidence, 0.85)

    def test_reconstructs_nominal_comparison_and_versus_prompts(self):
        cases = {
            "Comparación entre nodo de decisión y nodo fork.":
                "¿Cómo se comparan nodo de decisión y nodo fork?",
            "Nodo de decisión versus nodo fork.":
                "¿Cuál es la diferencia entre nodo de decisión y nodo fork?",
            "Comparación nodo de decisión y nodo fork.":
                "¿Cómo se comparan nodo de decisión y nodo fork?",
        }
        for prompt, expected in cases.items():
            with self.subTest(prompt=prompt):
                result = interpret_question_candidate(prompt)
                self.assertEqual(result.question, expected)
                self.assertEqual(result.intent, "compare")
                self.assertGreaterEqual(result.confidence, 0.82)

    def test_rejects_comparison_words_inside_non_request_chatter(self):
        chatter = (
            "La comparación entre ambos nodos quedó clara.",
            "Usamos nodo de decisión versus nodo fork ayer.",
            "Estuvimos diferenciando los dos conceptos en clase.",
        )
        for text in chatter:
            with self.subTest(text=text):
                self.assertEqual(interpret_question_candidate(text).question, "")

    def test_repairs_comparison_question_missing_verb(self):
        cases = {
            "¿qué diferencia fundamental entre clase y objeto?":
                "¿qué diferencia fundamental hay entre clase y objeto?",
            "Qué diferencia entre clase y objeto":
                "¿Qué diferencia hay entre clase y objeto?",
        }
        for spoken, expected in cases.items():
            with self.subTest(spoken=spoken):
                result = interpret_question_candidate(spoken)
                self.assertEqual(result.question, expected)
                self.assertEqual(result.intent, "compare")

    def test_oral_modes_accept_concise_content_for_both_answer_cards(self):
        short_only = InterviewAssist("Pregunta", ["Respuesta principal"])
        concise = InterviewAssist(
            "Pregunta",
            [
                " ".join(["respuesta"] * 35),
                " ".join(["detalle"] * 45),
            ],
        )
        complete = InterviewAssist(
            "Pregunta",
            [
                " ".join(["respuesta"] * 110),
                " ".join(["ampliación"] * 150),
            ],
        )
        # Passed the old 70/100 floor but still reads as a two-line answer,
        # which is the complaint the floor was raised to catch.
        old_floor = InterviewAssist(
            "Pregunta",
            [
                " ".join(["respuesta"] * 70),
                " ".join(["ampliación"] * 100),
            ],
        )

        self.assertFalse(_has_required_answers(short_only, "resolver"))
        self.assertFalse(_has_required_answers(short_only, "examen_oral"))
        self.assertTrue(_has_required_answers(concise, "resolver"))
        self.assertTrue(_has_required_answers(concise, "examen_oral"))
        self.assertTrue(_has_required_answers(old_floor, "resolver"))
        self.assertTrue(_has_required_answers(complete, "resolver"))
        self.assertTrue(_has_required_answers(complete, "examen_oral"))
        self.assertTrue(_has_required_answers(short_only, "entrevista"))

    def test_oral_exam_uses_low_variance_generation(self):
        config = _coach_config("gemini-2.5-flash", "examen_oral")
        self.assertEqual(config.temperature, 0.2)
        self.assertEqual(config.max_output_tokens, 700)

    def test_resolver_has_room_for_a_reasoned_answer(self):
        config = _coach_config("gemini-2.5-flash", "resolver")
        self.assertEqual(config.temperature, 0.2)
        self.assertEqual(config.max_output_tokens, 700)

    def test_detects_real_ads_iii_exam_questions(self):
        questions = [
            "¿Qué tipo de vista de UML representa el diagrama de clases?",
            "¿Qué tipo de vista de UML representa el diagrama de actividad?",
            "¿Qué diagramas de UML corresponden a una vista dinámica?",
            "¿Qué diagramas representan una vista estática?",
            "¿Cuáles son los elementos de un diagrama de secuencia?",
            "¿Cuáles son los elementos de un diagrama de actividad?",
            "¿Por qué decimos que un diagrama de secuencia tiene que estar balanceado?",
        ]
        for question in questions:
            with self.subTest(question=question):
                self.assertEqual(extract_question_candidate(question), question)

    def test_accepts_short_contextual_follow_up_questions(self):
        follow_ups = ["¿Y la G?", "¿Por qué?", "¿Cómo?"]
        for question in follow_ups:
            with self.subTest(question=question):
                self.assertEqual(extract_question_candidate(question), question)

    def test_extracts_latest_question_from_classroom_chatter(self):
        transcript = (
            "Perfecto, esa respuesta está muy bien. Otra pregunta. "
            "¿Cuáles son los elementos de un diagrama de secuencia? "
            "Dale, te escucho."
        )
        self.assertEqual(
            extract_question_candidate(transcript),
            "¿Cuáles son los elementos de un diagrama de secuencia?",
        )

    def test_keeps_main_question_with_adjacent_contextual_follow_up(self):
        utterance = (
            "Exacto. Siguiente. ¿Qué vista representa el diagrama de actividad? "
            "¿Y qué modela?"
        )

        self.assertEqual(
            extract_question_candidate(utterance),
            "¿Qué vista representa el diagrama de actividad? ¿Y qué modela?",
        )

    def test_keeps_two_adjacent_questions_when_second_is_vague(self):
        utterance = (
            "¿Cuándo conviene usar un diagrama de actividad en vez de uno de secuencia? "
            "¿Qué situación te parece?"
        )

        self.assertEqual(extract_question_candidate(utterance), utterance)

    def test_rejects_confirmation_tags_as_question_signals(self):
        statements = [
            "¿no? Simulada. Exactamente, sí.",
            "¿no? Que que que si van iban tomando.",
            "¿no? Pero después en un contexto real se ven afectados. Sí. Mhm.",
            "pero pero una cosa que estaría para mí interesante es poder hacer algún tener algún pequeño laboratorio como para poder este realizar trabajo que se puedan palpar más que nada, ¿no?",
            "Eh Pero bueno, eso es lo que lo que me dijo me dijeron a mí, ¿no?",
            "Bueno, chicos, eh fue un gusto este haberlos tenido en el grupo trabajando la verdad que muchas gracias por todo, ¿no?",
            "No, pero fíjate que acá cuando a mí me dieron esta materia al principio de marzo, empezamos en abril, ¿no?",
        ]
        for statement in statements:
            with self.subTest(statement=statement):
                self.assertEqual(extract_question_candidate(statement), "")

    def test_accepts_commands_after_discourse_markers(self):
        commands = [
            "Bueno, cuéntame un poquito cómo lo pensaron, qué lo cómo los llevaron a cabo. Desarrollaron el tema.",
            "Bien. Bueno, cuénteme cómo lo vieron, cómo se sintieron cuando realizaron esto, los protocolos que que estuvieron utiliz…",
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(extract_question_candidate(command))

    def test_accepts_infinitive_exam_instruction_with_attached_pronoun(self):
        instruction = (
            "Contarme las diferencias entre el diagrama de casos de uso "
            "y el diagrama de clases."
        )

        self.assertEqual(extract_question_candidate(instruction), instruction)

    def test_accepts_embedded_question_without_punctuation(self):
        question = (
            "En un diagrama de actividades para que sirven los nodos de decisión "
            "y los de for o join"
        )

        self.assertEqual(extract_question_candidate(question), question)

    def test_accepts_hypothetical_professor_prompt_variants(self):
        prompts = [
            "En UML qué representa un diagrama de clases",
            "Dentro del modelo cómo funciona el nodo join",
            "Respecto del diagrama de secuencia por qué importa el orden temporal",
            "Me podés explicar la diferencia entre fork y join",
            "Podrías decirme cuándo conviene usar un diagrama de actividad",
            "Quiero saber cuál es la vista estática",
            "Necesito una comparación entre casos de uso y clases",
            "La diferencia entre actividad y secuencia cuál sería",
            "A ver si podés contarme qué modela un diagrama de estados",
            "Hablemos del árbol mínimo: decime qué condición debe cumplir",
        ]

        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertTrue(extract_question_candidate(prompt))

    def test_hypothetical_detection_still_rejects_explanatory_chatter(self):
        chatter = [
            "El profesor explicó cómo funciona el nodo join.",
            "Ya sabemos cuál es la vista estática.",
            "Después veremos por qué importa el orden temporal.",
            "Estuvimos comparando casos de uso y clases.",
        ]

        for text in chatter:
            with self.subTest(text=text):
                self.assertEqual(extract_question_candidate(text), "")

    def test_question_detection_real_session_regression_matrix(self):
        accepted = [
            "¿qué limitaciones detectaron en la simulación y qué mejoras propondrían para un uso real del sistema?",
            "¿cómo explican el rol del Jason?",
            "¿Y por qué es importante el formato en el envío de datos?",
            "¿Y qué ventaja tiene ese intervalo?",
            "Defina bucle y vértice aislado.",
            "En el simulador del trabajo, ¿cuál es la diferencia",
        ]
        ignored = [
            "Muy bien.",
            "Claro.",
            "Sí.",
            "Perfecto, gracias. Está bien, en serio.",
            "Ahí le muestro, profe.",
            "Por supuesto, al exponer su proyecto explican el rol del Jason y por qué es importante el formato en el envío de datos.",
            "Desarrollaron el tema.",
        ]
        for text in accepted:
            with self.subTest(expected="accepted", text=text):
                self.assertTrue(extract_question_candidate(text))
        for text in ignored:
            with self.subTest(expected="ignored", text=text):
                self.assertEqual(extract_question_candidate(text), "")

    def test_extracts_indirect_exam_instruction_and_ignores_chinese_hallucination(self):
        transcript = (
            "A ver, detengámonos un segundo. Me dijiste que hiciste un modelo de una cola tipo MM1. "
            "Y que comparaste con la ley de Little. Bien. Ahora necesito que definas con claridad "
            "qué es verificación y qué es validación de un modelo. Respondeme como si estuvieras "
            "en la mesa. 第三，为什么选择考虑这个模型用一个MM1，而不用其他？"
        )

        self.assertEqual(
            extract_question_candidate(transcript),
            "necesito que definas con claridad qué es verificación y qué es validación de un modelo.",
        )

    def test_rejects_question_transcribed_entirely_in_chinese(self):
        self.assertEqual(
            extract_question_candidate("第三，为什么选择考虑这个模型用一个MM1，而不用其他？"),
            "",
        )

    def test_oral_response_contract_is_short_and_in_student_voice(self):
        rules = interview_live._EXAM_RESPONSE_RULES

        self.assertIn("como estudiante", rules)
        self.assertIn("1 a 3 oraciones", rules)
        self.assertIn("criterio técnico decisivo", rules)
        self.assertIn("Cada oración agrega", rules)
        self.assertIn("viñetas", rules)
        self.assertIn("sin repetir", rules)
        self.assertNotIn("Si el profesor pide más:", rules)

    def test_ignores_feedback_without_a_question(self):
        self.assertEqual(
            extract_question_candidate(
                "Perfecto, está muy bien. Si querés seguimos con otra."
            ),
            "",
        )

    def test_rejects_long_unknown_definition_outside_course_context(self):
        self.assertTrue(
            _looks_like_unsupported_definition(
                "Matemática discreta: congruencia y relaciones de equivalencia.",
                "¿Qué es una pueratología y por qué es funcionalmente completa?",
            )
        )

    def test_keeps_definition_supported_by_course_context(self):
        self.assertFalse(
            _looks_like_unsupported_definition(
                "Arquitectura web: autenticación, autorización y sesiones.",
                "¿Qué es la autenticación?",
            )
        )
    def test_streaming_transcript_preserves_word_boundaries(self):
        text = ""
        for chunk in ("no se esta mero", "s es", "tán en la misma clase"):
            text = merge_transcript_delta(text, chunk)
        self.assertEqual(text, "no se esta meros están en la misma clase")

    def test_streaming_transcript_preserves_leading_word_space(self):
        text = merge_transcript_delta("misma", " clase")
        self.assertEqual(text, "misma clase")

    def test_resolver_status_names_module_and_fallbacks(self):
        from infrastructure.services import groq_provider

        with (
            mock.patch.object(groq_provider, "is_configured", return_value=True),
            mock.patch.object(groq_provider.pool, "count", return_value=1),
            mock.patch.object(
                interview_live.nvidia_provider, "is_configured", return_value=True
            ),
            mock.patch.object(
                interview_live.nvidia_provider.pool, "count", return_value=2
            ),
        ):
            status = _session_status("resolver")
        self.assertIn("Resolver activo", status)
        self.assertIn("Gemini", status)
        # Ambos respaldos visibles: si Gemini se queda sin cuota el usuario tiene
        # que poder ver de antemano que hay con que seguir.
        self.assertIn("Groq (1)", status)
        self.assertIn("NVIDIA (2)", status)
        self.assertNotIn("Entrevista Live", status)

    def test_status_names_the_provider_that_actually_answered(self):
        from infrastructure.services import groq_provider

        with (
            mock.patch.object(groq_provider, "is_configured", return_value=True),
            mock.patch.object(groq_provider.pool, "count", return_value=3),
        ):
            status = _session_status("resolver", "Groq")
        self.assertIn("Groq · 3 claves", status)

    def test_pcm_convert(self):
        audio = np.array([0.0, 0.5, -1.0, 1.0], dtype=np.float32)
        pcm = float32_to_pcm16(audio)
        self.assertEqual(len(pcm), 8)
        samples = np.frombuffer(pcm, dtype=np.int16)
        self.assertEqual(samples[0], 0)
        self.assertGreater(samples[1], 0)
        self.assertEqual(samples[2], -32767)  # clip of -1.0 * 32767
        self.assertEqual(samples[3], 32767)

    def test_parse_json(self):
        assist = parse_assist_text(
            '{"pregunta_es":"¿Cuál es tu experiencia?","respuestas":["I have 5 years.","I worked on X."]}'
        )
        self.assertIsInstance(assist, InterviewAssist)
        self.assertIn("experiencia", assist.pregunta_es)
        self.assertEqual(len(assist.respuestas), 2)

    def test_parse_fenced(self):
        assist = parse_assist_text(
            'Sure:\n```json\n{"pregunta_es":"Hola","respuestas":["Hi there"]}\n```'
        )
        self.assertIsNotNone(assist)
        self.assertEqual(assist.pregunta_es, "Hola")
        self.assertEqual(assist.respuestas, ["Hi there"])

    def test_parse_empty(self):
        self.assertIsNone(parse_assist_text(""))
        self.assertIsNone(parse_assist_text("not json"))
        self.assertIsNone(
            parse_assist_text('{"pregunta_es":"","respuestas":[]}')
        )

    def test_parse_fluency_support(self):
        assist = parse_assist_text(
            '{"pregunta_es":"Contame sobre vos",'
            '"respuestas":["I am a developer.","My background is in Python."],'
            '"ideas_clave":["mencionar experiencia","dar un resultado"]}'
        )
        self.assertEqual(assist.ideas_clave, ["mencionar experiencia", "dar un resultado"])

    def test_parse_single_response(self):
        # Coach now prioritizes 1 short reply; a single respuesta must parse.
        assist = parse_assist_text(
            '{"pregunta_es":"¿Por qué querés el puesto?","respuestas":["I want to grow here."]}'
        )
        self.assertIsNotNone(assist)
        self.assertEqual(assist.respuestas, ["I want to grow here."])

    def test_parse_ignores_frase_puente(self):
        # frase_puente is no longer parsed; extra key must not break parsing
        # and the field stays at its empty default.
        assist = parse_assist_text(
            '{"pregunta_es":"Hola","respuestas":["Hi there"],'
            '"frase_puente":"That is a great question."}'
        )
        self.assertIsNotNone(assist)
        self.assertEqual(assist.frase_puente, "")

    def test_parse_prueba_oral_ideas(self):
        # prueba_oral regression guard: ideas_clave must stay useful in the
        # same single response alongside short starters.
        assist = parse_assist_text(
            '{"pregunta_es":"Explicá la fotosíntesis",'
            '"respuestas":["The main idea is..."],'
            '"ideas_clave":["clorofila","luz solar","glucosa"]}'
        )
        self.assertIsNotNone(assist)
        self.assertEqual(assist.ideas_clave, ["clorofila", "luz solar", "glucosa"])


class TestContextTruncation(unittest.TestCase):
    def test_short_context_unchanged(self):
        self.assertEqual(_truncate_context("  hola CV  "), "hola CV")

    def test_none_context(self):
        self.assertEqual(_truncate_context(None), "")

    def test_long_context_truncated(self):
        long_ctx = "x" * (_MAX_CONTEXT_CHARS + 500)
        out = _truncate_context(long_ctx)
        self.assertTrue(out.endswith("[…]"))
        self.assertLessEqual(len(out), _MAX_CONTEXT_CHARS + 5)


if __name__ == "__main__":
    unittest.main()
