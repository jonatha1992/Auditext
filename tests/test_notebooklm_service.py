"""NotebookLM catalog and synchronization adapter tests."""

from __future__ import annotations

import unittest
from unittest import mock

from infrastructure.services import notebooklm_service


class NotebookLMServiceTests(unittest.TestCase):
    def test_list_notebooks_builds_sorted_subject_selector(self):
        payload = {
            "status": "success",
            "notebooks": [
                {
                    "id": "physics",
                    "title": "FÍSICA I",
                    "source_count": 36,
                },
                {
                    "id": "math",
                    "title": "MATEMÁTICA DISCRETA",
                    "source_count": 18,
                },
            ],
        }
        with mock.patch.object(
            notebooklm_service, "_run_json", return_value=payload
        ):
            notebooks = notebooklm_service.list_notebooks()

        self.assertEqual(
            [notebook.title for notebook in notebooks],
            ["FÍSICA I", "MATEMÁTICA DISCRETA"],
        )
        self.assertEqual(notebooks[0].label, "FÍSICA I · 36 fuentes")

    def test_sync_returns_grounded_study_context(self):
        with mock.patch.object(
            notebooklm_service,
            "_run_json",
            return_value={"answer": "Unidad 1: conceptos fundamentales."},
        ) as run_json:
            result = notebooklm_service.sync_study_context("notebook-id")

        self.assertEqual(result, "Unidad 1: conceptos fundamentales.")
        args = run_json.call_args.args[0]
        self.assertEqual(args[:3], ["query", "notebook", "notebook-id"])
        self.assertIn("examen oral", args[3])

    def test_missing_auth_produces_actionable_message(self):
        completed = mock.Mock(
            returncode=1,
            stdout="",
            stderr="Authentication required. Run nlm login.",
        )
        with (
            mock.patch.object(
                notebooklm_service, "_nlm_executable", return_value="nlm"
            ),
            mock.patch.object(
                notebooklm_service.subprocess,
                "run",
                return_value=completed,
            ),
        ):
            with self.assertRaisesRegex(
                notebooklm_service.NotebookLMError, "Conectar"
            ):
                notebooklm_service.list_notebooks()


if __name__ == "__main__":
    unittest.main()
