"""Adaptive interviewer simulation independent from system-audio capture."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, replace
from typing import Callable

from config import logger
from infrastructure.services import gemini_keys, nvidia_provider, provider_chain


VALID_ACTIONS = {"follow_up", "next_topic", "finish"}
DEFAULT_MODEL = os.getenv("GEMINI_COACH_MODEL", "").strip() or "gemini-3.1-flash-lite"

# Un turno del simulacro se pide con el usuario mirando la pantalla, así que la
# llamada va acotada dos veces igual que en los demás proveedores: presupuesto
# total y presupuesto por tramo. Sin el techo, una rotación de claves con
# timeouts encadenados congela la app, que es peor que un error reintentable.
TOTAL_BUDGET_SECONDS = float(
    os.getenv("SIMULATION_TOTAL_BUDGET_SECONDS", "").strip() or 40.0
)
# 24 s, no 16: con el piso real de 10 s de Gemini (ver
# `_GEMINI_MIN_DEADLINE_SECONDS`) 16 s no alcanzaban ni para dos intentos, y un
# 503 transitorio en la primera key mataba el turno. La aritmética del peor
# caso, con los 40 s de `TOTAL_BUDGET_SECONDS`:
#
#   etapa Gemini, tope 24 s
#     intento 1: min(24, max(10, 12)) = 12 s  -> quedan 12
#     intento 2: min(12, max(10,  6)) = 10 s  -> quedan  2 -> bajo el piso, corta
#   consumo máximo de Gemini: 22 s (los 2 s sobrantes no alcanzan para nadie)
#
#   a la cadena de respaldo le quedan 40 - 22 = 18 s
#     Groq  : min(_DEFAULT_PROVIDER_BUDGET_SECONDS 12, 18) = 12 s -> quedan 6
#     NVIDIA: 6 >= _MIN_PROVIDER_SECONDS 4                  ->      6 s
#
# O sea que los dos respaldos siguen arrancando incluso si Gemini se come todo
# lo suyo. Subir la etapa a 24 s en vez de dejarla en 16 no es gratis pero sí
# barato: los 2 s de cola que quedan sin usar son el precio de que el segundo
# intento sea legal.
GEMINI_BUDGET_SECONDS = float(
    os.getenv("SIMULATION_GEMINI_BUDGET_SECONDS", "").strip() or 24.0
)
# Un 503 de Gemini es transitorio: se reintenta la misma key una vez tras una
# pausa corta antes de bajar al respaldo.
_GEMINI_SATURATION_BACKOFF_SECONDS = 1.5
_GEMINI_ATTEMPTS_PER_KEY = 2
# Techo de salida del turno. Medido el 2026-09-20 sobre el prompt real del
# simulacro academico: `completion_tokens` tipico 312-544, con cola que pasa los
# 700. En 700 el turno no tenia aire: al superarlo la API corta con
# `finish_reason=length`, el JSON llega partido a mitad de un string y
# `parse_simulation_turn` mata el turno. Reproducido: "Unterminated string
# starting at: line 7 column 21 (char 578)" en el tercer turno.
#
# Peor aun en Groq, que es el respaldo que de verdad contesta: `gpt-oss-120b`
# cobra el reasoning del MISMO `max_tokens` (ver groq_provider), y
# `effective_max_tokens` ya piso el minimo en 700 — o sea que el simulacro
# corria exactamente en el piso, con cero margen sobre los 544 medidos.
#
# 1600 deja ~3x el maximo observado. Subirlo NO estrangula la cadena: el
# timeout de socket se deriva de los tokens (`request_timeout_for`: 28 s con
# 700, 40 s con 1600) pero el intento se acota con `min(base_timeout,
# remaining)` y `remaining` lo fija `provider_chain` en 12 s, que ya era el
# limite efectivo con 700. El techo es un limite, no un objetivo: no alarga la
# respuesta ni gasta tokens de mas, solo compra lugar para que termine.
_MAX_OUTPUT_TOKENS = 1600

# Un modelo no determinista devuelve salida malformada cada tanto: es esperable,
# no excepcional. Subir el techo baja la frecuencia, NO elimina el caso, así que
# el turno se reintenta una vez más. Dos intentos y no más: cada uno es una
# llamada real que se cobra de la cuota del usuario, y con el rescate del JSON
# truncado delante, llegar acá ya significa dos respuestas rotas seguidas.
_TURN_ATTEMPTS = 2
_RETRY_INSTRUCTION = (
    "Your previous reply was not valid JSON: it was cut off before the closing "
    "brace. Answer again with ONE compact JSON object, no code fence, no line "
    "breaks inside strings, and keep feedback and example_answer under 40 words "
    "each."
)

# Un intento de Gemini se lleva, como mucho, esta fracción de lo que queda del
# presupuesto. No se le da todo: un solo request colgado se comería el turno de
# las nueve claves que vienen detrás. No se le da menos que el piso: por debajo
# de eso ni vale la pena abrir el socket, el modelo no termina de escribir.
_GEMINI_REQUEST_SHARE = 0.5

# Piso DURO, puesto por el servidor y no por nosotros. Gemini rechaza cualquier
# deadline menor, y lo dice con todas las letras en el cuerpo del error:
#
#   400 INVALID_ARGUMENT. {'error': {'code': 400, 'message': 'Manually set
#   deadline 8s is too short. Minimum allowed deadline is 10s.', 'status':
#   'INVALID_ARGUMENT'}}
#
# Ese texto es la fuente del número. El fix anterior inventó un piso propio de
# 4 s y con los defaults de entonces (etapa de 16 s, mitad por intento) NINGÚN
# intento llegaba a los 10 s: el camino de Gemini quedó muerto al 100 % y el
# simulacro sólo seguía vivo porque Groq lo levantaba. 109 de esos 400 quedaron
# en `logs/errores.log` (deadlines de 4, 5, 6, 7 y 8 s).
_GEMINI_MIN_DEADLINE_SECONDS = 10.0
# Nombre histórico, que usan los llamadores. Ya no puede divergir del piso real.
_MIN_GEMINI_REQUEST_SECONDS = _GEMINI_MIN_DEADLINE_SECONDS


def gemini_request_timeout(remaining_seconds: float) -> float | None:
    """Socket timeout for ONE Gemini attempt, derived from the budget left.

    Esto existe porque faltaba la mitad del contrato que CLAUDE.md exige a todo
    proveedor: "acotado dos veces, timeout de socket por request y presupuesto
    total por llamada". Groq y NVIDIA lo cumplían; Gemini sólo miraba el reloj
    *entre* intentos, así que un request que se colgaba corría sin techo. El
    control volvía a los 15,9 s, el presupuesto de 40 s ya estaba consumido y
    la cadena de respaldo arrancaba muerta — el usuario veía literalmente
    "Groq: sin tiempo restante" y el simulacro se frenaba después de la primera
    pregunta.

    Derivado, pero POR ENCIMA de un piso duro del servidor. Esa segunda mitad
    es la que faltaba: derivar el timeout del presupuesto sin mirar el mínimo
    de Gemini producía deadlines de 4 a 8 s que la API rechaza de plano con un
    400, así que el arreglo "acotar el request" terminó siendo peor que el
    problema que arreglaba — no acotaba nada, mataba todo.

    Contrato, y no hay una tercera salida:

    - ``None``  -> con lo que queda no se puede pedir un deadline legal. El
      llamador NO debe llamar a Gemini: tiene que saltar al respaldo. Abrir el
      socket igual es un 400 garantizado que encima gasta el turno de una key.
    - ``float`` -> siempre en ``[_GEMINI_MIN_DEADLINE_SECONDS, remaining]``.
    """
    if remaining_seconds < _GEMINI_MIN_DEADLINE_SECONDS:
        return None
    return min(
        remaining_seconds,
        max(_GEMINI_MIN_DEADLINE_SECONDS, remaining_seconds * _GEMINI_REQUEST_SHARE),
    )


class SimulationOutputError(ValueError):
    """Raised when a provider violates the simulator JSON contract."""


def is_configured() -> bool:
    return gemini_keys.is_configured() or provider_chain.has_fallback_provider()


@dataclass(frozen=True)
class SimulationTurn:
    action: str
    question: str
    feedback: str
    strengths: tuple[str, ...]
    improvements: tuple[str, ...]
    example_answer: str = ""


def _text_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())[:4]


def _close_truncated_json(text: str) -> dict | None:
    """Rescatar el objeto más grande que quedó completo antes del corte.

    Una respuesta cortada por ``finish_reason=length`` no es basura: es JSON
    válido hasta el byte donde el modelo se quedó sin techo. Tirar el turno
    entero por el último campo incompleto es desperdiciar una pregunta y una
    devolución que ya están escritas — y en el simulacro eso se ve como
    "arranca bien y después se queda".

    Se corta en la última frontera segura (una coma o un cierre fuera de
    string), se descartan los contenedores que quedaron abiertos y se cierran.
    Devuelve ``None`` cuando no hay nada rescatable: el que llama decide, acá no
    se inventa contenido.

    Deliberado: el miembro incompleto se **descarta**, no se cierra la comilla.
    Media ``example_answer`` presentada como respuesta modelo completa es peor
    que ninguna — el alumno no tiene cómo saber que le falta el final.
    """
    stack: list[str] = []
    in_string = False
    escaped = False
    cut: tuple[int, tuple[str, ...]] | None = None
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append("}" if char == "{" else "]")
        elif char in "}]":
            if not stack:
                return None
            stack.pop()
            # Un contenedor cerrado también es frontera: todo lo anterior cerró.
            cut = (index + 1, tuple(stack))
        elif char == ",":
            cut = (index, tuple(stack))
    if cut is None or not stack:
        # Sin frontera no hay nada completo que rescatar, y con la pila vacía el
        # texto estaba balanceado: si igual no parsea, el problema no es el
        # corte y taparlo acá escondería un contrato roto de verdad.
        return None
    end, still_open = cut
    candidate = text[:end].rstrip().rstrip(",") + "".join(reversed(still_open))
    try:
        payload = json.loads(candidate)
    except (TypeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def parse_simulation_turn(raw: str) -> SimulationTurn:
    """Parse and validate the provider's structured turn."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```")
        text = text.removesuffix("```").strip()
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        payload = _close_truncated_json(text)
        if payload is None:
            raise SimulationOutputError(
                "The interview simulator returned invalid JSON"
            ) from exc
        logger.warning(
            "Simulacro: respuesta truncada (%d caracteres), se recuperó el "
            "turno parcial: %s",
            len(text),
            exc,
        )
    if not isinstance(payload, dict):
        raise SimulationOutputError("The interview simulator response must be an object")
    action = str(payload.get("action", "")).strip()
    if action not in VALID_ACTIONS:
        raise SimulationOutputError(f"Invalid simulator action: {action or '(empty)'}")
    question = str(payload.get("question", "")).strip()
    if action != "finish" and not question:
        raise SimulationOutputError("A continuing turn requires a question")
    return SimulationTurn(
        action=action,
        question=question,
        feedback=str(payload.get("feedback", "")).strip(),
        strengths=_text_list(payload.get("strengths")),
        improvements=_text_list(payload.get("improvements")),
        example_answer=str(payload.get("example_answer", "")).strip(),
    )


def parse_hint(raw: str) -> str:
    text = (raw or "").strip().removeprefix("```json").removeprefix("```")
    text = text.removesuffix("```").strip()
    try:
        payload = json.loads(text)
        hint = str(payload.get("hint", "")).strip() if isinstance(payload, dict) else ""
    except (TypeError, json.JSONDecodeError) as exc:
        raise SimulationOutputError("The simulator returned an invalid hint") from exc
    if not hint:
        raise SimulationOutputError("The simulator returned an empty hint")
    return hint


def _gemini_request(key: str, prompt: str, timeout_seconds: float) -> str:
    """One Gemini call, bounded. Isolated so the retry loop stays fakeable."""
    from google import genai
    from google.genai import types

    # `HttpOptions.timeout` va en MILISEGUNDOS (google-genai 2.15.0). Pasarle
    # segundos deja un techo de 12 ms y todo falla al instante.
    client = genai.Client(
        api_key=key,
        http_options=types.HttpOptions(timeout=int(timeout_seconds * 1000)),
    )
    response = client.models.generate_content(
        model=DEFAULT_MODEL,
        contents=prompt,
        config={"temperature": 0.35, "max_output_tokens": _MAX_OUTPUT_TOKENS},
    )
    text = getattr(response, "text", None)
    if not text:
        raise RuntimeError("Gemini devolvió una respuesta vacía")
    return text


def _generate_with_gemini(prompt: str, deadline: float) -> str | None:
    """Walk the key pool; return None when Gemini cannot answer in time.

    Antes se usaba ``pool.available()[0]`` y nada más: un 503 transitorio en la
    primera key mataba el turno entero aunque hubiera dos claves sanas detrás.
    Ahora la saturación se reintenta UNA vez sobre la misma key, cuota y auth
    la marcan agotada (no vuelve a probarse en esta sesión) y todo corre contra
    un deadline: los reintentos están acotados por diseño, un simulacro
    congelado es peor que un error reintentable.
    """
    for key in gemini_keys.pool.available():
        for attempt in range(_GEMINI_ATTEMPTS_PER_KEY):
            remaining = deadline - time.monotonic()
            timeout = gemini_request_timeout(remaining)
            if timeout is None:
                # Se corta en el piso de la API, no en cero: por debajo de
                # `_GEMINI_MIN_DEADLINE_SECONDS` Gemini contesta 400 sin
                # mirar el prompt, así que el request no es "corto", es
                # imposible. Los segundos que quedan valen más en el respaldo.
                logger.warning(
                    "Simulacro: quedan %.1f s, menos del mínimo de %.0f s que "
                    "acepta Gemini; se salta al respaldo",
                    max(0.0, remaining),
                    _GEMINI_MIN_DEADLINE_SECONDS,
                )
                return None
            try:
                return _gemini_request(key, prompt, timeout)
            except Exception as exc:
                kind = nvidia_provider.classify_error(exc)
                logger.warning(
                    "Interview simulator Gemini call failed (%s, %s): %s",
                    gemini_keys.pool.current_label(),
                    kind,
                    exc,
                )
                if nvidia_provider.is_client_request_error(exc):
                    # La petición estaba mal armada: es un bug nuestro, no un
                    # problema de la clave ni del endpoint. Va ANTES de la rama
                    # de cuota/auth justamente para que no pueda terminar
                    # quemando una key sana por un error de código.
                    #
                    # Y corta la etapa entera en vez de pasar a la clave
                    # siguiente: las otras nueve fallarían idéntico, porque lo
                    # que Gemini rechaza es el request. Ese `break` que seguía
                    # rotando costaba diez llamadas condenadas por turno, y cada
                    # una le robaba presupuesto a Groq y a NVIDIA.
                    logger.error(
                        "Simulacro: Gemini rechazó la petición (error de "
                        "cliente, no de la clave): %s",
                        exc,
                    )
                    return None
                if gemini_keys.pool.is_quota_error(exc) or kind in (
                    nvidia_provider.RATE_LIMIT,
                    nvidia_provider.AUTH,
                ):
                    # Cuota y auth no se arreglan reintentando: se quema la key.
                    gemini_keys.pool.mark_exhausted(key)
                    break
                is_last_attempt = attempt == _GEMINI_ATTEMPTS_PER_KEY - 1
                if (
                    kind == nvidia_provider.SATURATION
                    and not is_last_attempt
                    and deadline - time.monotonic() > _GEMINI_SATURATION_BACKOFF_SECONDS
                ):
                    time.sleep(_GEMINI_SATURATION_BACKOFF_SECONDS)
                    continue
                break
    return None


def generate_simulation_text(prompt: str, *, budget_seconds: float | None = None) -> str:
    """Generate one simulator turn: Gemini, luego Groq, luego NVIDIA.

    Misma cadena que el coach (``provider_chain``). El simulacro la salteaba y
    caía directo a NVIDIA, así que el día que el modelo NVIDIA por defecto
    llegó a su end of life el profesor IA quedaba mudo con Groq configurado.
    """
    budget = budget_seconds if budget_seconds is not None else TOTAL_BUDGET_SECONDS
    started = time.monotonic()
    deadline = started + budget

    tried_gemini = bool(gemini_keys.pool.available())
    if tried_gemini:
        text = _generate_with_gemini(
            prompt, min(deadline, started + min(GEMINI_BUDGET_SECONDS, budget))
        )
        if text:
            return text

    if not provider_chain.has_fallback_provider():
        if tried_gemini:
            raise RuntimeError(
                "Gemini no pudo responder el simulacro y no hay respaldo "
                "configurado (GROQ_API_KEY / NVIDIA_API_KEY en .env)"
            )
        raise RuntimeError("No hay un proveedor de IA disponible para el simulacro")
    return provider_chain.generate_with_fallback(
        prompt, max_tokens=_MAX_OUTPUT_TOKENS, deadline=deadline
    )


class InterviewSimulationSession:
    """Small state machine for proactive questions and reactive follow-ups."""

    def __init__(
        self,
        context: str,
        generate: Callable[[str], str] = generate_simulation_text,
        max_questions: int = 6,
        answer_lang: str = "es",
        simulation_type: str = "interview",
    ) -> None:
        self.context = (context or "").strip() or "General job interview"
        self.generate = generate
        self.max_questions = max(1, min(int(max_questions), 12))
        self.answer_lang = answer_lang if answer_lang in {"es", "en"} else "es"
        self.simulation_type = (
            simulation_type if simulation_type in {"interview", "academic"} else "interview"
        )
        self.state = "ready"
        self.question_count = 0
        self._history: list[tuple[str, str]] = []
        self._turns: list[SimulationTurn] = []

    def start(self) -> SimulationTurn:
        if self.state != "ready":
            raise RuntimeError("The simulation has already started")
        turn = self._request_turn(answer="")
        if turn.action == "finish":
            raise SimulationOutputError("The first turn must contain a question")
        self.question_count = 1
        self.state = "waiting_answer"
        self._history.append(("INTERVIEWER", turn.question))
        self._turns.append(turn)
        return turn

    def submit_answer(self, answer: str) -> SimulationTurn:
        if self.state != "waiting_answer":
            raise RuntimeError("The simulation is not waiting for an answer")
        clean = (answer or "").strip()
        if not clean:
            raise ValueError("La respuesta no puede estar vacía")
        self.state = "evaluating"
        mark = len(self._history)
        self._history.append(("CANDIDATE", clean))
        try:
            turn = self._request_turn(answer=clean)
            if turn.action != "finish" and self._was_asked(turn.question):
                turn = self._request_turn(answer=clean, avoid_question=turn.question)
                if turn.action != "finish" and self._was_asked(turn.question):
                    turn = replace(turn, action="finish", question="")
        except Exception:
            # Un turno fallido NO puede dejar la sesión trabada. Sin esto el
            # estado quedaba en "evaluating" para siempre: la UI reactivaba los
            # botones (``_simulation_failed``) y el siguiente click moría con
            # "The simulation is not waiting for an answer". Ese era el
            # simulacro muerto de verdad, no el JSON roto.
            #
            # La respuesta del alumno también se saca: el reintento la vuelve a
            # mandar, y dos CANDIDATE iguales en ``_history`` le enseñan al
            # modelo que el alumno se repitió.
            del self._history[mark:]
            self.state = "waiting_answer"
            raise
        if self.question_count >= self.max_questions:
            turn = replace(turn, action="finish", question="")
        if turn.action == "finish":
            self.state = "completed"
        else:
            self.question_count += 1
            self.state = "waiting_answer"
            self._history.append(("INTERVIEWER", turn.question))
        self._turns.append(turn)
        return turn

    def hint(self) -> str:
        if self.state != "waiting_answer":
            raise RuntimeError("The simulation is not waiting for an answer")
        current = next(
            (text for role, text in reversed(self._history) if role == "INTERVIEWER"),
            "",
        )
        language = "Spanish" if self.answer_lang == "es" else "English"
        prompt = f"""You are an oral exam professor. Give one short conceptual hint in {language}.
Do not state the answer and do not ask another question.
Study material:
{self.context}
Current question: {current}
Return only valid JSON: {{"hint":""}}"""
        return parse_hint(self.generate(prompt))

    def skip_mastered_question(self) -> SimulationTurn:
        """Advance without inventing or grading a candidate answer."""
        if self.state != "waiting_answer":
            raise RuntimeError("The simulation is not waiting for an answer")
        current = next(
            (text for role, text in reversed(self._history) if role == "INTERVIEWER"),
            "",
        )
        self.state = "evaluating"
        try:
            turn = self._request_turn(answer="", avoid_question=current)
        except Exception:
            # Mismo contrato que ``submit_answer``: saltear una pregunta y que
            # falle el proveedor deja la sesión donde estaba, reintentable.
            self.state = "waiting_answer"
            raise
        if turn.action != "finish" and self._was_asked(turn.question):
            turn = replace(turn, action="finish", question="")
        if self.question_count >= self.max_questions:
            turn = replace(turn, action="finish", question="")
        if turn.action == "finish":
            self.state = "completed"
        else:
            self.question_count += 1
            self.state = "waiting_answer"
            self._history.append(("INTERVIEWER", turn.question))
        self._turns.append(turn)
        return turn

    @staticmethod
    def _question_key(question: str) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", question.casefold()))

    def _was_asked(self, question: str) -> bool:
        key = self._question_key(question)
        return bool(key) and any(
            role == "INTERVIEWER" and self._question_key(text) == key
            for role, text in self._history
        )

    def finish(self) -> str:
        self.state = "completed"
        return self._build_report()

    def report(self) -> str:
        return self._build_report()

    def _build_report(self) -> str:
        strengths = self._unique_items("strengths")
        improvements = self._unique_items("improvements")
        lines = ["Simulacro finalizado."]
        if strengths:
            lines.append("Fortalezas: " + "; ".join(strengths))
        if improvements:
            lines.append("Para mejorar: " + "; ".join(improvements))
            lines.append("Recomendación: practicá otra respuesta enfocándote en " + improvements[0] + ".")
        if not strengths and not improvements:
            lines.append("Completá al menos una respuesta para recibir una devolución.")
        return "\n".join(lines)

    def _unique_items(self, field: str) -> list[str]:
        result: list[str] = []
        for turn in self._turns:
            for item in getattr(turn, field):
                if item not in result:
                    result.append(item)
        return result[:6]

    def _request_turn(self, answer: str, avoid_question: str = "") -> SimulationTurn:
        history = "\n".join(f"{role}: {text}" for role, text in self._history[-10:])
        language = "Spanish" if self.answer_lang == "es" else "English"
        stage = "Ask the opening question" if not answer else "Evaluate the answer and choose the next action"
        role = (
            "You are an oral exam professor conducting academic practice. "
            "Ask only about the supplied study material, evaluate conceptual correctness, "
            "and identify omissions or confusion."
            if self.simulation_type == "academic"
            else "You conduct a realistic job interview simulation. "
            "Cover relevant competencies and professional experience."
        )
        participant = "student" if self.simulation_type == "academic" else "candidate"
        normalized_answer = answer.casefold()
        needs_teaching = self.simulation_type == "academic" and any(
            marker in normalized_answer
            for marker in ("no lo sé", "no lo se", "explicame", "explain")
        )
        teaching_instruction = (
            "The student explicitly does not know. Teach the concept clearly in feedback, "
            "provide a concrete example_answer, and ask one easier follow-up question to "
            "check understanding. Do not shame or merely mark the answer incorrect."
            if needs_teaching
            else ""
        )
        prompt = f"""{role}
Be proactive: cover relevant topics and advance the simulation.
Be reactive: use the {participant}'s actual answer to decide whether to follow up, change topic, or finish.
Ask exactly one concise question at a time in {language}. Do not repeat covered questions.

Context and study material:
{self.context}

Recent conversation:
{history or '(no turns yet)'}

Task: {stage}.
{teaching_instruction}
{f'The proposed question was already asked: {avoid_question!r}. Generate a different question.' if avoid_question else ''}
Return only valid JSON with this exact shape:
{{"action":"follow_up|next_topic|finish","question":"", "feedback":"brief private feedback", "strengths":[""], "improvements":[""], "example_answer":""}}
After evaluating an answer, example_answer must show one concise improved answer grounded in the supplied material and written in {language}.
For the opening turn use next_topic, leave feedback lists and example_answer empty, and ask a question.
Use finish only when the interview has enough evidence. A finish response must have an empty question.
"""
        return self._generate_turn(prompt)

    def _generate_turn(self, prompt: str) -> SimulationTurn:
        """Pedir un turno válido, con un reintento acotado.

        ``_was_asked`` ya reintentaba cuando el modelo repetía la pregunta, pero
        no cuando el JSON venía roto: cualquier ``SimulationOutputError`` subía
        hasta la UI y terminaba el simulacro. El reintento no toca
        ``_history``, así que no puede duplicar la pregunta, y la deduplicación
        de ``submit_answer`` corre igual sobre lo que devuelva.

        El techo de tiempo es el mismo presupuesto que ya existe: si el primer
        intento se comió los 40 s no hay segundo, porque un usuario mirando la
        pantalla prefiere un error reintentable a una ventana congelada. En la
        práctica el caso que importa es rápido — llegar acá significa que un
        proveedor SÍ contestó, mal pero en 1,3 s.
        """
        started = time.monotonic()
        last_exc: SimulationOutputError | None = None
        remaining = TOTAL_BUDGET_SECONDS
        for attempt in range(_TURN_ATTEMPTS):
            text = prompt
            if attempt:
                text = prompt + "\n" + _RETRY_INSTRUCTION
            try:
                if attempt:
                    # self.generate is generate_simulation_text: called without
                    # budget_seconds it opens a BRAND NEW 40 s Gemini+fallback
                    # window. A slow invalid answer then chained two full windows
                    # and froze the simulator well past the documented ceiling.
                    # The retry spends only what is left (CLAUDE.md: every
                    # provider is bounded twice).
                    raw = self._generate_bounded(text, remaining)
                else:
                    raw = self.generate(text)
                return parse_simulation_turn(raw)
            except SimulationOutputError as exc:
                last_exc = exc
                logger.warning(
                    "Simulacro: turno inválido (intento %d de %d): %s",
                    attempt + 1,
                    _TURN_ATTEMPTS,
                    exc,
                )
                remaining = TOTAL_BUDGET_SECONDS - (time.monotonic() - started)
                if remaining <= 0:
                    logger.warning(
                        "Simulacro: sin presupuesto para reintentar el turno"
                    )
                    break
        raise last_exc  # type: ignore[misc]

    def _generate_bounded(self, prompt: str, budget_seconds: float) -> str:
        """Call ``self.generate`` with a budget when it accepts one.

        Injected single-argument callables (tests, adapters) keep working:
        they simply get the prompt, as before.
        """
        import inspect

        try:
            params = inspect.signature(self.generate).parameters
        except (TypeError, ValueError):
            params = {}
        accepts_budget = "budget_seconds" in params or any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
        )
        if accepts_budget:
            return self.generate(prompt, budget_seconds=budget_seconds)
        return self.generate(prompt)
