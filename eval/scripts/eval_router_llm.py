"""Eval del router LLM EN VIVO (router-tiny, 8B) contra la bateria por
especialista - la mitad que las pruebas locales no ven.

A diferencia de eval_router.py (heuristico, 0 tokens), esto llama al modelo
real por cada prompt, exactamente como produccion: llm_classify_domain ya
calcula la pista del heuristico y se la pasa al clasificador. Sirve para el
bucle de ajuste de GLOSAS: correr -> ver en que difiere del dominio correcto
-> afinar _DOMAIN_GLOSS/_classifier_system_prompt -> repetir.

Uso:
    python -m eval.scripts.eval_router_llm                # bateria entera
    python -m eval.scripts.eval_router_llm --only research,debugging,go
    python -m eval.scripts.eval_router_llm --ids go-07,kt-04 --repeat 3
    python -m eval.scripts.eval_router_llm --workers 5 --json out.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BATTERY = REPO / "eval" / "router_eval_specialists.json"


def _load_dotenv() -> None:
    env = REPO / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="coma-separado de dominios a evaluar")
    ap.add_argument("--ids", help="coma-separado de ids concretos")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--repeat", type=int, default=1, help="veces que se corre cada prompt (el 8B es no-determinista)")
    ap.add_argument("--json", help="volcar resultados crudos a este archivo")
    args = ap.parse_args()

    _load_dotenv()
    from groq_agent.auto_router import CHARLA, llm_classify_domain
    from groq_agent.groq_client import GroqClient

    cases = json.loads(BATTERY.read_text(encoding="utf-8"))["cases"]
    if args.only:
        keep = set(args.only.split(","))
        cases = [c for c in cases if c["expected_domain"] in keep]
    if args.ids:
        keep = set(args.ids.split(","))
        cases = [c for c in cases if c["id"] in keep]
    # Expandir por --repeat.
    work = [(c, r) for c in cases for r in range(args.repeat)]

    client = GroqClient()

    def run(item):
        c, _r = item
        try:
            got = llm_classify_domain(client, c["task"])
        except Exception as e:  # noqa: BLE001 - queremos ver el fallo, no que reviente el eval
            got = f"ERROR:{type(e).__name__}"
        if got is CHARLA or got == CHARLA:
            got = "__charla__"
        elif got is None:
            got = "__continue__"
        return {"id": c["id"], "expected": c["expected_domain"], "got": got, "task": c["task"]}

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i, res in enumerate(ex.map(run, work), 1):
            results.append(res)
            print(f"\r  {i}/{len(work)}", end="", file=sys.stderr, flush=True)
    print("", file=sys.stderr)

    # Agregado: por id, cuenta de aciertos sobre --repeat.
    ok = sum(1 for r in results if r["got"] == r["expected"])
    total = len(results)
    acc = ok / total if total else 0.0
    confusion: Counter = Counter()
    fallos = [r for r in results if r["got"] != r["expected"]]
    for r in fallos:
        confusion[(r["expected"], r["got"])] += 1

    print(f"\nRouter LLM en vivo | {total} llamadas ({len(cases)} prompts x{args.repeat}) | modelo router-tiny")
    print(f"Exactitud: {acc:.1%} ({ok}/{total})\n")
    if fallos:
        print("DISCREPANCIAS (esperado != obtenido):")
        for r in sorted(fallos, key=lambda x: (x["expected"], x["id"])):
            print(f"  [{r['id']}] esperado={r['expected']:<20} obtenido={r['got']:<20} -- {r['task']}")
        print("\nMatriz de confusion (esperado -> obtenido : n):")
        for (e, g), n in sorted(confusion.items(), key=lambda kv: -kv[1]):
            print(f"  {e} -> {g}: {n}")
    else:
        print("Sin discrepancias: el router en vivo coincide con lo esperado en todos.")

    if args.json:
        Path(args.json).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
