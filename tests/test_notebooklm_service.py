"""NotebookLM catalog and synchronization adapter tests."""

from __future__ import annotations

import os
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


class NotebookLMProfileTests(unittest.TestCase):
    def _completed(self, stdout: str, returncode: int = 0):
        return mock.Mock(returncode=returncode, stdout=stdout, stderr="")

    def test_profiles_are_read_from_the_cli_listing(self):
        listing = (
            "Available profiles:\n"
            "  default: jonicorrea1992@gmail.com\n"
            "  personal2: tecnofusion.it@gmail.com\n"
        )
        with (
            mock.patch.object(
                notebooklm_service, "_nlm_executable", return_value="nlm"
            ),
            mock.patch.object(
                notebooklm_service.subprocess,
                "run",
                return_value=self._completed(listing),
            ) as run,
        ):
            profiles = notebooklm_service.list_profiles()

        self.assertEqual(
            [(p.name, p.email) for p in profiles],
            [
                ("default", "jonicorrea1992@gmail.com"),
                ("personal2", "tecnofusion.it@gmail.com"),
            ],
        )
        self.assertEqual(profiles[0].label, "jonicorrea1992@gmail.com (default)")
        self.assertEqual(
            run.call_args.args[0], ["nlm", "login", "profile", "list"]
        )

    def test_profiles_without_valid_credentials_are_not_offered(self):
        listing = (
            "Available profiles:\n"
            "  default: jonicorrea1992@gmail.com\n"
            "  rota: (invalid)\n"
        )
        with (
            mock.patch.object(
                notebooklm_service, "_nlm_executable", return_value="nlm"
            ),
            mock.patch.object(
                notebooklm_service.subprocess,
                "run",
                return_value=self._completed(listing),
            ),
        ):
            profiles = notebooklm_service.list_profiles()

        self.assertEqual([p.name for p in profiles], ["default"])

    def test_a_chosen_profile_only_scopes_its_own_subprocess(self):
        """``nlm login switch`` cambiaría la cuenta de toda la máquina."""
        with (
            mock.patch.object(
                notebooklm_service, "_nlm_executable", return_value="nlm"
            ),
            mock.patch.object(
                notebooklm_service.subprocess,
                "run",
                return_value=self._completed("[]"),
            ) as run,
        ):
            notebooklm_service.list_notebooks(profile="personal2")

        env = run.call_args.kwargs["env"]
        self.assertEqual(env["NLM_PROFILE"], "personal2")
        # El resto del entorno viaja intacto: sin PATH el CLI no arranca.
        self.assertEqual(env.get("PATH"), os.environ.get("PATH"))
        # Y el proceso de la app queda como estaba: nada global se tocó.
        self.assertNotIn("NLM_PROFILE", os.environ)
        self.assertNotIn("switch", run.call_args.args[0])

    def test_without_a_profile_the_environment_is_left_alone(self):
        with (
            mock.patch.object(
                notebooklm_service, "_nlm_executable", return_value="nlm"
            ),
            mock.patch.object(
                notebooklm_service.subprocess,
                "run",
                return_value=self._completed("[]"),
            ) as run,
        ):
            notebooklm_service.list_notebooks()

        self.assertIsNone(run.call_args.kwargs["env"])

    def test_sync_queries_the_notebook_under_its_own_account(self):
        with mock.patch.object(
            notebooklm_service,
            "_run_json",
            return_value={"answer": "Unidad 1."},
        ) as run_json:
            notebooklm_service.sync_study_context("nb-1", profile="default")

        self.assertEqual(run_json.call_args.kwargs["profile"], "default")

    def test_catalog_pins_the_account_with_the_documented_flag(self):
        """``--profile`` es el mecanismo que el CLI documenta y garantiza.

        ``NLM_PROFILE`` funciona hoy (0.9.4) porque ``load_config()`` lo vuelca
        sobre ``auth.default_profile``, que es justo el fallback de
        ``get_client(None)``. Pero el CLI no lo documenta en ninguna parte
        (``nlm --ai`` no lo menciona), así que es comportamiento no contractual:
        alcanza con que renombren esa variable para que la app vuelva a leer el
        catálogo de la cuenta equivocada, y en silencio. El flag sí está en
        ``nlm notebook list --help``.
        """
        with (
            mock.patch.object(
                notebooklm_service, "_nlm_executable", return_value="nlm"
            ),
            mock.patch.object(
                notebooklm_service.subprocess,
                "run",
                return_value=self._completed("[]"),
            ) as run,
        ):
            notebooklm_service.list_notebooks(profile="personal2")

        command = run.call_args.args[0]
        self.assertIn("--profile", command)
        self.assertEqual(command[command.index("--profile") + 1], "personal2")

    def test_sync_pins_the_account_with_the_documented_flag(self):
        with (
            mock.patch.object(
                notebooklm_service, "_nlm_executable", return_value="nlm"
            ),
            mock.patch.object(
                notebooklm_service.subprocess,
                "run",
                return_value=self._completed('{"answer": "Unidad 1."}'),
            ) as run,
        ):
            notebooklm_service.sync_study_context("nb-1", profile="personal2")

        command = run.call_args.args[0]
        self.assertIn("--profile", command)
        self.assertEqual(command[command.index("--profile") + 1], "personal2")

    def test_without_a_profile_no_flag_is_invented(self):
        """Sin cuenta elegida el CLI tiene que quedarse con su propio default."""
        with (
            mock.patch.object(
                notebooklm_service, "_nlm_executable", return_value="nlm"
            ),
            mock.patch.object(
                notebooklm_service.subprocess,
                "run",
                return_value=self._completed("[]"),
            ) as run,
        ):
            notebooklm_service.list_notebooks()

        self.assertNotIn("--profile", run.call_args.args[0])

    def test_login_targets_the_selected_account(self):
        with (
            mock.patch.object(
                notebooklm_service, "_nlm_executable", return_value="nlm"
            ),
            mock.patch.object(notebooklm_service.subprocess, "Popen") as popen,
        ):
            notebooklm_service.launch_login("personal2")

        self.assertEqual(
            popen.call_args.args[0],
            ["nlm", "login", "--profile", "personal2"],
        )


if __name__ == "__main__":
    unittest.main()
