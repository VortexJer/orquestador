"""Dos "bases de datos gratis" enormes para el que construye webs/apps:

1. find_api  -> DIRECTORIO de APIs publicas (APIs.guru: miles de APIs con su
   OpenAPI, categoria y docs). Para cuando el usuario pide "una web que
   muestre el tiempo / citas / criptos" y hay que cablear una API real en
   vez de inventar datos. Gratis, sin clave.

2. datos_de_ejemplo -> DATOS FALSOS realistas (DummyJSON: productos, usuarios,
   posts, carritos, recetas...). Para prototipar una web con contenido creible
   sin inventar un JSON a mano ni exponer datos reales. Gratis, sin clave.
"""
from __future__ import annotations

from app.tools.api_client import get_json

# list.json de APIs.guru es grande (~5 MB): se baja UNA vez por proceso y se
# cachea, asi varias busquedas en la misma sesion no repiten la descarga.
_CACHE_DIRECTORIO: dict | None = None
_RECURSOS_EJEMPLO = (
    "products", "carts", "users", "posts", "comments", "todos", "quotes", "recipes",
)


def _cargar_directorio() -> dict:
    global _CACHE_DIRECTORIO
    if _CACHE_DIRECTORIO is None:
        r = get_json("https://api.apis.guru/v2/list.json", max_bytes=15_000_000)
        _CACHE_DIRECTORIO = r["data"] if r["ok"] and isinstance(r["data"], dict) else {}
    return _CACHE_DIRECTORIO


def _info_preferida(entrada: dict) -> dict:
    versiones = entrada.get("versions") or {}
    pref = entrada.get("preferred")
    ver = versiones.get(pref) or (next(iter(versiones.values())) if versiones else {})
    return ver.get("info", {}) if isinstance(ver, dict) else {}


def buscar_api(consulta: str, limite: int = 6) -> dict:
    """Busca APIs publicas por palabra clave en el directorio de APIs.guru.

    - consulta: tema/categoria (ej. 'weather', 'crypto', 'currency', 'sports').
    - limite: cuantas devolver (1-15).

    Devuelve {consulta, resultados:[{nombre, descripcion, categorias, docs,
    openapi}], total, error}."""
    if not consulta or not consulta.strip():
        return {"consulta": consulta, "resultados": [], "total": 0, "error": "Consulta vacia."}
    limite = max(1, min(int(limite or 6), 15))
    directorio = _cargar_directorio()
    if not directorio:
        return {"consulta": consulta, "resultados": [], "total": 0,
                "error": "No se pudo cargar el directorio de APIs.guru (reintenta)."}

    termino = consulta.strip().lower()
    encontrados = []
    for nombre, entrada in directorio.items():
        info = _info_preferida(entrada)
        titulo = info.get("title", "")
        descripcion = info.get("description", "") or ""
        categorias = info.get("x-apisguru-categories", []) or []
        heno = f"{nombre} {titulo} {descripcion} {' '.join(categorias)}".lower()
        if termino in heno:
            docs = ""
            ext = info.get("externalDocs") or {}
            if isinstance(ext, dict):
                docs = ext.get("url", "")
            versiones = entrada.get("versions") or {}
            pref = versiones.get(entrada.get("preferred")) or {}
            openapi = pref.get("swaggerUrl") or pref.get("openapiUrl") or "" if isinstance(pref, dict) else ""
            encontrados.append({
                "nombre": titulo or nombre,
                "descripcion": " ".join(descripcion.split())[:280],
                "categorias": categorias,
                "docs": docs or "https://apis.guru/",
                "openapi": openapi,
            })

    encontrados.sort(key=lambda e: len(e["descripcion"]) or 0, reverse=True)
    return {
        "consulta": consulta, "resultados": encontrados[:limite],
        "total": len(encontrados),
        "error": None if encontrados else "Sin coincidencias. Prueba otra palabra (en ingles suele haber mas).",
    }


def datos_de_ejemplo(tipo: str = "products", cantidad: int = 5) -> dict:
    """Trae datos FALSOS realistas para prototipar (DummyJSON).

    - tipo: products, carts, users, posts, comments, todos, quotes, recipes.
    - cantidad: cuantos registros (1-20).

    Devuelve {tipo, items:[...], error}. Ideal para rellenar una web de
    demo con contenido creible sin inventarlo ni usar datos reales."""
    t = (tipo or "products").strip().lower()
    if t not in _RECURSOS_EJEMPLO:
        return {"tipo": tipo, "items": [], "error": (
            f"tipo '{tipo}' no disponible. Usa uno de: {', '.join(_RECURSOS_EJEMPLO)}.")}
    cantidad = max(1, min(int(cantidad or 5), 20))
    r = get_json(f"https://dummyjson.com/{t}", params={"limit": cantidad})
    if not r["ok"]:
        return {"tipo": t, "items": [], "error": f"no se pudo traer datos de ejemplo ({r['error']})."}
    d = r["data"] or {}
    # DummyJSON devuelve {<tipo>: [...], total, skip, limit}; la lista esta
    # bajo la clave del tipo (o 'items'/la primera lista que haya).
    items = d.get(t) or d.get("items")
    if items is None:
        items = next((v for v in d.values() if isinstance(v, list)), [])
    return {"tipo": t, "items": items, "total": d.get("total"), "error": None}
