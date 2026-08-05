"""Sonda de function-calling por candidato de una cadena de proveedores.

Motivo (visto en vivo 2026-08-03): web-builder-large narraba sus tool calls
como TEXTO -`load_skill_section(migrations: {...})`- en vez de emitirlas como
function-calling, asi que web-builder no producia NINGUN archivo. El comentario
de providers.py dice que la cadena se eligio porque "DeepSeek V3.x lo pasa
4/4", pero eso hay que RE-verificarlo: un proveedor cambia de modelo detras del
mismo id, o cae y la cadena baja a uno que narra.

Esta sonda prueba CADA candidato de una cadena con el system prompt REAL del
especialista (~62K, que es donde aparece el fallo; una prueba trivial no lo
reproduce) + sus tools, y reporta: TOOLCALL_OK (emitio una tool call de verdad)
o NARRA (escribio la llamada como texto) o ERROR.

Uso:
    python -m eval.scripts.probe_toolcall --tier web-builder-large --skill web-builder-specialist
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# pinta de tool call NARRADA como texto: nombre_de_tool( ... o nombre: {json}
_NARRA = re.compile(
    r"\b(load_skill_section|list_reference_components|fetch_business_from_maps|"
    r"search_images|search_web|write_file|read_file|verificar_web|deep_research|"
    r"wikipedia|generate_\w+)\s*[\(:]",
    re.I,
)


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
    ap.add_argument("--tier", required=True)
    ap.add_argument("--skill", required=True)
    ap.add_argument("--task", default="Hazme una landing para el estudio de diseno 'Studio Zero'.")
    args = ap.parse_args()

    _load_dotenv()
    from groq_agent import providers as prov
    from groq_agent.groq_client import GroqClient
    from groq_agent.skills_loader import build_system_prompt
    from groq_agent.tools import schemas_for

    chain = prov.CHAIN_BY_TIER[args.tier]
    system = build_system_prompt(args.skill)
    tools = schemas_for(args.skill)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": args.task},
    ]
    print(f"sonda tier={args.tier} skill={args.skill} "
          f"(system {len(system)} chars, {len(tools)} tools, {len(chain)} candidatos)\n")

    filas = []
    for cand in chain:
        etq = f"{cand.provider}:{cand.model}"
        provider = prov.PROVIDERS.get(cand.provider)
        if provider is None or not provider.activo or prov.resolve_base_url(provider) is None:
            filas.append((etq, "SIN-KEY/INACTIVO", ""))
            print(f"  {etq:55s} SIN-KEY/INACTIVO")
            continue
        try:
            cli = GroqClient(provider=cand.provider)
            resp = cli.chat(messages=messages, tools=tools, model=f"{cand.provider}:{cand.model}")
            msg = resp["choices"][0]["message"]
            if msg.get("tool_calls"):
                nombres = ",".join(c["function"]["name"] for c in msg["tool_calls"][:3])
                veredicto, detalle = "TOOLCALL_OK", nombres
            else:
                cont = (msg.get("content") or "")[:120].replace("\n", " ")
                veredicto = "NARRA" if _NARRA.search(msg.get("content") or "") else "TEXTO-SIN-TOOL"
                detalle = cont
        except Exception as e:  # noqa: BLE001
            veredicto, detalle = "ERROR", f"{type(e).__name__}: {str(e)[:80]}"
        filas.append((etq, veredicto, detalle))
        print(f"  {etq:55s} {veredicto:16s} {detalle}")

    ok = [f for f in filas if f[1] == "TOOLCALL_OK"]
    print(f"\n{len(ok)}/{len(chain)} candidatos emiten tool call de verdad.")
    print("Primeros que PASAN (candidatos validos para la cabeza de la cadena):")
    for etq, _v, _d in ok:
        print(f"  - {etq}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
