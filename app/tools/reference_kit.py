"""Acceso de solo lectura a app/web_kit_reference/ - una libreria de
animaciones, componentes y layouts HTML de referencia, YA probados y
resueltos (timing, accesibilidad, prefers-reduced-motion), copiada de
un kit usado en la practica para generar webs. Vive DENTRO del repo
(no en el --workspace del usuario) porque es material de referencia
del sistema, no contenido del proyecto del usuario - por eso no pasa
por safety.resolve_within_root, pero si esta blindado contra path
traversal (ver _resolve) ya que el nombre del archivo lo elige el
modelo.
"""
from __future__ import annotations

import re
from pathlib import Path

_KIT_ROOT = Path(__file__).resolve().parent.parent / "web_kit_reference"
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)


class ReferenceNotFoundError(Exception):
    pass


def _resolve(relative_path: str) -> Path:
    candidate = (_KIT_ROOT / relative_path).resolve()
    if _KIT_ROOT not in candidate.parents and candidate != _KIT_ROOT:
        raise ReferenceNotFoundError(f"Ruta fuera de la referencia permitida: {relative_path}")
    return candidate


def _title_of(path: Path) -> str:
    # Cada archivo de referencia ya trae un <title> descriptivo de una
    # linea (ej. "Header completo - sticky + hamburguesa + CTA") - se
    # reusa como descripcion en vez de mantener un manifiesto aparte que
    # se puede desincronizar del contenido real.
    match = _TITLE_RE.search(path.read_text(encoding="utf-8"))
    return match.group(1).strip() if match else ""


def list_reference_components(category: str = "") -> list[dict]:
    """category: 'animaciones' | 'componentes' | 'layouts' | 'datos' | '3d' | ''
    (vacio = todas). Devuelve [{'path': ..., 'description': ...}, ...] -
    la descripcion (su <title>) alcanza para decidir CUALES 2-4 archivos
    hacen falta para este proyecto SIN tener que abrir cada uno; solo
    hay que leer con read_reference_component los que de verdad se van a
    usar, no explorar la libreria entera archivo por archivo."""
    base = _resolve(category) if category else _KIT_ROOT
    if not base.exists():
        return []
    paths = sorted(base.rglob("*.html"), key=lambda p: str(p))
    return [
        {"path": str(p.relative_to(_KIT_ROOT)).replace("\\", "/"), "description": _title_of(p)}
        for p in paths
    ]


def read_reference_component(relative_path: str) -> str:
    """relative_path ej. 'animaciones/scroll-reveal.html'. Devuelve el
    contenido tal cual - adáptalo al sistema de diseño del proyecto
    (sus custom properties, paleta, tipografia), no lo copies con los
    colores/valores de demo."""
    target = _resolve(relative_path)
    if not target.is_file():
        disponibles = ", ".join(list_reference_components())
        raise ReferenceNotFoundError(f"No existe '{relative_path}'. Disponibles: {disponibles}")
    return target.read_text(encoding="utf-8")
