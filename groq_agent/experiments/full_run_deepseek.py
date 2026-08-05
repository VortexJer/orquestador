"""Prueba de punta a punta: deepseek-v3.2 corriendo el skill de
produccion COMPLETO (web-builder-specialist.md, Fase 0 a Fase 6, sin
diseño pre-resuelto por otro modelo) con el catalogo de tools SIN
restringir (incluye render_check con la pasada movil+escritorio nueva,
critique_screenshot, search_images con la regla de no inventar URLs,
fetch_menu_and_reviews_from_maps con reseñas reales ordenadas por
valoracion) - el resultado que el usuario obtendria en produccion sin
intervencion manual, para juzgar si las correcciones de esta sesion
alcanzan.

Uso: ./.venv/Scripts/python.exe -m groq_agent.experiments.full_run_deepseek
"""
from __future__ import annotations

from pathlib import Path

from groq_agent.agent_loop import run_agent
from groq_agent.groq_client import GroqClient
from groq_agent.skills_loader import build_system_prompt
from groq_agent.tools import ToolExecutor

MODELO = "deepseek/deepseek-v3.2"
NEGOCIO = "Taberna La Fragua de Vulcano, Madrid"
CARPETA = "taberna-fragua-vulcano-full-deepseek-v3-fotos-y-color"
BASE_DIR = Path.home() / "Downloads" / "comparacion-disenadores"
MAX_TURNS = 60


def main() -> None:
    system_prompt = build_system_prompt("web-builder-specialist")
    client = GroqClient()
    executor = ToolExecutor(BASE_DIR, auto_yes=True)

    tarea = (
        f'Construi la web completa para "{NEGOCIO}". Es un negocio real - '
        "usa las herramientas para traer datos reales (Maps, carta, reseñas, "
        "fotos) antes de decidir e inventar nada. Segui el skill entero, "
        f'fase por fase. Crea la carpeta del proyecto "{CARPETA}/" dentro '
        "del workspace."
    )

    print(f"--- {MODELO} corriendo el skill completo (Fase 0 a Fase 6) ---", flush=True)
    texto, _ = run_agent(
        client, executor, system_prompt, user_task=tarea, model=MODELO, max_turns=MAX_TURNS,
    )
    (BASE_DIR / f"{CARPETA}__resumen.txt").write_text(texto, encoding="utf-8")
    print("\n--- resumen final ---")
    print(texto)
    print(f"\nListo. Revisa {BASE_DIR / CARPETA}")


if __name__ == "__main__":
    main()
