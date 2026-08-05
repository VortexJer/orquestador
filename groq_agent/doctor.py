"""`--doctor`: por que no funciona algo, en una pantalla.

QUE COMPRUEBA Y POR QUE ESAS COSAS
Cada chequeo corresponde a un fallo REAL que se ha visto en este proyecto:
  - keys invisibles/no-ASCII: tumbaban httpx con UnicodeEncodeError, y el
    mensaje no se parecia en nada a la causa.
  - keys demasiado cortas: se colaron palabras del propio asistente ("no",
    "fin") como si fueran API keys.
  - sin cobertura antes de NVIDIA: la red de seguridad se gasta a diario y
    deja de serlo.
  - playwright sin navegador: render_check falla a mitad de una tarea web,
    despues de haber gastado turnos.
  - base de casos dificiles sin cargar: search_hard_cases devuelve vacio
    siempre y nadie se entera.
Es deliberadamente RAPIDO: no hace ni una llamada a un modelo. Para probar
que los proveedores responden de verdad esta --check-providers, que si
cuesta red.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

ESTADO_OK = "ok"
ESTADO_AVISO = "aviso"
ESTADO_FALLO = "fallo"


def _chequeo(nombre, estado, detalle, arreglo=""):
    return {"nombre": nombre, "estado": estado, "detalle": detalle, "arreglo": arreglo}


def _revisar_env() -> list[dict]:
    from groq_agent.config_wizard import _env_path, _leer_env

    ruta = _env_path()
    if not ruta.exists():
        return [_chequeo(
            ".env", ESTADO_FALLO, "no existe",
            "orquestador --config  (lo crea y te pide las keys)",
        )]
    _, valores = _leer_env(ruta)
    fuera = [
        v for v in ("API_KEYS", "QDRANT_MODE", "INFERENCE_MODE")
        if v not in valores
    ]
    if fuera:
        return [_chequeo(
            ".env", ESTADO_AVISO,
            f"faltan variables del orquestador de produccion: {', '.join(fuera)}",
            "revisa .env.example",
        )]
    return [_chequeo(".env", ESTADO_OK, f"{len(valores)} variables")]


def _revisar_keys() -> list[dict]:
    from groq_agent import providers as prov
    from groq_agent.config_wizard import validar_key

    salida = []
    activos = [(n, p) for n, p in prov.PROVIDERS.items() if p.activo]
    con_key = [n for n, p in activos if prov.keys_for(p)]
    sospechosas = []
    for nombre, provider in activos:
        for i, key in enumerate(prov.keys_for(provider)):
            motivo = validar_key(key) if key else None
            if motivo:
                sospechosas.append(f"{nombre} #{i + 1}: {motivo}")

    if sospechosas:
        salida.append(_chequeo(
            "API keys", ESTADO_FALLO, "; ".join(sospechosas[:3]),
            "orquestador --config",
        ))
    else:
        salida.append(_chequeo(
            "API keys", ESTADO_OK,
            f"{len(con_key)}/{len(activos)} proveedores activos con key",
        ))

    apagados = prov.desactivados()
    if apagados:
        salida.append(_chequeo(
            "proveedores de pago", ESTADO_OK,
            f"{len(apagados)} desactivados: {', '.join(n for n, _ in apagados)}",
        ))
    return salida


def _revisar_cobertura() -> list[dict]:
    """Cobertura por tier sin tocar la red: solo mira si hay candidatos de
    proveedores ACTIVOS y CON KEY antes de NVIDIA."""
    from groq_agent import providers as prov

    sin_cobertura = []
    for tier, chain in prov.CHAIN_BY_TIER.items():
        vivos = 0
        for candidate in chain:
            if candidate.provider == "nvidia":
                break
            p = prov.PROVIDERS.get(candidate.provider)
            if p and p.activo and prov.keys_for(p):
                vivos += 1
        if vivos == 0:
            sin_cobertura.append(tier)

    if sin_cobertura:
        return [_chequeo(
            "cobertura antes de NVIDIA", ESTADO_AVISO,
            f"sin alternativa: {', '.join(sin_cobertura)}",
            "orquestador --config  (NVIDIA es la red de seguridad: si se usa "
            "a diario, se agota)",
        )]
    return [_chequeo(
        "cobertura antes de NVIDIA", ESTADO_OK,
        f"los {len(prov.CHAIN_BY_TIER)} tiers tienen alternativa",
    )]


def _revisar_dependencias() -> list[dict]:
    salida = []

    # Playwright: render_check es la unica forma que tiene el agente de
    # "ver" la web que construye. Si falta el navegador, la tarea falla
    # DESPUES de haber gastado turnos escribiendo.
    if importlib.util.find_spec("playwright") is None:
        salida.append(_chequeo(
            "playwright", ESTADO_AVISO, "no instalado - render_check no funcionara",
            "pip install playwright && playwright install chromium",
        ))
    else:
        from pathlib import Path as _P

        cache = _P.home() / "AppData" / "Local" / "ms-playwright"
        if os.name != "nt":
            cache = _P.home() / ".cache" / "ms-playwright"
        if cache.exists() and any(cache.glob("chromium*")):
            salida.append(_chequeo("playwright", ESTADO_OK, "con chromium"))
        else:
            salida.append(_chequeo(
                "playwright", ESTADO_AVISO, "instalado pero sin navegador",
                "playwright install chromium",
            ))
    return salida


def _revisar_rag(repo: Path) -> list[dict]:
    datos = repo / "qdrant_local_data"
    casos = repo / "hard_cases" / "by_domain"
    n_archivos = len(list(casos.glob("*.json"))) if casos.exists() else 0

    if not datos.exists() or not any(datos.iterdir()):
        return [_chequeo(
            "casos dificiles", ESTADO_AVISO,
            f"{n_archivos} archivos de casos, pero la base no esta cargada - "
            "search_hard_cases devolvera vacio",
            "python -m app.rag.ingest",
        )]
    return [_chequeo("casos dificiles", ESTADO_OK, f"{n_archivos} dominios cargados")]


def _revisar_skills(repo: Path) -> list[dict]:
    from groq_agent.skills_loader import list_available_skills

    skills = list_available_skills()
    if not skills:
        return [_chequeo("skills", ESTADO_FALLO, "no se encontro ninguno", "revisa skills/")]

    # Un skill listado en tool_sets.yaml que ya no existe (renombrado,
    # borrado) se detecta aca en vez de en mitad de una tarea.
    import yaml

    cfg_path = repo / "config" / "tool_sets.yaml"
    huerfanos = []
    if cfg_path.exists():
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        huerfanos = [e for e in (cfg.get("especialistas") or {}) if e not in skills]
    if huerfanos:
        return [_chequeo(
            "skills", ESTADO_AVISO,
            f"{len(skills)} skills; tool_sets.yaml menciona {len(huerfanos)} que no existen: "
            f"{', '.join(huerfanos[:3])}",
            "revisa config/tool_sets.yaml",
        )]
    return [_chequeo("skills", ESTADO_OK, f"{len(skills)} disponibles")]


def diagnosticar(repo: Path) -> list[dict]:
    """Todos los chequeos. Ninguno hace una llamada a un modelo."""
    salida: list[dict] = []
    for fn in (_revisar_env, _revisar_keys, _revisar_cobertura, _revisar_dependencias):
        try:
            salida.extend(fn())
        except Exception as exc:  # noqa: BLE001
            salida.append(_chequeo(fn.__name__, ESTADO_FALLO, f"el chequeo fallo: {exc}"))
    for fn2 in (_revisar_rag, _revisar_skills):
        try:
            salida.extend(fn2(repo))
        except Exception as exc:  # noqa: BLE001
            salida.append(_chequeo(fn2.__name__, ESTADO_FALLO, f"el chequeo fallo: {exc}"))
    return salida
