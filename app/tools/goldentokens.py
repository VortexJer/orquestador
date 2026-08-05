"""Puente a los CLIs de goldentokens: leer cosas grandes sin volcarlas.

QUE SON
Un conjunto de herramientas del usuario (~/.claude/bin) pensadas para que
un agente gaste menos tokens leyendo. Encajan exactamente con el problema
de este sistema: los modelos de estos tiers tienen ventana limitada, y este
orquestador reenvia la conversacion entera en cada turno, asi que un
`read_file` de 3.000 lineas no se paga una vez - se paga en cada vuelta
posterior hasta que la poda lo recorte.

QUE APORTA CADA UNO
  peek    esqueleto de un archivo grande al ~5% de tokens. El agente ve el
          MAPA (que funciones hay y en que linea) y luego lee solo el rango
          que importa, en vez de tragarse el archivo entero para encontrar
          una funcion.
  jsonq   consultar/agregar un JSON grande sin volcarlo. Evita el
          antipatron de leer un JSON de 2 MB para mirar tres campos.
  repomap mapa compacto de un repo entero: rutas, lineas y simbolos.
  pdftext texto de un PDF sin pasar por una imagen.
  img     inspeccion de imagenes por datos en vez de por pixeles.

POR QUE COMO SUBPROCESO Y NO IMPORTANDOLOS
Viven fuera de este repo (en el directorio del usuario) y evolucionan por
su cuenta. Importarlos ataria este proyecto a su estructura interna;
llamarlos por linea de comandos solo depende de su interfaz publica, que es
lo que ellos garantizan.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Ruta estable, documentada en el CLAUDE.md del usuario. Se permite
# sobreescribirla por entorno para no dejar el proyecto atado a un usuario
# concreto (y para poder testear).
_DEFECTO = Path.home() / ".claude" / "bin"
TIMEOUT_S = 60

# Solo estas. Una lista blanca, no una ruta arbitraria: aunque el agente
# no puede elegir el ejecutable, dejar la puerta abierta a "corre este .py"
# convertiria una herramienta de lectura en ejecucion de codigo arbitrario.
PERMITIDAS = {"peek", "jsonq", "repomap", "pdftext", "img", "csvq", "grepq"}


def bin_dir() -> Path:
    return Path(os.environ.get("GOLDENTOKENS_BIN") or _DEFECTO)


def disponible(nombre: str) -> bool:
    return nombre in PERMITIDAS and (bin_dir() / f"{nombre}.py").is_file()


def herramientas_disponibles() -> list[str]:
    return sorted(n for n in PERMITIDAS if disponible(n))


def ejecutar(nombre: str, argumentos: list[str], cwd: Path | None = None) -> str:
    """Corre un CLI de goldentokens y devuelve su salida.

    Los errores se devuelven como TEXTO, no como excepcion: esto lo llama
    una tool del agente, y el modelo necesita leer que paso para corregir,
    no que se le caiga el turno."""
    if not disponible(nombre):
        return (
            f"ERROR: '{nombre}' no esta disponible. Herramientas instaladas: "
            f"{', '.join(herramientas_disponibles()) or '(ninguna)'}"
        )
    script = bin_dir() / f"{nombre}.py"
    try:
        proceso = subprocess.run(
            [sys.executable, str(script), *argumentos],
            capture_output=True, text=True, timeout=TIMEOUT_S,
            cwd=str(cwd) if cwd else None,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: '{nombre}' tardo mas de {TIMEOUT_S}s y se corto."
    except OSError as exc:
        return f"ERROR ejecutando '{nombre}': {exc}"

    salida = (proceso.stdout or "").strip()
    error = (proceso.stderr or "").strip()
    if proceso.returncode != 0:
        # stderr primero: cuando falla, el motivo esta ahi.
        return f"ERROR ({proceso.returncode}): {error or salida or 'sin salida'}"
    if error and not salida:
        return error
    return salida or "(sin salida)"
