"""Busqueda web general, robusta y sin API key por defecto.

FRAGILIDAD = un solo motor. Aqui hay una CASCADA de buscadores
INDEPENDIENTES; el primero que devuelve resultados gana, asi que si uno
esta throttleando la IP, otro responde:

  1. DuckDuckGo Lite  (UTF-8 limpio, HTML estable)
  2. DuckDuckGo HTML  (fallback; arregla el mojibake latin-1 del endpoint)
  3. SearXNG          (metabuscador: agrega Google/Bing/etc.; JSON;
                       configurable con SEARXNG_URL + lista publica)
  4. Brave API        (si hay BRAVE_API_KEY; la opcion mas fiable)

Ademas: User-Agent rotado (menos bloqueos por fingerprint), dos pasadas
con espera y un error EXPLICITO si TODOS fallan (para que el modelo diga
"no lo encontre" en vez de inventar). `engine="duckduckgo"` (default)
recorre la cascada; `engine="brave"` fuerza Brave.

Dos bugs del extractor viejo, corregidos: snippets con palabras pegadas
(`get_text(strip=True)` -> `get_text(" ", strip=True)`) y mojibake (el
endpoint HTML declara UTF-8 y sirve latin-1 -> `_decodificar`).
"""
from __future__ import annotations

import os
import random
import re
import time
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

# Varios User-Agents de navegador real: rotarlos reduce el bloqueo por
# fingerprint (siempre el mismo UA es facil de marcar).
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
]
_TIMEOUT_S = 15.0
_CARACTER_REEMPLAZO = "�"


def _headers() -> dict:
    return {"User-Agent": random.choice(_USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/json"}


def _decodificar(contenido: bytes) -> str:
    """El endpoint HTML de DDG declara UTF-8 pero a veces sirve latin-1.
    Se prueba UTF-8 y, si aparece un caracter de reemplazo, latin-1."""
    try:
        texto = contenido.decode("utf-8")
        if _CARACTER_REEMPLAZO not in texto:
            return texto
    except UnicodeDecodeError:
        pass
    return contenido.decode("latin-1")


def _unwrap_duckduckgo_redirect(href: str) -> str:
    if not href:
        return href
    parsed = urlparse(href if "://" in href else f"https:{href}")
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path == "/l/":
        target = parse_qs(parsed.query).get("uddg")
        if target:
            return target[0]
    return href


def _dedup(results: list[dict], count: int) -> list[dict]:
    vistos: set[str] = set()
    unicos: list[dict] = []
    for r in results:
        clave = (r.get("url") or "").rstrip("/")
        if not clave or clave in vistos:
            continue
        vistos.add(clave)
        unicos.append(r)
        if len(unicos) >= count:
            break
    return unicos


# --- Motores -----------------------------------------------------------

def _search_duckduckgo_lite(query: str, count: int) -> dict:
    response = httpx.post(
        "https://lite.duckduckgo.com/lite/",
        data={"q": query}, headers=_headers(), timeout=_TIMEOUT_S, follow_redirects=True,
    )
    response.raise_for_status()
    soup = BeautifulSoup(_decodificar(response.content), "html.parser")
    enlaces = soup.select("a.result-link")
    snippets = soup.select(".result-snippet")
    results = []
    for i, a in enumerate(enlaces):
        results.append({
            "title": a.get_text(" ", strip=True),
            "url": _unwrap_duckduckgo_redirect(a.get("href", "")),
            "snippet": snippets[i].get_text(" ", strip=True) if i < len(snippets) else "",
        })
    return {"engine": "duckduckgo-lite", "results": _dedup(results, count), "error": None}


def _search_duckduckgo_html(query: str, count: int) -> dict:
    response = httpx.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query}, headers=_headers(), timeout=_TIMEOUT_S, follow_redirects=True,
    )
    response.raise_for_status()
    soup = BeautifulSoup(_decodificar(response.content), "html.parser")
    results = []
    for result in soup.select(".result"):
        title_tag = result.select_one(".result__title a") or result.select_one("a.result__a")
        snippet_tag = result.select_one(".result__snippet")
        if not title_tag:
            continue
        results.append({
            "title": title_tag.get_text(" ", strip=True),
            "url": _unwrap_duckduckgo_redirect(title_tag.get("href", "")),
            "snippet": snippet_tag.get_text(" ", strip=True) if snippet_tag else "",
        })
    return {"engine": "duckduckgo-html", "results": _dedup(results, count), "error": None}


# Instancias publicas de SearXNG que suelen permitir salida JSON. Se puede
# anteponer una propia/de confianza con SEARXNG_URL (self-hosted = la mas
# fiable: sin throttle compartido).
_SEARXNG_PUBLICAS = [
    "https://searx.be",
    "https://baresearch.org",
    "https://search.inetol.net",
    "https://opnxng.com",
    "https://priv.au",
    "https://search.rhscz.eu",
]


def _searxng_instancias() -> list[str]:
    urls = []
    propia = os.environ.get("SEARXNG_URL")
    if propia:
        urls.append(propia.rstrip("/"))
    urls += _SEARXNG_PUBLICAS
    return urls


def _searxng_parse(data: dict, count: int) -> list[dict]:
    """Transforma la respuesta JSON de SearXNG en nuestro formato. Aparte
    para poder testearla sin red."""
    results = [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
        for r in (data.get("results") or []) if r.get("url")
    ]
    return _dedup(results, count)


def _search_searxng(query: str, count: int) -> dict:
    """Metabuscador: agrega Google/Bing/etc. Prueba varias instancias hasta
    que una devuelve JSON con resultados (muchas desactivan el JSON o
    throttlean, por eso se recorre una lista)."""
    ultimo = None
    for base in _searxng_instancias():
        try:
            r = httpx.get(
                base + "/search",
                params={"q": query, "format": "json", "language": "es-ES"},
                headers=_headers(), timeout=12.0, follow_redirects=True,
            )
            if "json" not in r.headers.get("content-type", "").lower():
                ultimo = f"{urlparse(base).netloc}: sin JSON"
                continue
            results = _searxng_parse(r.json(), count)
            if results:
                return {"engine": f"searxng:{urlparse(base).netloc}", "results": results, "error": None}
            ultimo = f"{urlparse(base).netloc}: 0"
        except Exception as exc:  # noqa: BLE001 - se prueba la siguiente instancia
            ultimo = f"{urlparse(base).netloc}: {type(exc).__name__}"
    return {"engine": "searxng", "results": [], "error": ultimo or "ninguna instancia respondio"}


def _search_brave(query: str, count: int) -> dict:
    api_key = os.environ.get("BRAVE_API_KEY")
    if not api_key:
        return {"engine": "brave", "results": [], "error": (
            "Falta BRAVE_API_KEY (gratis, 2000/mes, en https://brave.com/search/api/) - "
            "la opcion mas fiable si DuckDuckGo te throttlea."
        )}
    response = httpx.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        params={"q": query, "count": count}, timeout=_TIMEOUT_S,
    )
    response.raise_for_status()
    data = response.json()
    results = [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("description", "")}
        for r in data.get("web", {}).get("results", [])[:count]
    ]
    return {"engine": "brave", "results": _dedup(results, count), "error": None}


# Orden de la cascada: DDG (rapido, va bien en IP residencial) -> SearXNG
# (cuando DDG bloquea) -> Brave (si hay key). El primero con resultados gana.
_CASCADA = [_search_duckduckgo_lite, _search_duckduckgo_html, _search_searxng, _search_brave]


def _buscar_en_cascada(query: str, count: int, pasadas: int = 2) -> dict:
    ultimo_error = None
    for intento in range(pasadas):
        for motor in _CASCADA:
            try:
                out = motor(query, count)
                if out.get("results"):
                    return out
                ultimo_error = f"{out.get('engine')}: {out.get('error') or '0 resultados'}"
            except Exception as exc:  # noqa: BLE001 - se prueba el siguiente motor
                ultimo_error = f"{motor.__name__}: {type(exc).__name__}: {exc}"
        if intento < pasadas - 1:
            time.sleep(2.0 + random.random() * 1.5)  # espera con jitter entre pasadas
    return {"engine": "web", "results": [], "error": (
        f"Ningun buscador respondio ({ultimo_error}). Puede ser un bloqueo temporal: "
        "NO inventes la respuesta, dilo o reintenta. (Para busqueda fiable: BRAVE_API_KEY "
        "o SEARXNG_URL propia en el .env.)"
    )}


# --- Fecha/hora actual: fuente ONLINE fiable -------------------------------
# "que dia es hoy" en un buscador normal cae en paginas viejas y devolvia
# años equivocados (2024). Cuando la pregunta es por la fecha/hora de AHORA,
# se consulta una API de hora en internet (no el reloj del proceso, que en un
# server podria estar en otra zona) y se devuelve como resultado de busqueda.
_DIAS_ES = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")
_MESES_ES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)
# Frase clasica ("que dia es", "que hora es") o nombre-de-tiempo + marcador de
# AHORA. Exige el marcador para no dispararse con "que dia se estreno Matrix".
_RE_FRASE_FECHA = re.compile(
    r"qu[eé]\s+d[ií]a\s+es|qu[eé]\s+hora\s+es|what\s+time\s+is\s+it|what('?s| is)\s+the\s+date",
    re.IGNORECASE,
)
_RE_NOMBRE_TIEMPO = re.compile(r"\b(d[ií]a|fecha|hora|day|date|time)\b", re.IGNORECASE)
_RE_AHORA = re.compile(
    r"\b(hoy|today|ahora|actual(es|mente)?|right\s+now|current(ly)?|estamos)\b",
    re.IGNORECASE,
)


def _es_consulta_de_fecha(query: str) -> bool:
    q = query or ""
    if _RE_FRASE_FECHA.search(q):
        return True
    return bool(_RE_NOMBRE_TIEMPO.search(q) and _RE_AHORA.search(q))


def _formatear_es(dt: datetime, zona: str) -> str:
    return (
        f"{_DIAS_ES[dt.weekday()]} {dt.day} de {_MESES_ES[dt.month - 1]} de "
        f"{dt.year}, {dt.hour:02d}:{dt.minute:02d} ({zona})"
    )


def _hora_actual_online(zona: str = "Europe/Madrid") -> dict | None:
    """Fecha y hora reales desde una API de tiempo en internet. Devuelve un
    resultado de busqueda con el dato ya en castellano, o None si nada responde
    (entonces search_web cae a la busqueda normal)."""
    etiqueta = "hora de España" if zona == "Europe/Madrid" else zona
    # 1) timeapi.io: JSON limpio (year/month/day/hour/minute).
    try:
        r = httpx.get(
            "https://timeapi.io/api/Time/current/zone",
            params={"timeZone": zona}, headers=_headers(), timeout=8.0,
        )
        if r.status_code == 200:
            d = r.json()
            dt = datetime(d["year"], d["month"], d["day"], d["hour"], d["minute"])
            fecha = _formatear_es(dt, etiqueta)
            return {"engine": "reloj-online", "error": None, "results": [{
                "title": "Fecha y hora actual",
                "url": "https://timeapi.io",
                "snippet": f"Ahora mismo es {fecha}.",
            }]}
    except Exception:  # noqa: BLE001 - se prueba la siguiente fuente
        pass
    # 2) worldtimeapi.org: campo datetime ISO con la hora local ya aplicada.
    try:
        r = httpx.get(
            f"https://worldtimeapi.org/api/timezone/{zona}",
            headers=_headers(), timeout=8.0,
        )
        if r.status_code == 200:
            iso = r.json().get("datetime", "")[:16]  # "2026-07-30T16:45"
            dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M")
            fecha = _formatear_es(dt, etiqueta)
            return {"engine": "reloj-online", "error": None, "results": [{
                "title": "Fecha y hora actual",
                "url": "https://worldtimeapi.org",
                "snippet": f"Ahora mismo es {fecha}.",
            }]}
    except Exception:  # noqa: BLE001
        pass
    return None


def search_web(query: str, count: int = 5, engine: str = "duckduckgo") -> dict:
    """Busca en la web y devuelve {engine, results:[{title,url,snippet}], error}.

    engine: 'duckduckgo' (default; cascada DDG->SearXNG->Brave con fallback
    automatico) o 'brave' (fuerza Brave, requiere BRAVE_API_KEY)."""
    if not query or not query.strip():
        return {"engine": engine, "results": [], "error": "Consulta vacia."}
    # Pregunta por la fecha/hora de AHORA -> fuente de hora online fiable, no
    # una busqueda que cae en paginas viejas. Si la fuente no responde, sigue
    # la busqueda normal de abajo.
    if _es_consulta_de_fecha(query):
        reloj = _hora_actual_online()
        if reloj:
            return reloj
    if engine == "brave":
        return _search_brave(query, count)
    if engine == "searxng":
        return _search_searxng(query, count)
    if engine in ("duckduckgo", "auto", ""):
        return _buscar_en_cascada(query, count)
    return {"engine": engine, "results": [], "error": f"Motor desconocido '{engine}'."}
