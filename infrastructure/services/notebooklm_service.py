"""Small subprocess adapter for the unofficial NotebookLM CLI.

NotebookLM does not currently expose a public application API. Keeping the
community CLI behind this module prevents presentation code from depending on
its command syntax and makes failures recoverable.
"""

from __future__ import annotations

import json
import os
import re
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


@dataclass(frozen=True)
class ProfileRef:
    """One authenticated NotebookLM account known to the CLI."""

    name: str
    email: str = ""

    @property
    def label(self) -> str:
        return f"{self.email} ({self.name})" if self.email else self.name


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


def _profile_env(profile: str | None) -> dict[str, str] | None:
    """Environment that pins one account for a single CLI call.

    ``nlm login switch`` would rewrite the CLI's default profile for the whole
    machine, and that same CLI backs other tools on this box. ``NLM_PROFILE``
    scopes the account to the subprocess, so choosing a subject here never
    changes which account anything else is talking to.
    """
    if not profile:
        return None
    env = dict(os.environ)
    env["NLM_PROFILE"] = profile
    return env


def _run_json(args: list[str], timeout: int = 120, profile: str | None = None):
    command = [_nlm_executable(), *args, "--json"]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_profile_env(profile),
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


def list_notebooks(profile: str | None = None) -> list[NotebookRef]:
    payload = _run_json(["notebook", "list"], timeout=45, profile=profile)
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


def sync_study_context(notebook_id: str, profile: str | None = None) -> str:
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
        profile=profile,
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


def launch_login(profile: str | None = None) -> None:
    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    args = [_nlm_executable(), "login"]
    if profile:
        args += ["--profile", profile]
    subprocess.Popen(args, creationflags=creationflags)


# ``  <nombre>: <email>`` en la salida de ``nlm login profile list``. Ese
# comando no acepta ``--json`` (verificado en la 0.9.4), así que la lista se lee
# del texto plano; sin TTY el CLI no emite códigos de color.
_PROFILE_LINE = re.compile(r"^\s{2}(\S+):\s*(.*)$")


def list_profiles() -> list[ProfileRef]:
    """Accounts the CLI has credentials for, in the order it reports them."""
    command = [_nlm_executable(), "login", "profile", "list"]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise NotebookLMError("NotebookLM tardó demasiado en responder.") from exc
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise NotebookLMError(detail or "No se pudieron leer las cuentas de NotebookLM.")
    profiles: list[ProfileRef] = []
    for line in (completed.stdout or "").splitlines():
        match = _PROFILE_LINE.match(line.rstrip())
        if not match:
            continue
        name, email = match.group(1), match.group(2).strip()
        # Una cuenta sin credenciales válidas se lista como "(invalid)": no
        # sirve para consultar, y ofrecerla solo produce un error más tarde.
        if not email or email.startswith("("):
            continue
        profiles.append(ProfileRef(name, email))
    return profiles
