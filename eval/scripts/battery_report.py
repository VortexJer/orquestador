"""Diagnostico de la bateria por-especialista contra el HINT del heuristico.

No es un test: es la foto que separa los dos tipos de "no acierta":
  - HINT=None  -> el heuristico no vio señal y no propone nada; en produccion
    decide el LLM. Es INOCUO (no arrastra al modelo a un dominio equivocado).
  - HINT=<otro dominio> -> el heuristico propone un dominio EQUIVOCADO. Eso SI
    es un bug de precision: le mete al LLM un prior falso. Hay que arreglarlo.

Uso: python -m eval.scripts.battery_report
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from app.router.heuristic_router import match_domain_or_none

DATASET = Path(__file__).resolve().parents[2] / "eval" / "router_eval_specialists.json"


def main() -> int:
    cases = json.loads(DATASET.read_text(encoding="utf-8"))["cases"]
    wrong: list[tuple] = []
    none_hint: Counter = Counter()
    exact = 0
    for c in cases:
        exp = c["expected_domain"]
        hint = match_domain_or_none(c["task"])
        if hint == exp:
            exact += 1
        elif hint is None:
            none_hint[exp] += 1
        else:
            wrong.append((c["id"], exp, hint, c["task"]))

    total = len(cases)
    print(f"Bateria: {total} prompts")
    print(f"  hint EXACTO al dominio esperado: {exact} ({exact/total:.0%})")
    print(f"  hint None (inocuo, decide el LLM): {sum(none_hint.values())}")
    print(f"  hint EQUIVOCADO (bug de precision): {len(wrong)}")
    print()
    if wrong:
        print("HINTS EQUIVOCADOS (a arreglar en el regex, son falsos positivos):")
        for i, e, h, t in wrong:
            print(f"  [{i}] esperado={e} hint={h} -- {t}")
        print()
    print("Dominios que caen a None (los resuelve la GLOSA del LLM, no el regex):")
    for d, n in none_hint.most_common():
        print(f"  {d}: {n}")
    return 1 if wrong else 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
