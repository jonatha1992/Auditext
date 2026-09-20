"""Shared pytest setup for the suite.

The one job here: keep the test run out of the user's error journal — el JSONL
de la bitácora y también los dos archivos de log rotativos.
"""

from __future__ import annotations

import logging
import os
import tempfile
from logging.handlers import RotatingFileHandler

import pytest

import config
from core import errors


@pytest.fixture(autouse=True, scope="session")
def _isolate_log_files():
    """Point the rotating log files at a temp dir for the whole run.

    Companion to ``_isolate_bitacora``, which only covers the JSONL journal.
    ``config`` installs two ``RotatingFileHandler`` s at import time
    (``logs/error_log.txt`` and ``logs/errores.log``) and the suite writes to
    both: every provider-failure test logs its fixture as a real WARNING. The
    result was invented outages sitting in the user's journal — "NVIDIA HTTP 410
    Gone" and "Groq HTTP 503" lines that never happened, found on 2026-09-20
    while chasing a genuine one. A log that cannot be trusted is worse than no
    log, because it is read during an incident.

    The handler objects are kept and re-pointed rather than removed: code under
    test resolves the logger by name, so swapping handlers underneath it keeps
    every call site working and keeps the formatters intact.

    Residuo conocido y aceptado: ``import config`` corre en la colección de
    pytest, antes de que exista cualquier fixture, así que su
    ``INFO Configuración inicial completada`` sí llega a ``error_log.txt``. Es
    una línea de arranque, no una falla inventada, y ``errores.log`` — el que
    se lee desde Ajustes -> Bitácora de errores — queda byte a byte intacto.
    """
    handlers = [
        handler
        for handler in {*config.logger.handlers, *logging.getLogger().handlers}
        if isinstance(handler, RotatingFileHandler)
    ]
    with tempfile.TemporaryDirectory() as directory:
        saved = []
        try:
            for handler in handlers:
                saved.append((handler, handler.baseFilename))
                handler.acquire()
                try:
                    handler.stream.close()
                    handler.baseFilename = os.path.join(
                        directory, os.path.basename(handler.baseFilename)
                    )
                    handler.stream = handler._open()
                finally:
                    handler.release()
            yield
        finally:
            # Restaurar ANTES de que se borre el directorio: en Windows un
            # stream abierto ahi adentro hace fallar la limpieza.
            for handler, original in saved:
                handler.acquire()
                try:
                    handler.stream.close()
                    handler.baseFilename = original
                    handler.stream = handler._open()
                finally:
                    handler.release()


@pytest.fixture(autouse=True)
def _isolate_bitacora(monkeypatch):
    """Point the error journal at a temp file for every test.

    ``errors.record()`` is reached indirectly by most of the suite — any test
    that exercises a coach fallback, a guarded callback or a thread target
    journals its deliberate failures. Written to the real
    ``logs/bitacora.jsonl``, those entries drown the genuine ones the user reads
    from Ajustes -> Bitacora de errores.

    Every journal function resolves ``BITACORA_PATH`` from the module at call
    time, so redirecting the attribute is enough. Tests that patch it themselves
    still work: their patch nests over this one and restores it on cleanup.

    Uses ``tempfile`` rather than the ``tmp_path`` fixture on purpose: pytest's
    tmp factory cleans up a ``pytest-current`` symlink at session end, which
    needs privileges this Windows box does not grant (``WinError 5``).
    """
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(
            errors, "BITACORA_PATH", os.path.join(directory, "bitacora.jsonl")
        )
        yield
