"""Uniform latency instrumentation for every transcription pipeline.

A verification run should produce numbers, not impressions. Each stage emits a
single greppable line with the same shape, so one session log can be reduced to
a per-stage breakdown:

    LATENCY pipeline=live_interview stage=flush_wait ms=4001 mode=examen_oral

Pipelines:
    live_interview  Gemini Live STT + coach (Entrevista / Resolver tabs)
    live_local      faster-whisper on captured chunks (Live tab)
    file            faster-whisper on a picked file (transcription tab)

Read a run with:

    python tools/latency_report.py
"""

from __future__ import annotations

import time
from contextlib import contextmanager

import config

PREFIX = "LATENCY"


def log_stage(pipeline: str, stage: str, ms: float, **fields) -> None:
    """Emit one latency record. Never raises: instrumentation must not break audio."""
    try:
        extra = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
        config.logger.info(
            "%s pipeline=%s stage=%s ms=%.0f%s",
            PREFIX,
            pipeline,
            stage,
            ms,
            f" {extra}" if extra else "",
        )
    except Exception:  # pragma: no cover - logging must stay non-fatal
        pass


@contextmanager
def timed(pipeline: str, stage: str, **fields):
    """Time a block and log it. Yields a dict so the body can add fields.

    Fields are read after the block, which lets a caller attach values that are
    only known once the work is done (audio duration, segment count, ...).
    """
    info = dict(fields)
    start = time.perf_counter()
    try:
        yield info
    finally:
        log_stage(pipeline, stage, (time.perf_counter() - start) * 1000.0, **info)


def rtf(elapsed_ms: float, audio_ms: float) -> str | None:
    """Real-time factor: <1.0 means transcription outruns the audio."""
    if audio_ms <= 0:
        return None
    return f"{elapsed_ms / audio_ms:.2f}"
