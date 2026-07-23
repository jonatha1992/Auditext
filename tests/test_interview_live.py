"""Tests for Gemini key pool and interview Live helpers (no network)."""

from __future__ import annotations

import os
import unittest
from unittest import mock

import numpy as np

from infrastructure.services.gemini_keys import KeyPool
from infrastructure.services.interview_live import (
    InterviewAssist,
    _MAX_CONTEXT_CHARS,
    _truncate_context,
    float32_to_pcm16,
    parse_assist_text,
)


class TestKeyPool(unittest.TestCase):
    def test_collect_and_rotate(self):
        env = {
            "GEMINI_API_KEYS": "",
            "GEMINI_API_KEY": "key-a",
            "GEMINI_API_KEY_2": "key-b",
            "GEMINI_API_KEY_3": "key-c",
            "GEMINI_API_KEY1": "",
            "GEMINI_API_KEY2": "",
            "GEMINI_API_KEY3": "",
            "GEMINI_API_KEY4": "",
            "GEMINI_API_KEY5": "",
            "GEMINI_API_KEY_4": "",
            "GEMINI_API_KEY_5": "",
        }
        with mock.patch.dict(os.environ, env, clear=False):
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
        env = {
            "GEMINI_API_KEYS": "aaa, bbb",
            "GEMINI_API_KEY": "",
            "GEMINI_API_KEY_2": "",
            "GEMINI_API_KEY_3": "",
            "GEMINI_API_KEY1": "",
            "GEMINI_API_KEY2": "",
            "GEMINI_API_KEY3": "",
            "GEMINI_API_KEY4": "",
            "GEMINI_API_KEY5": "",
            "GEMINI_API_KEY_4": "",
            "GEMINI_API_KEY_5": "",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            pool = KeyPool()
            pool.reload()
            self.assertEqual(pool.count(), 2)

    def test_key1_variant(self):
        env = {
            "GEMINI_API_KEYS": "",
            "GEMINI_API_KEY": "",
            "GEMINI_API_KEY_2": "",
            "GEMINI_API_KEY_3": "",
            "GEMINI_API_KEY1": "solo1",
            "GEMINI_API_KEY2": "solo2",
            "GEMINI_API_KEY3": "",
            "GEMINI_API_KEY4": "",
            "GEMINI_API_KEY5": "",
            "GEMINI_API_KEY_4": "",
            "GEMINI_API_KEY_5": "",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            pool = KeyPool()
            pool.reload()
            self.assertEqual(pool.count(), 2)
            self.assertEqual(pool.current(), "solo1")

    def test_is_quota_error(self):
        pool = KeyPool()
        self.assertTrue(pool.is_quota_error(Exception("429 resource_exhausted")))
        self.assertFalse(pool.is_quota_error(Exception("network timeout")))


class TestInterviewHelpers(unittest.TestCase):
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
