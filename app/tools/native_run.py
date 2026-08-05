"""Compilar y EJECUTAR codigo C++ en un sandbox temporal, para el
especialista C++ (el lenguaje donde mas errores son comportamiento
indefinido que compila sin un solo aviso).

Motiva su existencia un caso medido: ante un `push_back` dentro del bucle
que itera el mismo vector, el especialista diagnosticaba bien la
invalidacion de iterador pero "arreglaba" reiniciando el iterador, lo que
entra en BUCLE INFINITO. Eso no se ve leyendo el codigo; se ve al
ejecutarlo con un limite de tiempo. La compilacion con warnings altos
ademas convierte en señal muchos errores que si no pasan silenciosos.

Requiere `g++` en el PATH. Si no esta, la tool lo dice y el modelo puede
seguir con su propio criterio (no rompe el ciclo).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

# Tiempo maximo de EJECUCION del binario. El bucle infinito del caso que
# motiva la tool se manifiesta justo aqui: agota el limite y se reporta
# como timeout, que es la señal buscada.
_TIMEOUT_EJECUCION_S = 5
_TIMEOUT_COMPILACION_S = 30
_MAX_SALIDA = 4000

_FLAGS_BASE = ["-std=c++20", "-Wall", "-Wextra", "-Wpedantic", "-O0", "-g"]
# UBSan atrapa en ejecucion parte de lo que compila sin aviso (desbordes,
# desreferencias invalidas). Es opcional porque en algunos toolchains
# (MinGW) el runtime no enlaza; si falla la compilacion con el flag, se
# reintenta sin el.
_FLAG_SANITIZER = "-fsanitize=undefined"


def compile_run_cpp(code: str, stdin: str = "", timeout_s: int = _TIMEOUT_EJECUCION_S) -> dict:
    """Compila `code` (una unidad de traduccion con `main`) y lo ejecuta.

    Devuelve {ok, fase, warnings, error, stdout, stderr, timeout}.
    - fase: 'compilacion' si no llego a ejecutar, 'ejecucion' si corrio.
    - timeout=True cuando el binario no termino a tiempo (señal fuerte de
      bucle infinito / bloqueo).
    """
    gpp = shutil.which("g++") or shutil.which("clang++")
    if not gpp:
        return {
            "ok": False,
            "fase": "compilacion",
            "error": "No hay compilador de C++ (g++/clang++) en el PATH. "
            "Instala uno o razona el fix sin ejecutar, dejando claro que no se verifico.",
        }

    timeout_s = max(1, min(int(timeout_s or _TIMEOUT_EJECUCION_S), 15))
    tmpdir = Path(tempfile.mkdtemp(prefix="cpprun_"))
    src = tmpdir / "main.cpp"
    exe = tmpdir / ("main.exe" if os.name == "nt" else "main")
    src.write_text(code, encoding="utf-8")

    # La carpeta del compilador tiene que estar en el PATH tanto para
    # COMPILAR como para EJECUTAR: en toolchains tipo MSYS2/UCRT el propio
    # g++.exe y el binario resultante cargan sus DLLs de ahi, y sin esto
    # g++ falla con rc=1 y stderr vacio (el error del cargador no sale por
    # stderr) - un fallo mudo que parece "no compila" sin decir por que.
    entorno = dict(os.environ)
    entorno["PATH"] = str(Path(gpp).parent) + os.pathsep + entorno.get("PATH", "")
    entorno["UBSAN_OPTIONS"] = "print_stacktrace=0:halt_on_error=1"

    try:
        # Intento 1: con sanitizer. Intento 2 (si el enlazado del sanitizer
        # falla, ej. MinGW sin libubsan): sin el, para no perder la
        # ejecucion por un toolchain que no trae el runtime.
        compilo = None
        for flags in ([*_FLAGS_BASE, _FLAG_SANITIZER], _FLAGS_BASE):
            compilo = subprocess.run(
                [gpp, *flags, str(src), "-o", str(exe)],
                capture_output=True, text=True, timeout=_TIMEOUT_COMPILACION_S,
                cwd=tmpdir, env=entorno,
            )
            if compilo.returncode == 0:
                break

        warnings = (compilo.stderr or "").strip()
        if compilo.returncode != 0:
            return {
                "ok": False,
                "fase": "compilacion",
                "error": "No compila:\n" + warnings[-_MAX_SALIDA:],
            }

        # Ejecucion con limite de tiempo (mismo `entorno` con el PATH del
        # compilador, para que el binario encuentre sus DLLs en Windows).
        try:
            corre = subprocess.run(
                [str(exe)],
                input=stdin, capture_output=True, text=True,
                timeout=timeout_s, cwd=tmpdir, env=entorno,
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "fase": "ejecucion",
                "timeout": True,
                "warnings": warnings[-1500:],
                "error": f"El programa no termino en {timeout_s}s: probable BUCLE INFINITO "
                "o bloqueo. Revisa el bucle (¿modificas el contenedor mientras lo recorres "
                "y reinicias el iterador?).",
            }

        salida_ok = corre.returncode == 0
        return {
            "ok": salida_ok,
            "fase": "ejecucion",
            "timeout": False,
            "codigo_salida": corre.returncode,
            "warnings": warnings[-1500:],
            "stdout": (corre.stdout or "")[-_MAX_SALIDA:],
            "stderr": (corre.stderr or "")[-_MAX_SALIDA:],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "fase": "compilacion", "error": f"La compilacion excedio {_TIMEOUT_COMPILACION_S}s."}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
