"""Gemini Live interview coach — realtime system-audio assist.

Live API models only support AUDIO response modality. We:
1. Stream PCM into Live for low-latency input transcription (interviewer EN).
2. Discard model audio (never play it — interviewer must not hear the coach).
3. On each interviewer turn, call generate_content (flash) for Spanish gloss +
   1-2 short English reply suggestions (first is the priority) as JSON.

Keys: GEMINI_API_KEY / _2 / _3 or GEMINI_API_KEY1/2/3.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from config import logger
from infrastructure.services import gemini_keys, latency_log, nvidia_provider

LIVE_MODELS = [
    os.getenv("GEMINI_LIVE_MODEL", "").strip() or "gemini-3.1-flash-live-preview",
    "gemini-2.5-flash-native-audio-latest",
    "gemini-2.5-flash-native-audio-preview-12-2025",
    "gemini-2.5-flash-native-audio-preview-09-2025",
]

# Latency-sensitive: lite models first, thinking disabled (see _coach_config).
# Latency-sensitive, ordered by measured time-to-answer on this account
# (2026-08-06). The 2.5 family was removed: `gemini-2.5-flash-lite` and
# `gemini-2.5-flash` now answer 404 "no longer available" on every key, so
# keeping them first meant every session opened with two dead round-trips.
# 3.1-flash-lite leads because it is the only current lite model that still
# accepts thinking_budget=0. Measured end to end on the resolver prompt:
# 3.1-flash-lite 5.3 s vs 3.5-flash-lite 6.7 s — the newer model has to think
# before answering, and that thinking lands entirely in the wait the user feels.
COACH_MODELS = gemini_keys.usable_models(
    [
        os.getenv("GEMINI_COACH_MODEL", "").strip() or "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
        "gemini-flash-lite-latest",
        os.getenv("GEMINI_MODEL", "").strip() or "gemini-3.6-flash",
        "gemini-2.0-flash-lite",
    ]
)

_LIVE_SYSTEM = """You are listening to a speaker via system audio.
Transcribe the speaker faithfully in the language they use; never translate their words.
Stay silent / minimal. Do not coach out loud. The client app will handle coaching separately.
Acknowledge briefly only if needed; prefer no spoken reply.
"""

DEFAULT_ASSIST_MODE = "entrevista"
ORAL_ASSIST_MODES = frozenset({"examen_oral", "prueba_oral", "resolver"})

# Language the coach must answer in. The app started out English-only (job
# interviews), but an oral exam is usually taken in Spanish, so the language is
# now an explicit choice instead of being baked into the prompt.
DEFAULT_ANSWER_LANG = "en"
ANSWER_LANGS = {
    "en": "SIEMPRE en inglés",
    "es": "SIEMPRE en español",
    "auto": "en el mismo idioma en que habla el INTERLOCUTOR",
}


def resolve_answer_lang(mode: str, answer_lang: str | None) -> str:
    """Return the effective answer language for an assistance mode."""
    if mode in ORAL_ASSIST_MODES:
        return "es"
    return answer_lang if answer_lang in ANSWER_LANGS else DEFAULT_ANSWER_LANG

_MODE_INSTRUCTIONS = {
    "entrevista": (
        "Sos coach de entrevista laboral para un candidato hispanohablante. "
        "El INTERLOCUTOR es el entrevistador. Las respuestas deben ayudar al candidato "
        "a impresionar: profesionales, concretas, apoyadas en su CV/contexto. "
        "ideas_clave: puntos del CV o de la experiencia del candidato relevantes al turno."
    ),
    "practica": (
        "Sos asistente de práctica de idioma para un hispanohablante que practica inglés. "
        "El INTERLOCUTOR puede ser un profesor, tutor o compañero de práctica. "
        "Si pide un ejercicio o ejemplo (p. ej. 'try saying that'), las respuestas deben CUMPLIR "
        "el ejercicio con frases naturales. Si pregunta algo, las respuestas la contestan. "
        "ideas_clave: tips breves de vocabulario, gramática o pronunciación relevantes al turno."
    ),
    "general": (
        "Sos copiloto de conversación para un hispanohablante que conversa en inglés. "
        "El INTERLOCUTOR puede ser cualquiera (reunión, llamada, charla). "
        "Las respuestas deben ser naturales, adecuadas al contexto pegado por el usuario. "
        "ideas_clave: puntos del contexto o de la conversación útiles para el próximo turno."
    ),
    "examen_oral": (
        "Sos asistente de examen oral para un hispanohablante. El INTERLOCUTOR hace de "
        "examinador y el contexto pegado es el temario o cronograma de la materia. "
        "Las respuestas deben ser COMPLETAS y listas para decir en voz alta: correctas, "
        "concretas y apoyadas en el temario. "
        "Priorizá el temario para preguntas específicas del trabajo práctico. "
        "Si la pregunta es clara y trata un concepto académico estándar (por ejemplo UML, "
        "diagramas, arquitectura o patrones), respondé también con conocimiento general "
        "correcto aunque la definición no figure literalmente en el contexto. "
        "No inventes definiciones únicamente cuando el término sea dudoso o parezca mal transcripto. "
        "Si la transcripción parece cortada o corrupta, indicá que no se entendió la pregunta. "
        "ideas_clave: conceptos del temario que sostienen la respuesta, por si el "
        "examinador repregunta."
    ),
    "prueba_oral": (
        "Sos asistente de PRÁCTICA para pruebas orales. El usuario estudia respondiendo en voz alta; "
        "el INTERLOCUTOR hace de examinador. El contexto pegado es el temario o material de estudio. "
        "REGLA CENTRAL: NO des respuestas completas — el usuario debe formular con sus palabras. "
        "ideas_clave es el campo principal: 3 o 4 conceptos o palabras clave del temario que responden "
        "la pregunta, ordenados como esqueleto de respuesta. "
        "La respuesta corta es SOLO un arranque de frase (máximo 6 palabras, terminado en ...), "
        "p. ej. 'El concepto central es...' o 'The main idea is...'. "
        "La respuesta ampliada es el esqueleto en 3 pasos de lo que conviene decir "
        "(p. ej. '1) definí X. 2) nombrá sus partes. 3) cerrá con un ejemplo.'), "
        "nunca la respuesta ya redactada. "
        "pregunta_es: glosa clara de la pregunta del examinador."
    ),
    "resolver": (
        "Sos un asistente académico que RESUELVE preguntas en español. "
        "El INTERLOCUTOR formula una pregunta y el contexto pegado contiene el tema, "
        "apuntes o material de consulta. Contestá exactamente lo preguntado, sin convertir "
        "la consulta en una entrevista ni en un ejercicio de práctica. "
        "La primera respuesta debe ser COMPLETA, directa, correcta y lista para decir en voz alta. "
        "Si el material no alcanza, usá conocimiento general solo cuando la pregunta sea clara. "
        "No inventes términos, definiciones ni relaciones ausentes de la pregunta o el contexto. "
        "Si la transcripción parece incompleta, contradictoria o contiene un término desconocido, "
        "respondé que no se entendió la pregunta y que debe repetirse. "
        "ideas_clave: 3 conceptos concretos que justifican la respuesta."
    ),
}

# Coach input is truncated before prompting: with thinking_budget=0 the cost is
# dominated by TTFT/prefill (input length), so a shorter prompt is the main lever
# for lowering latency (see tech plan Path A).
_MAX_CONTEXT_CHARS = 1800
_COACH_HISTORY_TURNS = 4

# El usuario habla en voz alta con sus propias palabras: un texto correcto pero
# enciclopédico no le sirve. Estas reglas van en TODOS los modos.
_PLAIN_LANGUAGE_RULES = (
    "REGISTRO: escribí como habla un estudiante frente a su profesor, no como un manual ni como "
    "un coach. Frases cortas, directas y naturales. Respondé en primera persona cuando corresponda. "
    "Conservá el término técnico solo cuando ese término es lo que se pregunta o no tiene "
    "equivalente común; todo lo demás explicalo con palabras de todos los días. "
    "Nada de 'cabe destacar', 'en el ámbito de', enumeraciones ni definiciones de diccionario."
)

# A es el ejemplo, no el resumen. Reformular R con otras palabras hacía que las
# dos cajas dijeran lo mismo, que es exactamente lo que el usuario no quiere.
_EXAMPLE_RULES = (
    "A NO vuelve a explicar ni reformula lo que ya dice R: eso sería repetir. "
    "A son DOS ejemplos y nada más, arrancando directo con 'Por ejemplo,' y después 'Otro caso:'. "
    "El primero, el caso típico. El segundo, un caso límite o una excepción que se preste a "
    "confusión. Matemático: resueltos con números hasta el resultado. Conceptual: situaciones "
    "puntuales, no genéricas. Sin definiciones, sin introducción y sin cierre. "
    "Máximo 25 palabras por ejemplo y 60 en total."
)

_DEFAULT_RESPONSE_RULES = (
    "R: una sola oración, máximo 25 palabras, lista para decir ya. "
    "A: NO reformula R. Es un solo ejemplo concreto, arrancando con 'Por ejemplo,', "
    "máximo 40 palabras. Sin introducciones, consejos ni información lateral."
)
# Este modo existe para que el usuario formule con sus palabras, así que no
# hereda las reglas por defecto: un ejemplo resuelto le entregaría la respuesta.
_PRACTICE_RESPONSE_RULES = (
    "R: SOLO un arranque de frase, máximo 6 palabras, terminado en puntos suspensivos. "
    "A: el esqueleto de la respuesta en 3 pasos numerados, diciendo QUÉ mencionar en cada uno, "
    "nunca el contenido ya redactado. Ningún ejemplo resuelto: la respuesta la arma el usuario."
)
_RESOLVER_RESPONSE_RULES = (
    "R: respuesta directa en 1 a 3 oraciones, entre 20 y 55 palabras, lista para decir como estudiante. "
    "La primera oración da la conclusión concreta; la siguiente aporta el criterio técnico decisivo. "
    "Cada oración agrega información distinta. No anuncies que vas a responder, no reformules la pregunta, "
    "no uses elogios ni afirmaciones vagas. Sin introducción, cierre ni relleno. "
    "Si comparan conceptos, nombrá la variable que realmente cambia. "
    "A: agregá entre 20 y 60 palabras con detalles que no repitan R. Si ayudan, incluí uno o dos "
    "ejemplos marcados con 'Por ejemplo,' y 'Otro caso:' para mostrarlos como viñetas. "
    "No escribas el rótulo 'Si el profesor pide más' ni anticipes temas que no preguntó."
)
_EXAM_RESPONSE_RULES = (
    "R: respuesta directa en 1 a 3 oraciones, entre 20 y 55 palabras, como estudiante frente al profesor. "
    "La primera oración da la conclusión concreta; la siguiente aporta el criterio técnico decisivo. "
    "Cada oración agrega información distinta. No anuncies que vas a responder, no reformules la pregunta, "
    "no uses elogios ni afirmaciones vagas. Sin introducción, cierre ni relleno. "
    "Si comparan conceptos, nombrá la variable que realmente cambia. "
    "A: agregá entre 20 y 60 palabras con detalles o un ejemplo concreto para una posible repregunta, "
    "sin repetir R. Si hay varios ejemplos, marcá 'Por ejemplo,' y 'Otro caso:' para crear viñetas. "
    "No escribas el rótulo 'Si el profesor pide más'. No hables como coach ni des consejos al alumno, "
    "no inventes una nueva pregunta y no atribuyas decisiones al trabajo práctico sin contexto."
)


def _truncate_context(context: str) -> str:
    """Clamp pasted context (CV, notes, temario) to keep the coach prompt short."""
    ctx = (context or "").strip()
    if len(ctx) <= _MAX_CONTEXT_CHARS:
        return ctx
    return ctx[:_MAX_CONTEXT_CHARS].rstrip() + " […]"


def _select_context(context: str, utterance: str) -> str:
    """Fit the pasted context into the prompt budget, keeping what's relevant.

    A full-course study guide does not fit in _MAX_CONTEXT_CHARS, so we retrieve
    the sections matching the examiner's question instead of head-truncating —
    otherwise every question past unit one would be answered without its
    material. Retrieval must never break the coach, so any failure degrades to
    the previous truncation behaviour.
    """
    ctx = (context or "").strip()
    if len(ctx) <= _MAX_CONTEXT_CHARS:
        return ctx
    try:
        from infrastructure.services.temario_index import select_context

        selected = select_context(ctx, utterance, _MAX_CONTEXT_CHARS)
    except Exception as exc:
        logger.exception("Context retrieval failed, truncating instead: %s", exc)
        return _truncate_context(ctx)
    return selected or _truncate_context(ctx)


_COACH_PROMPT = """{role_instructions}

Contexto pegado por el usuario (CV, puesto, tema de práctica, apuntes...):
---
{context}
---

Texto reciente del INTERLOCUTOR (STT en su idioma original, puede tener ruido):
---
{utterance}
---

Conversación reciente, separada por rol:
---
{history}
---

IDIOMA DE LAS RESPUESTAS: escribí las líneas R y A {answer_lang}.
Las líneas P e I van siempre en español.
No mezcles idiomas ni uses palabras de otro idioma parecido: se escribe "ecuación",
nunca "equação"; "función", nunca "função". Si dudás, usá la palabra más común del idioma pedido.

Todo lo que escribas se dice EN VOZ ALTA y se lee tal cual: nada de LaTeX, markdown ni
símbolos sueltos. Prohibido usar $, asteriscos, guiones bajos, comillas invertidas y barras
invertidas. Las variables y fórmulas van en palabras: "el máximo común divisor de a y b tiene
que dividir a c", nunca su versión simbólica entre signos de dólar.

Esto NO significa evitar la matemática: si la pregunta pide una cuenta, resolvela y dá el
resultado. Solo escribilo como se pronuncia. Las fracciones son "tres cuartos" o "tres sobre
cuatro"; las potencias, "x al cuadrado"; las raíces, "raíz de dos"; los subíndices, "a sub uno".

Respondé EXACTAMENTE en estas cuatro líneas, sin markdown, sin JSON y sin texto extra:
R: la respuesta corta, lista para decir en voz alta ya mismo
A: información adicional y ejemplos para continuar si piden más
P: glosa clara en español de lo que dijo o pidió el interlocutor
I: 2 o 3 ideas relevantes separadas por punto y coma

La línea R va PRIMERA y es la más importante: se muestra en pantalla apenas llega,
antes de que termines de escribir las otras. Nunca la dejes para el final.
A no repite ni reformula R con otras palabras: agrega profundidad útil sobre el mismo tema.
Su contenido y largo los fijan las reglas de salida de más abajo. Tampoco cambies de tema en A.

{plain_language_rules}

Reglas específicas de salida:
{response_rules}
En modos de examen o resolución, respondé SOLO la pregunta o consigna concreta.
Ignorá felicitaciones, muletillas, respuestas del alumno y comentarios sin una consigna.
Si no hay una pregunta clara o el texto es ininteligible, devolvé únicamente "R:" sin contenido.
Nunca completes por imaginación una palabra cortada ni definas un término dudoso.
Usá el historial para mantener el hilo. Si el INTERLOCUTOR hace una pregunta repetida,
respondela de nuevo: puede estar reforzándola porque la respuesta anterior fue insuficiente.
En ese caso agregá más profundidad, corregí el enfoque o explicalo desde otro ángulo.
La pregunta actual tiene prioridad absoluta. Usá el historial solo para resolver pronombres o
referencias explícitas, nunca reemplazar su tema por un tema anterior. Si la pregunta actual
nombra "diagrama de actividad", la respuesta debe tratar ese concepto aunque el historial hable de otro.
Priorizá frases fáciles de pronunciar.
"""


@dataclass
class InterviewAssist:
    pregunta_es: str
    respuestas: list[str]
    ideas_clave: list[str] | None = None
    frase_puente: str = ""
    provider: str = "Gemini"
    # True while the model is still streaming: the UI must not overwrite a field
    # that is already filled with the empty value of a field that has not
    # arrived yet (the answer line lands before the key ideas).
    partial: bool = False


# Minimum spoken depth for a long-form answer, in words. Raised from 70/100:
# the models kept returning two-line answers that technically passed the old
# floor, which is exactly the "too short" complaint.
_MIN_ANSWER_WORDS = 20
_MIN_EXPANSION_WORDS = 20

# Total time a NVIDIA-backed turn may spend, first attempt plus repair. Past
# this the user is better served by a short answer now than a long one later.
_NVIDIA_TURN_BUDGET_SECONDS = 55.0
# Below this there is no room left for a full second generation.
_NVIDIA_REPAIR_MIN_SECONDS = 22.0

_LONG_FORM_MODES = frozenset({"resolver", "examen_oral"})


def _has_required_answers(assist: InterviewAssist, mode: str) -> bool:
    """Require useful spoken depth, not merely non-empty answer cards."""
    answers = [text for text in assist.respuestas if (text or "").strip()]
    if mode not in _LONG_FORM_MODES:
        return bool(answers)
    if len(answers) < 2:
        return False
    return (
        len(answers[0].split()) >= _MIN_ANSWER_WORDS
        and len(answers[1].split()) >= _MIN_EXPANSION_WORDS
    )


def _nvidia_token_budget(mode: str) -> int:
    # Concise oral answers need room for R/A/P/I without inviting essay-length output.
    return 700 if mode in _LONG_FORM_MODES else 270


def _generate_nvidia_assist(prompt: str, mode: str, provider=None) -> InterviewAssist | None:
    """Generate once, then repair output that is present but too shallow.

    The repair is opportunistic: it runs only if the shared turn budget still
    allows a full second generation, and a failing repair never discards the
    answer we already have — losing a short answer to a repair timeout is
    strictly worse than showing the short answer.

    `provider` is any module exposing ``generate(prompt, max_tokens=...,
    budget_seconds=...)`` — NVIDIA and Groq speak the same OpenAI-compatible
    contract, so the repair logic is shared instead of duplicated.
    """
    provider = provider or nvidia_provider
    deadline = time.monotonic() + _NVIDIA_TURN_BUDGET_SECONDS
    max_tokens = _nvidia_token_budget(mode)
    assist = parse_assist(
        provider.generate(
            prompt,
            max_tokens=max_tokens,
            budget_seconds=_NVIDIA_TURN_BUDGET_SECONDS,
        )
    )
    if assist is None or _has_required_answers(assist, mode):
        return assist

    remaining = deadline - time.monotonic()
    if remaining < _NVIDIA_REPAIR_MIN_SECONDS:
        logger.info(
            "Sin presupuesto para reintentar la ampliación NVIDIA (%.0f s restantes)",
            remaining,
        )
        return assist

    previous = "\n".join(
        f"{'R' if index == 0 else 'A'}: {text}"
        for index, text in enumerate(assist.respuestas[:2])
    )
    repair_prompt = (
        prompt
        + "\n\nLa salida anterior fue demasiado breve y no cumple los mínimos.\n"
        + previous
        + f"\nReescribí las cuatro líneas completas. R debe tener al menos "
        f"{_MIN_ANSWER_WORDS} palabras; A debe tener al menos {_MIN_EXPANSION_WORDS} "
        "palabras, aportar información nueva y mantener el tono de estudiante."
    )
    try:
        repaired = parse_assist(
            provider.generate(
                repair_prompt,
                max_tokens=max_tokens,
                budget_seconds=remaining,
            )
        )
    except Exception as exc:
        logger.warning("Reintento de ampliación falló, se usa la original: %s", exc)
        return assist
    if repaired is None:
        return assist
    if _has_required_answers(repaired, mode):
        return repaired
    original_words = sum(len(text.split()) for text in assist.respuestas)
    repaired_words = sum(len(text.split()) for text in repaired.respuestas)
    return repaired if repaired_words > original_words else assist


class InterviewLiveError(Exception):
    """Live session cannot start or all keys/models failed."""


class CoachUnavailable(Exception):
    """No provider could answer this turn. Carries a user-facing reason.

    A turn that dies with `assist=none` and nothing on screen is the worst
    outcome: the user keeps waiting for an answer that will never arrive. This
    exception makes the reason reach the status bar.
    """


def _provider_reason(name: str, exc: BaseException) -> str:
    """Translate a provider failure into something the user can act on."""
    kind = nvidia_provider.classify_error(exc)
    if kind == nvidia_provider.SATURATION:
        return f"{name} saturado ahora mismo; repetí la pregunta en unos segundos"
    if kind == nvidia_provider.RATE_LIMIT:
        return f"{name} sin cuota; esperá o sumá otra clave en .env"
    if kind == nvidia_provider.AUTH:
        return f"Clave {name} rechazada; revisá el .env"
    if kind == nvidia_provider.NETWORK:
        return f"Sin respuesta de {name} (red o timeout); reintentá la pregunta"
    return f"{name} no pudo responder este turno"


def _fallback_providers() -> list[tuple[str, object]]:
    """Configured non-Gemini providers, fastest first.

    Groq leads because it fails for reasons unrelated to NVIDIA's: NVIDIA's
    shared endpoint returns 503 when its worker pool saturates, and that is
    precisely when a second, independent provider earns its place.
    """
    from infrastructure.services import groq_provider

    chain: list[tuple[str, object]] = []
    if groq_provider.is_configured():
        chain.append(("Groq", groq_provider))
    if nvidia_provider.is_configured():
        chain.append(("NVIDIA", nvidia_provider))
    return chain


def has_fallback_provider() -> bool:
    return bool(_fallback_providers())


def _fallback_assist(prompt: str, mode: str, origen: str) -> InterviewAssist | None:
    """Walk the fallback chain, journaling failures instead of swallowing them."""
    from core.errors import record

    chain = _fallback_providers()
    if not chain:
        raise CoachUnavailable("No hay proveedor de respaldo configurado (Groq/NVIDIA)")

    reason = ""
    last_exc: BaseException | None = None
    for name, provider in chain:
        try:
            assist = _generate_nvidia_assist(prompt, mode, provider)
        except Exception as exc:
            last_exc = exc
            reason = _provider_reason(name, exc)
            record(
                f"coach.{name.lower()}.{origen}",
                exc,
                user_msg=reason,
                modo=mode,
                clase=nvidia_provider.classify_error(exc),
            )
            continue
        if assist is not None:
            assist.provider = name
            return assist
    if last_exc is not None:
        raise CoachUnavailable(reason) from last_exc
    return None


def float32_to_pcm16(audio: np.ndarray) -> bytes:
    """Convert mono float32 [-1,1] to little-endian 16-bit PCM."""
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    pcm = np.clip(audio.astype(np.float32) * 32767.0, -32768, 32767).astype(np.int16)
    return pcm.tobytes()


def merge_transcript_delta(current: str, chunk: str) -> str:
    """Join streaming STT deltas without inventing word boundaries.

    Gemini can split a word across events (``mero`` + ``s``). Leading spaces
    carried by the API already identify real word boundaries, so stripping each
    delta and inserting our own space corrupts otherwise correct Spanish.
    """
    raw = chunk or ""
    if not raw.strip():
        return current
    return current + raw if current else raw.lstrip()


# Un examinador pide tanto con pregunta ("¿qué es...?") como con consigna
# ("Defina bucle y vértice aislado."). Sin la segunda forma el turno se
# descartaba en silencio y el coach nunca respondía.
_ORAL_COMMAND_STEMS = (
    "explic", "describ", "defin", "mencion", "justific", "compar",
    "analiz", "desarroll", "enumer", "nombr", "indic", "señal", "senal",
    "diferenci", "distingu", "relacion", "ejemplific", "caracteriz",
    "clasific", "argument", "fundament", "resolv", "calcul", "demostr",
    "plante", "detall", "ampli", "profundiz", "cont", r"cu[eé]nt", "habl",
    "decim", "dec", r"d[ií]g", "resuelv", "pens", "piens",
)
# Terminación de imperativo/subjuntivo (voseo, tuteo y usted) más el pronombre
# pegado que usa el habla real: "definime", "contame", "explicanos".
_ORAL_COMMAND_RE = (
    r"(?:" + "|".join(_ORAL_COMMAND_STEMS) + r")"
    r"(?:[aáeéií]|(?:ar|er|ir))(?:me|nos|lo|la|los|las|le|les)?"
)

_QUESTION_START = re.compile(
    r"^\s*¿?\s*(?:"
    r"qu[eé]|por\s+qu[eé]|para\s+qu[eé]|c[oó]mo|cu[aá]l(?:es)?|cu[aá]ndo|d[oó]nde|"
    r"qui[eé]n(?:es)?|cu[aá]nt[oa]s?|"
    + _ORAL_COMMAND_RE +
    r")\b",
    re.IGNORECASE,
)

# Las confirmaciones de borde heredan el signo de pregunta, pero no convierten
# la afirmación vecina en una pregunta. Se quitan antes de buscar una consigna.
_CONFIRMATION_TAG = (
    r"(?:no|verdad|cierto|s[ií]|ok|viste|entend[eé]s|se\s+entiende|"
    r"me\s+explico|correcto|vale)"
)
_LEADING_CONFIRMATION_TAG = re.compile(
    r"^\s*¿\s*" + _CONFIRMATION_TAG + r"\s*\?\s*", re.IGNORECASE
)
_TRAILING_CONFIRMATION_TAG = re.compile(
    r"\s*,?\s*¿\s*" + _CONFIRMATION_TAG + r"\s*\?\s*$", re.IGNORECASE
)
_LEADING_DISCOURSE = re.compile(
    r"^\s*(?:bueno(?:\s+a\s+ver)?|bien|y\s+bueno|eh|este|a\s+ver|"
    r"entonces|ahora|est[aá]\s+bien|mir[aá])\s*[,.:;!?-]*\s*",
    re.IGNORECASE,
)
_ORAL_COMMAND_START = re.compile(
    r"^\s*(?:" + _ORAL_COMMAND_RE + r")\b", re.IGNORECASE
)
_INDIRECT_COMMAND_START = re.compile(
    r"^\s*(?:necesito|quiero|quisiera|te\s+pido)(?:\s+que)?\s+"
    r"(?:me\s+)?(?:explic|describ|defin|mencion|justific|compar|analiz|desarroll|"
    r"enumer|nombr|indic|diferenci|relacion|ejemplific|resolv|calcul|demostr)\w*\b",
    re.IGNORECASE,
)
_POLITE_REQUEST = re.compile(
    r"^\s*(?:(?:a\s+ver\s+si\s+)?(?:me\s+)?(?:pod[eé]s|puede|podr[ií]as?)\s+"
    r"(?:explic|dec|cont|describ|compar)\w*|(?:quiero|quisiera)\s+saber\b|"
    r"necesito\s+(?:una\s+)?(?:comparaci[oó]n|explicaci[oó]n|definici[oó]n)\b)",
    re.IGNORECASE,
)
_EMBEDDED_COMMAND = re.compile(
    r"(?:^|:\s+|a\s+ver\s+si\s+)(?:" + _ORAL_COMMAND_RE + r")\b",
    re.IGNORECASE,
)
_EMBEDDED_QUESTION = re.compile(
    r"\b(?:para\s+qu[eé]\s+sirv(?:e|en)|qu[eé]\s+(?:diferencia\s+hay|"
    r"representa|muestra|modela|significa)|c[oó]mo\s+funciona|"
    r"por\s+qu[eé]\s+importa|cu[aá]l(?:es)?\s+ser[ií]a(?:n)?)\b",
    re.IGNORECASE,
)
_EXPLANATORY_LEAD = re.compile(
    r"^\s*(?:el\s+profesor|la\s+profesora|ya\s+sabemos|despu[eé]s\s+veremos|"
    r"luego\s+veremos|estuvimos|estuve|hemos|se\s+explic[oó])\b",
    re.IGNORECASE,
)
_SHORT_CONTEXTUAL_QUESTION = re.compile(
    r"^\s*¿\s*(?:(?:y\s+)?(?:qué|por\s+qué|para\s+qué|cómo|cuál(?:es)?|"
    r"cuándo|dónde|quién(?:es)?|cuánt[oa]s?)\b|y\s+.+?)\s*\?\s*$",
    re.IGNORECASE,
)


def _strip_leading_discourse(text: str) -> str:
    """Expose the examiner's verb after a short run of conversational filler."""
    clean = text
    # El límite evita borrar contenido real si el reconocedor repite muletillas.
    for _ in range(4):
        stripped = _LEADING_DISCOURSE.sub("", clean, count=1)
        if stripped == clean:
            break
        clean = stripped
    return clean


def _has_incompatible_script(text: str) -> bool:
    """Reject obvious non-Latin STT hallucinations in Spanish oral modes."""
    return bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", text or ""))


def extract_question_candidate(text: str) -> str:
    """Return only the latest actual question/academic instruction in STT text."""
    clean = re.sub(r"\s+", " ", (text or "")).strip()
    # Short follow-ups depend on conversation context ("¿Y la G?", "¿Por qué?").
    # They are real examiner prompts even though they are below the general
    # length floor used to reject acknowledgements and STT fragments.
    if _SHORT_CONTEXTUAL_QUESTION.match(clean) and not _has_incompatible_script(clean):
        return clean
    if len(clean) < 12:
        return ""

    # A follow-up beginning with "¿Y...?" often completes the immediately
    # preceding question in the same spoken turn. Returning only the tail loses
    # the subject (real case: activity diagram -> only "¿Y qué modela?").
    closed_questions = list(re.finditer(r"¿[^?]{2,}\?", clean))
    if len(closed_questions) >= 2:
        previous_match, latest_match = closed_questions[-2:]
        previous, latest = previous_match.group(0), latest_match.group(0)
        gap = clean[previous_match.end():latest_match.start()]
        if (
            not gap.strip()
            and not _has_incompatible_script(previous + latest)
        ):
            return f"{previous.strip()} {latest.strip()}"

    # Un "¿no?" inicial o final sólo pide asentimiento. El texto restante debe
    # aportar por sí mismo una pregunta o consigna para gastar una llamada.
    had_leading_confirmation = False
    while True:
        without_leading_tag = _LEADING_CONFIRMATION_TAG.sub("", clean, count=1)
        if without_leading_tag == clean:
            break
        had_leading_confirmation = True
        clean = without_leading_tag.strip()
    clean = _TRAILING_CONFIRMATION_TAG.sub("", clean, count=1).strip()
    if len(clean) < 12:
        return ""

    # The opening "¿" is the strongest signal there is, so anchor on the LAST
    # one and take everything from there. Requiring a closing "?" threw away
    # real questions: the STT drops final punctuation constantly, and an
    # examiner who prefaces the question ("En el simulador del trabajo, ¿cuál
    # es la diferencia") leaves the marker mid-sentence, where the sentence-level
    # scan below can never see it.
    last_open = clean.rfind("¿")
    if last_open >= 0:
        tail = clean[last_open:]
        closed = re.match(r"¿[^?]{8,}\?", tail)
        if closed:
            return closed.group(0).strip()
        if len(tail) >= 12:
            return tail[:600].strip()

    sentences = [
        part.strip(" -–—")
        for part in re.split(r"(?<=[.!?。！？])\s+|\n+", clean)
        if part.strip()
    ]
    for sentence in reversed(sentences):
        candidate = _strip_leading_discourse(sentence)
        if _has_incompatible_script(candidate):
            continue
        is_explanatory = bool(_EXPLANATORY_LEAD.match(candidate))
        qualifies = (
            candidate.endswith("?")
            or bool(_QUESTION_START.match(candidate))
            or bool(_INDIRECT_COMMAND_START.match(candidate))
            or bool(_POLITE_REQUEST.match(candidate))
            or bool(_EMBEDDED_COMMAND.search(candidate))
            or (not is_explanatory and bool(_EMBEDDED_QUESTION.search(candidate)))
        )
        if had_leading_confirmation and not candidate.endswith("?"):
            # Después de una coletilla inicial, "que..." suele continuar una
            # afirmación truncada; sólo una consigna verbal sigue siendo señal.
            qualifies = bool(_ORAL_COMMAND_START.match(candidate))
        if len(candidate) >= 12 and qualifies:
            return candidate[-600:]
    return ""


@dataclass(frozen=True)
class QuestionInterpretation:
    """Conservative reconstruction of a question damaged by streaming STT."""

    question: str = ""
    normalized: str = ""
    confidence: float = 0.0
    reasons: tuple[str, ...] = ()


def _canonical_question(text: str) -> str:
    clean = re.sub(r"\s+", " ", text).strip().rstrip(".!?").strip()
    clean = clean.lstrip("¿").strip()
    return f"¿{clean}?" if clean else ""


def interpret_question_candidate(text: str) -> QuestionInterpretation:
    """Recover high-confidence Spanish questions without inventing their intent."""
    raw = re.sub(r"\s+", " ", (text or "")).strip()
    if not raw or _has_incompatible_script(raw):
        return QuestionInterpretation(normalized=raw)

    normalized = raw
    reasons: list[str] = []

    repaired = re.sub(r"\bd[ií]a\s+rama\b", "diagrama", normalized, flags=re.I)
    repaired = re.sub(r"\bd[ií]a\s+grama\b", "diagrama", repaired, flags=re.I)
    if repaired != normalized:
        normalized = repaired
        reasons.append("diagrama")

    repaired = re.sub(r"\bcasos?\s+de\s+usoy\b", "casos de uso y", normalized, flags=re.I)
    if repaired != normalized:
        normalized = repaired
        reasons.append("uso_y")

    if re.search(r"\b(?:nodos?|join)\b", normalized, re.I):
        repaired = re.sub(r"\bfor\b(?=\s+o\s+join\b)", "fork", normalized, flags=re.I)
        if repaired != normalized:
            normalized = repaired
            reasons.append("fork_join")

    academic_cue = re.search(
        r"\b(?:diagramas?|nodos?|asociaci[oó]n|multiplicidad|modelos?|clases?|grafos?)\b",
        normalized,
        re.I,
    )
    if academic_cue:
        repaired = re.sub(
            r"\beh\s+(?=(?:representa|muestra|modela|sirve)\b)",
            "qué ",
            normalized,
            flags=re.I,
        )
        if repaired != normalized:
            normalized = repaired
            reasons.append("interrogativo")

    candidate = extract_question_candidate(normalized)
    if not candidate:
        return QuestionInterpretation(normalized=normalized, reasons=tuple(reasons))

    confidence = 0.9 if reasons else 1.0
    return QuestionInterpretation(
        question=_canonical_question(candidate),
        normalized=normalized,
        confidence=confidence,
        reasons=tuple(reasons) or ("directa",),
    )


_DANGLING_QUESTION_END = re.compile(
    r"\b(?:y|o|de|del|la|el|los|las|que|qué|entre|para|por|con|sin|un|una)\s*$",
    re.I,
)


def looks_actionable_question(text: str) -> bool:
    """True for a clear unpunctuated prompt that is not cut mid-phrase."""
    clean = (text or "").strip()
    if len(clean.split()) < 6 or _DANGLING_QUESTION_END.search(clean):
        return False
    return bool(interpret_question_candidate(clean).question)


def looks_complete_question(text: str) -> bool:
    """True when the buffer already holds a finished question.

    Gates the fast flush path. `extract_question_candidate` alone is not enough:
    it matches on an opening interrogative, so a buffer cut mid-sentence
    ("¿Qué es un diagrama de") would pass and get flushed early — the exact bug
    the 4 s timer was raised to fix. Requiring closing punctuation means the STT
    itself considered the sentence finished. When Gemini emits no punctuation the
    fast path simply never fires and the old timing stands.
    """
    clean = (text or "").strip()
    if len(clean) < 12 or not clean.endswith(("?", ".", "!")):
        return False
    return bool(extract_question_candidate(clean))


def _looks_like_unsupported_definition(context: str, utterance: str) -> bool:
    """Fast local guard against defining a corrupted, out-of-context term."""
    question = (utterance or "").casefold()
    match = re.search(r"\bqu[eé]\s+es\s+(?:un[ao]?\s+)?([a-záéíóúñ]{10,})", question)
    if not match:
        return False
    term = match.group(1)
    material = (context or "").casefold()
    if len(material) < 40 or material == "(sin contexto)":
        return False
    return term not in material


def parse_assist_text(text: str) -> InterviewAssist | None:
    """Extract InterviewAssist from model TEXT (JSON, optionally fenced)."""
    raw = (text or "").strip()
    if not raw:
        return None

    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    if fence:
        raw = fence.group(1).strip()

    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    blob = raw[start : end + 1]
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return None

    pregunta = str(data.get("pregunta_es") or "").strip()
    respuestas_raw = data.get("respuestas") or []
    if not isinstance(respuestas_raw, list):
        respuestas_raw = [respuestas_raw]
    respuestas = [c for r in respuestas_raw if (c := _clean_spoken(str(r)))][:2]
    ideas_raw = data.get("ideas_clave") or []
    if not isinstance(ideas_raw, list):
        ideas_raw = [ideas_raw]
    ideas = [c for v in ideas_raw if (c := _clean_spoken(str(v)))][:3]
    if not pregunta and not respuestas:
        return None
    return InterviewAssist(pregunta, respuestas, ideas)


_ASSIST_LINE = re.compile(r"^\s*([RAPI])\s*[:：]\s*(.*)$")

# Las respuestas se dicen en voz alta y el TTS las lee literal: "$a$" se escucha
# "dólar a dólar", y en pantalla tampoco se entiende. El prompt ya prohíbe LaTeX
# y markdown, pero los modelos lite recaen en cuanto la pregunta es matemática,
# así que la limpieza es la red de seguridad.
_LATEX_WRAPPER = re.compile(
    r"\\(?:text|textbf|textit|mathrm|mathit|mathbf|operatorname)\s*\{([^{}]*)\}"
)
# Una fracción no se borra: se dice. Perder "\frac{3}{4}" dejaría la respuesta
# incompleta, así que se traduce a la forma hablada en lugar de eliminarla.
_LATEX_FRACTION = re.compile(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_LATEX_SQRT = re.compile(r"\\sqrt\s*\{([^{}]*)\}")
_LATEX_OPERATORS = {
    r"\cdot": " por ",
    r"\times": " por ",
    r"\div": " dividido ",
    r"\leq": " menor o igual que ",
    r"\le": " menor o igual que ",
    r"\geq": " mayor o igual que ",
    r"\ge": " mayor o igual que ",
    r"\neq": " distinto de ",
    r"\equiv": " congruente con ",
    r"\pm": " más o menos ",
    r"\mid": " divide a ",
    r"\in": " pertenece a ",
    r"\infty": " infinito ",
}
# Más largos primero: sin esto "\le" se comería el prefijo de "\leq".
_LATEX_OPERATOR_RE = re.compile(
    "|".join(re.escape(k) for k in sorted(_LATEX_OPERATORS, key=len, reverse=True))
)
_MATH_DELIMS = re.compile(r"\$\$?|\\[()\[\]]|\\\\")
# Comandos sin traducción: se conserva el nombre, que suele ser la palabra que
# hace falta ("\gcd" -> "gcd"). Los puramente tipográficos sí se descartan.
_LATEX_LEFTOVER = re.compile(r"\\([a-zA-Z]+)")
_LATEX_TYPOGRAPHIC = frozenset(
    {"left", "right", "displaystyle", "textstyle", "quad", "qquad", "limits"}
)
_BRACES = re.compile(r"[{}]")
_MD_MARKS = re.compile(r"\*\*|__|`+")


# El modelo debe emitir A en UNA línea (el formato R:/A:/P:/I: se parsea línea a
# línea, y un salto lo rompería), pero en pantalla eso queda como un bloque
# corrido ilegible. Las viñetas las pone la app al mostrar, no el modelo.
_EXAMPLE_MARKERS = re.compile(
    r"\s*\b(Por ejemplo,|Otro caso:|Otro ejemplo:|Segundo caso:)\s*",
    re.IGNORECASE,
)


def _format_examples(value: str) -> str:
    """Split the expansion into bullets so the examples are scannable."""
    if not value:
        return value
    text = _EXAMPLE_MARKERS.sub(lambda m: "\n• " + m.group(1) + " ", value)
    return text.strip()


def _clean_spoken(value: str) -> str:
    """Turn LaTeX/markdown markup into something sayable out loud.

    Notation is translated, not deleted: an answer about fractions that loses
    its fractions is worse than one that keeps them. Nested braces are out of
    scope — the prompt is the primary defence and this is the safety net.
    """
    text = _LATEX_WRAPPER.sub(r"\1", value or "")
    text = _LATEX_FRACTION.sub(r"\1 sobre \2", text)
    text = _LATEX_SQRT.sub(r"raíz de \1", text)
    text = _LATEX_OPERATOR_RE.sub(lambda m: _LATEX_OPERATORS[m.group(0)], text)
    text = _MATH_DELIMS.sub("", text)
    text = _LATEX_LEFTOVER.sub(
        lambda m: "" if m.group(1) in _LATEX_TYPOGRAPHIC else m.group(1), text
    )
    text = _BRACES.sub("", text)
    text = _MD_MARKS.sub("", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def parse_assist_lines(text: str, partial: bool = False) -> InterviewAssist | None:
    """Extract InterviewAssist from the line format (R:/A:/P:/I:).

    Unlike JSON, this parses correctly while the model is still streaming, which
    is the whole reason the coach stopped emitting JSON: the answer can be shown
    as soon as its line closes instead of after the last token.

    R is the short answer and A the expanded one; they feed the left and right
    reply boxes. A without R is dropped: the short answer is what the user says
    first, so a lone expansion would fill the wrong box.
    """
    raw = (text or "").strip()
    if not raw:
        return None

    respuesta = ampliada = pregunta = ""
    ideas: list[str] = []
    for line in raw.splitlines():
        match = _ASSIST_LINE.match(line)
        if not match:
            continue
        tag, value = match.group(1), _clean_spoken(match.group(2))
        if tag == "R":
            respuesta = value
        elif tag == "A":
            ampliada = value
        elif tag == "P":
            pregunta = value
        elif tag == "I":
            ideas = [p.strip() for p in value.split(";") if p.strip()][:3]

    if not respuesta and not pregunta:
        return None
    respuestas = [respuesta] if respuesta else []
    if respuesta and ampliada:
        respuestas.append(_format_examples(ampliada))
    return InterviewAssist(
        pregunta,
        respuestas,
        ideas,
        partial=partial,
    )


def parse_assist(text: str, partial: bool = False) -> InterviewAssist | None:
    """Parse coach output in either format.

    Gemini is prompted for lines, but the NVIDIA fallback and older responses
    may still come back as JSON, so both are accepted.
    """
    return parse_assist_lines(text, partial=partial) or parse_assist_text(text)


def is_configured() -> bool:
    return gemini_keys.is_configured()


def _session_status(mode: str, provider: str = "Gemini") -> str:
    """Reader-facing status with module and active/fallback provider."""
    title = "Resolver activo" if mode == "resolver" else "Entrevista activa"
    if provider != "Gemini":
        for name, module in _fallback_providers():
            if name == provider:
                return f"{title} · {name} · {module.pool.count()} claves"
        return f"{title} · {provider}"
    gemini = gemini_keys.pool.current_label().replace("API", "Gemini", 1)
    ready = ", ".join(
        f"{name} ({module.pool.count()})" for name, module in _fallback_providers()
    )
    fallback = f" · respaldo: {ready}" if ready else ""
    return f"{title} · {gemini}{fallback}"


# Reuse one client per key: avoids TLS/session setup on every coach call.
_client_cache: dict[str, object] = {}

# Models that rejected our request outright. A 400/404 is deterministic — the
# model does not exist or refuses the config — so retrying it on every turn just
# buys latency. Measured: `gemini-flash-lite-latest` 400s consistently and the
# logs carry 93 of those, each one delaying a live answer.
_broken_models: set[str] = set()


def _rejects_thinking_budget(exc: BaseException) -> bool:
    """True when a 400 looks like the model refusing thinking_budget."""
    detail = str(exc).lower()
    return "400" in detail or "invalid_argument" in detail


def _is_permanent_model_error(exc: BaseException) -> bool:
    detail = str(exc).lower()
    return (
        "invalid_argument" in detail
        or "not found" in detail
        or "not supported" in detail
        or "400" in detail
        or "404" in detail
    )


def _get_client(genai, api_key: str):
    client = _client_cache.get(api_key)
    if client is None:
        client = genai.Client(api_key=api_key)
        _client_cache[api_key] = client
    return client


# Models that rejected thinking_budget=0 with a 400. Measured: the Gemini 3.x
# lite/flash models refuse the field outright, and because a 400 also reads as a
# permanent model error they were being blacklisted for the whole session — a
# healthy model disabled by one bad parameter. This set lets the retry drop the
# field instead of dropping the model.
_no_thinking_models: set[str] = set()


def _coach_config(model: str, mode: str = DEFAULT_ASSIST_MODE, thinking: bool = True):
    """Low-latency generation config. thinking_budget=0 skips the multi-second
    default 'thinking' phase; 2.0 models and the 3.x family reject the field."""
    from google.genai import types

    # Plain text, not JSON: the line format is what makes partial output
    # renderable mid-stream, and it spends no tokens on syntax.
    # Two answers per turn (short + expanded, and the expansion closes with a
    # worked example) need more output room. This does not move TTFT — the short
    # line still paints first — only the moment the expansion lands.
    kwargs = dict(
        temperature=0.2 if mode in ORAL_ASSIST_MODES else 0.4,
        max_output_tokens=700 if mode in _LONG_FORM_MODES else 240,
    )
    if thinking and "2.0" not in model and model not in _no_thinking_models:
        kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
    return types.GenerateContentConfig(**kwargs)


def _stream_coach(client, model: str, prompt: str, config, on_partial) -> str:
    """Consume a streaming coach response, emitting partial answers as they grow.

    Returns the full text.

    The answer occupies a single line, so waiting for that line to close would
    mean waiting for the whole generation — measured at 1221 ms to first token
    against 5254 ms to completion, i.e. four seconds thrown away. So the first
    line (R) is emitted while it grows, cut at word boundaries to avoid
    rendering half a word. Later lines are only emitted once closed: a partial
    question gloss is not worth the churn.
    """
    buffer = ""
    emitted = ""
    first_token_at: float | None = None
    start = time.perf_counter()

    for chunk in client.models.generate_content_stream(
        model=model, contents=prompt, config=config
    ):
        piece = getattr(chunk, "text", None) or ""
        if not piece:
            continue
        if first_token_at is None:
            first_token_at = time.perf_counter()
            latency_log.log_stage(
                "live_interview",
                "coach_ttft",
                (first_token_at - start) * 1000.0,
                model=model,
            )
        buffer += piece
        if on_partial is None:
            continue
        if "\n" in buffer:
            renderable = buffer.rsplit("\n", 1)[0]
        elif piece[-1:].isspace():
            # Still on the answer line: safe to paint up to this word.
            renderable = buffer
        else:
            continue
        if renderable == emitted:
            continue
        emitted = renderable
        assist = parse_assist_lines(renderable, partial=True)
        if assist is not None:
            on_partial(assist)

    return buffer


def coach_assist(
    context: str,
    utterance: str,
    api_key: str | None = None,
    conversation_history: str = "",
    mode: str = DEFAULT_ASSIST_MODE,
    answer_lang: str = DEFAULT_ANSWER_LANG,
    on_partial: Callable[[InterviewAssist], None] | None = None,
) -> InterviewAssist | None:
    """Streaming coach call. Tries keys + model fallbacks.

    `on_partial` is called from this thread each time an output line closes, so
    the answer reaches the screen before generation finishes.
    """
    utterance = (utterance or "").strip()
    if not utterance:
        return None
    answer_lang = resolve_answer_lang(mode, answer_lang)
    try:
        from google import genai
    except ImportError:
        return None

    prompt = _COACH_PROMPT.format(
        role_instructions=_MODE_INSTRUCTIONS.get(mode, _MODE_INSTRUCTIONS[DEFAULT_ASSIST_MODE]),
        answer_lang=ANSWER_LANGS.get(answer_lang, ANSWER_LANGS[DEFAULT_ANSWER_LANG]),
        context=_select_context(context, utterance) or "(sin contexto)",
        utterance=utterance,
        history=(conversation_history or "").strip() or "(sin historial previo)",
        plain_language_rules=_PLAIN_LANGUAGE_RULES,
        response_rules=(
            _RESOLVER_RESPONSE_RULES
            if mode == "resolver"
            else _EXAM_RESPONSE_RULES
            if mode == "examen_oral"
            else _PRACTICE_RESPONSE_RULES
            if mode == "prueba_oral"
            else _DEFAULT_RESPONSE_RULES
        ),
    )
    if mode in ORAL_ASSIST_MODES and _looks_like_unsupported_definition(
        context, utterance
    ):
        return InterviewAssist(
            pregunta_es="La pregunta parece haberse transcripto incorrectamente.",
            respuestas=[
                "No se entendió con claridad el término de la pregunta; pedí que la repitan."
            ],
            ideas_clave=["transcripción dudosa", "no inventar definiciones"],
            provider="Validación local",
        )
    models = []
    for m in COACH_MODELS:
        if m and m not in models and m not in _broken_models:
            models.append(m)
    if not models:
        # Permanent 400/404 failures must stay disabled for this process.
        # Retrying every dead model before each NVIDIA request creates the
        # appearance that the app froze.
        if has_fallback_provider():
            return _fallback_assist(prompt, mode, "modelos_agotados")
        raise CoachUnavailable(
            "Todos los modelos Gemini fallaron y no hay respaldo configurado"
        )

    keys: list[str] = []
    if api_key and api_key in gemini_keys.pool.available():
        keys.append(api_key)
    for k in gemini_keys.pool.available():
        if k not in keys:
            keys.append(k)
    if not keys and has_fallback_provider():
        return _fallback_assist(prompt, mode, "sin_claves_gemini")
    if not keys:
        raise CoachUnavailable(
            "Sin claves Gemini disponibles y sin respaldo configurado"
        )

    last_exc = None
    incomplete_assist: InterviewAssist | None = None
    for key in keys:
        client = _get_client(genai, key)
        for model in models:
            if model in _broken_models:
                continue
            try:
                try:
                    text = _stream_coach(
                        client, model, prompt, _coach_config(model, mode), on_partial
                    )
                except Exception as exc:
                    # A 400 usually means the config, not the model. Retry once
                    # without thinking_budget before writing the model off: the
                    # 3.x family rejects that field and was being blacklisted
                    # for the whole session over a parameter we can just drop.
                    if not (
                        _rejects_thinking_budget(exc)
                        and model not in _no_thinking_models
                    ):
                        raise
                    _no_thinking_models.add(model)
                    logger.info(
                        "Coach model %s no acepta thinking_budget; reintentando sin él",
                        model,
                    )
                    text = _stream_coach(
                        client,
                        model,
                        prompt,
                        _coach_config(model, mode, thinking=False),
                        on_partial,
                    )
                assist = parse_assist(text)
                if assist is not None and _has_required_answers(assist, mode):
                    return assist
                if assist is not None:
                    incomplete_assist = assist
                    logger.warning(
                        "Coach model %s omitted required resolver expansion; trying fallback",
                        model,
                    )
                    continue
                logger.warning(
                    "Coach model %s returned no valid assistance; trying fallback",
                    model,
                )
                continue
            except Exception as exc:
                last_exc = exc
                if gemini_keys.pool.is_quota_error(exc):
                    logger.warning("Coach quota on model=%s key=%s", model, gemini_keys.pool.current_label())
                    if model == models[-1]:
                        gemini_keys.pool.mark_exhausted(key)
                    continue
                logger.warning("Coach model %s failed: %s", model, exc)
                if _is_permanent_model_error(exc):
                    _broken_models.add(model)
                    logger.warning(
                        "Coach model %s deshabilitado para esta sesión (error permanente)",
                        model,
                    )
                continue
    if last_exc and gemini_keys.pool.is_quota_error(last_exc):
        logger.warning("Gemini coach sin cuota; usando respaldo NVIDIA")
        try:
            assist = _fallback_assist(prompt, mode, "respaldo_cuota")
            if assist is not None:
                return assist
        except CoachUnavailable:
            # A partial Gemini answer beats showing nothing at all.
            if incomplete_assist is not None:
                return incomplete_assist
            raise
        if incomplete_assist is not None:
            return incomplete_assist
        raise last_exc
    if incomplete_assist is not None:
        return incomplete_assist
    if last_exc:
        from core.errors import record

        record("coach.gemini", last_exc, modo=mode)
        raise CoachUnavailable(f"Gemini no pudo responder: {last_exc}") from last_exc
    return None


class InterviewLiveSession:
    """Background asyncio Live session fed with PCM from capture thread."""

    def __init__(
        self,
        context: str,
        on_transcript: Callable[[str], None],
        on_assist: Callable[[InterviewAssist], None],
        on_status: Callable[[str], None],
        mode: str = DEFAULT_ASSIST_MODE,
        answer_lang: str = DEFAULT_ANSWER_LANG,
    ):
        self._context = (context or "").strip() or "(sin contexto del candidato)"
        self._mode = mode if mode in _MODE_INSTRUCTIONS else DEFAULT_ASSIST_MODE
        self._answer_lang = resolve_answer_lang(self._mode, answer_lang)
        self._on_transcript = on_transcript
        self._on_assist = on_assist
        self._on_status = on_status
        self._audio_q: asyncio.Queue[bytes | None] | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = threading.Event()
        self._utterance_buf = ""
        self._orphan_fragments: list[tuple[str, float]] = []
        self._last_transcript_ts = 0.0
        # Bounds of the current interlocutor turn, so the wait they actually
        # perceive (silence timer + coach) can be measured, not estimated.
        self._utterance_start_ts = 0.0
        self._speech_end_ts = 0.0
        self._first_paint_logged = False
        self._api_key: str | None = None
        self._coach_lock = asyncio.Lock()
        self._coach_task: asyncio.Task | None = None
        self._pending_utterance: str | None = None
        self._history: list[str] = []
        self._history_lock = threading.Lock()

    def add_candidate_turn(self, text: str) -> None:
        """Add microphone speech to context without mixing speaker roles."""
        clean = (text or "").strip()
        if not clean:
            return
        with self._history_lock:
            self._history.append(f"CANDIDATO: {clean}")
            self._history = self._history[-12:]

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not is_configured():
            raise InterviewLiveError(
                "Falta GEMINI_API_KEY / GEMINI_API_KEY1. "
                "Configurala en .env (podés sumar KEY2 / KEY3)."
            )
        self._stop.clear()
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        loop = self._loop
        q = self._audio_q
        if loop and q is not None and loop.is_running():
            def _poison():
                try:
                    q.put_nowait(None)
                except Exception:
                    pass
            loop.call_soon_threadsafe(_poison)
        if self._thread:
            self._thread.join(timeout=8)
            self._thread = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def send_audio(self, pcm: bytes) -> None:
        if not pcm or self._stop.is_set():
            return
        loop = self._loop
        q = self._audio_q
        if loop is None or q is None or not loop.is_running():
            return

        def _put():
            try:
                q.put_nowait(pcm)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(pcm)
                except Exception:
                    pass

        loop.call_soon_threadsafe(_put)

    def _thread_main(self) -> None:
        from core.errors import install_asyncio, record

        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        # Tasks nobody awaits (send/recv/watchdog) used to die into a stderr the
        # windowed build discards, so the session looked stuck with a clean log.
        install_asyncio(loop)
        try:
            loop.run_until_complete(self._run())
        except Exception as exc:
            self._on_status(
                record("live.sesion", exc, modo=self._mode, user_msg=f"Error Live: {exc}")
            )
        finally:
            try:
                loop.close()
            except Exception:
                pass
            self._loop = None
            self._audio_q = None

    async def _run(self) -> None:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise InterviewLiveError(
                "Falta google-genai (pip install google-genai)."
            ) from exc

        gemini_keys.pool.reload()
        nvidia_provider.pool.reload()
        # Reload every fallback so a key added to .env mid-session is picked up
        # without restarting the app.
        for _name, _module in _fallback_providers():
            _module.pool.reload()
        keys = list(gemini_keys.pool.available())
        if not keys:
            raise InterviewLiveError("No hay claves Gemini disponibles.")

        models = [m for m in LIVE_MODELS if m]
        last_exc: BaseException | None = None

        for key in keys:
            if self._stop.is_set():
                return
            self._api_key = key
            for model in models:
                if self._stop.is_set():
                    return
                try:
                    self._on_status(
                        f"Conectando Live ({gemini_keys.pool.current_label()})..."
                    )
                    client = genai.Client(api_key=key)
                    config = types.LiveConnectConfig(
                        response_modalities=[types.Modality.AUDIO],
                        system_instruction=_LIVE_SYSTEM,
                        input_audio_transcription=types.AudioTranscriptionConfig(),
                        output_audio_transcription=types.AudioTranscriptionConfig(),
                    )
                    async with client.aio.live.connect(model=model, config=config) as session:
                        self._on_status(_session_status(self._mode))
                        logger.info("Interview Live connected model=%s", model)
                        self._audio_q = asyncio.Queue(maxsize=80)
                        self._utterance_buf = ""
                        await self._session_loop(session, types)
                    return
                except Exception as exc:
                    last_exc = exc
                    detail = str(exc).lower()
                    if gemini_keys.pool.is_quota_error(exc):
                        gemini_keys.pool.mark_exhausted(key)
                        self._on_status(
                            f"Cuota agotada ({gemini_keys.pool.current_label()}), rotando..."
                        )
                        break
                    if "not found" in detail or "not supported" in detail or "1007" in detail or "1008" in detail:
                        logger.warning("Live model unavailable %s: %s", model, exc)
                        continue
                    logger.exception("Live connect failed: %s", exc)
                    if "api key" in detail or "401" in detail or "403" in detail or "permission" in detail:
                        gemini_keys.pool.mark_exhausted(key)
                        break
                    continue

        msg = "No se pudo conectar a Gemini Live con las keys/modelos disponibles."
        if last_exc:
            msg = f"{msg} ({last_exc})"
        self._on_status(msg)
        raise InterviewLiveError(msg) from last_exc

    async def _session_loop(self, session, types) -> None:
        assert self._audio_q is not None
        send_task = asyncio.create_task(self._send_loop(session, types))
        recv_task = asyncio.create_task(self._recv_loop(session))
        watch_task = asyncio.create_task(self._flush_watchdog())
        try:
            done, pending = await asyncio.wait(
                {send_task, recv_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            for t in done:
                exc = t.exception() if not t.cancelled() else None
                if exc and not self._stop.is_set():
                    raise exc
        finally:
            send_task.cancel()
            recv_task.cancel()
            watch_task.cancel()
            if self._coach_task and not self._coach_task.done():
                self._coach_task.cancel()

    # Gemini's turn_complete/generation_complete fire on the MODEL's turn, which
    # may stay silent per _LIVE_SYSTEM and never signal — so the interviewer's
    # utterance would sit in the buffer forever. This watchdog flushes it once
    # transcription goes quiet for a bit, independent of that signal.
    # Una pregunta oral puede incluir pausas naturales mayores a un segundo.
    # El valor anterior dividía una oración en varios prompts sin contexto.
    # Sigue siendo el techo, pero ahora solo para texto ambiguo: una pregunta ya
    # cerrada no necesita esperar a confirmar un silencio que ya es evidente.
    _FLUSH_IDLE_SECONDS = 4.0
    _FLUSH_ACTIONABLE_SECONDS = 1.2
    _FLUSH_FAST_SECONDS = 0.65
    _ORPHAN_TTL_SECONDS = 7.0
    _ORPHAN_MAX_PARTS = 2
    _ORPHAN_MAX_CHARS = 240

    def _idle_threshold(self) -> float:
        if looks_complete_question(self._utterance_buf):
            return self._FLUSH_FAST_SECONDS
        if looks_actionable_question(self._utterance_buf):
            return self._FLUSH_ACTIONABLE_SECONDS
        return self._FLUSH_IDLE_SECONDS

    async def _flush_watchdog(self) -> None:
        while not self._stop.is_set():
            # Polled faster than before: a 300 ms tick would add up to a third
            # of the new 1 s threshold as pure rounding error.
            await asyncio.sleep(0.10)
            if not self._utterance_buf:
                continue
            loop = asyncio.get_event_loop()
            if loop.time() - self._last_transcript_ts >= self._idle_threshold():
                self._flush_utterance()

    async def _send_loop(self, session, types) -> None:
        assert self._audio_q is not None
        while not self._stop.is_set():
            chunk = await self._audio_q.get()
            if chunk is None:
                break
            try:
                await session.send_realtime_input(
                    audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
                )
            except Exception:
                if self._stop.is_set():
                    return
                raise

    async def _recv_loop(self, session) -> None:
        while not self._stop.is_set():
            async for msg in session.receive():
                if self._stop.is_set():
                    return
                await self._handle_message(msg)

    async def _handle_message(self, msg) -> None:
        sc = getattr(msg, "server_content", None)
        if sc is None:
            return

        # Input transcription = interviewer speech (what we care about)
        in_tx = getattr(sc, "input_transcription", None)
        if in_tx is not None:
            raw = getattr(in_tx, "text", None) or ""
            if raw.strip():
                now = asyncio.get_event_loop().time()
                if not self._utterance_buf:
                    self._utterance_start_ts = now
                self._utterance_buf = merge_transcript_delta(
                    self._utterance_buf, raw
                )
                self._last_transcript_ts = now
                self._on_transcript(raw)

        # Intentionally ignore model audio / output transcription (never play coach).

        # Do not flush immediately on the model-side completion flags. Gemini
        # can emit them during a natural pause in the examiner's sentence.
        # The idle watchdog is the single debounce point for input speech.

    def _flush_utterance(self) -> None:
        buf = self._utterance_buf.strip()
        fast = looks_complete_question(buf)
        self._utterance_buf = ""
        # Cerrar el turno en pantalla. Los deltas se concatenan sin espacio
        # (correcto dentro de una frase), así que sin esta marca el turno
        # siguiente arranca pegado al anterior: "...MQTT real.Por supuesto".
        if buf:
            self._on_transcript("\n")
        now = asyncio.get_event_loop().time()
        speech_start, speech_end = self._utterance_start_ts, self._last_transcript_ts
        if not buf:
            return
        if self._mode in ORAL_ASSIST_MODES:
            interpretation = self._interpret_or_hold(buf, now)
            if not interpretation:
                logger.info(
                    "Coach retained incomplete/non-question oral turn: %s", buf[:120]
                )
                latency_log.log_stage(
                    "live_interview",
                    "flush_discarded",
                    (now - speech_end) * 1000.0,
                    mode=self._mode,
                    chars=len(buf),
                )
                return
            if interpretation.normalized != buf:
                logger.info(
                    "STT question interpreted confidence=%.2f reasons=%s raw=%s interpreted=%s",
                    interpretation.confidence,
                    ",".join(interpretation.reasons),
                    buf[:160],
                    interpretation.question[:160],
                )
                self._on_assist(
                    InterviewAssist(
                        pregunta_es=f"Pregunta interpretada: {interpretation.question}",
                        respuestas=[],
                        partial=True,
                    )
                )
            buf = interpretation.question
        if not self._api_key:
            return
        # El turno DESCARTADO se loguea arriba, pero el ACEPTADO no se registraba
        # en ningún lado: cuando el coach contestaba cualquier cosa era imposible
        # saber con qué texto se había disparado. Sin esto no se puede afinar el
        # corte de turno con evidencia.
        logger.info("Coach turn accepted (%d chars): %s", len(buf), buf[:200])
        # Streaming STT deltas arrive while they talk, so this is how long they
        # spoke — not a delay. The delay is flush_wait.
        if speech_start:
            latency_log.log_stage(
                "live_interview",
                "stt_window",
                (speech_end - speech_start) * 1000.0,
                mode=self._mode,
                chars=len(buf),
            )
        latency_log.log_stage(
            "live_interview",
            "flush_wait",
            (now - speech_end) * 1000.0,
            mode=self._mode,
            branch="fast" if fast else "idle",
        )
        self._speech_end_ts = speech_end
        self._first_paint_logged = False
        with self._history_lock:
            self._history.append(f"ENTREVISTADOR: {buf}")
            self._history = self._history[-12:]
        # Latest-wins: never cancel an in-flight coach call. If one is
        # running, stash the newest utterance; _run_coach drains it.
        if self._coach_task and not self._coach_task.done():
            self._pending_utterance = buf
        else:
            self._coach_task = asyncio.create_task(self._run_coach(buf))

    def _interpret_or_hold(
        self, fragment: str, now: float | None = None
    ) -> QuestionInterpretation | None:
        """Join at most two recent STT fragments and require high confidence."""
        current_time = self._loop_time() if now is None else now
        self._orphan_fragments = [
            item
            for item in self._orphan_fragments
            if current_time - item[1] <= self._ORPHAN_TTL_SECONDS
        ]
        pieces = [item[0] for item in self._orphan_fragments] + [fragment.strip()]
        combined = " ".join(part for part in pieces if part).strip()
        interpretation = interpret_question_candidate(combined)
        if interpretation.question and interpretation.confidence >= 0.85:
            self._orphan_fragments.clear()
            return interpretation

        if fragment.strip():
            self._orphan_fragments.append((fragment.strip(), current_time))
            self._orphan_fragments = self._orphan_fragments[-self._ORPHAN_MAX_PARTS :]
            while (
                self._orphan_fragments
                and sum(len(item[0]) for item in self._orphan_fragments)
                > self._ORPHAN_MAX_CHARS
            ):
                self._orphan_fragments.pop(0)
        return None

    async def _run_coach(self, utterance: str) -> None:
        async with self._coach_lock:
            while True:
                if self._stop.is_set() or not self._api_key:
                    return
                await self._coach_once(utterance)
                if self._pending_utterance is None:
                    return
                utterance, self._pending_utterance = self._pending_utterance, None

    def _emit_assist(self, assist: InterviewAssist) -> None:
        """Push an assist to the UI and time the first one that reaches it.

        Called from the coach worker thread for partials, so it must stay
        thread-safe: `_on_assist` ends on a queue.Queue.
        """
        if not self._first_paint_logged and self._speech_end_ts:
            self._first_paint_logged = True
            latency_log.log_stage(
                "live_interview",
                "perceived_total",
                (self._loop_time() - self._speech_end_ts) * 1000.0,
                mode=self._mode,
                partial="yes" if assist.partial else "no",
            )
        self._on_assist(assist)

    def _loop_time(self) -> float:
        """Event loop clock, readable from the coach worker thread."""
        loop = self._loop
        return loop.time() if loop is not None else time.perf_counter()

    async def _coach_once(self, utterance: str) -> None:
        self._on_status(
            "Resolviendo pregunta..."
            if self._mode == "resolver"
            else "Generando sugerencias..."
        )
        # Timing: turn_complete/flush -> coach return, to falsify latency wins.
        start = time.perf_counter()
        try:
            with self._history_lock:
                history = "\n".join(self._history[-_COACH_HISTORY_TURNS:])
            assist = await asyncio.to_thread(
                coach_assist,
                self._context,
                utterance,
                self._api_key,
                history,
                self._mode,
                self._answer_lang,
                self._emit_assist,
            )
            if assist:
                self._emit_assist(assist)
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            logger.info(
                "Coach turn complete in %.0f ms (mode=%s, assist=%s)",
                elapsed_ms,
                self._mode,
                "yes" if assist else "none",
            )
            latency_log.log_stage(
                "live_interview",
                "coach",
                elapsed_ms,
                mode=self._mode,
                assist="yes" if assist else "none",
                provider=assist.provider if assist else None,
            )
            if assist:
                self._on_status(_session_status(self._mode, assist.provider))
            else:
                self._on_status(
                    "No se pudo generar una respuesta válida; intentá repetir la pregunta"
                )
        except CoachUnavailable as exc:
            # Already journaled with its traceback where it was raised; here it
            # only has to reach the screen so the user stops waiting.
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            latency_log.log_stage(
                "live_interview",
                "coach",
                elapsed_ms,
                mode=self._mode,
                assist="unavailable",
            )
            logger.warning(
                "Coach sin proveedor tras %.0f ms (mode=%s): %s",
                elapsed_ms,
                self._mode,
                exc,
            )
            self._on_status(str(exc))
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            logger.info("Coach turn failed after %.0f ms (mode=%s)", elapsed_ms, self._mode)
            latency_log.log_stage(
                "live_interview", "coach", elapsed_ms, mode=self._mode, assist="error"
            )
            if gemini_keys.pool.is_quota_error(exc):
                nxt = gemini_keys.pool.mark_exhausted(self._api_key)
                if nxt:
                    self._api_key = nxt
                    self._on_status(f"Cuota coach: rotando a {gemini_keys.pool.current_label()}")
                else:
                    self._on_status("Cuota agotada en todas las keys (coach)")
            else:
                from core.errors import record

                self._on_status(
                    record(
                        "coach.turno",
                        exc,
                        modo=self._mode,
                        user_msg=f"Error coach: {exc}",
                    )
                )
