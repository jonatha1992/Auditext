"""Error journal: nothing may fail silently, and the journal may not fail either."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from unittest import mock

from core import errors


class BitacoraTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self._patch = mock.patch.object(
            errors, "BITACORA_PATH", os.path.join(self._dir.name, "bitacora.jsonl")
        )
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.addCleanup(self._dir.cleanup)

    def _entries(self) -> list[dict]:
        with open(errors.BITACORA_PATH, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def test_record_stores_context_type_and_traceback(self):
        try:
            raise ValueError("clave rechazada")
        except ValueError as exc:
            message = errors.record("coach.groq", exc, modo="resolver")

        entries = self._entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["context"], "coach.groq")
        self.assertEqual(entries[0]["error_type"], "ValueError")
        self.assertEqual(entries[0]["modo"], "resolver")
        self.assertIn("ValueError: clave rechazada", entries[0]["traceback"])
        self.assertTrue(message)

    def test_user_message_is_returned_verbatim_when_given(self):
        message = errors.record(
            "coach.nvidia", RuntimeError("503"), user_msg="NVIDIA saturado"
        )
        self.assertEqual(message, "NVIDIA saturado")

    def test_capturing_swallows_the_failure_and_journals_it(self):
        with errors.capturing("resolver.tts") as cap:
            raise RuntimeError("sin dispositivo de audio")

        self.assertTrue(cap.failed)
        self.assertIsInstance(cap.error, RuntimeError)
        self.assertEqual(self._entries()[0]["context"], "resolver.tts")

    def test_capturing_can_reraise_after_journaling(self):
        with self.assertRaises(RuntimeError):
            with errors.capturing("resolver.tts", reraise=True):
                raise RuntimeError("boom")
        self.assertEqual(len(self._entries()), 1)

    def test_guard_returns_the_default_instead_of_propagating(self):
        @errors.guard("worker.audio", default=[])
        def leer_dispositivos():
            raise OSError("PaErrorCode -9999")

        self.assertEqual(leer_dispositivos(), [])
        self.assertEqual(self._entries()[0]["error_type"], "OSError")

    def test_recent_returns_newest_first(self):
        errors.record("uno", RuntimeError("a"))
        errors.record("dos", RuntimeError("b"))
        self.assertEqual([e["context"] for e in errors.recent()], ["dos", "uno"])

    def test_journal_never_raises_when_the_file_cannot_be_written(self):
        """A broken journal must not take down the feature it is recording."""
        with mock.patch.object(errors, "BITACORA_PATH", os.path.join("Z:\\", "nope", "x.jsonl")):
            self.assertTrue(errors.record("ctx", RuntimeError("x")))

    def test_thread_failures_reach_the_journal(self):
        errors.install()

        def explota():
            raise RuntimeError("hilo caido")

        thread = threading.Thread(target=explota, name="worker-test")
        thread.start()
        thread.join()

        contexts = [entry["context"] for entry in self._entries()]
        self.assertIn("uncaught.thread", contexts)

    def test_recent_survives_a_corrupted_line(self):
        errors.record("ok", RuntimeError("a"))
        with open(errors.BITACORA_PATH, "a", encoding="utf-8") as handle:
            handle.write("{no es json\n")
        self.assertEqual(len(errors.recent()), 1)


if __name__ == "__main__":
    unittest.main()
