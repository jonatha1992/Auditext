"""Adaptive interviewer simulation independent from system-audio capture."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, replace
from typing import Callable

from config import logger
from infrastructure.services import gemini_keys, nvidia_provider


VALID_ACTIONS = {"follow_up", "next_topic", "finish"}
DEFAULT_MODEL = os.getenv("GEMINI_COACH_MODEL", "").strip() or "gemini-3.1-flash-lite"


class SimulationOutputError(ValueError):
    """Raised when a provider violates the simulator JSON contract."""


def is_configured() -> bool:
    return gemini_keys.is_configured() or nvidia_provider.is_configured()


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


def parse_simulation_turn(raw: str) -> SimulationTurn:
    """Parse and validate the provider's structured turn."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```")
        text = text.removesuffix("```").strip()
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise SimulationOutputError("The interview simulator returned invalid JSON") from exc
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


def generate_simulation_text(prompt: str) -> str:
    """Generate one simulator turn through configured providers."""
    keys = gemini_keys.pool.available()
    if keys:
        try:
            from google import genai

            client = genai.Client(api_key=keys[0])
            response = client.models.generate_content(
                model=DEFAULT_MODEL,
                contents=prompt,
                config={"temperature": 0.35, "max_output_tokens": 700},
            )
            text = getattr(response, "text", None)
            if text:
                return text
        except Exception as exc:
            logger.warning("Interview simulator Gemini call failed: %s", exc)
    if nvidia_provider.is_configured():
        return nvidia_provider.generate(prompt, budget_seconds=12.0)
    raise RuntimeError("No hay un proveedor de IA disponible para el simulacro")


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
        self._history.append(("CANDIDATE", clean))
        turn = self._request_turn(answer=clean)
        if turn.action != "finish" and self._was_asked(turn.question):
            turn = self._request_turn(answer=clean, avoid_question=turn.question)
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
        turn = self._request_turn(answer="", avoid_question=current)
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
        return parse_simulation_turn(self.generate(prompt))
