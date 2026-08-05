"""Prueba de carga para sacar el numero real de concurrencia sostenible.

Contexto (feedback del usuario, criterio de aceptacion de fase 1): con
router + embedder + un especialista de 14B cargados (~12-13GB en la
L4 de 24GB), quedan ~11GB para KV cache. Cuantas requests simultaneas
soporta vLLM con continuous batching en ese margen NO se puede asumir
desde el diseño - hay que medirlo contra el servidor real.

Este script no sirve para nada corriendo contra INFERENCE_MODE=mock (el
mock responde en microsegundos y no refleja nada del cuello de botella
real de GPU) - correrlo recien tiene sentido una vez el servidor este
arriba con vLLM real (ver DEPLOY_PROMPT.txt, que incluye este paso como
parte del checklist de aceptacion).

Uso:
    python scripts/load_test.py --base-url http://localhost:8080 \
        --api-key dev-local-key --levels 1,2,4,8,16,24,32
"""
from __future__ import annotations

import argparse
import asyncio
import time

import httpx

DEFAULT_TASK = "Escribe una funcion def es_primo(n: int) -> bool y sus tests con pytest."


async def _one_request(client: httpx.AsyncClient, api_key: str, task: str) -> tuple[bool, float]:
    start = time.monotonic()
    try:
        resp = await client.post(
            "/v1/generate",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"task": task, "constraints": {"quality_level": "standard"}},
        )
        ok = resp.status_code == 200
        return ok, time.monotonic() - start
    except httpx.HTTPError:
        return False, time.monotonic() - start


async def _run_level(base_url: str, api_key: str, task: str, concurrency: int, timeout_s: float) -> dict:
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout_s) as client:
        results = await asyncio.gather(*[_one_request(client, api_key, task) for _ in range(concurrency)])

    latencies = [lat for _ok, lat in results]
    errors = sum(1 for ok, _lat in results if not ok)
    latencies.sort()

    def pct(p: float) -> float:
        if not latencies:
            return 0.0
        idx = min(len(latencies) - 1, int(len(latencies) * p))
        return latencies[idx]

    return {
        "concurrency": concurrency,
        "p50_s": round(pct(0.50), 2),
        "p95_s": round(pct(0.95), 2),
        "max_s": round(max(latencies), 2) if latencies else 0.0,
        "error_rate": round(errors / concurrency, 3),
    }


async def main_async(args: argparse.Namespace) -> None:
    levels = [int(x) for x in args.levels.split(",")]
    print(f"Probando contra {args.base_url} con niveles de concurrencia: {levels}")
    print(f"{'concurrencia':>12} | {'p50 (s)':>8} | {'p95 (s)':>8} | {'max (s)':>8} | {'error %':>8}")

    previous_p95 = None
    saturation_point = None
    for level in levels:
        stats = await _run_level(args.base_url, args.api_key, args.task, level, args.timeout)
        print(
            f"{stats['concurrency']:>12} | {stats['p50_s']:>8} | {stats['p95_s']:>8} | "
            f"{stats['max_s']:>8} | {stats['error_rate'] * 100:>7.1f}%"
        )
        # Heuristica simple de saturacion: p95 se dispara (>2x el nivel
        # anterior) o aparecen errores - marca el primer nivel donde eso
        # pasa como candidato a "concurrencia sostenible = nivel anterior".
        if saturation_point is None:
            if stats["error_rate"] > 0.02:
                saturation_point = level
            elif previous_p95 is not None and stats["p95_s"] > previous_p95 * 2:
                saturation_point = level
        previous_p95 = stats["p95_s"]

    print()
    if saturation_point:
        print(
            f"Punto de saturacion detectado en concurrencia={saturation_point}. "
            f"Concurrencia sostenible recomendada: el nivel inmediatamente anterior probado."
        )
    else:
        print(
            "No se detecto saturacion clara dentro de los niveles probados - "
            "correr con niveles mas altos (--levels) para encontrar el techo real."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--api-key", default="dev-local-key")
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--levels", default="1,2,4,8,16,24,32")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
