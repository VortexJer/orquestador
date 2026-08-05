"""Corre un especialista con el LOOP AGENTICO COMPLETO (sus herramientas
reales), no de una sola pasada. Necesario para los especialistas que
ENTREGAN llamando a una herramienta (office_*, que generan .docx/.xlsx/.pptx,
o research que busca): la pasada unica de run_specialist.py no ejecuta tools,
asi que no puede validar que el ARCHIVO se genere.

Reporta que archivos NUEVOS aparecieron en el workspace: esa es la prueba de
que el especialista entrego de verdad y no solo escupio un JSON.

Uso:
    python -m eval.scripts.run_specialist_agentic --skill office-presentation-specialist \
        --task "hazme una presentacion sobre el bmw F40 118d" --workspace /tmp/pptx_test
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


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
    ap.add_argument("--skill", required=True)
    ap.add_argument("--task")
    ap.add_argument("--task-file")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--tier")
    ap.add_argument("--max-turns", type=int, default=8)
    args = ap.parse_args()
    task = args.task or Path(args.task_file).read_text(encoding="utf-8")

    _load_dotenv()
    from groq_agent.agent_loop import run_agent
    from groq_agent.auto_router import model_for_specialist
    from groq_agent.groq_client import GroqClient
    from groq_agent.skills_loader import build_system_prompt
    from groq_agent.tools import ToolExecutor, schemas_for

    ws = Path(args.workspace)
    ws.mkdir(parents=True, exist_ok=True)
    antes = {p.name for p in ws.rglob("*") if p.is_file()}

    client = GroqClient()
    executor = ToolExecutor(ws, auto_yes=True, client=client, skill_name=args.skill)
    model = args.tier or model_for_specialist(args.skill)
    try:
        texto, _ = run_agent(
            client=client, executor=executor,
            system_prompt=build_system_prompt(args.skill),
            user_task=task, model=model, max_turns=args.max_turns,
            tool_schemas=schemas_for(args.skill),
        )
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    nuevos_paths = sorted(
        (p for p in ws.rglob("*") if p.is_file() and p.name not in antes),
        key=lambda p: p.name,
    )
    nuevos = [p.name for p in nuevos_paths]
    print("\n===== RESULTADO =====")
    print(f"skill={args.skill} modelo={model}")
    print(f"ARCHIVOS NUEVOS EN EL WORKSPACE: {nuevos or '(NINGUNO - no entrego archivo)'}")

    # LECCION del bug de oficina: el evaluador tiene que VER el entregable real
    # (el archivo), no solo el chat. Si solo ve el chat, "arregla" el skill
    # quitandole el guardar para leer el resultado como texto - justo lo que no
    # queremos. Por eso aqui se muestra el contenido de cada archivo (o se anota
    # que es binario/render), para juzgar la ENTREGA, nunca el volcado.
    _TEXTO = {".html", ".htm", ".css", ".js", ".mjs", ".json", ".md", ".txt",
              ".py", ".ts", ".tsx", ".jsx", ".java", ".cs", ".cpp", ".cc", ".h",
              ".go", ".rs", ".php", ".rb", ".kt", ".sql", ".sh", ".yaml", ".yml",
              ".toml", ".ini", ".cfg", ".xml", ".svg", ""}
    _RENDER = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    for p in nuevos_paths:
        ext = p.suffix.lower()
        tam = p.stat().st_size
        if ext in _RENDER:
            print(f"\n----- {p.name} ({tam} bytes) -> RENDER/imagen (evidencia visual)")
            continue
        if ext in _TEXTO or p.name.lower() in {"dockerfile", "makefile"}:
            try:
                cuerpo = p.read_text(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                print(f"\n----- {p.name} ({tam} bytes) -> no legible como texto")
                continue
            cab = cuerpo[:1200]
            print(f"\n----- {p.name} ({tam} bytes, {cuerpo.count(chr(10)) + 1} lineas) -----")
            print(cab + ("\n...[recortado]..." if len(cuerpo) > 1200 else ""))
        else:
            print(f"\n----- {p.name} ({tam} bytes) -> binario ({ext or 'sin ext'})")
    # El texto final ENTERO (hasta 6000): para un especialista de TEXTO
    # (email, research, review...) ESTE es el entregable, y truncarlo es el
    # mismo "no ver el resultado" que rompio el afinado de oficina. Se muestra
    # completo para poder juzgarlo y afinar bien.
    t = texto or ""
    print(f"\n----- TEXTO FINAL EN EL CHAT ({len(t)} chars) -----")
    print(t[:6000] + ("\n...[recortado a 6000]..." if len(t) > 6000 else ""))
    return 0 if nuevos else 2


if __name__ == "__main__":
    sys.exit(main())
