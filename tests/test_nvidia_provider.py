"""NVIDIA provider rotation and fallback tests (no network)."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from infrastructure.services import interview_live, nvidia_provider


class NvidiaProviderTests(unittest.TestCase):
    def test_collects_both_numbered_keys(self):
        env = {
            "NVIDIA_API_KEYS": "",
            "NVIDIA_API_KEY": "",
            "NVIDIA_API_KEY1": "nv-one",
            "NVIDIA_API_KEY2": "nv-two",
            "NVIDIA_API_KEY_1": "",
            "NVIDIA_API_KEY_2": "",
            "NVIDIA_API_KEY3": "",
            "NVIDIA_API_KEY4": "",
            "NVIDIA_API_KEY5": "",
            "NVIDIA_API_KEY_3": "",
            "NVIDIA_API_KEY_4": "",
            "NVIDIA_API_KEY_5": "",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            pool = nvidia_provider.NvidiaKeyPool()
            pool.reload()
        self.assertEqual(pool.available(), ["nv-one", "nv-two"])

    def test_generate_rotates_to_second_key(self):
        pool = nvidia_provider.NvidiaKeyPool(
            _keys=["nv-one", "nv-two"]
        )
        with (
            mock.patch.object(nvidia_provider, "pool", pool),
            mock.patch.object(
                nvidia_provider,
                "_request",
                side_effect=[
                    nvidia_provider.NvidiaError("NVIDIA HTTP 429"),
                    "respuesta",
                ],
            ) as request,
        ):
            self.assertEqual(nvidia_provider.generate("pregunta"), "respuesta")
        self.assertEqual(request.call_count, 2)
        self.assertNotIn("nv-one", pool.available())

    def test_coach_uses_nvidia_when_gemini_has_no_keys(self):
        raw = (
            '{"pregunta_es":"¿Qué es HTTP?",'
            '"respuestas":["HTTP es un protocolo de comunicación."],'
            '"ideas_clave":["protocolo"]}'
        )
        with (
            mock.patch.object(interview_live.gemini_keys.pool, "available", return_value=[]),
            mock.patch.object(interview_live.nvidia_provider.pool, "reload"),
            mock.patch.object(interview_live.nvidia_provider, "is_configured", return_value=True),
            mock.patch.object(interview_live.nvidia_provider, "generate", return_value=raw),
        ):
            assist = interview_live.coach_assist(
                "Arquitectura web", "¿Qué es HTTP?", mode="resolver"
            )
        self.assertIsNotNone(assist)
        self.assertIn("protocolo", assist.respuestas[0])

    def test_exhausted_live_key_is_not_retried_before_nvidia(self):
        raw = (
            '{"pregunta_es":"¿Qué es HTTP?",'
            '"respuestas":["HTTP es un protocolo."],"ideas_clave":[]}'
        )
        with (
            mock.patch.object(interview_live.gemini_keys.pool, "available", return_value=[]),
            mock.patch.object(interview_live.nvidia_provider, "is_configured", return_value=True),
            mock.patch.object(interview_live.nvidia_provider, "generate", return_value=raw),
            mock.patch.object(interview_live, "_get_client") as get_client,
        ):
            assist = interview_live.coach_assist(
                "Arquitectura web sobre HTTP", "¿Qué es HTTP?",
                api_key="gemini-agotada", mode="resolver",
            )
        self.assertIsNotNone(assist)
        get_client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
