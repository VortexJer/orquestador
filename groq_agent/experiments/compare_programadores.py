"""Script de un solo uso: compara distintos modelos candidatos a
"programador" (el que EJECUTA un diseño ya decidido, ver
build_desde_disenos.py) construyendo la MISMA web a partir del MISMO
documento de diseño (el de deepseek-v3.2, ya elegido como disenador) -
asi la unica variable que cambia es quien programa, no que se le pide
construir.

Candidatos elegidos alrededor del mismo precio que "quencoder"
(qwen3-coder-30b-a3b-instruct, ~$0.07/$0.27 por millon de tokens
input/output en OpenRouter) - ni mucho mas caro ni mucho mas barato,
para que la comparacion sea sobre CALIDAD de ejecucion, no sobre
presupuesto:
  - qwen/qwen3-coder-30b-a3b-instruct  ~$0.07 / $0.27  (quencoder, control)
  - qwen/qwen3-coder-next              ~$0.11 / $0.80  (mismo linaje, mas grande)
  - deepseek/deepseek-v3.2             ~$0.23 / $0.34  (ya probado como buen "todo terreno")

Uso: ./.venv/Scripts/python.exe -m groq_agent.experiments.compare_programadores
"""
from __future__ import annotations

import json

from groq_agent.agent_loop import run_agent
from groq_agent.experiments.build_desde_disenos import BASE_DIR, MAX_TURNS, _tarea_para
from groq_agent.groq_client import GroqClient
from groq_agent.skills_loader import build_system_prompt
from groq_agent.tools import ToolExecutor

DISEÑO_FUENTE = "deepseek/deepseek-v3.2"
RESULTADOS_DISEÑO = BASE_DIR / "ronda-1-sin-fotos" / "resultados.json"

PROGRAMADORES = [
    "openai/gpt-oss-20b",           # input $0.029/M - el mas barato con diferencia
    "qwen/qwen3-235b-a22b-2507",    # input $0.09/M (casi igual a quencoder), output $0.10/M (mucho mas barato)
]

_NOMBRE_CARPETA = {
    "qwen/qwen3-coder-30b-a3b-instruct": "taberna-fragua-vulcano-programador-quencoder",
    "qwen/qwen3-coder-next": "taberna-fragua-vulcano-programador-qwencodernext",
    "deepseek/deepseek-v3.2": "taberna-fragua-vulcano-programador-deepseek",
    "openai/gpt-oss-20b": "taberna-fragua-vulcano-programador-gptoss20b",
    "qwen/qwen3-235b-a22b-2507": "taberna-fragua-vulcano-programador-qwen235b",
}


def main() -> None:
    resultados = json.loads(RESULTADOS_DISEÑO.read_text(encoding="utf-8"))
    entrada = next(e for e in resultados if e["modelo"] == DISEÑO_FUENTE and e.get("ok"))
    spec = entrada["spec"]

    system_prompt = build_system_prompt("web-builder-specialist")
    client = GroqClient()

    for programador in PROGRAMADORES:
        carpeta = _NOMBRE_CARPETA[programador]
        print(f"\n--- programador: {programador} -> {carpeta}/ ---", flush=True)
        executor = ToolExecutor(BASE_DIR, auto_yes=True)
        try:
            texto, _ = run_agent(
                client,
                executor,
                system_prompt,
                user_task=_tarea_para(spec, carpeta),
                model=programador,
                max_turns=MAX_TURNS,
            )
        except Exception as exc:  # noqa: BLE001 - seguir con el resto de candidatos
            texto = f"ERROR: {exc}"
            print(f"ERROR: {exc}", flush=True)
        else:
            print("terminado", flush=True)

        (BASE_DIR / f"{carpeta}__resumen.txt").write_text(texto, encoding="utf-8")

    print(f"\nListo. Revisa las carpetas dentro de {BASE_DIR}")


if __name__ == "__main__":
    main()
