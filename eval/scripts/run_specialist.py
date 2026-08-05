"""Corre UN especialista real (su modelo del free-tier + su skill.md como
system prompt) sobre una tarea, y devuelve su salida. Es el ladrillo del
bucle de ajuste de skills: se compara esta salida con la de referencia
(Opus) y se afina el skill hasta acercarlas.

Es de una sola pasada (system=skill, user=tarea) - NO el loop agentico con
herramientas. Para lo que controla el skill -estilo, estructura, formato de
salida, gotchas del dominio, idioma- la pasada unica es un proxy fiel y
barato; la verificacion con ruff/pytest es harina de otro costal.

Uso:
    python -m eval.scripts.run_specialist --skill python-specialist --task "..."
    python -m eval.scripts.run_specialist --skill go-specialist --task-file t.txt --tier coder-main
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


def run_specialist(skill: str, task: str, tier: str | None = None, temperature: float = 0.2) -> str:
    from groq_agent.auto_router import model_for_specialist
    from groq_agent.groq_client import GroqClient
    from groq_agent.skills_loader import build_system_prompt

    system = build_system_prompt(skill)
    tier = tier or model_for_specialist(skill)
    client = GroqClient()
    resp = client.chat(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": task},
        ],
        model=tier,
        temperature=temperature,
    )
    return resp["choices"][0]["message"]["content"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skill", required=True)
    ap.add_argument("--task")
    ap.add_argument("--task-file")
    ap.add_argument("--tier")
    ap.add_argument("--temperature", type=float, default=0.2)
    args = ap.parse_args()

    if not args.task and not args.task_file:
        ap.error("da --task o --task-file")
    task = args.task or Path(args.task_file).read_text(encoding="utf-8")

    _load_dotenv()
    try:
        out = run_specialist(args.skill, task, args.tier, args.temperature)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
