"""Busqueda de imagenes ROYALTY-FREE (uso comercial permitido) para el
especialista web-builder: fondos, fotos de platos, etc. A proposito NO
hay una funcion "buscar en Google Images y traer la primera" - ver
hard_cases/by_domain/web.json:web-scraped-images-copyright-risk sobre
por que eso es un riesgo legal real para un sitio de un cliente.

Cada fuente requiere su propia API key (gratis, con signup) via
variable de entorno. Sin la key configurada, devuelve un error claro en
vez de fallar con una excepcion generica - el especialista puede
reportarle esto al usuario y sugerir otra fuente.

Fuentes soportadas y donde sacar una key gratis:
  - Pexels:  PEXELS_API_KEY   (https://www.pexels.com/api/)
  - Unsplash: UNSPLASH_ACCESS_KEY (https://unsplash.com/developers)
  - Pixabay: PIXABAY_API_KEY  (https://pixabay.com/api/docs/)

OPTIMIZACION DE TURNOS: cada candidata ya viene con el analisis tecnico
de analyze_image (nitidez/brillo/resolucion) FUSIONADO en el propio
resultado, bajo la clave 'analisis' - antes hacia falta primero
search_images y despues UNA llamada a analyze_image POR CADA candidata
que se queria evaluar, y cada llamada a herramienta es un turno
completo que reenvia toda la conversacion de nuevo (ver
skills/web-builder-specialist.md, "Turno 0"). analyze_image es un
calculo local con Pillow (sin IA, sin modelo de vision) - no hay motivo
para pedirlo aparte cuando de todos modos hay que bajar la imagen para
mostrar sus datos.

Si se pasa `rubro_negocio`, TAMBIEN se fusiona el analisis de CONTENIDO
real (clasify_image_content, modelo de vision local) bajo la clave
'contenido' - esto es lo que evita usar por error la foto de OTRO
negocio (ver hard_cases/by_domain/web.json:
web-search-description-text-mismatches-real-photo-content: una foto
con la description "restaurante tradicional Madrid" resulto ser la
fachada de un restaurante distinto, con su propio cartel visible, y
paso sin que nadie lo notara porque nunca se verifico el contenido
real). A diferencia del analisis tecnico, ESTE es mas lento (llama a un
modelo de vision de verdad) y depende de que Ollama este corriendo - si
falla o no esta disponible, esa candidata queda con 'contenido':
{'ok': False, ...} en vez de tirar toda la busqueda, y quien llama tiene
que decidir que hacer (mismo criterio que si hubiera llamado
classify_image_content aparte)."""
from __future__ import annotations

import os

import httpx

from app.tools.image_analysis import analyze_image as _analyze_image
from app.tools.image_vision import classify_image as _classify_image_content

_TIMEOUT_S = 15.0


def _con_analisis(resultados: list[dict], rubro_negocio: str | None) -> list[dict]:
    for r in resultados:
        try:
            r["analisis"] = _analyze_image(r["url"])
        except Exception as exc:  # noqa: BLE001 - una candidata que falla no debe tirar toda la busqueda
            r["analisis"] = {"error": f"No se pudo analizar esta candidata: {exc}"}
        if rubro_negocio:
            try:
                r["contenido"] = _classify_image_content(r["url"], rubro_negocio)
            except Exception as exc:  # noqa: BLE001 - idem, no tirar toda la busqueda por una candidata
                r["contenido"] = {"ok": False, "error": f"No se pudo clasificar el contenido: {exc}"}
    return resultados


def _missing_key_result(source: str, env_var: str, signup_url: str) -> dict:
    return {
        "source": source,
        "results": [],
        "error": (
            f"Falta {env_var} en el entorno. Conseguir una key gratis en {signup_url} "
            f"y configurarla como variable de entorno antes de usar esta fuente."
        ),
    }


def _search_pexels(query: str, count: int, rubro_negocio: str | None) -> dict:
    api_key = os.environ.get("PEXELS_API_KEY")
    if not api_key:
        return _missing_key_result("pexels", "PEXELS_API_KEY", "https://www.pexels.com/api/")

    response = httpx.get(
        "https://api.pexels.com/v1/search",
        headers={"Authorization": api_key},
        params={"query": query, "per_page": count},
        timeout=_TIMEOUT_S,
    )
    response.raise_for_status()
    data = response.json()
    results = [
        {
            "url": photo["src"]["large"],
            "page_url": photo["url"],
            "photographer": photo["photographer"],
            "description": photo.get("alt") or "(sin descripcion)",
            "attribution_required": False,  # licencia Pexels no lo exige, pero es buena practica citarlo
        }
        for photo in data.get("photos", [])
    ]
    return {"source": "pexels", "results": _con_analisis(results, rubro_negocio), "error": None}


def _search_unsplash(query: str, count: int, rubro_negocio: str | None) -> dict:
    access_key = os.environ.get("UNSPLASH_ACCESS_KEY")
    if not access_key:
        return _missing_key_result("unsplash", "UNSPLASH_ACCESS_KEY", "https://unsplash.com/developers")

    response = httpx.get(
        "https://api.unsplash.com/search/photos",
        headers={"Authorization": f"Client-ID {access_key}"},
        params={"query": query, "per_page": count},
        timeout=_TIMEOUT_S,
    )
    response.raise_for_status()
    data = response.json()
    results = [
        {
            "url": photo["urls"]["regular"],
            "page_url": photo["links"]["html"],
            "photographer": photo["user"]["name"],
            "description": photo.get("alt_description") or photo.get("description") or "(sin descripcion)",
            "attribution_required": True,  # Unsplash pide atribucion como buena practica de su licencia
        }
        for photo in data.get("results", [])
    ]
    return {"source": "unsplash", "results": _con_analisis(results, rubro_negocio), "error": None}


def _search_pixabay(query: str, count: int, rubro_negocio: str | None) -> dict:
    api_key = os.environ.get("PIXABAY_API_KEY")
    if not api_key:
        return _missing_key_result("pixabay", "PIXABAY_API_KEY", "https://pixabay.com/api/docs/")

    response = httpx.get(
        "https://pixabay.com/api/",
        params={"key": api_key, "q": query, "per_page": max(count, 3), "safesearch": "true"},
        timeout=_TIMEOUT_S,
    )
    response.raise_for_status()
    data = response.json()
    results = [
        {
            "url": hit["largeImageURL"],
            "page_url": hit["pageURL"],
            "photographer": hit["user"],
            "description": hit.get("tags") or "(sin descripcion)",
            "attribution_required": False,
        }
        for hit in data.get("hits", [])[:count]
    ]
    return {"source": "pixabay", "results": _con_analisis(results, rubro_negocio), "error": None}


_SOURCES = {"pexels": _search_pexels, "unsplash": _search_unsplash, "pixabay": _search_pixabay}


def _search_una(query: str, source: str, count: int, rubro_negocio: str | None) -> dict:
    handler = _SOURCES.get(source)
    if handler is None:
        return {"source": source, "results": [], "error": f"Fuente desconocida '{source}'. Usar: {list(_SOURCES)}."}
    return handler(query, count, rubro_negocio)


def search_images(
    query: str | None = None,
    queries: list[str] | None = None,
    source: str = "pexels",
    count: int = 5,
    rubro_negocio: str | None = None,
) -> dict:
    """query: un solo tema (comportamiento de siempre, devuelve
    {source, results, error}). queries: VARIOS temas distintos (ej.
    ["interior acogedor", "cocido madrileño plato", "barra de bar
    madera"]) en UNA sola llamada - cada tema es una busqueda real
    independiente (no se pueden fusionar en una peticion a la API del
    banco de imagenes, son consultas distintas), pero asi el modelo
    pide las 5 fotos de una vez en vez de 5 turnos separados. Devuelve
    un dict {query: {source, results, error}} - una entrada por tema,
    en el mismo formato de siempre por tema. Si se pasan ambos, `queries`
    gana."""
    if queries:
        return {q: _search_una(q, source, count, rubro_negocio) for q in queries}
    if not query:
        return {"source": source, "results": [], "error": "Falta 'query' o 'queries'."}
    return _search_una(query, source, count, rubro_negocio)
