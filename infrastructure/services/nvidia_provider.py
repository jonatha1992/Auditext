"""NVIDIA NIM text generation with API-key rotation.

Latency contract: this provider runs inside a live conversation, so a call that
takes longer than the user is willing to wait is worth less than no call at all.
Every request is therefore bounded twice — a per-request socket timeout and a
total budget across key rotation — and failures are classified so a transient
saturation error does not park a key for half a minute.
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

load_dotenv()

NVIDIA_BASE_URL = os.getenv(
    "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"
).rstrip("/")
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "").strip() or "nvidia/nemotron-3-nano-30b-a3b"

# Socket timeout for a small request. Non-streaming responses arrive in one go,
# so a long answer needs proportionally more room than a short one.
NVIDIA_REQUEST_TIMEOUT_SECONDS = 20
NVIDIA_MAX_REQUEST_TIMEOUT_SECONDS = 40

# Hard ceiling for a whole generate() call, key rotation and retries included.
# This is the value that stops the app from appearing frozen: whatever happens
# upstream, the coach gets an answer or an error back within this window.
NVIDIA_TOTAL_BUDGET_SECONDS = float(
    os.getenv("NVIDIA_TOTAL_BUDGET_SECONDS", "").strip() or 45.0
)
# Below this there is no point starting another HTTP request.
_MIN_ATTEMPT_SECONDS = 3.0

# Cooldowns per failure class. The old flat 30 s was the bug: NVIDIA's
# "Worker local total request limit reached" clears in seconds, so parking the
# only configured key for 30 s turned one transient 503 into half a minute of
# dead app.
_COOLDOWN_SATURATION = 6.0
_COOLDOWN_RATE_LIMIT = 45.0
_COOLDOWN_AUTH = 600.0
_COOLDOWN_DEFAULT = 30.0

# Pause before re-trying a saturated endpoint, when the budget allows it.
_SATURATION_BACKOFF_SECONDS = 1.5


# ─── Colector canónico de API keys ────────────────────────────────────────
# Este bloque es IDÉNTICO en todos los repos (Auditext, anotador, spinpredictor,
# matchanalyzer, mis_gastos_tracker). Si se toca acá, se replica en los demás.
#
# Unifica cuatro comportamientos que antes estaban repartidos y desparejos:
#   1. Limpia BOM (U+FEFF) y comillas. Bug real de agosto 2026: una key pegada
#      en Railway traía BOM y httpx reventaba con UnicodeEncodeError al armar
#      el header x-goog-api-key. Sólo matchanalyzer lo contemplaba.
#   2. Llega hasta la key N°20. Antes Auditext y anotador cortaban en la 5 y
#      cualquier key nueva se ignoraba sin log ni error.
#   3. Acepta lista en una línea: PREFIJO+S separado por comas o punto y coma
#      (GEMINI_API_KEYS=k1,k2,k3), cómodo para deploys tipo Railway.
#   4. Descarta placeholders del .env.example para que un archivo sin completar
#      falle con "no hay key" y no con un 401 críptico.

_KEY_LIMIT = 20


def _looks_like_placeholder(value: str) -> bool:
    low = value.lower()
    if low.startswith(("your", "tu_", "tu-", "<")):
        return True
    return any(
        marker in low
        for marker in ("api-key-here", "api_key_here", "xxxxxxxx", "changeme", "replace-me")
    )


def _sanitize_api_key(raw):
    """Normaliza una key suelta; devuelve None si no sirve."""
    if not raw:
        return None
    cleaned = str(raw).strip().strip("\ufeff").strip().strip('"').strip("'").strip()
    if not cleaned or _looks_like_placeholder(cleaned):
        return None
    return cleaned


def collect_api_keys(prefix: str, limit: int = _KEY_LIMIT) -> list:
    """
    Junta todas las keys de un proveedor desde el entorno.

    Lee, en este orden y deduplicando por valor:
        PREFIJO+S  (lista separada por comas o ';')
        PREFIJO
        PREFIJO1 .. PREFIJO{limit}      y      PREFIJO_1 .. PREFIJO_{limit}

    Ej. collect_api_keys("GEMINI_API_KEY") lee GEMINI_API_KEYS, GEMINI_API_KEY,
    GEMINI_API_KEY1..20 y GEMINI_API_KEY_1..20.
    """
    keys = []
    seen = set()

    def add(raw) -> None:
        if not raw:
            return
        for part in str(raw).replace(";", ",").split(","):
            key = _sanitize_api_key(part)
            if key and key not in seen:
                seen.add(key)
                keys.append(key)

    add(os.getenv(f"{prefix}S"))
    add(os.getenv(prefix))
    for n in range(1, limit + 1):
        add(os.getenv(f"{prefix}{n}"))
        add(os.getenv(f"{prefix}_{n}"))
    return keys
# ─── fin del colector canónico ────────────────────────────────────────────


def _collect_keys() -> list[str]:
    """Keys de NVIDIA. Nombre histórico; la lógica vive en collect_api_keys()."""
    return collect_api_keys("NVIDIA_API_KEY")


@dataclass
class NvidiaKeyPool:
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
        """Wait needed before any key comes back, or 0 if one is ready now."""
        if not self._keys:
            return 0.0
        if self.available():
            return 0.0
        now = time.monotonic()
        deadlines = [
            self._unavailable_until[key]
            for key in self._keys
            if key in self._unavailable_until
        ]
        if not deadlines:
            return 0.0
        return max(0.0, min(deadlines) - now)

    def label(self, key: str | None = None) -> str:
        total = self.count()
        if key in self._keys:
            return f"NVIDIA {self._keys.index(key) + 1}/{total}"
        return f"NVIDIA 0/{total}"

    def mark_unavailable(self, key: str, cooldown_seconds: float = 30.0) -> None:
        self._unavailable_until[key] = time.monotonic() + cooldown_seconds
        logger.warning(
            "%s pausada %.0f segundos antes de reintentar",
            self.label(key),
            cooldown_seconds,
        )

    def clear_cooldown(self, key: str) -> None:
        self._unavailable_until.pop(key, None)


pool = NvidiaKeyPool()


class NvidiaError(RuntimeError):
    pass


class NvidiaBudgetExceeded(NvidiaError):
    """The total time budget ran out before any key produced an answer."""


# Failure classes. The string is what decides the cooldown and whether another
# attempt is worth the remaining budget.
SATURATION = "saturacion"
RATE_LIMIT = "cuota"
AUTH = "auth"
NETWORK = "red"
PERMANENT = "permanente"

_SATURATION_MARKERS = (
    "worker local total request limit",
    "resourceexhausted",
    "503",
    "502",
    "504",
    "overloaded",
    "capacity",
)
_RATE_LIMIT_MARKERS = ("429", "rate limit", "rate_limit", "quota", "too many requests")
_AUTH_MARKERS = ("401", "402", "403", "credit", "invalid api key", "unauthorized")
_NETWORK_MARKERS = (
    "timed out", "timeout", "408", "connection", "conexión", "conexion",
    "temporarily unavailable", "reset by peer", "500",
)


def classify_error(exc: BaseException) -> str:
    """Bucket a failure so the caller knows how long to back off."""
    detail = str(exc).lower()
    for marker in _SATURATION_MARKERS:
        if marker in detail:
            return SATURATION
    for marker in _RATE_LIMIT_MARKERS:
        if marker in detail:
            return RATE_LIMIT
    for marker in _AUTH_MARKERS:
        if marker in detail:
            return AUTH
    for marker in _NETWORK_MARKERS:
        if marker in detail:
            return NETWORK
    return PERMANENT


_COOLDOWNS = {
    SATURATION: _COOLDOWN_SATURATION,
    RATE_LIMIT: _COOLDOWN_RATE_LIMIT,
    AUTH: _COOLDOWN_AUTH,
    NETWORK: _COOLDOWN_DEFAULT,
}


def cooldown_for(kind: str) -> float:
    return _COOLDOWNS.get(kind, _COOLDOWN_DEFAULT)


def is_retryable_error(exc: BaseException) -> bool:
    """True when another key (or another try) can still succeed."""
    return classify_error(exc) != PERMANENT


def request_timeout_for(max_tokens: int) -> float:
    """Scale the socket timeout with the size of the requested answer.

    `stream: False` means nothing arrives until the model finished writing, so a
    1200-token answer legitimately needs longer than a 270-token one. Using the
    short timeout for both is what turned long resolver answers into a parade of
    timeouts that then marked every key unavailable.
    """
    extra = max(0, max_tokens - 300) / 300.0 * 6.0
    return min(
        NVIDIA_MAX_REQUEST_TIMEOUT_SECONDS,
        NVIDIA_REQUEST_TIMEOUT_SECONDS + extra,
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
    timeout: float | None = None,
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
        with urllib.request.urlopen(
            request, timeout=timeout or NVIDIA_REQUEST_TIMEOUT_SECONDS
        ) as response:
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
    budget_seconds: float | None = None,
) -> str:
    """Generate text, rotating through every configured NVIDIA key.

    `history` carries prior chat turns as {"role": "user"|"assistant", "content": ...}.
    `budget_seconds` caps the whole call — rotation and retries included — so a
    saturated endpoint costs a bounded wait instead of freezing the session.
    """
    if not pool.is_configured():
        raise NvidiaError("No hay claves NVIDIA configuradas")

    budget = budget_seconds if budget_seconds is not None else NVIDIA_TOTAL_BUDGET_SECONDS
    deadline = time.monotonic() + budget
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
            # Every key is cooling down. Waiting is only worth it when the
            # cooldown is shorter than what is left of the budget.
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
                    NVIDIA_MODEL,
                    (time.monotonic() - started) * 1000.0,
                    attempts,
                )
                return text
            except Exception as exc:
                last_exc = exc
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
                    raise NvidiaError(str(exc)) from exc
                pool.mark_unavailable(key, cooldown_for(kind))

        if not progressed:
            break
        # Only saturation is worth a second sweep: the endpoint frees workers in
        # seconds, whereas quota and auth failures will fail identically.
        if not saturated_only:
            break
        remaining = deadline - time.monotonic()
        if remaining < _MIN_ATTEMPT_SECONDS + _SATURATION_BACKOFF_SECONDS:
            break
        time.sleep(_SATURATION_BACKOFF_SECONDS)

    elapsed = budget - max(0.0, deadline - time.monotonic())
    if last_exc is None:
        raise NvidiaBudgetExceeded(
            f"NVIDIA sin claves disponibles dentro de {budget:.0f}s"
        )
    message = (
        f"Todas las claves NVIDIA fallaron tras {attempts} intento(s) "
        f"en {elapsed:.0f}s: {last_exc}"
    )
    if attempts == 0:
        raise NvidiaBudgetExceeded(message) from last_exc
    raise NvidiaError(message) from last_exc


def is_configured() -> bool:
    return pool.is_configured()
