"""Qué garantiza el indicador de carga único (``presentation/views/loading.py``).

Sin Tk a propósito, con un reloj falso: lo que se prueba es la máquina de
estados, no el toolkit. Un root de Tcl por caso agota el intérprete alrededor de
los 50 tests (ver la nota de ``test_resolver_ui.py``).
"""

from __future__ import annotations

import unittest

from presentation.views.interview_frame import InterviewFrame
from presentation.views.loading import FRAMES, LoadingText


class _FakeClock:
    """``schedule``/``cancel`` de mentira, con disparo manual."""

    def __init__(self, *, broken: bool = False):
        self.broken = broken
        self.pending: dict[int, object] = {}
        self.cancelled: list[int] = []
        self._next = 0

    def schedule(self, ms: int, callback):
        if self.broken:
            # La ventana ya no existe: `after` no pudo agendar nada.
            return None
        self._next += 1
        self.pending[self._next] = callback
        return self._next

    def cancel(self, token) -> None:
        self.cancelled.append(token)
        self.pending.pop(token, None)

    def tick(self) -> None:
        token = sorted(self.pending)[-1]
        self.pending.pop(token)()


def _loader(clock: _FakeClock, written: list, **kwargs) -> LoadingText:
    return LoadingText(
        lambda text, color=None: written.append((text, color)),
        clock.schedule,
        clock.cancel,
        **kwargs,
    )


class LoadingTextTests(unittest.TestCase):
    def setUp(self):
        self.clock = _FakeClock()
        self.written: list = []

    def test_busy_dibuja_el_primer_frame_y_arma_un_solo_timer(self):
        loader = _loader(self.clock, self.written)
        loader.show("Evaluando tu respuesta", busy=True)
        self.assertEqual(self.written[-1][0], f"{FRAMES[0]}  Evaluando tu respuesta")
        self.assertEqual(len(self.clock.pending), 1)
        self.assertTrue(loader.busy)

    def test_los_ticks_avanzan_y_dan_la_vuelta(self):
        loader = _loader(self.clock, self.written)
        loader.show("Sincronizando", busy=True)
        for _ in range(len(FRAMES)):
            self.clock.tick()
        textos = [text for text, _ in self.written]
        self.assertEqual(
            textos,
            [f"{frame}  Sincronizando" for frame in FRAMES] + [f"{FRAMES[0]}  Sincronizando"],
        )

    def test_un_estado_no_busy_apaga_y_cancela(self):
        loader = _loader(self.clock, self.written, idle_prefix="●")
        loader.show("Evaluando tu respuesta", busy=True)
        token = next(iter(self.clock.pending))
        loader.show("Error en el simulacro", "rojo")
        self.assertFalse(loader.busy)
        self.assertEqual(self.written[-1], ("●  Error en el simulacro", "rojo"))
        self.assertIn(token, self.clock.cancelled)
        self.assertFalse(self.clock.pending)

    def test_reescribir_un_estado_busy_no_duplica_el_timer_ni_reinicia(self):
        loader = _loader(self.clock, self.written)
        loader.show("Consultando materias", busy=True)
        self.clock.tick()
        loader.show("Consultando materias", busy=True)
        self.assertEqual(len(self.clock.pending), 1)
        # Sigue en el frame al que había llegado: reescribir el mismo estado
        # ocupado no tiene por qué verse como un salto.
        self.assertEqual(self.written[-1][0], f"{FRAMES[1]}  Consultando materias")

    def test_stop_corta_sin_reescribir_el_texto(self):
        loader = _loader(self.clock, self.written)
        loader.show("Leyendo la pregunta", busy=True)
        escrituras = len(self.written)
        loader.stop()
        self.assertFalse(loader.busy)
        self.assertFalse(self.clock.pending)
        self.assertEqual(len(self.written), escrituras)

    def test_sin_prefijo_el_texto_sale_crudo(self):
        loader = _loader(self.clock, self.written)
        loader.show("Primera")
        # Igualdad exacta: test_resolver_ui compara el texto de question_label así.
        self.assertEqual(self.written[-1][0], "Primera")

    def test_ventana_destruida_la_animacion_muere_callada(self):
        clock = _FakeClock(broken=True)
        loader = _loader(clock, self.written)
        loader.show("Preparando material", busy=True)
        self.assertFalse(clock.pending)
        # Y no queda nada que pueda rearmarse después.
        loader.stop()
        self.assertFalse(clock.cancelled)


class _FakeLabel:
    def __init__(self):
        self.text = ""
        self.color = None

    def configure(self, text=None, text_color=None):
        if text is not None:
            self.text = text
        if text_color is not None:
            self.color = text_color


class _FakeFrame:
    """Presta los escritores reales sin construir la ventana entera."""

    _set_status = InterviewFrame._set_status
    _set_notebook_status = InterviewFrame._set_notebook_status
    _set_question_text = InterviewFrame._set_question_text
    _label_writer = InterviewFrame._label_writer
    _build_loaders = InterviewFrame._build_loaders
    _loaders = InterviewFrame._loaders

    def __init__(self, clock: _FakeClock):
        self.status_label = _FakeLabel()
        self.notebook_status = _FakeLabel()
        self.question_label = _FakeLabel()
        self.answer_loading_label = _FakeLabel()
        self._after_safe = clock.schedule
        self._cancel_after = clock.cancel
        self._build_loaders()


class StructuralStopTests(unittest.TestCase):
    """El freno del spinner no depende de que nadie se acuerde de frenarlo."""

    def setUp(self):
        self.clock = _FakeClock()
        self.frame = _FakeFrame(self.clock)

    def test_el_estado_de_error_apaga_el_spinner_de_notebooklm(self):
        self.frame._set_notebook_status("Preparando material de X…", "azul", busy=True)
        self.assertTrue(self.frame._notebook_loader.busy)
        # Lo que ya hacía `finish_error`, sin una línea nueva.
        self.frame._set_notebook_status("nlm: no such notebook", "rojo")
        self.assertFalse(self.frame._notebook_loader.busy)
        self.assertEqual(self.frame.notebook_status.text, "nlm: no such notebook")

    def test_simulation_failed_apaga_el_spinner_de_sesion(self):
        self.frame._set_status("Evaluando tu respuesta", "azul", busy=True)
        self.assertTrue(self.frame._status_loader.busy)
        self.frame._set_status("Error en el simulacro", "rojo")
        self.assertFalse(self.frame._status_loader.busy)
        self.assertEqual(self.frame.status_label.text, "●  Error en el simulacro")

    def test_la_pregunta_deja_de_animarse_cuando_llega(self):
        self.frame._set_question_text("Preparando la primera pregunta…", busy=True)
        self.assertTrue(self.frame._question_loader.busy)
        self.frame._set_question_text("¿Qué es una transacción?")
        self.assertFalse(self.frame._question_loader.busy)
        self.assertEqual(self.frame.question_label.text, "¿Qué es una transacción?")

    def test_una_etiqueta_ausente_no_rompe(self):
        del self.frame.notebook_status
        self.frame._set_notebook_status("Buscando cuentas…", "azul", busy=True)
        self.assertTrue(self.frame._notebook_loader.busy)

    def test_el_teardown_frena_los_cuatro(self):
        self.frame._set_status("Conectando...", "azul", busy=True)
        self.frame._set_notebook_status("Consultando materias…", "azul", busy=True)
        for loader in self.frame._loaders():
            loader.stop()
        self.assertFalse(self.clock.pending)


if __name__ == "__main__":
    unittest.main()
