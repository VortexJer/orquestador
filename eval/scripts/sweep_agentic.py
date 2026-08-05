"""Barrido AGENTICO de TODOS los especialistas: corre cada uno con el loop
completo (sus herramientas reales) y una tarea de su dominio, y clasifica el
resultado -> ¿ENTREGO un archivo, o solo escribio texto en la terminal?

Responde en concreto a "a ver si todos crean documentos o escriben en la
terminal": para los que DEBEN generar un archivo (office_*, web) comprueba que
el archivo aparezca; para los demas registra que su entrega es texto/codigo
(lo correcto en su dominio) y AVISA si soltaron un artefacto estructurado como
texto sin crear nada (el bug de la presentacion).

Uso:
    python -m eval.scripts.sweep_agentic                 # todos
    python -m eval.scripts.sweep_agentic --solo office   # filtra por substring
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# (skill, tarea, extension_esperada_o_None). None = su entrega es texto/codigo,
# no un documento; se registra pero no se exige archivo.
CASOS: list[tuple[str, str, str | None]] = [
    ("office-word-specialist", "Hazme un informe Word corto sobre el BMW Serie 1 F40 118d.", ".docx"),
    ("office-spreadsheet-specialist", "Hazme un Excel con un presupuesto mensual de gastos del hogar, con totales.", ".xlsx"),
    ("office-presentation-specialist", "Hazme una presentacion sobre el BMW Serie 1 F40 118d.", ".pptx"),
    ("web-builder-specialist", "Hazme una landing sencilla de una cafeteria de barrio.", ".html"),
    ("office-email-specialist", "Redacta un correo avisando a un cliente de un retraso de dos dias en la entrega.", None),
    ("web-designer-specialist", "Propon la direccion de diseno (paleta, tipografia, retícula) para la web de una cafeteria.", None),
    ("python-specialist", "Escribe una funcion que valide un IBAN espanol y guardala en iban.py.", ".py"),
    ("typescript-specialist", "Escribe una funcion TS debounce y guardala en debounce.ts.", ".ts"),
    ("javascript-specialist", "Escribe una funcion JS que agrupe un array por clave y guardala en groupBy.js.", ".js"),
    ("java-specialist", "Escribe una clase Java Stack generica y guardala en Stack.java.", ".java"),
    ("csharp-specialist", "Escribe un metodo de extension C# para trocear un IEnumerable y guardalo en Chunk.cs.", ".cs"),
    ("cpp-specialist", "Escribe una funcion C++ que invierta una lista enlazada y guardala en reverse.cpp.", ".cpp"),
    ("go-specialist", "Escribe una funcion Go que fusione dos slices ordenados y guardala en merge.go.", ".go"),
    ("rust-specialist", "Escribe una funcion Rust de fibonacci con memoizacion y guardala en fib.rs.", ".rs"),
    ("php-specialist", "Escribe una funcion PHP que sanitice un slug y guardala en slug.php.", ".php"),
    ("ruby-specialist", "Escribe un metodo Ruby que aplane un hash anidado y guardalo en flatten.rb.", ".rb"),
    ("kotlin-specialist", "Escribe una funcion de extension Kotlin para reintentar y guardala en Retry.kt.", ".kt"),
    ("sql-specialist", "Escribe una consulta de los 5 clientes con mas pedidos y guardala en top_clientes.sql.", ".sql"),
    ("testing-specialist", "Escribe tests pytest para una funcion suma(a,b) y guardalos en test_suma.py.", ".py"),
    ("devops-iac-specialist", "Escribe un Dockerfile para una app Node y guardalo como Dockerfile.", None),
    ("docs-specialist", "Escribe un README para una CLI llamada foo y guardalo en README.md.", ".md"),
    ("refactor-specialist", "Refactoriza esta funcion para que sea mas clara: def f(x):\n  return [i*2 for i in x if i%2==0]", None),
    ("code-review-specialist", "Revisa este codigo: def div(a,b):\n  return a/b", None),
    ("debugging-specialist", "Este codigo peta con IndexError a veces: xs=[1,2,3]; print(xs[len(xs)]). Que pasa?", None),
    ("security-specialist", "Revisa por seguridad: query = \"SELECT * FROM u WHERE n='\" + nombre + \"'\"", None),
    ("accessibility-specialist", "Que problemas de accesibilidad tiene: <img src=a.png><div onclick=go()>Ir</div>", None),
    ("research-specialist", "Quien fundo Anthropic y en que ano?", None),
    ("generalist-tiny", "Resume en tres bullets: el gato se subio al tejado, maullo, y bajo cuando llovio.", None),
]

# Pinta de artefacto de oficina volcado como TEXTO (el bug que buscamos).
_ARTEFACTO = re.compile(
    r'"(?:slides|sheets|sections)"\s*:\s*\[|"type"\s*:\s*"(?:heading|paragraph|list)"|'
    r'<!doctype html|<html', re.I)


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
    ap.add_argument("--solo", default="", help="filtra especialistas por substring")
    ap.add_argument("--saltar", default="", help="coma-lista de substrings a excluir")
    ap.add_argument("--no-lenguajes", action="store_true",
                    help="salta los especialistas de lenguaje de programacion (los 'obvios')")
    ap.add_argument("--max-turns", type=int, default=8)
    ap.add_argument("--out", default=str(REPO / "eval" / "sweep_agentic_result.md"))
    args = ap.parse_args()
    saltar = [s for s in args.saltar.split(",") if s]
    lenguajes = {
        "python-specialist", "typescript-specialist", "javascript-specialist",
        "java-specialist", "csharp-specialist", "cpp-specialist", "go-specialist",
        "rust-specialist", "php-specialist", "ruby-specialist", "kotlin-specialist",
        "sql-specialist",
    }

    _load_dotenv()
    from groq_agent.agent_loop import run_agent
    from groq_agent.auto_router import model_for_specialist
    from groq_agent.groq_client import GroqClient
    from groq_agent.skills_loader import build_system_prompt
    from groq_agent.tools import ToolExecutor, schemas_for

    casos = [c for c in CASOS if args.solo in c[0]
             and not any(s in c[0] for s in saltar)
             and not (args.no_lenguajes and c[0] in lenguajes)]
    client = GroqClient()
    out = Path(args.out)
    filas: list[str] = []
    print(f"barriendo {len(casos)} especialistas...\n", flush=True)

    for skill, task, ext in casos:
        ws = REPO / "_sweep_ws" / skill.replace("-specialist", "")
        ws.mkdir(parents=True, exist_ok=True)
        antes = {p.name for p in ws.rglob("*") if p.is_file()}
        try:
            model = model_for_specialist(skill)  # KeyError si no esta en specialists.yaml
            texto, _ = run_agent(
                client=client,
                executor=ToolExecutor(ws, auto_yes=True, client=client, skill_name=skill),
                system_prompt=build_system_prompt(skill), user_task=task,
                model=model, max_turns=args.max_turns, tool_schemas=schemas_for(skill),
            )
        except Exception as e:  # noqa: BLE001
            mdl = locals().get("model", "?")
            filas.append(f"| {skill} | `{mdl}` | ERROR | {type(e).__name__}: {str(e)[:80]} |")
            print(f"  {skill}: ERROR {type(e).__name__}: {str(e)[:80]}", flush=True)
            traceback.print_exc()
            continue

        nuevos = sorted(p.name for p in ws.rglob("*") if p.is_file() and p.name not in antes)
        texto = texto or ""
        volcado = bool(_ARTEFACTO.search(texto))
        if ext is not None:  # DEBIA crear un archivo
            ok = any(n.lower().endswith(ext) for n in nuevos)
            veredicto = "OK archivo" if ok else ("FALLO: volcado en texto" if volcado else "FALLO: sin archivo")
        else:  # su entrega es texto/codigo; solo avisamos si soltó un artefacto
            veredicto = "AVISO: artefacto en texto" if volcado else ("texto (ok)" if not nuevos else "texto + archivo")
        archivos = ", ".join(nuevos) or "(ninguno)"
        filas.append(f"| {skill} | `{model}` | {veredicto} | {archivos} |")
        print(f"  {skill}: {veredicto} -> {archivos}", flush=True)

    tabla = ("# Barrido agentico de especialistas\n\n"
             "| Especialista | Modelo | Veredicto | Archivos creados |\n"
             "|---|---|---|---|\n" + "\n".join(filas) + "\n")
    out.write_text(tabla, encoding="utf-8")
    print(f"\nescrito {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
