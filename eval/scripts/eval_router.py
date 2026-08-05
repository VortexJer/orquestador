"""Criterio de aceptacion de fase 1 (pedido explicito del usuario): no
confiar en el router en produccion sin medir su tasa de acierto contra
un dataset etiquetado a mano. Corre contra el router activo
(HeuristicRouter por defecto; ROUTER_IMPL=llm para medir el router real
una vez este disponible via GPU).

Uso:
    python -m eval.scripts.eval_router
    ROUTER_IMPL=llm python -m eval.scripts.eval_router   # una vez haya endpoint real

Sale con codigo distinto de 0 si la exactitud queda por debajo de
ROUTER_EVAL_MIN_ACCURACY (default 0.85) - pensado para usarse como gate
de CI antes de promover un router nuevo a produccion.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from app.config import get_settings
from app.orchestrator import get_router
from app.schemas import GenerateRequest

DATASET_PATH = Path(__file__).resolve().parent.parent / "router_eval_set.json"


def main() -> int:
    settings = get_settings()
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    router = get_router()

    correct = 0
    confusion: Counter[tuple[str, str]] = Counter()
    misclassified: list[dict] = []

    for item in dataset:
        request = GenerateRequest(task=item["task"])
        decision = router.route(request, currently_warm_ref=None)
        expected = item["expected_domain"]
        actual = decision.domain
        confusion[(expected, actual)] += 1
        if actual == expected:
            correct += 1
        else:
            misclassified.append({"id": item["id"], "task": item["task"], "expected": expected, "actual": actual})

    total = len(dataset)
    accuracy = correct / total if total else 0.0

    print(f"Dataset: {total} casos | Router activo: {type(router).__name__}")
    print(f"Exactitud: {accuracy:.2%} ({correct}/{total})")
    print(f"Umbral de aceptacion: {settings.router_eval_min_accuracy:.0%}")
    print()

    if misclassified:
        print("Casos mal clasificados:")
        for m in misclassified:
            print(f"  [{m['id']}] esperado={m['expected']} obtenido={m['actual']} -- {m['task']}")
        print()

    print("Matriz de confusion (esperado -> obtenido : conteo), solo discrepancias:")
    for (expected, actual), n in sorted(confusion.items()):
        if expected != actual:
            print(f"  {expected} -> {actual}: {n}")

    passed = accuracy >= settings.router_eval_min_accuracy
    print()
    print("RESULTADO: " + ("APROBADO" if passed else "RECHAZADO - no promover este router a produccion todavia"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
