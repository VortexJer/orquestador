"""`@archivo` en la tarea: inyecta el fichero ya leido.

POR QUE AQUI VALE MAS QUE EN OTROS CLIs
El preambulo de este sistema lo dice explicitamente: "cada turno reenvia la
conversacion ENTERA". Un `read_file` no cuesta solo su resultado - cuesta
un turno completo del presupuesto, y ese turno reenvia todo lo anterior.
Escribiendo `@src/app.py revisa esto`, el contenido llega en el PRIMER
mensaje y el modelo se ahorra la ida y vuelta entera.

Ademas evita un fallo real: el modelo tiene que ADIVINAR la ruta antes de
poder leerla, y con nombres parecidos (`config.py` vs `app/config.py`)
gasta varios turnos tanteando.
"""
from __future__ import annotations

import re
from pathlib import Path

# @ruta hasta el primer espacio. Se aceptan letras, digitos, . _ - / \ y
# ~, que cubre cualquier ruta real sin tragarse la puntuacion de la frase
# ("mira @app.py, esta roto" no debe incluir la coma).
_PATRON = re.compile(r"(?<![\w@])@([\w.\-/\\~]+)")

# Tope por archivo. Un fichero mas grande que esto arruina el proposito:
# se comeria el contexto que veniamos de liberar. Se inyecta el principio y
# se avisa, que es mas util que no inyectar nada.
MAX_CHARS_POR_ARCHIVO = 20_000
# Tope total, por si alguien escribe @ diez veces.
MAX_CHARS_TOTAL = 60_000

_EXT_BINARIAS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".pdf",
    ".zip", ".gz", ".tar", ".7z", ".rar", ".exe", ".dll", ".so", ".dylib",
    ".docx", ".xlsx", ".pptx", ".woff", ".woff2", ".ttf", ".otf", ".mp4",
    ".mp3", ".wav", ".sqlite", ".db", ".pyc",
}


def _parece_binario(path: Path) -> bool:
    if path.suffix.lower() in _EXT_BINARIAS:
        return True
    try:
        # Un NUL en los primeros KB es la señal clasica de binario, y
        # cubre las extensiones que no estan en la lista.
        return b"\0" in path.read_bytes()[:4096]
    except OSError:
        return False


def encontrar_referencias(texto: str) -> list[str]:
    """Rutas mencionadas con @ en el texto, en orden y sin repetir."""
    vistas: list[str] = []
    for m in _PATRON.finditer(texto or ""):
        ruta = m.group(1).rstrip(".,;:)")
        if ruta and ruta not in vistas:
            vistas.append(ruta)
    return vistas


def expandir(texto: str, workspace: Path) -> tuple[str, list[str]]:
    """Devuelve (texto_con_los_archivos, notas_para_la_ui).

    El texto original NO se reescribe: los `@ruta` se dejan tal cual (el
    modelo los ve y entiende de que archivo se habla) y el contenido se
    añade debajo, delimitado. Sustituir la mencion por el contenido haria
    ilegible la frase del usuario.

    Un @ruta que no resuelve NO es un error: puede ser una direccion de
    correo, un usuario de redes o una ruta que el modelo tendra que buscar
    el mismo. Se ignora en silencio y se anota para la UI.
    """
    referencias = encontrar_referencias(texto)
    if not referencias:
        return texto, []

    bloques: list[str] = []
    notas: list[str] = []
    total = 0

    for ref in referencias:
        candidato = Path(ref).expanduser()
        rutas = [candidato] if candidato.is_absolute() else [
            workspace / ref, Path.cwd() / ref,
        ]
        destino = next((r for r in rutas if r.is_file()), None)
        if destino is None:
            continue

        if _parece_binario(destino):
            notas.append(f"@{ref}: binario, no se inyecta")
            continue

        if total >= MAX_CHARS_TOTAL:
            notas.append(f"@{ref}: omitido (se alcanzo el tope total)")
            continue

        try:
            contenido = destino.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            notas.append(f"@{ref}: no se pudo leer ({exc})")
            continue

        recortado = ""
        if len(contenido) > MAX_CHARS_POR_ARCHIVO:
            contenido = contenido[:MAX_CHARS_POR_ARCHIVO]
            recortado = (
                f"\n… [recortado a {MAX_CHARS_POR_ARCHIVO} caracteres. "
                f"Usa read_file si necesitas el resto.]"
            )
        total += len(contenido)
        bloques.append(
            f"--- {ref} ---\n{contenido}{recortado}\n--- fin de {ref} ---"
        )
        notas.append(f"@{ref}: {len(contenido)} chars")

    if not bloques:
        return texto, notas

    cabecera = (
        "\n\n[Archivos que el usuario referencio con @ en su mensaje, ya leidos "
        "por tú - NO hace falta que los vuelvas a leer con read_file:]\n\n"
    )
    return texto + cabecera + "\n\n".join(bloques), notas
