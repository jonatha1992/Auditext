"""Indicador de carga único de la app.

Un solo juego de frames y un solo motor de animación para toda espera larga:
NotebookLM, simulacro y coach. Antes cada pantalla lo resolvía por su cuenta —o
no lo resolvía— y una sincronización de materia medida en 48 s
(``logs/error_log.txt`` 13:50:53 → 13:51:41) quedaba como texto inmóvil: desde
afuera no hay forma de distinguir eso de una app colgada.

**Por qué animación de texto y no un canvas.** ``presentation/views/spinner.py``
existe, pero es un ``tk.Canvas`` con ``bg`` fijo que no combina con los frames de
customtkinter, hay que meterlo a mano en dos contenedores distintos, y su bucle
``after(80, ...)`` no guarda token: no se puede cancelar. Animar el prefijo del
texto reusa los tres puntos que la UI ya tenía para escribir estado, así que
**el freno es estructural**: cualquier camino de salida que ya reportaba algo
—éxito, error, cancelación— apaga el spinner sin que haya que tocarlo. Parear
``start``/``stop`` a mano en quince lugares es justamente la forma de olvidarse
de uno y dejarlo girando para siempre.

Sin ``tkinter`` acá a propósito: así se prueba con un reloj falso, sin gastar un
intérprete de Tcl por caso (ver la nota de ``tests/test_resolver_ui.py``).
"""

from __future__ import annotations

# Los mismos glifos que ya usaba el spinner del coach: se sabe que Segoe UI los
# dibuja. Cambiarlos por braille o ASCII rompe la continuidad visual que el
# usuario pidió ("el mismo para todos").
FRAMES = ("◌", "◔", "◑", "◕")

# 220 ms: suficientemente vivo para leerse como movimiento, suficientemente
# lento para no competir con el refresco de 100 ms de ``_drain_queues``.
INTERVAL_MS = 220


class LoadingText:
    """Anima el prefijo de una etiqueta mientras haya trabajo en vuelo.

    No hace falta parear ``start``/``stop``: el estado sale del texto que se
    escribe. Cada ``show(..., busy=False)`` apaga la animación, y como todo
    camino de salida ya escribía su propio texto, el spinner frena solo.
    """

    def __init__(
        self,
        apply,
        schedule,
        cancel=None,
        *,
        idle_prefix: str = "",
        interval: int = INTERVAL_MS,
        frames=FRAMES,
    ) -> None:
        self._apply = apply
        self._schedule = schedule
        self._cancel = cancel
        self._idle_prefix = idle_prefix
        self._interval = interval
        self._frames = tuple(frames)
        self._text = ""
        self._color = None
        self._busy = False
        self._index = 0
        self._token = None

    @property
    def busy(self) -> bool:
        return self._busy

    def show(self, text: str, color=None, *, busy: bool = False) -> None:
        """Escribe el estado; ``busy`` decide si además tiene que moverse."""
        # El índice se reinicia solo al *entrar* en espera. Reescribir el mismo
        # estado ocupado es común (``_apply_context_source_state`` lo hace en
        # cada cambio de fuente) y no tiene por qué verse como un salto.
        if busy and not self._busy:
            self._index = 0
        self._text = text
        self._color = color
        self._busy = busy
        self._render()
        if busy:
            self._arm()
        else:
            self._disarm()

    def stop(self) -> None:
        """Corta la animación sin reescribir el texto: para el teardown."""
        self._busy = False
        self._disarm()

    def _render(self) -> None:
        prefix = self._frames[self._index] if self._busy else self._idle_prefix
        # Sin prefijo se escribe el texto crudo: hay tests que comparan el
        # contenido de la etiqueta por igualdad exacta.
        self._apply(f"{prefix}  {self._text}" if prefix else self._text, self._color)

    def _arm(self) -> None:
        # Un solo timer vivo. Sin esta guarda, cada reescritura de un estado
        # ocupado dejaría un temporizador más corriendo en paralelo.
        if self._token is not None:
            return
        self._token = self._schedule(self._interval, self._tick)

    def _disarm(self) -> None:
        token, self._token = self._token, None
        if token is not None and self._cancel is not None:
            self._cancel(token)

    def _tick(self) -> None:
        self._token = None
        if not self._busy:
            return
        self._index = (self._index + 1) % len(self._frames)
        self._render()
        # Si ``schedule`` devuelve ``None`` —la ventana ya no existe— la
        # animación muere acá y no se vuelve a agendar. Ese es el caso que
        # producía ``after`` huérfanos tras destruir el frame.
        self._arm()
