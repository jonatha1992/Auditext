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


def _collect_keys() -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()

    def add(raw: str | None) -> None:
        if not raw:
            return
        for part in raw.replace(";", ",").split(","):
            k = part.strip()
            if k and k not in seen:
                seen.add(k)
                keys.append(k)

    add(os.getenv("GEMINI_API_KEYS"))
    # Canonical names
    add(os.getenv("GEMINI_API_KEY"))
    for i in range(2, 6):
        add(os.getenv(f"GEMINI_API_KEY_{i}"))
    # Also accept GEMINI_API_KEY1 / KEY2 / KEY3 (no underscore) — common typo/variant
    for i in range(1, 6):
        add(os.getenv(f"GEMINI_API_KEY{i}"))
    return keys


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
