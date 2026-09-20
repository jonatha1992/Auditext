"""Non-Gemini fallback chain (Groq -> NVIDIA), shared by coach and simulator.

One source of truth on purpose. The coach (``interview_live``) and the oral
exam simulator (``interview_simulation``) hit the same three providers for the
same reason — each fails independently — and two hand-written copies of the
order drift: the simulator used to chain Gemini -> NVIDIA only, so every turn
died the day NVIDIA's default model reached end of life.

``interview_live`` is deliberately *not* imported here: pulling it in would drag
websockets/numpy/genai into a plain text call.
"""

from __future__ import annotations

import time
from typing import Any

from config import logger
from infrastructure.services import nvidia_provider

# Below this there is no point starting another provider: the request would be
# cut by its own timeout before the model finished writing.
_MIN_PROVIDER_SECONDS = 4.0
_DEFAULT_PROVIDER_BUDGET_SECONDS = 12.0


class ProviderChainError(RuntimeError):
    """Every configured fallback provider failed for this call."""


def fallback_providers() -> list[tuple[str, Any]]:
    """Configured non-Gemini providers, fastest first.

    Groq leads because it fails for reasons unrelated to NVIDIA's: NVIDIA's
    shared endpoint returns 503 when its worker pool saturates, and that is
    precisely when a second, independent provider earns its place.
    """
    from infrastructure.services import groq_provider

    chain: list[tuple[str, Any]] = []
    if groq_provider.is_configured():
        chain.append(("Groq", groq_provider))
    if nvidia_provider.is_configured():
        chain.append(("NVIDIA", nvidia_provider))
    return chain


def has_fallback_provider() -> bool:
    return bool(fallback_providers())


def generate_with_fallback(
    prompt: str,
    *,
    max_tokens: int = 480,
    system: str | None = None,
    provider_budget_seconds: float = _DEFAULT_PROVIDER_BUDGET_SECONDS,
    deadline: float | None = None,
) -> str:
    """Walk the chain for one plain-text answer, bounded like every provider.

    ``deadline`` is a ``time.monotonic()`` stamp for the whole call; each
    provider gets whatever is left, capped at ``provider_budget_seconds``. A
    chain that runs out of budget raises instead of hanging: in a live UI a
    frozen window is worse than an error the user can retry.
    """
    chain = fallback_providers()
    if not chain:
        raise ProviderChainError("No hay proveedor de respaldo configurado (Groq/NVIDIA)")

    reasons: list[str] = []
    last_exc: BaseException | None = None
    for name, provider in chain:
        budget = provider_budget_seconds
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining < _MIN_PROVIDER_SECONDS:
                reasons.append(f"{name}: sin tiempo restante")
                break
            budget = min(budget, remaining)
        try:
            text = provider.generate(
                prompt,
                max_tokens=max_tokens,
                system=system,
                budget_seconds=budget,
            )
        except Exception as exc:
            last_exc = exc
            reasons.append(f"{name}: {exc}")
            logger.warning("%s falló en la cadena de respaldo: %s", name, exc)
            continue
        if text and text.strip():
            return text
        reasons.append(f"{name}: respuesta vacía")
    message = "Ningún proveedor de respaldo pudo responder — " + "; ".join(reasons)
    raise ProviderChainError(message) from last_exc
