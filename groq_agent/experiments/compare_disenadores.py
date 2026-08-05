"""Script de un solo uso: compara que tan bien deciden estructura/color/
animaciones distintos modelos candidatos a "disenador web" (rol
solo-decision, ver skills/web-designer-specialist.md), corriendo el
mismo negocio real contra cada uno. Guarda el JSON crudo de cada
candidato en SALIDA_DIR para poder armar despues una comparacion visual.

No construye ninguna web real (sin write_file) - cada candidato solo
entrega el documento de diseño (paleta, mood, mapa de secciones,
tipografia, componentes de web-kit elegidos).

Uso: ./.venv/Scripts/python.exe -m groq_agent.experiments.compare_disenadores
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from groq_agent.agent_loop import run_agent
from groq_agent.groq_client import GroqClient
from groq_agent.skills_loader import load_skill_text
from groq_agent.tools import TOOL_SCHEMAS, ToolExecutor

NEGOCIO = "Taberna La Fragua de Vulcano, Madrid"
CANDIDATOS = [
    "deepseek/deepseek-v3.2",       # baseline actual (web-builder-large de produccion)
    "qwen/qwen3-235b-a22b-2507",    # mismo linaje que "quencoder" pero ~7x mas parametros activos, muy barato
    "moonshotai/kimi-k2.6",         # mas caro, fama de buen razonamiento/creatividad - techo de referencia
]
HERRAMIENTAS_PERMITIDAS = {
    "fetch_business_from_maps",
    "fetch_menu_and_reviews_from_maps",
    "list_reference_components",
    "search_images",          # OJO: el nombre real de la tool es search_images, no image_search
    "classify_image_content",
}
SALIDA_DIR = Path.home() / "Downloads" / "comparacion-disenadores"


def _extraer_json(texto: str) -> dict | None:
    # El modelo puede envolver el JSON en ```json ... ``` a pesar de que
    # se le pide que no lo haga - buscar el primer bloque {...} balanceado
    # es mas robusto que asumir que la respuesta entera es JSON puro.
    match = re.search(r"\{.*\}", texto, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def main() -> None:
    SALIDA_DIR.mkdir(parents=True, exist_ok=True)
    system_prompt = load_skill_text("web-designer-specialist")
    schemas = [s for s in TOOL_SCHEMAS if s["function"]["name"] in HERRAMIENTAS_PERMITIDAS]
    client = GroqClient()

    resultados: list[dict] = []
    for modelo in CANDIDATOS:
        print(f"\n--- {modelo} ---", flush=True)
        # workspace_root no se usa (sin write_file en el schema restringido)
        # - se pasa SALIDA_DIR solo porque el constructor lo exige.
        executor = ToolExecutor(SALIDA_DIR, auto_yes=True)
        entrada: dict = {"modelo": modelo}
        try:
            texto, _ = run_agent(
                client,
                executor,
                system_prompt,
                user_task=(
                    f"Disena la web para: {NEGOCIO}. Trae datos reales con las "
                    "herramientas disponibles antes de decidir (nombre exacto, "
                    "categoria, carta/resenas si es un negocio de comida)."
                ),
                model=modelo,
                max_turns=14,
                tool_schemas=schemas,
            )
        except Exception as exc:  # noqa: BLE001 - queremos seguir con el resto de candidatos
            entrada.update(ok=False, error=str(exc))
            print(f"ERROR: {exc}", flush=True)
        else:
            spec = _extraer_json(texto)
            if spec is None:
                entrada.update(ok=False, error="no se pudo parsear JSON", texto_crudo=texto[:3000])
                print("ERROR: no se pudo parsear JSON de la respuesta final", flush=True)
            else:
                entrada.update(ok=True, spec=spec)
                print("OK", flush=True)

        resultados.append(entrada)
        (SALIDA_DIR / f"{modelo.replace('/', '__')}.json").write_text(
            json.dumps(entrada, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    (SALIDA_DIR / "resultados.json").write_text(
        json.dumps(resultados, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nGuardado en {SALIDA_DIR}")


if __name__ == "__main__":
    main()
