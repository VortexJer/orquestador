"""Ejecuta el ciclo de herramientas del especialista Python: ruff -> mypy
-> pytest, sobre un sandbox efimero (directorio temporal + subprocess).

Nota de endurecimiento para produccion: aca se usa subprocess con
timeout y sin acceso a la red del proceso padre porque alcanza para el
prototipo corriendo en la maquina de desarrollo. En el servidor real
(ver DEPLOY_PROMPT.txt) esto debe correr dentro de un contenedor
efimero sin red saliente (salvo whitelist) y sin acceso al filesystem
del host, tal como exige el doc de arquitectura (seccion 11, NFR de
seguridad) - subprocess directo no aisla de eso.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from app.config import get_settings
from app.schemas import ToolExecutionResult


def _run(cmd: list[str], cwd: Path) -> tuple[bool, str]:
    settings = get_settings()
    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=settings.tool_sandbox_timeout_s,
        )
        ok = proc.returncode == 0
        output = (proc.stdout + "\n" + proc.stderr).strip()
        return ok, output[-3000:]
    except subprocess.TimeoutExpired:
        return False, f"Timeout tras {settings.tool_sandbox_timeout_s}s ejecutando {' '.join(cmd)}"
    finally:
        _ = time.monotonic() - start


def write_sandbox(sandbox_dir: Path, code: str, tests: str) -> None:
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    (sandbox_dir / "solution.py").write_text(code, encoding="utf-8")
    (sandbox_dir / "test_solution.py").write_text(tests, encoding="utf-8")


def run_ruff(sandbox_dir: Path) -> ToolExecutionResult:
    start = time.monotonic()
    ok, output = _run([sys.executable, "-m", "ruff", "check", "solution.py"], sandbox_dir)
    return ToolExecutionResult(
        tool="ruff",
        result="pass" if ok else "fail",
        detail=output,
        duration_ms=int((time.monotonic() - start) * 1000),
    )


def run_mypy(sandbox_dir: Path) -> ToolExecutionResult:
    start = time.monotonic()
    ok, output = _run(
        [sys.executable, "-m", "mypy", "--no-error-summary", "solution.py"], sandbox_dir
    )
    return ToolExecutionResult(
        tool="mypy",
        result="pass" if ok else "fail",
        detail=output,
        duration_ms=int((time.monotonic() - start) * 1000),
    )


def run_pytest(sandbox_dir: Path) -> ToolExecutionResult:
    start = time.monotonic()
    ok, output = _run([sys.executable, "-m", "pytest", "-q"], sandbox_dir)
    return ToolExecutionResult(
        tool="pytest",
        result="pass" if ok else "fail",
        detail=output,
        duration_ms=int((time.monotonic() - start) * 1000),
    )


def run_full_chain(sandbox_dir: Path, code: str, tests: str) -> list[ToolExecutionResult]:
    """Corre en orden y corta apenas una herramienta falla (no tiene
    sentido correr pytest si ni siquiera compila/tipa bien) - ahorra
    segundos de CPU/latencia, mismo principio de costo que en GPU."""
    write_sandbox(sandbox_dir, code, tests)
    results = [run_ruff(sandbox_dir)]
    if results[-1].result != "pass":
        return results
    results.append(run_mypy(sandbox_dir))
    if results[-1].result != "pass":
        return results
    results.append(run_pytest(sandbox_dir))
    return results
