"""Gemini API key pool with rotation on quota exhaustion.

Reads keys from .env (never hardcoded):

    GEMINI_API_KEY=...
    GEMINI_API_KEY_2=...
    GEMINI_API_KEY_3=...

Or a single comma-separated line:

    GEMINI_API_KEYS=key1,key2,key3
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

from config import logger

load_dotenv()


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
    """Keys de Gemini. Nombre histórico; la lógica vive en collect_api_keys()."""
    return collect_api_keys("GEMINI_API_KEY")


# Modelos retirados por Google: responden 404 "no longer available" con
# cualquier key. Se filtran en un solo lugar porque llegan desde el .env
# (GEMINI_MODEL) y envenenan varias features a la vez — coach, resumen y
# transcripción apuntaban todas a gemini-2.5-flash.
# Verificado contra models.list el 2026-08-06.
RETIRED_MODELS = frozenset(
    {
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-1.5-flash",
        "gemini-1.5-pro",
        "gemini-1.0-pro",
        "gemini-pro",
    }
)


def usable_models(models) -> list[str]:
    """Deduplicate a model preference list and drop the retired ones.

    Order is preserved: the caller's priority is the whole point. An empty
    result means every configured model is dead, which the caller must handle —
    silently substituting a model would hide a broken .env.
    """
    seen: set[str] = set()
    usable: list[str] = []
    for model in models:
        name = (model or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        if name in RETIRED_MODELS:
            logger.warning(
                "Modelo %s esta retirado por Google (404); se omite. "
                "Actualiza GEMINI_MODEL en .env",
                name,
            )
            continue
        usable.append(name)
    return usable


@dataclass
class KeyPool:
    """Ordered API keys; skip exhausted ones within a run."""

    _keys: list[str] = field(default_factory=_collect_keys)
    _exhausted: set[str] = field(default_factory=set)
    _index: int = 0

    def reload(self) -> None:
        self._keys = _collect_keys()
        self._exhausted.clear()
        self._index = 0

    def is_configured(self) -> bool:
        return bool(self._keys)

    def count(self) -> int:
        return len(self._keys)

    def available(self) -> list[str]:
        return [k for k in self._keys if k not in self._exhausted]

    def current_label(self) -> str:
        """Human label like 'API 1/3' (never exposes the key)."""
        total = self.count()
        if not total:
            return "API 0/0"
        avail = self.available()
        if not avail:
            return f"API agotadas 0/{total}"
        # Position among all keys of the current one
        cur = avail[0]
        pos = self._keys.index(cur) + 1
        return f"API {pos}/{total}"

    def current(self) -> str | None:
        for k in self._keys[self._index :] + self._keys[: self._index]:
            if k not in self._exhausted:
                self._index = self._keys.index(k)
                return k
        return None

    def mark_exhausted(self, key: str | None = None) -> str | None:
        """Mark key exhausted; return next available key or None."""
        k = key or self.current()
        if k:
            self._exhausted.add(k)
            logger.warning("Gemini key marcada agotada (%s)", self.current_label())
        return self.current()

    def is_quota_error(self, exc: BaseException) -> bool:
        detail = str(exc).lower()
        return (
            "429" in detail
            or "quota" in detail
            or "resource_exhausted" in detail
            or "rate limit" in detail
            or "rate_limit" in detail
        )


# Process-wide pool (reloadable).
pool = KeyPool()


def is_configured() -> bool:
    return pool.is_configured()


def load_keys() -> list[str]:
    pool.reload()
    return list(pool._keys)
