"""Busqueda de imagenes en la web GENERAL (DuckDuckGo Images, sin API
key) - a diferencia de app/tools/image_search.py (Pexels/Unsplash/
Pixabay, bancos de stock con licencia de uso comercial explicita,
pensados para fotos GENERICAS de un negocio: interior, platos, etc.),
esta tool busca en TODO internet, sin ninguna garantia de licencia.

ALCANCE ESTRICTO: solo para sujetos ESPECIFICOS E IDENTIFICABLES donde
un banco de stock nunca va a tener el resultado exacto - una persona
famosa puntual, el poster de una pelicula puntual, la portada de un
libro/videojuego puntual, el logo oficial de una marca puntual - algo
que practicamente seguro aparece como primer resultado en una busqueda
de imagenes real. NUNCA para fotos genericas de negocio (interior,
comida, "gente sonriendo en una oficina") ni para el sitio PUBLICO de
un cliente real: eso reproduce exactamente el riesgo legal que ya esta
documentado en hard_cases/by_domain/web.json:
web-scraped-images-copyright-risk (un poster/famoso esta MAS protegido
por derechos de autor que una foto de stock generica, no menos - un
estudio de cine o una agencia de fotografia litigan activamente). Esta
tool es para contenido propio/informal del usuario (documentos,
presentaciones, sitios personales o de broma), nunca para negocios
reales.

Tecnica: el mismo endpoint no oficial que usa la UI de DuckDuckGo
Images (sin key, sin contrato de API estable - puede romperse si
DuckDuckGo cambia su frontend, en cuyo caso devuelve error explicito en
vez de fallar con una excepcion generica).

OPTIMIZACION DE TURNOS: mismo patron que app/tools/image_search.py -
cada candidata ya viene con el analisis TECNICO (nitidez/brillo/
resolucion, clave 'analisis') Y una VERIFICACION de que la imagen
realmente muestra el sujeto buscado (clave 'verificacion', modelo de
vision local) fusionados en el propio resultado - no hace falta
analyze_image ni una verificacion aparte por candidata. Esto ultimo es
critico especificamente aca: a diferencia de un banco de stock
curado, una busqueda en TODA la web puede devolver el poster de OTRA
pelicula, un fan-art, o una imagen no relacionada con un titulo que
"suena bien" - el titulo/descripcion del resultado de busqueda no lo
garantiza (ver hard_cases: web-search-description-text-mismatches-
real-photo-content, mismo principio aplicado aca).

`queries` (lista) permite pedir VARIOS sujetos distintos en una sola
llamada, igual que `search_images` - cada uno sigue siendo una
busqueda real independiente por dentro, pero asi es un turno en vez de
uno por sujeto.
"""
from __future__ import annotations

import re

import httpx

from app.tools.image_analysis import analyze_image as _analyze_image
from app.tools.image_vision import verify_image_subject as _verify_image_subject

_USER_AGENT = "Mozilla/5.0 (compatible; OrquestadorWebBuilder/1.0; +local-testing)"
_TIMEOUT_S = 15.0
_VQD_RE = re.compile(r"vqd=['\"]?([\d-]+)['\"]?")


def _obtener_vqd(query: str) -> str | None:
    # DuckDuckGo Images requiere un token de sesion ('vqd') sacado de la
    # pagina de resultados normal antes de poder pedir el JSON de
    # imagenes - no es parte de un contrato de API publico, es como
    # funciona su propio frontend.
    response = httpx.get(
        "https://duckduckgo.com/",
        params={"q": query},
        headers={"User-Agent": _USER_AGENT},
        timeout=_TIMEOUT_S,
    )
    response.raise_for_status()
    match = _VQD_RE.search(response.text)
    return match.group(1) if match else None


def _con_analisis(resultados: list[dict], sujeto: str) -> list[dict]:
    for r in resultados:
        try:
            r["analisis"] = _analyze_image(r["url"])
        except Exception as exc:  # noqa: BLE001 - una candidata que falla no debe tirar toda la busqueda
            r["analisis"] = {"error": f"No se pudo analizar esta candidata: {exc}"}
        try:
            r["verificacion"] = _verify_image_subject(r["url"], sujeto)
        except Exception as exc:  # noqa: BLE001 - idem, no tirar toda la busqueda por una candidata
            r["verificacion"] = {"ok": False, "error": f"No se pudo verificar el sujeto: {exc}"}
    return resultados


def _search_una(query: str, count: int) -> dict:
    try:
        vqd = _obtener_vqd(query)
        if not vqd:
            return {
                "results": [],
                "error": "No se pudo obtener el token de busqueda de DuckDuckGo (vqd) - probar de nuevo o reformular la consulta.",
            }
        response = httpx.get(
            "https://duckduckgo.com/i.js",
            params={"l": "us-en", "o": "json", "q": query, "vqd": vqd, "f": ",,,", "p": "1"},
            headers={"User-Agent": _USER_AGENT, "Referer": "https://duckduckgo.com/"},
            timeout=_TIMEOUT_S,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:  # noqa: BLE001 - un fallo aca no debe tirar toda la tarea, se reporta como error
        return {"results": [], "error": f"No se pudo buscar la imagen: {exc}"}

    results = [
        {
            "url": item.get("image"),
            "source_page": item.get("url"),
            "title": item.get("title"),
            "width": item.get("width"),
            "height": item.get("height"),
        }
        for item in data.get("results", [])[:count]
        if item.get("image")
    ]
    return {"results": _con_analisis(results, query), "error": None}


def search_web_image(query: str | None = None, queries: list[str] | None = None, count: int = 3) -> dict:
    """Busca imagenes en TODA la web - ver el docstring del modulo para
    el alcance permitido (sujetos especificos e identificables, nunca
    fotos genericas de negocio) y para el detalle de que trae fusionado
    cada resultado ('analisis' + 'verificacion').

    query: un solo sujeto (devuelve {results, error}). queries: VARIOS
    sujetos distintos (ej. ["poster pelicula Inception 2010", "foto
    Tom Hanks actor"]) en UNA sola llamada - devuelve
    {sujeto: {results, error}}, una entrada por elemento de `queries`.
    Si se pasan ambos, `queries` gana."""
    if queries:
        return {q: _search_una(q, count) for q in queries}
    if not query:
        return {"results": [], "error": "Falta 'query' o 'queries'."}
    return _search_una(query, count)
