"""Groq text generation — the low-latency rung of the provider ladder.

Why this exists: the two existing providers fail for reasons that have nothing
to do with each other, and both failures were costing whole turns.

* Gemini runs out of free-tier quota mid-session (five keys exhausted in
  seconds, see the bitacora).
* NVIDIA NIM answers ``503 Worker local total request limit reached (33/32)``
  when its shared endpoint saturates.

Groq is a third, independent pool running on LPUs, so it is usually the fastest
of the three and it is available exactly when the other two are not. The API is
OpenAI-compatible, so the wire format matches NVIDIA's and the failure
classification, cooldowns and time budget are reused from that module instead of
being written twice.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from dotenv import load_dotenv

from config import logger
from infrastructure.services.nvidia_provider import (
    PERMANENT,
    SATURATION,
    classify_error,
    collect_api_keys,
    cooldown_for,
)

load_dotenv()

GROQ_BASE_URL = os.getenv(
    "GROQ_BASE_URL", "https://api.groq.com/openai/v1"
).rstrip("/")
# Default verificado con llamada real (1114 ms, JSON válido con el prompt del
# simulacro y max_tokens=700) el 2026-09-20.
#
# El anterior, `llama-3.3-70b-versatile`, ya no existe: toda la familia Llama
# salió del catálogo de Groq y cualquier key contesta
# `HTTP 404 ... "code":"model_not_found"`. Ese 404 cae en PERMANENT para
# `classify_error`, así que abortaba la cadena en el primer intento sin
# reintentar ni rotar: apenas Gemini se saturaba, el profesor IA quedaba mudo
# con Groq "configurado".
#
# Se elige el 120B y no el 20B (595 ms) por el mismo motivo del comentario
# original: las respuestas largas en español que pide esta app le quedan
# grandes a un modelo chico. `qwen/qwen3.8-27b` queda descartado aunque sea el
# más rápido (427 ms): devolvió prosa, no JSON, y rompe `parse_simulation_turn`.
#
# Aparecer en `GET /v1/models` NO prueba que el modelo sirva — misma lección
# que `nvidia_provider.NVIDIA_MODEL`. Cualquier reemplazo se valida con una
# llamada real, con el prompt real, antes de entrar acá.
GROQ_MODEL = os.getenv("GROQ_MODEL", "").strip() or "openai/gpt-oss-120b"

# TRAMPA de los modelos de razonamiento (medida el 2026-09-20 sobre
# `openai/gpt-oss-120b`): el reasoning se cobra del MISMO `max_tokens` que la
# respuesta. Con un techo bajo (medido con 16) la API contesta **HTTP 200 con
# `content` vacío** — se gastó el presupuesto pensando y no quedó nada para
# escribir. `_request` lo convierte en "Groq devolvió una respuesta vacía" y
# `provider_chain` lo cuenta como falla, así que el proveedor parece caído
# estando sano: el peor modo de falla posible, porque no hay error que leer.
#
# Es el mismo patrón que el `thinking_budget` de los Gemini 3.x ya documentado
# en CLAUDE.md, y acá pega de verdad: el coach pide 270 tokens en los modos
# cortos (`interview_live._nvidia_token_budget`). Por eso el techo se sube a un
# piso, no se confía en el que manda quien llama. Subir el techo no alarga la
# respuesta — es un límite, no un objetivo; solo compra lugar para el reasoning.
_REASONING_MODEL_MARKERS = ("gpt-oss",)
_MIN_REASONING_TOKENS = 700

# El piso de 700 NO alcanza con el prompt real del coach (medido 2026-09-23,
# prompt de 3867 caracteres, `openai/gpt-oss-120b`): con el esfuerzo por
# defecto gastó 698 de 700 tokens pensando, `finish_reason=length` y `content`
# vacío — el mismo "Groq devolvió una respuesta vacía" de arriba, en cada turno.
# Con `reasoning_effort=low` y el mismo techo usó 191-295 tokens de reasoning y
# contestó en 1,2-1,3 s. Subir el techo también andaba (1500 -> 872 de
# reasoning, 2,6 s), pero paga el doble de latencia por pensar lo mismo.
_REASONING_EFFORT = "low"


def _is_reasoning_model(model: str | None = None) -> bool:
    name = (model if model is not None else GROQ_MODEL).casefold()
    return any(marker in name for marker in _REASONING_MODEL_MARKERS)


def effective_max_tokens(max_tokens: int, model: str | None = None) -> int:
    """Token ceiling a reasoning model needs to answer at all."""
    if _is_reasoning_model(model):
        return max(max_tokens, _MIN_REASONING_TOKENS)
    return max_tokens

GROQ_REQUEST_TIMEOUT_SECONDS = 20
GROQ_MAX_REQUEST_TIMEOUT_SECONDS = 40
GROQ_TOTAL_BUDGET_SECONDS = float(
    os.getenv("GROQ_TOTAL_BUDGET_SECONDS", "").strip() or 40.0
)

_MIN_ATTEMPT_SECONDS = 3.0
_SATURATION_BACKOFF_SECONDS = 1.5

__all__ = [
    "GROQ_BASE_URL",
    "GROQ_MODEL",
    "GroqError",
    "GroqKeyPool",
    "effective_max_tokens",
    "generate",
    "is_configured",
    "pool",
    "request_timeout_for",
    "reset_socket_block",
    "socket_is_blocked",
]


def _collect_keys() -> list[str]:
    """Keys de Groq. La lógica vive en el colector canónico de nvidia_provider."""
    return collect_api_keys("GROQ_API_KEY")


@dataclass
class GroqKeyPool:
    _keys: list[str] = field(default_factory=_collect_keys)
    _unavailable_until: dict[str, float] = field(default_factory=dict)

    def reload(self) -> None:
        self._keys = _collect_keys()
        self._unavailable_until.clear()

    def is_configured(self) -> bool:
        return bool(self._keys)

    def count(self) -> int:
        return len(self._keys)

    def available(self) -> list[str]:
        now = time.monotonic()
        self._unavailable_until = {
            key: deadline
            for key, deadline in self._unavailable_until.items()
            if deadline > now
        }
        return [key for key in self._keys if key not in self._unavailable_until]

    def seconds_until_available(self) -> float:
        if not self._keys or self.available():
            return 0.0
        deadlines = [
            self._unavailable_until[key]
            for key in self._keys
            if key in self._unavailable_until
        ]
        if not deadlines:
            return 0.0
        return max(0.0, min(deadlines) - time.monotonic())

    def label(self, key: str | None = None) -> str:
        total = self.count()
        if key in self._keys:
            return f"Groq {self._keys.index(key) + 1}/{total}"
        return f"Groq 0/{total}"

    def mark_unavailable(self, key: str, cooldown_seconds: float = 30.0) -> None:
        self._unavailable_until[key] = time.monotonic() + cooldown_seconds
        logger.warning(
            "%s pausada %.0f segundos antes de reintentar",
            self.label(key),
            cooldown_seconds,
        )

    def clear_cooldown(self, key: str) -> None:
        self._unavailable_until.pop(key, None)


pool = GroqKeyPool()


class GroqError(RuntimeError):
    pass


class GroqBudgetExceeded(GroqError):
    """The total time budget ran out before any key produced an answer."""


def request_timeout_for(max_tokens: int) -> float:
    """Scale the socket timeout with the requested answer size."""
    extra = max(0, max_tokens - 300) / 300.0 * 6.0
    return min(
        GROQ_MAX_REQUEST_TIMEOUT_SECONDS,
        GROQ_REQUEST_TIMEOUT_SECONDS + extra,
    )


_DEFAULT_SYSTEM = (
    "Seguí exactamente las instrucciones. Entregá únicamente la respuesta final."
)

_USER_AGENT = os.getenv("GROQ_USER_AGENT", "").strip() or "AudioText/1.0 (desktop)"


# WinError 10013 es el firewall/antivirus de Windows negando el socket saliente.
# No es cuota ni saturacion: ninguna clave lo arregla y ningun reintento lo cura,
# asi que reintentarlo en cada turno solo agrega latencia al coach y llena la
# bitacora (64 entradas identicas en el ultimo mes). Se apaga el proveedor para
# todo el proceso y se avisa una vez cual es el arreglo real.
_BLOCKED_SOCKET_MARKERS = ("10013", "winerror 10013")
_socket_blocked = False


def _is_socket_blocked_error(exc: BaseException) -> bool:
    detail = str(exc).casefold()
    return any(marker in detail for marker in _BLOCKED_SOCKET_MARKERS)


def socket_is_blocked() -> bool:
    return _socket_blocked


def reset_socket_block() -> None:
    """Test seam: forget the process-wide block."""
    global _socket_blocked
    _socket_blocked = False


def _mark_socket_blocked(exc: BaseException) -> None:
    global _socket_blocked
    if _socket_blocked:
        return
    _socket_blocked = True
    logger.warning(
        "Groq deshabilitado en este proceso: el sistema bloquea el socket saliente "
        "(%s). Hay que permitir la app en el firewall/antivirus; cambiar la clave "
        "no cambia nada.",
        exc,
    )


def _request(
    key: str,
    prompt: str,
    max_tokens: int,
    history: list[dict] | None = None,
    system: str | None = None,
    timeout: float | None = None,
) -> str:
    messages: list[dict] = [{"role": "system", "content": system or _DEFAULT_SYSTEM}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": prompt})

    payload_body: dict = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if _is_reasoning_model():
        payload_body["reasoning_effort"] = _REASONING_EFFORT
    body = json.dumps(payload_body).encode("utf-8")
    request = urllib.request.Request(
        f"{GROQ_BASE_URL}/chat/completions",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            # Groq sits behind Cloudflare, which answers 403 error 1010 to the
            # default "Python-urllib/3.x" signature. Any ordinary UA passes.
            "User-Agent": _USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(
            request, timeout=timeout or GROQ_REQUEST_TIMEOUT_SECONDS
        ) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:500]
        except Exception:
            detail = str(exc)
        raise GroqError(f"Groq HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise GroqError(f"Groq conexión: {exc}") from exc

    try:
        text = payload["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise GroqError("Groq devolvió una respuesta sin contenido") from exc
    if not text:
        raise GroqError("Groq devolvió una respuesta vacía")
    return text


def generate(
    prompt: str,
    *,
    max_tokens: int = 480,
    history: list[dict] | None = None,
    system: str | None = None,
    budget_seconds: float | None = None,
) -> str:
    """Generate text, rotating keys within a bounded total budget.

    Same contract as ``nvidia_provider.generate``: bounded per request and
    bounded overall, so a saturated provider costs a known wait, never a freeze.
    """
    if _socket_blocked:
        raise GroqError(
            "Groq deshabilitado: el firewall/antivirus bloquea el socket saliente"
        )
    if not pool.is_configured():
        raise GroqError("No hay claves Groq configuradas")

    budget = budget_seconds if budget_seconds is not None else GROQ_TOTAL_BUDGET_SECONDS
    deadline = time.monotonic() + budget
    # Antes de medir el timeout, no después: un modelo de razonamiento con el
    # techo subido necesita proporcionalmente más socket, y el timeout se
    # escala con los tokens que realmente se piden.
    max_tokens = effective_max_tokens(max_tokens)
    base_timeout = request_timeout_for(max_tokens)

    last_exc: BaseException | None = None
    attempts = 0
    saturated_only = True

    while True:
        remaining = deadline - time.monotonic()
        if remaining < _MIN_ATTEMPT_SECONDS:
            break

        keys = pool.available()
        if not keys:
            wait = pool.seconds_until_available()
            if wait <= 0 or wait >= remaining - _MIN_ATTEMPT_SECONDS:
                break
            time.sleep(wait)
            continue

        progressed = False
        for key in keys:
            remaining = deadline - time.monotonic()
            if remaining < _MIN_ATTEMPT_SECONDS:
                break
            progressed = True
            attempts += 1
            started = time.monotonic()
            try:
                text = _request(
                    key,
                    prompt,
                    max_tokens,
                    history,
                    system,
                    timeout=min(base_timeout, remaining),
                )
                pool.clear_cooldown(key)
                logger.info(
                    "Respuesta generada con %s modelo=%s en %.0f ms (intento %d)",
                    pool.label(key),
                    GROQ_MODEL,
                    (time.monotonic() - started) * 1000.0,
                    attempts,
                )
                return text
            except Exception as exc:
                last_exc = exc
                if _is_socket_blocked_error(exc):
                    _mark_socket_blocked(exc)
                    raise GroqError(
                        "Groq bloqueado por el firewall/antivirus del sistema "
                        f"({exc}); deshabilitado hasta reiniciar la app"
                    ) from exc
                kind = classify_error(exc)
                if kind != SATURATION:
                    saturated_only = False
                logger.warning(
                    "%s falló (%s, %.0f ms): %s",
                    pool.label(key),
                    kind,
                    (time.monotonic() - started) * 1000.0,
                    exc,
                )
                if kind == PERMANENT:
                    raise GroqError(str(exc)) from exc
                pool.mark_unavailable(key, cooldown_for(kind))

        if not progressed or not saturated_only:
            break
        remaining = deadline - time.monotonic()
        if remaining < _MIN_ATTEMPT_SECONDS + _SATURATION_BACKOFF_SECONDS:
            break
        time.sleep(_SATURATION_BACKOFF_SECONDS)

    elapsed = budget - max(0.0, deadline - time.monotonic())
    if last_exc is None:
        raise GroqBudgetExceeded(f"Groq sin claves disponibles dentro de {budget:.0f}s")
    message = (
        f"Todas las claves Groq fallaron tras {attempts} intento(s) "
        f"en {elapsed:.0f}s: {last_exc}"
    )
    if attempts == 0:
        raise GroqBudgetExceeded(message) from last_exc
    raise GroqError(message) from last_exc


def is_configured() -> bool:
    # Un proveedor cuyo socket bloquea el SO no esta "configurado" para nadie que
    # arme la cadena de respaldo: dejarlo adentro solo cuesta un intento muerto
    # por turno antes de llegar a NVIDIA.
    return pool.is_configured() and not _socket_blocked
