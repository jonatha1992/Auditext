"""Chat over a finished transcription (no network)."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from infrastructure.repositories.sqlite_repository import SQLiteTranscriptionRepository
from infrastructure.services import transcript_chat
from infrastructure.services.transcript_chat import (
    MAX_CONTEXT_CHARS,
    TranscriptChatError,
    build_history,
)


class AskTests(unittest.TestCase):
    def test_sends_the_full_transcription_not_a_summary(self):
        # The whole reason this exists: recover what the summary dropped.
        captured = {}

        def generate(prompt, **kwargs):
            captured["prompt"] = prompt
            captured["system"] = kwargs.get("system")
            return "la respuesta"

        with mock.patch.object(transcript_chat, "nvidia_provider") as nv:
            nv.is_configured.return_value = True
            nv.generate.side_effect = generate
            answer = transcript_chat.ask("dato escondido al final", "¿qué dijo?")

        self.assertEqual(answer, "la respuesta")
        self.assertIn("dato escondido al final", captured["prompt"])
        self.assertIn("no lo inventes", captured["system"].lower())

    def test_passes_prior_turns_as_history(self):
        with mock.patch.object(transcript_chat, "nvidia_provider") as nv:
            nv.is_configured.return_value = True
            nv.generate.return_value = "ok"
            transcript_chat.ask(
                "texto",
                "¿y eso?",
                [{"role": "user", "content": "hola"}, {"role": "assistant", "content": "hey"}],
            )

        history = nv.generate.call_args[1]["history"]
        self.assertEqual([m["role"] for m in history], ["user", "assistant"])

    def test_long_transcription_keeps_the_ending(self):
        # A head-only cut would answer "no aparece" for anything said late.
        text = "INICIO " + ("x" * (MAX_CONTEXT_CHARS * 2)) + " FINAL"
        captured = {}

        with mock.patch.object(transcript_chat, "nvidia_provider") as nv:
            nv.is_configured.return_value = True
            nv.generate.side_effect = lambda prompt, **kw: captured.setdefault(
                "prompt", prompt
            ) or "ok"
            transcript_chat.ask(text, "¿cómo terminó?")

        self.assertIn("INICIO", captured["prompt"])
        self.assertIn("FINAL", captured["prompt"])

    def test_blank_question_is_rejected_before_any_call(self):
        with mock.patch.object(transcript_chat, "nvidia_provider") as nv:
            with self.assertRaises(TranscriptChatError):
                transcript_chat.ask("texto", "   ")
            nv.generate.assert_not_called()

    def test_empty_answer_is_not_reported_as_success(self):
        with mock.patch.object(transcript_chat, "nvidia_provider") as nv:
            nv.is_configured.return_value = True
            nv.generate.return_value = "   "
            with self.assertRaises(TranscriptChatError):
                transcript_chat.ask("texto", "¿qué?")

    def test_missing_provider_explains_itself(self):
        with mock.patch.object(transcript_chat, "nvidia_provider") as nv:
            nv.is_configured.return_value = False
            with self.assertRaises(TranscriptChatError) as ctx:
                transcript_chat.ask("texto", "¿qué?")
        self.assertIn("NVIDIA", str(ctx.exception))


class BuildHistoryTests(unittest.TestCase):
    def test_keeps_only_recent_turns(self):
        messages = [{"role": "user", "content": f"q{i}"} for i in range(30)]
        self.assertEqual(len(build_history(messages)), transcript_chat.MAX_HISTORY_TURNS)

    def test_drops_empty_messages(self):
        self.assertEqual(build_history([{"role": "user", "content": ""}]), [])

    def test_unknown_roles_become_user(self):
        history = build_history([{"role": "system", "content": "x"}])
        self.assertEqual(history[0]["role"], "user")


class ChatPersistenceTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.repo = SQLiteTranscriptionRepository(self.db_path)
        self.repo.init_db()
        self.addCleanup(lambda: os.path.exists(self.db_path) and os.remove(self.db_path))

    def test_messages_round_trip_in_order(self):
        self.repo.add_chat_message("a.wav", "user", "¿qué dijo?")
        self.repo.add_chat_message("a.wav", "assistant", "dijo esto")
        rows = self.repo.get_chat_messages("a.wav")
        self.assertEqual([r["role"] for r in rows], ["user", "assistant"])
        self.assertEqual(rows[0]["content"], "¿qué dijo?")

    def test_chats_are_scoped_per_file(self):
        self.repo.add_chat_message("a.wav", "user", "de a")
        self.repo.add_chat_message("b.wav", "user", "de b")
        self.assertEqual(len(self.repo.get_chat_messages("a.wav")), 1)

    def test_clear_chat_only_touches_that_file(self):
        self.repo.add_chat_message("a.wav", "user", "de a")
        self.repo.add_chat_message("b.wav", "user", "de b")
        self.repo.clear_chat("a.wav")
        self.assertEqual(self.repo.get_chat_messages("a.wav"), [])
        self.assertEqual(len(self.repo.get_chat_messages("b.wav")), 1)

    def test_deleting_a_transcription_removes_its_chat(self):
        self.repo.add_chat_message("a.wav", "user", "hola")
        self.repo.delete("a.wav")
        self.assertEqual(self.repo.get_chat_messages("a.wav"), [])

    def test_existing_database_gains_the_table_on_init(self):
        # Simulates a database created before chat existed.
        fd, legacy = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(legacy) and os.remove(legacy))
        conn = sqlite3.connect(legacy)
        conn.execute("CREATE TABLE transcripciones (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        repo = SQLiteTranscriptionRepository(legacy)
        repo.init_db()
        repo.add_chat_message("a.wav", "user", "hola")
        self.assertEqual(len(repo.get_chat_messages("a.wav")), 1)


if __name__ == "__main__":
    unittest.main()
