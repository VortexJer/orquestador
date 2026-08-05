"""Script de un solo uso: toma los documentos de diseño ya generados por
compare_disenadores.py (Downloads/comparacion-disenadores/resultados.json)
y le pide al "programador" (quencoder = qwen3-coder-30b-a3b-instruct, el
mismo modelo de MODEL_BY_TIER['coder-main']) que construya la web REAL a
partir de cada uno - sin volver a decidir Fase 0, solo ejecutando.

Reusa el skill completo (web-builder-specialist.md) sin modificarlo: la
Fase 0 se "cierra" pasando el JSON del diseñador dentro de la tarea, con
una instruccion explicita de no volver a razonarla.

Uso: ./.venv/Scripts/python.exe -m groq_agent.experiments.build_desde_disenos
"""
from __future__ import annotations

import json
from pathlib import Path

from groq_agent.agent_loop import run_agent
from groq_agent.groq_client import GroqClient
from groq_agent.skills_loader import build_system_prompt
from groq_agent.tools import ToolExecutor

PROGRAMADOR = "qwen/qwen3-coder-30b-a3b-instruct"  # "quencoder"
BASE_DIR = Path.home() / "Downloads" / "comparacion-disenadores"
RESULTADOS_PATH = BASE_DIR / "resultados.json"
MAX_TURNS = 40

_NOMBRE_CARPETA = {
    "deepseek/deepseek-v3.2": "taberna-fragua-vulcano-diseno-deepseek",
    "qwen/qwen3-235b-a22b-2507": "taberna-fragua-vulcano-diseno-qwen235b",
}


def _tarea_para(spec: dict, carpeta: str) -> str:
    negocio = spec["negocio"]
    return f"""[FASE 0 YA RESUELTA POR EL DISEÑADOR - NO LA VUELVAS A RAZONAR NI A CAMBIAR NINGUN VALOR. Empeza directo en la Fase 1 del skill (esqueleto HTML) usando esta decision tal cual.]

{json.dumps(spec, ensure_ascii=False, indent=2)}

Construi la web completa para "{negocio['nombre']}" ({negocio['ciudad']}) aplicando EXACTAMENTE la paleta, el mood, el hero_layout, el elemento_visual_principal, el mapa_secciones, las tipografias, el idioma y los componentes_web_kit de arriba - no elijas otros. Si necesitas datos reales adicionales (horario, telefono exacto) que no vinieron en este JSON, llama fetch_business_from_maps vos mismo.

IMPORTANTE - el campo "momento_de_color" del JSON de arriba NO es opcional ni decorativo: es una seccion que tiene que tener el fondo COMPLETO en `color_acento` (no un boton chico, no un numero destacado - toda la seccion), tal como la describe ese campo. Sin eso, la web entera queda dominada por `color_fondo` (el tono claro) de principio a fin y se ve palida/diluida en vez de potente, que es exactamente el resultado que hay que evitar (ver seccion 2.2 del skill, "el color como narrativa visual", y el checklist final: al menos una seccion intermedia ademas del footer tiene que ir sobre un fondo fuerte - `color_fondo_oscuro` o `color_acento` completo, no solo el footer).

IMPORTANTE - fotos: este JSON de diseño NO trae una lista de fotos ya elegidas, asi que las fotos son responsabilidad tuya en esta tarea. Para cada foto de contenido real (carta, galeria, sobre el negocio) llama VOS `search_images` con una query especifica (no generica) y usa la URL que te devuelve la tool tal cual - NUNCA escribas de memoria una URL de Unsplash/Pexels/Pixabay que "parece" valida sin haber recibido ese resultado de la tool (ver `web-invented-stock-photo-urls-duplicated` en casos dificiles - es un error real y grave, no cosmetico). Si dos fotos de contenido distinto terminan con la misma URL, es señal segura de que al menos una se invento. Si no conseguis una foto real que encaje, usa un placeholder de diseño (gradiente/forma/textura con la paleta) en vez de forzar una URL sin verificar.

Crea la carpeta del proyecto con el nombre exacto "{carpeta}/" dentro del workspace, y escribi ahi index.html, styles.css y script.js."""


def main() -> None:
    resultados = json.loads(RESULTADOS_PATH.read_text(encoding="utf-8"))
    system_prompt = build_system_prompt("web-builder-specialist")
    client = GroqClient()

    for entrada in resultados:
        modelo_disenador = entrada["modelo"]
        if not entrada.get("ok"):
            print(f"\n--- salteando {modelo_disenador} (el diseño no se completo) ---")
            continue
        carpeta = _NOMBRE_CARPETA.get(modelo_disenador, modelo_disenador.replace("/", "__"))
        print(f"\n--- programador ({PROGRAMADOR}) construyendo diseño de {modelo_disenador} -> {carpeta}/ ---", flush=True)

        executor = ToolExecutor(BASE_DIR, auto_yes=True)
        try:
            texto, _ = run_agent(
                client,
                executor,
                system_prompt,
                user_task=_tarea_para(entrada["spec"], carpeta),
                model=PROGRAMADOR,
                max_turns=MAX_TURNS,
            )
        except Exception as exc:  # noqa: BLE001 - seguir con el resto
            texto = f"ERROR: {exc}"
            print(f"ERROR: {exc}", flush=True)
        else:
            print("terminado", flush=True)

        (BASE_DIR / f"{carpeta}__resumen.txt").write_text(texto, encoding="utf-8")

    print(f"\nListo. Revisa las carpetas dentro de {BASE_DIR}")


if __name__ == "__main__":
    main()
