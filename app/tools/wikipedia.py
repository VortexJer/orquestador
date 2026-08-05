"""Consulta a Wikipedia: para documentos/presentaciones/webs SOBRE un tema
real del mundo (un coche, una empresa, una persona, un lugar, un hecho).

El modelo escribe de memoria y se INVENTA cifras, fechas y detalles con total
seguridad; aqui saca el texto real de la enciclopedia. Es la fuente ideal para
un "word sobre X": una sola llamada trae el articulo entero en texto plano.

Usa la action API de MediaWiki (sin key): busca el articulo mas relevante y
devuelve su extracto en texto plano, el titulo y la URL para citar. Solo es/en
(ver la allowlist de app.tools.api_client). El host lo fija ESTE codigo por el
idioma; el modelo solo aporta el termino de busqueda.
"""
from __future__ import annotations

from app.tools import api_client

_IDIOMAS = ("es", "en")
# Tope de texto devuelto: un articulo largo (un coche popular) son decenas de
# miles de caracteres; con esto el modelo tiene material de sobra para un
# documento sin volcar el articulo entero al contexto. dispatch lo recorta
# ademas por TOPES; esto es el recorte "con sentido" (por el articulo).
_MAX_CHARS = 12_000


def consultar_wikipedia(consulta: str, idioma: str = "es", limite_chars: int = _MAX_CHARS) -> dict:
    """Busca en Wikipedia el articulo mas relevante para `consulta` y devuelve
    su texto real. `idioma`: 'es' (por defecto) o 'en'. Devuelve
    {ok, titulo, url, idioma, extracto, error}."""
    if not consulta or not consulta.strip():
        return {"ok": False, "titulo": None, "url": None, "idioma": idioma,
                "extracto": None, "error": "Consulta vacia."}
    lang = idioma.lower().strip() if idioma else "es"
    if lang not in _IDIOMAS:
        lang = "es"

    resultado = _buscar_en(consulta, lang, limite_chars)
    # Si en español no hay articulo, se prueba en inglés (mucho mas cobertura)
    # antes de rendirse - un modelo de coche puede no tener pagina en es.
    if not resultado["ok"] and lang == "es":
        alt = _buscar_en(consulta, "en", limite_chars)
        if alt["ok"]:
            return alt
    return resultado


def _buscar_en(consulta: str, lang: str, limite_chars: int) -> dict:
    url = f"https://{lang}.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",   # resuelve el termino al articulo mas relevante
        "gsrsearch": consulta,
        "gsrlimit": "1",
        "prop": "extracts|info",
        "explaintext": "1",       # texto plano, sin wikicode ni HTML
        "exlimit": "1",
        "inprop": "url",
        "redirects": "1",         # sigue redirecciones ("BMW Serie 1" -> pagina real)
    }
    resp = api_client.get_json(url, params=params)
    if not resp["ok"]:
        return _fallo(lang, resp.get("error") or "sin respuesta")

    paginas = (((resp.get("data") or {}).get("query") or {}).get("pages")) or {}
    if not paginas:
        return _fallo(lang, "sin resultados para esa busqueda")
    pagina = next(iter(paginas.values()))
    extracto = (pagina.get("extract") or "").strip()
    if not extracto:
        return _fallo(lang, "el articulo no tiene texto extraible")
    truncado = len(extracto) > limite_chars
    if truncado:
        extracto = extracto[:limite_chars].rstrip() + "\n\n[...articulo recortado...]"
    return {
        "ok": True,
        "titulo": pagina.get("title"),
        "url": pagina.get("fullurl") or f"https://{lang}.wikipedia.org/",
        "idioma": lang,
        "extracto": extracto,
        "error": None,
    }


def _fallo(lang: str, motivo: str) -> dict:
    return {"ok": False, "titulo": None, "url": None, "idioma": lang,
            "extracto": None, "error": motivo}
