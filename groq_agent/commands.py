"""Comandos guardados: plantillas de tareas que se repiten.

PARA QUE
Hay tareas que uno pide una y otra vez cambiando un dato: "hazme una web
para <negocio> en <ciudad>", "revisa <archivo> y añade tests". Reescribir el
prompt entero cada vez es tedioso y, peor, sale distinto cada vez - y un
prompt que varia da resultados que no se pueden comparar entre si.

SUSTITUCION
  $ARGUMENTS  todo lo que venga detras, junto
  $1 $2 $3    cada argumento por separado
Un $1 que no se paso se sustituye por vacio, no se deja literal: dejar un
"$1" crudo en el prompt hace que el modelo intente interpretarlo como parte
del texto.

DONDE SE GUARDAN
En el repo, no en el workspace: son plantillas de trabajo del usuario, no
archivos del proyecto que esta construyendo. Meterlas en el workspace las
mezclaria con el resultado y se irian con cada carpeta nueva.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

RUTA = Path(__file__).resolve().parent.parent / "config" / "comandos.json"

_NOMBRE_VALIDO = re.compile(r"^[\w.-]+$")


def _cargar() -> dict[str, str]:
    if not RUTA.exists():
        return {}
    try:
        datos = json.loads(RUTA.read_text(encoding="utf-8"))
        return {k: str(v) for k, v in datos.items()} if isinstance(datos, dict) else {}
    except (ValueError, OSError):
        return {}


def listar() -> dict[str, str]:
    return _cargar()


def guardar(nombre: str, plantilla: str) -> str:
    """Guarda o reemplaza un comando. Devuelve un mensaje para la UI."""
    nombre = (nombre or "").strip()
    if not _NOMBRE_VALIDO.match(nombre):
        return (
            f"ERROR: '{nombre}' no vale como nombre. Usa letras, numeros, "
            "guiones o puntos, sin espacios."
        )
    if not (plantilla or "").strip():
        return "ERROR: la plantilla esta vacia."

    datos = _cargar()
    existia = nombre in datos
    datos[nombre] = plantilla.strip()
    try:
        RUTA.parent.mkdir(parents=True, exist_ok=True)
        RUTA.write_text(json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        return f"ERROR guardando: {exc}"
    return f"comando '{nombre}' {'actualizado' if existia else 'guardado'}"


def borrar(nombre: str) -> str:
    datos = _cargar()
    if nombre not in datos:
        return f"ERROR: no existe el comando '{nombre}'."
    del datos[nombre]
    try:
        RUTA.write_text(json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        return f"ERROR borrando: {exc}"
    return f"comando '{nombre}' borrado"


def expandir(nombre: str, argumentos: list[str]) -> str | None:
    """La plantilla con sus argumentos sustituidos, o None si no existe."""
    plantilla = _cargar().get(nombre)
    if plantilla is None:
        return None

    texto = plantilla.replace("$ARGUMENTS", " ".join(argumentos))
    # De mayor a menor para que $10 no lo pise $1.
    for i in range(9, 0, -1):
        valor = argumentos[i - 1] if len(argumentos) >= i else ""
        texto = texto.replace(f"${i}", valor)
    return texto.strip()


def placeholders(plantilla: str) -> list[str]:
    """Que huecos usa una plantilla. Sirve para avisar de que espera."""
    encontrados = set(re.findall(r"\$(?:ARGUMENTS|[1-9])", plantilla))
    return sorted(encontrados)
