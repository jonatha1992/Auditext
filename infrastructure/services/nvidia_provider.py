"""NVIDIA NIM text generation with API-key rotation."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from dotenv import load_dotenv

from config import logger

load_dotenv()

NVIDIA_BASE_URL = os.getenv(
    "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"
).rstrip("/")
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "").strip() or "nvidia/nemotron-3-nano-30b-a3b"


def _collect_keys() -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()

    def add(raw: str | None) -> None:
        if not raw:
            return
        for part in raw.replace(";", ",").split(","):
            key = part.strip()
            if key and key not in seen:
                seen.add(key)
                keys.append(key)

    add(os.getenv("NVIDIA_API_KEYS"))
    add(os.getenv("NVIDIA_API_KEY"))
    for index in range(1, 6):
        add(os.getenv(f"NVIDIA_API_KEY{index}"))
        add(os.getenv(f"NVIDIA_API_KEY_{index}"))
    return keys


@dataclass
class NvidiaKeyPool:
    _keys: list[str] = field(default_factory=_collect_keys)
    _unavailable: set[str] = field(default_factory=set)

    def reload(self) -> None:
        self._keys = _collect_keys()
        self._unavailable.clear()

    def is_configured(self) -> bool:
        return bool(self._keys)

    def count(self) -> int:
        return len(self._keys)

    def available(self) -> list[str]:
        return [key for key in self._keys if key not in self._unavailable]

    def label(self, key: str | None = None) -> str:
        total = self.count()
        if key in self._keys:
            return f"NVIDIA {self._keys.index(key) + 1}/{total}"
        return f"NVIDIA 0/{total}"

    def mark_unavailable(self, key: str) -> None:
        self._unavailable.add(key)
        logger.warning("%s marcada no disponible durante esta operación", self.label(key))


pool = NvidiaKeyPool()


class NvidiaError(RuntimeError):
    pass


def is_retryable_error(exc: BaseException) -> bool:
    detail = str(exc).lower()
    return any(
        marker in detail
        for marker in (
            "401", "402", "403", "408", "429", "500", "502", "503", "504",
            "quota", "credit", "rate limit", "timed out", "timeout",
        )
    )


_DEFAULT_SYSTEM = (
    "/no_think\nSeguí exactamente las instrucciones. "
    "Entregá únicamente la respuesta final."
)


def _request(
    key: str,
    prompt: str,
    max_tokens: int,
    history: list[dict] | None = None,
    system: str | None = None,
) -> str:
    messages: list[dict] = [{"role": "system", "content": system or _DEFAULT_SYSTEM}]
    # Prior turns go between the system prompt and the new question so the model
    # can follow up without the caller re-stating the conversation by hand.
    messages.extend(history or [])
    messages.append({"role": "user", "content": prompt})

    body = json.dumps(
        {
            "model": NVIDIA_MODEL,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": max_tokens,
            "reasoning_budget": 0,
            "stream": False,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{NVIDIA_BASE_URL}/chat/completions",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:500]
        except Exception:
            detail = str(exc)
        raise NvidiaError(f"NVIDIA HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise NvidiaError(f"NVIDIA conexión: {exc}") from exc

    try:
        text = payload["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise NvidiaError("NVIDIA devolvió una respuesta sin contenido") from exc
    if not text:
        raise NvidiaError("NVIDIA devolvió una respuesta vacía")
    return text


def generate(
    prompt: str,
    *,
    max_tokens: int = 480,
    history: list[dict] | None = None,
    system: str | None = None,
) -> str:
    """Generate text, rotating through every configured NVIDIA key.

    `history` carries prior chat turns as {"role": "user"|"assistant", "content": ...}.
    """
    if not pool.is_configured():
        raise NvidiaError("No hay claves NVIDIA configuradas")

    last_exc: BaseException | None = None
    for key in pool.available():
        try:
            text = _request(key, prompt, max_tokens, history, system)
            logger.info("Respuesta generada con %s modelo=%s", pool.label(key), NVIDIA_MODEL)
            return text
        except Exception as exc:
            last_exc = exc
            logger.warning("%s falló: %s", pool.label(key), exc)
            if is_retryable_error(exc):
                pool.mark_unavailable(key)
                continue
            raise NvidiaError(str(exc)) from exc
    raise NvidiaError("Todas las claves NVIDIA fallaron") from last_exc


def is_configured() -> bool:
    return pool.is_configured()
