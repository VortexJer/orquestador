"""Importa las API keys de tu cuenta de NovaChat a este .env local.

- SUMA: las keys que ya tengas en el .env se CONSERVAN intactas.
- SIN DUPLICADOS: si una key ya está, no se vuelve a añadir.
- SESIÓN GUARDADA: tras el login, se guarda un token de refresco en
  .novachat_session.json (NO la contraseña) para sincronizar sin re-login.

Modos:
    python scripts/sync_novachat_keys.py --login       # pide email+contraseña, guarda sesión y sincroniza
    python scripts/sync_novachat_keys.py               # sincroniza con la sesión guardada (si no hay, hace --login)
    python scripts/sync_novachat_keys.py --background   # silencioso, para el arranque del orquestador

La URL de NovaChat se puede cambiar con NOVACHAT_URL o --url.
"""
from __future__ import annotations

import getpass
import json
import os
import sys
from pathlib import Path

import httpx

# Supabase de NovaChat — PÚBLICOS (el anon key va en el cliente web, es su rol).
SUPABASE_URL = "https://uyzwldbetcogjujpafzj.supabase.co"
SUPABASE_ANON = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6In"
    "V5endsZGJldGNvZ2p1anBhZnpqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODU4NzIyMTIs"
    "ImV4cCI6MjEwMTQ0ODIxMn0.Ol0uT9yCAGqlc34i8Dq5nRaT_JSu9BUjih6TgpFXbq4"
)
NOVACHAT_URL = os.environ.get("NOVACHAT_URL", "https://nova-chat-638n.onrender.com")

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
SESSION_PATH = ROOT / ".novachat_session.json"

MAPA = {
    "groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY", "mistral": "MISTRAL_API_KEY",
    "codestral": "CODESTRAL_API_KEY", "cohere": "COHERE_API_KEY", "huggingface": "HUGGINGFACE_API_KEY",
    "sambanova": "SAMBANOVA_API_KEY", "zai": "ZAI_API_KEY", "modelscope": "MODELSCOPE_API_KEY",
    "novita": "NOVITA_API_KEY", "chutes": "CHUTES_API_KEY", "nvidia": "NVIDIA_API_KEY", "ovh": "OVH_API_KEY",
    "github": "GITHUB_TOKEN", "pexels": "PEXELS_API_KEY", "unsplash": "UNSPLASH_ACCESS_KEY",
    "pixabay": "PIXABAY_API_KEY", "stackexchange": "STACKEXCHANGE_KEY",
    "tavily": "TAVILY_API_KEY", "ollama": "OLLAMA_API_KEY", "jina": "JINA_API_KEY",
}


# ---------- auth (habla con Supabase con el anon key público) ----------
def _auth_password(email: str, password: str) -> dict:
    r = httpx.post(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
        headers={"apikey": SUPABASE_ANON, "Content-Type": "application/json"},
        json={"email": email, "password": password}, timeout=90.0,
    )
    r.raise_for_status()
    return r.json()


def _auth_refresh(refresh_token: str) -> dict:
    r = httpx.post(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=refresh_token",
        headers={"apikey": SUPABASE_ANON, "Content-Type": "application/json"},
        json={"refresh_token": refresh_token}, timeout=90.0,
    )
    r.raise_for_status()
    return r.json()


def _fetch_keys(access_token: str, url: str) -> dict:
    r = httpx.post(
        f"{url.rstrip('/')}/api/export-keys",
        headers={"Authorization": f"Bearer {access_token}"}, json={}, timeout=90.0,
    )
    r.raise_for_status()
    return r.json().get("keys", {})


def _guardar_sesion(refresh_token: str) -> None:
    SESSION_PATH.write_text(json.dumps({"refresh_token": refresh_token}), encoding="utf-8")


def _cargar_sesion() -> str | None:
    if not SESSION_PATH.exists():
        return None
    try:
        return json.loads(SESSION_PATH.read_text(encoding="utf-8")).get("refresh_token")
    except Exception:  # noqa: BLE001
        return None


# ---------- merge en el .env ----------
def _leer_lineas() -> list[str]:
    return ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []


def _asig(l: str) -> bool:
    return "=" in l and not l.lstrip().startswith("#")


def _nombre(l: str) -> str:
    return l.split("=", 1)[0].strip()


def _valor(l: str) -> str:
    return l.split("=", 1)[1].strip() if "=" in l else ""


def _valores_existentes(lineas: list[str]) -> set[str]:
    vals: set[str] = set()
    for l in lineas:
        if _asig(l):
            for p in _valor(l).split(","):
                if p.strip():
                    vals.add(p.strip())
    return vals


def _slot_libre(lineas: list[str], base: str) -> str:
    def tiene(nombre: str) -> bool:
        return any(_asig(l) and _nombre(l) == nombre and _valor(l) for l in lineas)
    if not tiene(base):
        return base
    i = 2
    while tiene(f"{base}_{i}"):
        i += 1
    return f"{base}_{i}"


def _merge(keys: dict) -> tuple[int, int]:
    lineas = _leer_lineas()
    existentes = _valores_existentes(lineas)
    nuevas = saltadas = 0
    for prov, valores in keys.items():
        base = MAPA.get(prov)
        if not base:
            continue
        for v in valores:
            v = (v or "").strip()
            if not v:
                continue
            if v in existentes:
                saltadas += 1
                continue
            nombre = _slot_libre(lineas, base)
            lineas.append(f"{nombre}={v}")
            existentes.add(v)
            nuevas += 1
    if nuevas:
        ENV_PATH.write_text("\n".join(lineas).rstrip("\n") + "\n", encoding="utf-8")
    return nuevas, saltadas


# ---------- modos ----------
def _url_arg() -> str:
    if "--url" in sys.argv:
        return sys.argv[sys.argv.index("--url") + 1]
    return NOVACHAT_URL


def login(url: str) -> None:
    print(f"Inicia sesión en NovaChat ({url}) para importar tus API keys.")
    email = input("  Email: ").strip()
    password = getpass.getpass("  Contraseña (no se muestra ni se guarda): ")
    try:
        data = _auth_password(email, password)
    except httpx.HTTPStatusError:
        print("  Email o contraseña incorrectos.")
        sys.exit(1)
    _guardar_sesion(data["refresh_token"])
    nuevas, saltadas = _merge(_fetch_keys(data["access_token"], url))
    print(f"  Sesión guardada. {nuevas} key(s) nueva(s); {saltadas} ya la(s) tenías. "
          f"A partir de ahora se sincroniza sola al arrancar el orquestador.")


def sync(url: str, silencioso: bool) -> None:
    rt = _cargar_sesion()
    if not rt:
        if silencioso:
            return  # sin sesión: no molestar el arranque
        login(url)
        return
    try:
        data = _auth_refresh(rt)
        _guardar_sesion(data["refresh_token"])  # el refresh_token rota
        nuevas, saltadas = _merge(_fetch_keys(data["access_token"], url))
        if not silencioso:
            print(f"{nuevas} key(s) nueva(s) importada(s) de NovaChat; {saltadas} ya la(s) tenías.")
    except Exception as exc:  # noqa: BLE001 - en background nunca debe romper el arranque
        if not silencioso:
            print(f"No se pudo sincronizar con NovaChat: {exc}")


def main() -> None:
    url = _url_arg()
    if "--login" in sys.argv:
        login(url)
    elif "--background" in sys.argv:
        sync(url, silencioso=True)
    else:
        sync(url, silencioso=False)


if __name__ == "__main__":
    main()
