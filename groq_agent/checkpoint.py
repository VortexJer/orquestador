"""Instantaneas del workspace antes de cada tarea, con `--diff` y `--undo`.

POR QUE
El agente escribe archivos reales. Hoy pide confirmacion antes de cada
escritura, pero una vez aceptada NO HAY VUELTA ATRAS: si el modelo
reescribe un index.html de 600 lineas y el resultado es peor, lo anterior
se perdio. Y el workspace no es necesariamente un repo git, asi que no se
puede dar por hecho que haya historial.

POR QUE NO SE USA GIT
Seria lo natural, pero obligaria a `git init` en el workspace del usuario -
crear un repo dentro de una carpeta ajena es una decision que no le
corresponde tomar a esta herramienta, y ademas romperia el caso de un
workspace que YA es un repo (sus commits se mezclarian con los nuestros).
Se copian los archivos a un directorio propio y aparte.

QUE SE GUARDA
Solo archivos de texto por debajo de un tope, y nada de carpetas de
dependencias. Copiar node_modules o .venv convertiria cada tarea en varios
segundos de I/O y llenaria el disco - y no es lo que se quiere recuperar.
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

DIR_CHECKPOINTS = ".orquestador_checkpoints"
MAX_CHECKPOINTS = 20
# Un archivo mas grande que esto casi seguro es un binario o un volcado; no
# es lo que uno quiere deshacer y copiarlo sale caro.
MAX_BYTES_ARCHIVO = 2_000_000

# Carpetas que no se copian nunca: son regenerables y enormes.
IGNORAR = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "dist", "build", ".next", ".cache",
    DIR_CHECKPOINTS, ".orquestador_sessions",
}


@dataclass
class Checkpoint:
    id: str
    creado: str
    tarea: str
    n_archivos: int

    @property
    def resumen(self) -> str:
        return f"{self.creado}  ({self.n_archivos} archivos)  {self.tarea[:50]}"


def _raiz(workspace: Path) -> Path:
    return workspace / DIR_CHECKPOINTS


def _archivos_del_workspace(workspace: Path):
    for ruta in workspace.rglob("*"):
        if not ruta.is_file():
            continue
        partes = set(ruta.relative_to(workspace).parts)
        if partes & IGNORAR:
            continue
        try:
            if ruta.stat().st_size > MAX_BYTES_ARCHIVO:
                continue
        except OSError:
            continue
        yield ruta


def crear(workspace: Path, tarea: str) -> Checkpoint | None:
    """Copia el estado actual del workspace. None si no hay nada que copiar.

    Los fallos no se propagan: un checkpoint es una red de seguridad, y que
    la red falle no puede impedir hacer el trabajo."""
    try:
        raiz = _raiz(workspace)
        cid = time.strftime("%Y%m%d_%H%M%S")
        destino = raiz / cid
        if destino.exists():
            cid = f"{cid}_{int(time.time() * 1000) % 1000}"
            destino = raiz / cid

        n = 0
        for ruta in _archivos_del_workspace(workspace):
            relativa = ruta.relative_to(workspace)
            copia = destino / "archivos" / relativa
            copia.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ruta, copia)
            n += 1

        if n == 0:
            # Workspace vacio: no hay nada que deshacer, y un checkpoint
            # vacio solo ensuciaria la lista.
            if destino.exists():
                shutil.rmtree(destino, ignore_errors=True)
            return None

        meta = {
            "id": cid,
            "creado": time.strftime("%Y-%m-%d %H:%M:%S"),
            "tarea": tarea[:200],
            "n_archivos": n,
        }
        (destino / "meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )
        _podar(workspace)
        return Checkpoint(**meta)
    except OSError:
        return None


def _podar(workspace: Path) -> None:
    """Deja solo los MAX_CHECKPOINTS mas recientes."""
    puntos = sorted(_raiz(workspace).glob("*/"), key=lambda p: p.name)
    for viejo in puntos[:-MAX_CHECKPOINTS]:
        shutil.rmtree(viejo, ignore_errors=True)


def listar(workspace: Path) -> list[Checkpoint]:
    """Del mas reciente al mas antiguo."""
    raiz = _raiz(workspace)
    if not raiz.exists():
        return []
    salida = []
    for carpeta in sorted(raiz.iterdir(), reverse=True):
        meta = carpeta / "meta.json"
        if not meta.is_file():
            continue
        try:
            salida.append(Checkpoint(**json.loads(meta.read_text(encoding="utf-8"))))
        except (ValueError, TypeError, OSError):
            continue
    return salida


def diff(workspace: Path, checkpoint_id: str | None = None) -> list[tuple[str, str]]:
    """Que cambio desde un checkpoint. Devuelve [(estado, ruta)] con estado
    en {'nuevo', 'modificado', 'borrado'}.

    Se compara por tamaño y contenido, no por fecha: el agente reescribe
    archivos enteros y la fecha cambia siempre, incluso si el contenido
    quedo igual - eso daria un diff lleno de ruido."""
    puntos = listar(workspace)
    if not puntos:
        return []
    destino = checkpoint_id or puntos[0].id
    base = _raiz(workspace) / destino / "archivos"
    if not base.exists():
        return []

    antes = {
        p.relative_to(base).as_posix(): p
        for p in base.rglob("*") if p.is_file()
    }
    ahora = {
        p.relative_to(workspace).as_posix(): p
        for p in _archivos_del_workspace(workspace)
    }

    cambios: list[tuple[str, str]] = []
    for rel, actual in sorted(ahora.items()):
        previo = antes.get(rel)
        if previo is None:
            cambios.append(("nuevo", rel))
        else:
            try:
                if previo.read_bytes() != actual.read_bytes():
                    cambios.append(("modificado", rel))
            except OSError:
                continue
    for rel in sorted(antes):
        if rel not in ahora:
            cambios.append(("borrado", rel))
    return cambios


def restaurar(workspace: Path, checkpoint_id: str | None = None) -> tuple[int, str]:
    """Devuelve el workspace al estado de un checkpoint.

    Antes de restaurar se crea OTRO checkpoint del estado actual: deshacer
    tampoco puede ser irreversible. Si el usuario se arrepiente del undo,
    lo tiene ahi."""
    puntos = listar(workspace)
    if not puntos:
        return 0, "no hay checkpoints en este workspace"
    destino = checkpoint_id or puntos[0].id
    base = _raiz(workspace) / destino / "archivos"
    if not base.exists():
        return 0, f"el checkpoint '{destino}' no existe"

    crear(workspace, f"[antes de deshacer hasta {destino}]")

    restaurados = 0
    for origen in base.rglob("*"):
        if not origen.is_file():
            continue
        actual = workspace / origen.relative_to(base)
        actual.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(origen, actual)
            restaurados += 1
        except OSError:
            continue

    # Los archivos creados DESPUES del checkpoint no se borran: podrian ser
    # trabajo del usuario ajeno a la tarea, y borrarlos seria destruir algo
    # que nadie pidio destruir. Se listan para que decida.
    sobrantes = [r for estado, r in diff(workspace, destino) if estado == "nuevo"]
    nota = f"restaurados {restaurados} archivo(s) del checkpoint {destino}"
    if sobrantes:
        nota += (
            f"; {len(sobrantes)} archivo(s) creados despues NO se borraron "
            f"(borralos a mano si sobran): {', '.join(sobrantes[:5])}"
        )
    return restaurados, nota
