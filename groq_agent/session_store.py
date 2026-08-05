"""Persistencia de sesiones de la terminal - equivalente a `claude -c`
(continuar la ultima) y `claude -r` (elegir una anterior). Cada sesion
se guarda DENTRO del workspace (`<workspace>/.orquestador_sessions/`),
igual que Claude Code liga sus sesiones al directorio del proyecto: si
se mueve o se borra el workspace, sus sesiones van con el.

Una sesion guarda dos cosas distintas:
- `turns`: log plano de cada tarea (para mostrar el listado de --resume).
- `conversations`: el historial de mensajes POR ESPECIALISTA - en modo
  auto, tareas seguidas pueden ir a especialistas distintos, asi que
  cada especialista mantiene su propio hilo; continuar la sesion
  retoma el hilo correcto segun a que especialista se rutee la
  proxima tarea.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

SESSIONS_DIRNAME = ".orquestador_sessions"


def sessions_dir(workspace: Path) -> Path:
    directory = workspace / SESSIONS_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def new_session_id() -> str:
    return f"sess_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"


def session_path(workspace: Path, session_id: str) -> Path:
    return sessions_dir(workspace) / f"{session_id}.json"


def save_session(
    workspace: Path,
    session_id: str,
    conversations: dict[str, list[dict]],
    turns: list[dict],
    created_at: str,
) -> None:
    data = {
        "session_id": session_id,
        "created_at": created_at,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "turns": turns,
        "conversations": conversations,
    }
    # Un nombre puesto a mano (ver rename_session) tiene que sobrevivir a los
    # guardados posteriores de la sesion, que reescriben el fichero entero.
    path = session_path(workspace, session_id)
    if path.exists():
        try:
            previo = json.loads(path.read_text(encoding="utf-8"))
            if previo.get("name"):
                data["name"] = previo["name"]
        except (json.JSONDecodeError, OSError):
            pass
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def rename_session(workspace: Path, session_id: str, name: str) -> None:
    """Pone un nombre a mano a la sesion (el que se muestra en el selector en
    vez del auto-generado del primer prompt). Un nombre vacio lo quita."""
    data = load_session(workspace, session_id)
    nombre = (name or "").strip()
    if nombre:
        data["name"] = nombre
    else:
        data.pop("name", None)
    session_path(workspace, session_id).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def delete_session(workspace: Path, session_id: str) -> bool:
    """Borra el fichero de la sesion. Devuelve si existia."""
    path = session_path(workspace, session_id)
    existia = path.exists()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return existia


def load_session(workspace: Path, session_id: str) -> dict:
    path = session_path(workspace, session_id)
    if not path.exists():
        raise FileNotFoundError(f"No existe la sesion '{session_id}' en {workspace}.")
    return json.loads(path.read_text(encoding="utf-8"))


def list_sessions(workspace: Path) -> list[dict]:
    """Metadata de todas las sesiones del workspace, mas reciente primero."""
    sessions = []
    for path in sessions_dir(workspace).glob("sess_*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        turns = data.get("turns", [])
        preview = turns[0]["task"][:80] if turns else "(sin tareas)"
        sessions.append({
            "session_id": data.get("session_id", path.stem),
            "updated_at": data.get("updated_at", ""),
            "turn_count": len(turns),
            "preview": preview,
            "name": data.get("name") or None,   # nombre puesto a mano, si lo hay
        })
    sessions.sort(key=lambda s: s["updated_at"], reverse=True)
    return sessions


def find_latest_session_id(workspace: Path) -> str | None:
    sessions = list_sessions(workspace)
    return sessions[0]["session_id"] if sessions else None
