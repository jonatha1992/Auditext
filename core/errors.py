"""Error bitacora: structured error journal plus global exception capture.

Two problems this module solves:

1. Failures inside worker threads, asyncio loops and Tkinter callbacks used to
   die silently. Python prints them to a stderr that a windowed PyInstaller
   build does not have, so the app looked frozen with nothing in the log.
2. ``logs/error_log.txt`` mixes INFO chatter with real failures, so the actual
   error is impossible to find. The bitacora is a separate, append-only JSONL
   file holding failures only, readable by the UI.

Everything here is best-effort: the bitacora must never be the thing that
breaks the feature it is recording.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Callable, Iterator, TypeVar

from config import log_directory, logger

BITACORA_PATH = os.path.join(log_directory, "bitacora.jsonl")

# Keeps the journal bounded without a rotating handler: the file is read whole
# by the UI, so an unbounded file would eventually stall it.
_MAX_BYTES = 2 * 1024 * 1024
_KEEP_BYTES = 512 * 1024

_DEFAULT_USER_MESSAGE = "Ocurrio un error. Revisa la bitacora (logs/bitacora.jsonl)."

_write_lock = threading.Lock()

T = TypeVar("T")


@dataclass
class ErrorRecord:
    """One failure, as stored in the bitacora."""

    context: str
    error_type: str
    message: str
    severity: str = "error"
    timestamp: float = field(default_factory=time.time)
    thread: str = ""
    traceback_text: str = ""
    fields: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": self.timestamp,
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp)),
            "severity": self.severity,
            "context": self.context,
            "error_type": self.error_type,
            "message": self.message,
            "thread": self.thread,
            "traceback": self.traceback_text,
            **self.fields,
        }

    def summary(self) -> str:
        stamp = time.strftime("%H:%M:%S", time.localtime(self.timestamp))
        return f"[{stamp}] {self.context}: {self.error_type}: {self.message}"


def _trim_if_needed() -> None:
    """Drop the oldest half of the journal once it grows past the cap."""
    try:
        if os.path.getsize(BITACORA_PATH) <= _MAX_BYTES:
            return
        with open(BITACORA_PATH, "rb") as handle:
            handle.seek(-_KEEP_BYTES, os.SEEK_END)
            handle.readline()  # discard the partial line at the cut point
            tail = handle.read()
        with open(BITACORA_PATH, "wb") as handle:
            handle.write(tail)
    except OSError:
        pass


def _append(record: ErrorRecord) -> None:
    try:
        os.makedirs(log_directory, exist_ok=True)
        line = json.dumps(record.as_dict(), ensure_ascii=False)
        with _write_lock:
            with open(BITACORA_PATH, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            _trim_if_needed()
    except Exception:  # noqa: BLE001 - the journal must never raise
        logger.debug("No se pudo escribir la bitacora", exc_info=True)


def record(
    context: str,
    exc: BaseException | None = None,
    *,
    severity: str = "error",
    user_msg: str | None = None,
    **fields: Any,
) -> str:
    """Journal a failure and return a short message fit for the UI.

    ``exc_info=exc`` keeps the traceback even when called outside an active
    ``except`` block (the coach reports failures from a different thread).
    """
    entry = ErrorRecord(
        context=context,
        error_type=type(exc).__name__ if exc is not None else "Error",
        message=str(exc) if exc is not None else (user_msg or ""),
        severity=severity,
        thread=threading.current_thread().name,
        traceback_text=(
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            if exc is not None
            else ""
        ),
        fields={k: v for k, v in fields.items() if v is not None},
    )
    _append(entry)
    log = logger.warning if severity == "warning" else logger.error
    detail = " ".join(f"{k}={v}" for k, v in entry.fields.items())
    log("BITACORA %s%s: %s", context, f" [{detail}]" if detail else "", exc or user_msg,
        exc_info=exc if severity != "warning" else None)
    return user_msg or _DEFAULT_USER_MESSAGE


@dataclass
class Capture:
    """Result handle returned by :func:`capturing`."""

    error: BaseException | None = None
    message: str = ""

    @property
    def failed(self) -> bool:
        return self.error is not None


@contextmanager
def capturing(
    context: str,
    *,
    user_msg: str | None = None,
    reraise: bool = False,
    **fields: Any,
) -> Iterator[Capture]:
    """Run a block, journaling any failure instead of losing it.

    Use it where a failure must not take the feature down::

        with capturing("resolver.tts", device=name) as cap:
            speak(text)
        if cap.failed:
            status(cap.message)
    """
    result = Capture()
    try:
        yield result
    except Exception as exc:  # noqa: BLE001 - deliberate boundary
        result.error = exc
        result.message = record(context, exc, user_msg=user_msg, **fields)
        if reraise:
            raise


def guard(
    context: str,
    *,
    default: Any = None,
    reraise: bool = False,
    **fields: Any,
) -> Callable[[Callable[..., T]], Callable[..., T | Any]]:
    """Decorator form of :func:`capturing` for callbacks and thread targets."""

    def decorate(func: Callable[..., T]) -> Callable[..., T | Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any):
            try:
                return func(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - deliberate boundary
                record(context or func.__qualname__, exc, **fields)
                if reraise:
                    raise
                return default

        return wrapper

    return decorate


def recent(limit: int = 50, severity: str | None = None) -> list[dict[str, Any]]:
    """Return the newest journal entries, newest first."""
    try:
        with open(BITACORA_PATH, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return []
    entries: list[dict[str, Any]] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if severity and data.get("severity") != severity:
            continue
        entries.append(data)
        if len(entries) >= limit:
            break
    return entries


def clear() -> None:
    """Empty the journal (used by the UI and by tests)."""
    with _write_lock:
        try:
            open(BITACORA_PATH, "w", encoding="utf-8").close()
        except OSError:
            pass


_installed = False


def install(root: Any = None) -> None:
    """Route uncaught exceptions from every execution context into the bitacora.

    Without this, a crash in a Tk callback or a daemon thread only reaches
    stderr — which a windowed build replaces with a no-op writer, so the failure
    disappears entirely and the app merely looks stuck.
    """
    global _installed

    if not _installed:
        previous_hook = sys.excepthook

        def _excepthook(exc_type, exc, tb):
            if exc is not None:
                exc.__traceback__ = tb
                record("uncaught.main", exc, severity="critical")
            previous_hook(exc_type, exc, tb)

        sys.excepthook = _excepthook

        thread_hook = getattr(threading, "excepthook", None)
        if thread_hook is not None:

            def _thread_excepthook(args):
                exc = getattr(args, "exc_value", None)
                if exc is not None:
                    exc.__traceback__ = getattr(args, "exc_traceback", None)
                    thread_name = getattr(getattr(args, "thread", None), "name", "?")
                    record("uncaught.thread", exc, severity="critical", hilo=thread_name)
                thread_hook(args)

            threading.excepthook = _thread_excepthook

        _installed = True

    if root is not None:
        def _tk_report(_self, exc_type, exc, tb):
            if exc is not None:
                exc.__traceback__ = tb
                record("uncaught.tk", exc, severity="critical")

        type(root).report_callback_exception = _tk_report


def install_asyncio(loop: Any) -> None:
    """Journal exceptions raised by asyncio tasks nobody awaits."""

    def _handler(_loop, ctx):
        exc = ctx.get("exception")
        message = ctx.get("message") or "asyncio error"
        if exc is not None:
            record("uncaught.asyncio", exc, severity="critical", detalle=message)
        else:
            record("uncaught.asyncio", None, user_msg=message, severity="warning")

    try:
        loop.set_exception_handler(_handler)
    except Exception:  # noqa: BLE001
        logger.debug("No se pudo instalar el handler asyncio", exc_info=True)


__all__ = [
    "BITACORA_PATH",
    "Capture",
    "ErrorRecord",
    "capturing",
    "clear",
    "guard",
    "install",
    "install_asyncio",
    "recent",
    "record",
]
