"""Small subprocess adapter for the unofficial NotebookLM CLI.

NotebookLM does not currently expose a public application API. Keeping the
community CLI behind this module prevents presentation code from depending on
its command syntax and makes failures recoverable.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


class NotebookLMError(RuntimeError):
    """NotebookLM is unavailable, unauthenticated, or returned invalid data."""


@dataclass(frozen=True)
class NotebookRef:
    id: str
    title: str
    source_count: int = 0

    @property
    def label(self) -> str:
        suffix = f" · {self.source_count} fuentes" if self.source_count else ""
        return f"{self.title}{suffix}"


def _nlm_executable() -> str:
    local_name = "nlm.exe" if os.name == "nt" else "nlm"
    local = Path(sys.executable).resolve().parent / local_name
    if local.exists():
        return str(local)
    found = shutil.which("nlm")
    if found:
        return found
    raise NotebookLMError(
        "Falta el cliente NotebookLM. Instalá notebooklm-mcp-cli en el entorno."
    )


def _run_json(args: list[str], timeout: int = 120):
    command = [_nlm_executable(), *args, "--json"]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise NotebookLMError("NotebookLM tardó demasiado en responder.") from exc
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "").strip()
        if "auth" in detail.lower() or "login" in detail.lower():
            raise NotebookLMError(
                "NotebookLM no está conectado. Tocá «Conectar» e iniciá sesión."
            )
        raise NotebookLMError(detail or "NotebookLM no pudo completar la operación.")
    raw = completed.stdout.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise NotebookLMError("NotebookLM devolvió una respuesta inválida.") from exc


def _items(payload) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("notebooks", "items", "data", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _items(value)
            if nested:
                return nested
    return []


def list_notebooks() -> list[NotebookRef]:
    payload = _run_json(["notebook", "list"], timeout=45)
    notebooks: list[NotebookRef] = []
    for item in _items(payload):
        notebook_id = str(item.get("id") or item.get("notebook_id") or "").strip()
        title = str(item.get("title") or item.get("name") or "").strip()
        if not (notebook_id and title):
            continue
        count = item.get("source_count") or item.get("sources_count") or 0
        try:
            source_count = int(count)
        except (TypeError, ValueError):
            source_count = 0
        notebooks.append(NotebookRef(notebook_id, title, source_count))
    return sorted(notebooks, key=lambda notebook: notebook.title.casefold())


_SYNC_QUESTION = """Prepará una guía de consulta para responder un examen oral.
Incluí definiciones, relaciones, procedimientos, fórmulas o ejemplos importantes
de las fuentes. Organizala por temas y usá información concreta. Máximo 2200
caracteres. Respondé solo con la guía en español, sin introducción."""


def sync_study_context(notebook_id: str) -> str:
    payload = _run_json(
        [
            "query",
            "notebook",
            notebook_id,
            _SYNC_QUESTION,
            "--timeout",
            "120",
        ],
        timeout=135,
    )
    if isinstance(payload, str):
        answer = payload
    elif isinstance(payload, dict):
        answer = str(
            payload.get("answer")
            or payload.get("response")
            or payload.get("text")
            or ""
        )
    else:
        answer = ""
    answer = answer.strip()
    if not answer:
        raise NotebookLMError("NotebookLM no devolvió material para esta materia.")
    return answer[:2500]


def launch_login() -> None:
    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    subprocess.Popen([_nlm_executable(), "login"], creationflags=creationflags)
