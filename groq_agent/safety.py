"""Confinamiento de rutas: por defecto ninguna herramienta de groq_agent
toca nada fuera del --workspace que el usuario eligio al arrancar la
terminal. Esta es la primera red de seguridad frente a un modelo de
terceros (vía la API de OpenRouter) con capacidad de escritura sobre el
filesystem - pero NO alcanza sola: quedarse DENTRO del workspace no dice
nada sobre si el contenido de ahi adentro deberia ser visible. Si corres
la terminal parado en la raiz de un repo (incluido este mismo), el
workspace default ('.') incluye .env, .venv/, __pycache__/,
node_modules/, etc. - ahi es donde entran is_hidden_path/is_sensitive_file
de abajo.

EXCEPCION CONTROLADA (resolve_write_destination): cuando el USUARIO pide
guardar en otro sitio - una carpeta conocida ('descargas', 'escritorio',
'documentos') o una ruta absoluta que escribio - la escritura puede salir
del workspace, pero SOLO tras una confirmacion obligatoria que le muestra
la ruta absoluta (no la saltan --yes ni 'aceptar todas', ver
tools.ToolExecutor._destino_escritura). Sigue siendo "el humano fija el
limite, no el modelo": el modelo propone el destino, el humano lo aprueba.
Las rutas RELATIVAS nunca salen (mantienen la proteccion contra `../`), y
is_hidden_path/is_sensitive_file bloquean secretos dentro Y fuera.

Y por debajo de TODO, un bloqueo DURO: is_system_path prohibe escribir en
directorios del sistema (Windows/Program Files/ProgramData, la
instalacion de Python, /etc y demas) aunque el usuario lo pida y confirme.
Esa red no la levanta nada - un "si" a destiempo no puede tocar el SO.
"""
from __future__ import annotations

import fnmatch
import os
import sys
from pathlib import Path


class PathEscapeError(Exception):
    pass


# --- Bloqueo duro de rutas de SISTEMA ---------------------------------
#
# Segunda linea, por debajo de la confirmacion: aunque el usuario pida
# guardar fuera del workspace y confirme, NUNCA se escribe en un
# directorio del sistema operativo (ni en la instalacion de Python). Un
# "si" a destiempo, o una ruta que el modelo proponga mal, no puede
# terminar tocando C:\Windows o Program Files. Es estrecho a proposito:
# las carpetas normales del usuario (Escritorio/Descargas/Documentos, o
# cualquier ruta suya en C:\Users\... o en otra unidad) siguen valiendo.
def _sistema_roots() -> list[Path]:
    roots: list[Path] = []
    # Windows: por variable de entorno (robusto a unidad distinta de C: y
    # a nombres localizados).
    for var in (
        "SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)",
        "ProgramW6432", "ProgramData",
    ):
        v = os.environ.get(var)
        if v:
            try:
                roots.append(Path(v).resolve())
            except OSError:
                pass
    # Unix-like (el objetivo es win32, pero no cuesta cubrirlo).
    for p in ("/etc", "/bin", "/sbin", "/usr", "/boot", "/proc", "/sys",
              "/dev", "/System", "/Library", "/private", "/var/root"):
        pp = Path(p)
        if pp.exists():
            try:
                roots.append(pp.resolve())
            except OSError:
                pass
    # La instalacion de Python (o el venv activo): jamas es un destino de
    # guardado legitimo, y escribir ahi puede romper el propio runtime.
    for pref in (getattr(sys, "prefix", None), getattr(sys, "base_prefix", None)):
        if pref:
            try:
                roots.append(Path(pref).resolve())
            except OSError:
                pass
    # Dedup conservando orden.
    vistos: set[str] = set()
    unicos: list[Path] = []
    for r in roots:
        s = str(r)
        if s not in vistos:
            vistos.add(s)
            unicos.append(r)
    return unicos


_SISTEMA_ROOTS: list[Path] | None = None


def is_system_path(path: Path) -> bool:
    """True si `path` cae dentro de (o ES) un directorio del sistema
    operativo o la instalacion de Python. Bloqueo DURO: no lo levanta
    ninguna confirmacion (ver tools.ToolExecutor._destino_escritura)."""
    global _SISTEMA_ROOTS
    if _SISTEMA_ROOTS is None:
        _SISTEMA_ROOTS = _sistema_roots()
    try:
        p = path.resolve()
    except OSError:
        p = path
    for root in _SISTEMA_ROOTS:
        if p == root or root in p.parents:
            return True
    return False


def resolve_within_root(root: Path, user_path: str) -> Path:
    root_resolved = root.resolve()
    candidate = (root_resolved / user_path).resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise PathEscapeError(
            f"Ruta '{user_path}' resuelve fuera del workspace permitido ({root_resolved})."
        )
    return candidate


def _primera_existente(*candidatas: Path) -> Path:
    """La primera ruta que exista, o la primera de la lista si ninguna
    existe todavia (asi 'Descargas' sigue resolviendo aunque el usuario no
    la haya creado)."""
    for c in candidatas:
        if c.exists():
            return c
    return candidatas[0]


def carpeta_conocida(nombre: str) -> Path | None:
    """Traduce el nombre de una carpeta conocida del usuario (por el que la
    puede pedir en lenguaje normal: 'descargas', 'escritorio'...) a su ruta
    real. Devuelve None si no es una carpeta conocida.

    Contempla la variante OneDrive de Windows (Escritorio/Documentos suelen
    colgar de ~/OneDrive cuando esta activo), prefiriendo la que exista."""
    home = Path.home()
    clave = nombre.strip().strip("/\\").lower()
    mapa = {
        "escritorio": _primera_existente(home / "Desktop", home / "OneDrive" / "Desktop"),
        "desktop": _primera_existente(home / "Desktop", home / "OneDrive" / "Desktop"),
        "descargas": _primera_existente(home / "Downloads", home / "OneDrive" / "Downloads"),
        "downloads": _primera_existente(home / "Downloads", home / "OneDrive" / "Downloads"),
        "documentos": _primera_existente(home / "Documents", home / "OneDrive" / "Documents"),
        "documents": _primera_existente(home / "Documents", home / "OneDrive" / "Documents"),
    }
    return mapa.get(clave)


def resolve_write_destination(root: Path, user_path: str) -> tuple[Path, bool]:
    """Resuelve una ruta de ESCRITURA y dice si cae FUERA del workspace.

    Devuelve (destino, fuera_del_workspace). Tres casos:
    - Empieza por el nombre de una carpeta conocida ('descargas/informe.docx',
      'escritorio/web/index.html') -> esa carpeta real del usuario.
    - Ruta ABSOLUTA ('C:/Users/.../algo.txt') -> tal cual (el usuario la dio).
    - Cualquier otra (relativa) -> DENTRO del workspace, igual que siempre
      (y con la misma proteccion contra `../` de resolve_within_root).

    Escribir fuera del workspace exige confirmacion aparte (ver la tool):
    esta funcion solo RESUELVE y clasifica, no autoriza."""
    root_resolved = root.resolve()
    p = user_path.strip().replace("\\", "/")
    primero, _, resto = p.partition("/")
    conocida = carpeta_conocida(primero)
    if conocida is not None:
        destino = (conocida / resto).resolve() if resto else conocida.resolve()
        dentro = destino == root_resolved or root_resolved in destino.parents
        return destino, not dentro
    candidato = Path(user_path)
    if candidato.is_absolute():
        destino = candidato.resolve()
        dentro = destino == root_resolved or root_resolved in destino.parents
        return destino, not dentro
    # Relativa: dentro del workspace (comportamiento por defecto).
    return resolve_within_root(root, user_path), False


# Directorios que el agente nunca deberia listar, buscar, leer ni
# escribir: metadata de herramientas, entornos virtuales, dependencias
# instaladas, y el propio estado interno de esta terminal (sesiones
# guardadas). No son "el workspace" en el sentido que le importa a una
# tarea - mostrarlos solo agrega ruido y, en el caso de .git, podria
# filtrar historial no relacionado con lo que se esta pidiendo.
_HIDDEN_DIR_NAMES = {
    ".git", ".venv", "venv", ".env_backup", "__pycache__", "node_modules",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", ".tox", ".idea", ".vscode",
    ".orquestador_sessions",
}

# Sufijos de .env que son plantillas sin secretos reales - se pueden
# leer sin problema (de hecho conviene: el modelo a veces necesita ver
# que variables existen para saber cuales pedir).
_ENV_TEMPLATE_SUFFIXES = (".example", ".sample", ".template")

# Otros archivos que casi siempre tienen credenciales/claves privadas,
# sin importar en que carpeta del workspace aparezcan.
_SENSITIVE_FILE_GLOBS = ("*.pem", "*.key", "id_rsa*", "*.pfx", "*.p12", "*credentials*.json")


def is_hidden_path(path: Path) -> bool:
    """True si algun componente de la ruta es un directorio que el
    agente no deberia ver (control de versiones, entornos virtuales,
    caches, dependencias, o el estado interno de esta terminal)."""
    return any(part in _HIDDEN_DIR_NAMES for part in path.parts)


def is_sensitive_file(path: Path) -> bool:
    """True para archivos que casi siempre tienen secretos: .env real
    (no las plantillas .env.example/.sample/.template), claves privadas,
    certificados, dumps de credenciales."""
    name = path.name
    if name == ".env" or (
        name.startswith(".env.") and not name.endswith(_ENV_TEMPLATE_SUFFIXES)
    ):
        return True
    return any(fnmatch.fnmatch(name.lower(), pat) for pat in _SENSITIVE_FILE_GLOBS)


def is_visible_to_agent(path: Path) -> bool:
    """Combina ambos chequeos - lo que usan list_dir/glob_search/
    grep_search para decidir que entrada mostrar."""
    return not is_hidden_path(path) and not is_sensitive_file(path)
