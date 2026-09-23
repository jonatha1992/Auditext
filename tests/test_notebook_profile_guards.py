"""Qué pasa cuando la cuenta de NotebookLM cambia con trabajo en vuelo.

Sin Tk a propósito, igual que ``test_simulation_retry_buttons.py``: lo que se
prueba es la decisión (¿este resultado todavía pertenece a la cuenta que el
usuario está mirando?), no el toolkit. Un root de Tcl por caso agota el
intérprete alrededor de los 50 tests — ver la nota en ``test_resolver_ui.py``.
"""

from __future__ import annotations

import unittest
from unittest import mock

from infrastructure.services.notebooklm_service import NotebookRef, ProfileRef
from presentation.views import interview_frame
from presentation.views.interview_frame import InterviewFrame


class _ImmediateThread:
    """Corre el target en el mismo hilo, para que el test controle el orden."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self) -> None:
        self._target(*self._args, **self._kwargs)


class _FakeVar:
    def __init__(self, value: str = ""):
        self._value = value

    def get(self) -> str:
        return self._value

    def set(self, value: str) -> None:
        self._value = value


class _FakeWidget:
    def __init__(self):
        self.kwargs: dict = {}

    def configure(self, **kwargs):
        self.kwargs.update(kwargs)

    def pack_forget(self):
        pass


class _SyncFrame:
    """Presta ``_sync_notebook`` real sin construir la ventana entera."""

    _sync_notebook = InterviewFrame._sync_notebook
    _deactivate_notebook_context = InterviewFrame._deactivate_notebook_context

    def __init__(self, profile: str):
        self._active_profile = profile
        self._notebook_map = {"FÍSICA I": NotebookRef("nb-fisica", "FÍSICA I", 36)}
        self.notebook_var = _FakeVar("FÍSICA I")
        self.notebook_combo = _FakeWidget()
        self.notebook_sync_button = _FakeWidget()
        self._notebook_syncing = False
        self._active_notebook_id = None
        self._active_notebook_title = None
        self._active_notebook_context = None
        self.saved: list = []
        self.statuses: list[str] = []

    def after(self, _delay, callback):
        callback()

    def _set_notebook_status(self, text, color=None, *, busy=False):
        self.statuses.append(text)

    def _save_notebook_selection(self, notebook):
        self.saved.append(notebook)

    def _apply_context_source_state(self):
        pass


class LateSyncTests(unittest.TestCase):
    """Un sync que vuelve tarde no puede escribir el temario de la cuenta vieja."""

    def _run_sync(self, on_call):
        frame = _SyncFrame("cuenta-vieja")
        with (
            mock.patch.object(interview_frame.threading, "Thread", _ImmediateThread),
            mock.patch.object(interview_frame.config, "repository", None),
            mock.patch.object(
                interview_frame.notebooklm_service,
                "sync_study_context",
                side_effect=lambda *a, **k: on_call(frame),
            ),
        ):
            frame._sync_notebook()
        return frame

    def test_a_sync_that_lands_after_an_account_switch_is_discarded(self):
        def switch_account_mid_flight(frame):
            # El usuario cambió de cuenta mientras el CLI respondía.
            frame._active_profile = "cuenta-nueva"
            return "Unidad 1: material de la cuenta vieja."

        frame = self._run_sync(switch_account_mid_flight)

        # Arrancar el examen con el temario de la otra cuenta es peor que
        # arrancar sin temario: nada en pantalla avisa que no corresponde.
        self.assertIsNone(frame._active_notebook_context)
        self.assertIsNone(frame._active_notebook_id)
        # Y la materia no puede quedar guardada bajo la clave de la cuenta nueva.
        self.assertEqual(frame.saved, [])

    def test_a_failed_sync_that_lands_after_a_switch_does_not_touch_the_new_account(self):
        def fail_after_switch(frame):
            frame._active_profile = "cuenta-nueva"
            raise RuntimeError("NotebookLM no respondió")

        frame = self._run_sync(fail_after_switch)

        # El error pertenece a la cuenta anterior: no debe pisar el estado ni
        # el cartel de la cuenta que el usuario está mirando ahora.
        self.assertNotIn("NotebookLM no respondió", frame.statuses)

    def test_a_sync_that_lands_on_its_own_account_still_works(self):
        frame = self._run_sync(lambda frame: "Unidad 1: conceptos.")

        self.assertIn("FÍSICA I", frame._active_notebook_context or "")
        self.assertEqual(frame._active_notebook_id, "nb-fisica")
        self.assertEqual([n.id for n in frame.saved], ["nb-fisica"])


class _ProfileFrame:
    """Presta ``_refresh_profiles`` real."""

    _refresh_profiles = InterviewFrame._refresh_profiles
    _deactivate_notebook_context = InterviewFrame._deactivate_notebook_context

    def __init__(self, active: str, saved_setting: str | None):
        self._active_profile = active
        self._saved_setting = saved_setting
        self._profile_map: dict = {}
        self._notebook_map = {"QUÍMICA": NotebookRef("nb-quimica", "QUÍMICA", 5)}
        self._active_notebook_id = "nb-quimica"
        self._active_notebook_title = "QUÍMICA"
        self._active_notebook_context = "[NotebookLM · QUÍMICA]\nGuía vieja."
        self.profile_var = _FakeVar()
        self.notebook_var = _FakeVar("QUÍMICA")
        self.profile_combo = _FakeWidget()
        self.notebook_combo = _FakeWidget()
        self.refreshed = 0

    def _schedule_ui(self, callback):
        callback()
        return True

    def _set_notebook_status(self, text, color=None, *, busy=False):
        pass

    def _load_setting(self, key):
        return self._saved_setting

    def _refresh_notebooks(self):
        self.refreshed += 1

    def _apply_context_source_state(self):
        pass


class ProfileRefreshTests(unittest.TestCase):
    def _refresh(self, active, saved_setting, profiles):
        frame = _ProfileFrame(active, saved_setting)
        with (
            mock.patch.object(interview_frame.threading, "Thread", _ImmediateThread),
            mock.patch.object(
                interview_frame.notebooklm_service,
                "list_profiles",
                return_value=profiles,
            ),
        ):
            frame._refresh_profiles()
        return frame

    def test_a_refresh_that_lands_on_another_account_clears_the_old_subject(self):
        """El camino que no pasa por ``_on_profile_change`` también debe limpiar.

        ``_refresh_profiles`` cambia la cuenta activa por su cuenta cuando el
        nombre guardado ya no existe (o cuando no hay ninguno y toma la
        primera). Ese camino nunca llamaba a ``_on_profile_change``, así que la
        guía cacheada de la cuenta anterior quedaba armada para el Start.
        """
        frame = self._refresh(
            active="cuenta-vieja",
            saved_setting="cuenta-que-ya-no-existe",
            profiles=[ProfileRef("cuenta-nueva", "nueva@gmail.com")],
        )

        self.assertEqual(frame._active_profile, "cuenta-nueva")
        self.assertIsNone(frame._active_notebook_context)
        self.assertIsNone(frame._active_notebook_id)
        self.assertEqual(frame._notebook_map, {})
        self.assertEqual(frame.refreshed, 1)

    def test_a_refresh_that_keeps_the_same_account_keeps_its_subject(self):
        frame = self._refresh(
            active="cuenta-vieja",
            saved_setting="cuenta-vieja",
            profiles=[
                ProfileRef("cuenta-vieja", "vieja@gmail.com"),
                ProfileRef("otra", "otra@gmail.com"),
            ],
        )

        self.assertEqual(frame._active_profile, "cuenta-vieja")
        self.assertEqual(
            frame._active_notebook_context, "[NotebookLM · QUÍMICA]\nGuía vieja."
        )
        self.assertEqual(frame.refreshed, 1)


class _Anything:
    """Widget/colaborador permisivo: responde a cualquier cosa sin hacer nada."""

    def __getattr__(self, name):
        return _Anything()

    def __call__(self, *args, **kwargs):
        return _Anything()

    def __getitem__(self, item):
        return _Anything()

    def __iter__(self):
        return iter(())

    def __bool__(self):
        return True


class _TeardownFrame:
    """Presta los dos teardown reales sobre colaboradores permisivos."""

    _finish_simulation = InterviewFrame._finish_simulation
    reset_session = InterviewFrame.reset_session

    def __init__(self):
        # None a propósito: evita que el "informe" sea un _Anything.
        self._simulation_session = None
        self._fixed_mode = "practica_oral"

    def __getattr__(self, name):
        return _Anything()


class SessionTeardownSilencesTtsTests(unittest.TestCase):
    """Terminar la sesión tiene que callar la voz que todavía está sonando.

    Sin esto, la pregunta o la devolución en curso conserva su generation token
    válido: su ``on_done`` corre contra la sesión siguiente y puede volver a
    packear botones de respuesta, reescribir la línea de estado o agendar el
    control de conocimiento sobre un simulacro nuevo.
    """

    def test_finishing_a_simulation_stops_the_voice(self):
        frame = _TeardownFrame()
        with mock.patch.object(interview_frame.tts, "stop") as stop:
            frame._finish_simulation()
        stop.assert_called()

    def test_resetting_the_session_stops_the_voice(self):
        frame = _TeardownFrame()
        with mock.patch.object(interview_frame.tts, "stop") as stop:
            frame.reset_session()
        stop.assert_called()


if __name__ == "__main__":
    unittest.main()
