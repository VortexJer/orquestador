"""Importa las API keys de tu cuenta de NovaChat a este .env local.

- SUMA: las keys que ya tengas en el .env se CONSERVAN intactas.
- SIN DUPLICADOS: si una key ya está (en cualquier variable), no se vuelve a añadir.
- Cada key nueva va a su slot libre: GROQ_API_KEY, luego GROQ_API_KEY_2, _3...

Uso:
    python scripts/sync_novachat_keys.py
    (te pide el email y la contraseña de tu cuenta de NovaChat)

La URL de NovaChat se puede cambiar con la env var NOVACHAT_URL o --url.
Requiere: httpx (ya está en requirements).
"""
from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

import httpx

NOVACHAT_URL = os.environ.get("NOVACHAT_URL", "https://nova-chat-638n.onrender.com")
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

# proveedor en NovaChat -> variable base en el .env del orquestador.
MAPA = {
    "groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY", "mistral": "MISTRAL_API_KEY",
    "codestral": "CODESTRAL_API_KEY", "cohere": "COHERE_API_KEY", "huggingface": "HUGGINGFACE_API_KEY",
    "sambanova": "SAMBANOVA_API_KEY", "zai": "ZAI_API_KEY", "modelscope": "MODELSCOPE_API_KEY",
    "novita": "NOVITA_API_KEY", "chutes": "CHUTES_API_KEY", "nvidia": "NVIDIA_API_KEY", "ovh": "OVH_API_KEY",
    # servicios
    "github": "GITHUB_TOKEN", "pexels": "PEXELS_API_KEY", "unsplash": "UNSPLASH_ACCESS_KEY",
    "pixabay": "PIXABAY_API_KEY", "stackexchange": "STACKEXCHANGE_KEY",
    "tavily": "TAVILY_API_KEY", "ollama": "OLLAMA_API_KEY", "jina": "JINA_API_KEY",
}


def _leer_lineas() -> list[str]:
    if not ENV_PATH.exists():
        return []
    return ENV_PATH.read_text(encoding="utf-8").splitlines()


def _es_asignacion(linea: str) -> bool:
    return "=" in linea and not linea.lstrip().startswith("#")


def _nombre(linea: str) -> str:
    return linea.split("=", 1)[0].strip()


def _valor(linea: str) -> str:
    return linea.split("=", 1)[1].strip() if "=" in linea else ""


def _valores_existentes(lineas: list[str]) -> set[str]:
    """Todos los valores de key ya presentes (admite varias separadas por coma),
    para no volver a añadir una que ya tienes."""
    vals: set[str] = set()
    for l in lineas:
        if _es_asignacion(l):
            for parte in _valor(l).split(","):
                p = parte.strip()
                if p:
                    vals.add(p)
    return vals


def _slot_libre(lineas: list[str], base: str) -> str:
    """Primer nombre de variable libre para `base`: BASE, luego BASE_2, BASE_3...
    'Libre' = no existe o existe pero vacío."""
    def tiene_valor(nombre: str) -> bool:
        return any(_es_asignacion(l) and _nombre(l) == nombre and _valor(l) for l in lineas)

    if not tiene_valor(base):
        return base
    i = 2
    while tiene_valor(f"{base}_{i}"):
        i += 1
    return f"{base}_{i}"


def main() -> None:
    url = NOVACHAT_URL
    if "--url" in sys.argv:
        url = sys.argv[sys.argv.index("--url") + 1]
    url = url.rstrip("/")

    print(f"Importando keys de NovaChat ({url}) a {ENV_PATH}")
    email = input("Email de tu cuenta de NovaChat: ").strip()
    password = getpass.getpass("Contraseña (no se muestra): ")

    try:
        r = httpx.post(f"{url}/api/export-keys", json={"email": email, "password": password}, timeout=30.0)
    except Exception as exc:  # noqa: BLE001
        print(f"No se pudo contactar con NovaChat: {exc}")
        sys.exit(1)
    if r.status_code != 200:
        try:
            msg = r.json().get("error", r.text)
        except Exception:  # noqa: BLE001
            msg = r.text
        print(f"Error ({r.status_code}): {msg}")
        sys.exit(1)

    keys: dict[str, list[str]] = r.json().get("keys", {})
    if not keys:
        print("Tu cuenta de NovaChat no tiene ninguna key guardada.")
        return

    lineas = _leer_lineas()
    existentes = _valores_existentes(lineas)
    nuevas = 0
    saltadas = 0
    for prov, valores in keys.items():
        base = MAPA.get(prov)
        if not base:
            continue  # proveedor sin equivalente en el orquestador
        for v in valores:
            v = (v or "").strip()
            if not v:
                continue
            if v in existentes:
                saltadas += 1
                continue  # ya la tienes: no duplicar
            nombre = _slot_libre(lineas, base)
            lineas.append(f"{nombre}={v}")
            existentes.add(v)
            nuevas += 1
            print(f"  + {nombre}")

    if nuevas:
        ENV_PATH.write_text("\n".join(lineas).rstrip("\n") + "\n", encoding="utf-8")
    print(f"\nListo. {nuevas} key(s) nueva(s) añadida(s); {saltadas} ya la(s) tenías (no duplicadas). "
          f"Las keys que ya estaban en tu .env se conservan.")


if __name__ == "__main__":
    main()
