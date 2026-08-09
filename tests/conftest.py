"""Shared pytest setup for the suite.

The one job here: keep the test run out of the user's error journal.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from core import errors


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
