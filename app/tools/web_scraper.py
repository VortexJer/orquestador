"""Scraper general de paginas web para el especialista web-builder:
traer texto/links de una URL, y extraer datos estructurados de negocio
(horarios, telefono, direccion, valoraciones) cuando estan disponibles
como schema.org/JSON-LD - que es como la mayoria de sitios de negocios
exponen esa info para SEO, sin necesitar ninguna API de pago.

Nota de alcance: esto NO es un scraper generico "de lo que sea" -
respeta timeouts razonables, no reintenta agresivamente, y para datos
de negocio prioriza SIEMPRE la fuente estructurada (mas confiable) sobre
heuristicas de texto libre (mejor que nada, pero no confiable al 100%).
"""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

_PHONE_PATTERN = re.compile(r"(\+?\d[\d\s\-\(\)]{7,}\d)")
_HOURS_KEYWORDS = re.compile(
    r"\b(lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|horario)\b", re.I,
)


def fetch_page(url: str) -> dict:
    """Trae una pagina y devuelve texto limpio + links + titulo. No
    ejecuta JavaScript (es un fetch simple, no un navegador) - para
    sitios que dependen 100% de JS para renderizar contenido, usar
    render_check en su lugar.

    Pasa por web_safety.fetch_seguro: filtro SSRF (nada de localhost/IPs
    internas/metadatos de nube), tope de bytes y solo texto/HTML. Antes
    este fetch era 'a pelo' con follow_redirects=True - un vector de SSRF."""
    from app.tools.web_safety import decodificar, fetch_seguro

    res = fetch_seguro(url)
    if not res.get("ok"):
        return {"url": url, "status_code": res.get("status"), "title": "",
                "text": "", "links": [], "error": res.get("error")}

    soup = BeautifulSoup(decodificar(res["content"]), "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    base = res.get("url_final") or url
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    text = re.sub(r"\n{3,}", "\n\n", soup.get_text(separator="\n")).strip()

    links = sorted({
        urljoin(base, a["href"])
        for a in soup.find_all("a", href=True)
        if not a["href"].startswith(("javascript:", "mailto:", "tel:", "#"))
    })

    return {
        "url": base,
        "status_code": res.get("status"),
        "title": title,
        "text": text[:8000],
        "links": links[:100],
        "error": None,
    }


def extract_structured_data(url: str) -> dict | None:
    """Busca bloques <script type="application/ld+json"> con datos de
    tipo LocalBusiness/Restaurant/Organization (schema.org) - la fuente
    mas confiable de horarios/telefono/rating de un negocio, porque el
    propio sitio la publica para que Google la lea."""
    from app.tools.web_safety import decodificar, fetch_seguro

    res = fetch_seguro(url)
    if not res.get("ok"):
        return None
    soup = BeautifulSoup(decodificar(res["content"]), "html.parser")
    business_types = {"LocalBusiness", "Restaurant", "Organization", "Store", "FoodEstablishment"}

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue

        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            item_type = item.get("@type") if isinstance(item, dict) else None
            types = item_type if isinstance(item_type, list) else [item_type]
            if not any(t in business_types for t in types):
                continue

            return {
                "source": "structured_data",
                "name": item.get("name"),
                "telephone": item.get("telephone"),
                "address": item.get("address"),
                "opening_hours": item.get("openingHours") or item.get("openingHoursSpecification"),
                "aggregate_rating": item.get("aggregateRating"),
                "price_range": item.get("priceRange"),
            }
    return None


def extract_business_info(url: str) -> dict:
    """Intenta structured data primero (confiable); si no hay, cae a un
    escaneo heuristico de texto plano (telefono por regex, lineas cerca
    de palabras de dias/horario) - MENOS confiable, se marca
    explicitamente como tal para que el especialista no lo trate igual
    que un dato estructurado."""
    structured = extract_structured_data(url)
    if structured:
        return structured

    page = fetch_page(url)
    text = page["text"]

    phones = _PHONE_PATTERN.findall(text)
    hour_lines = [line.strip() for line in text.splitlines() if _HOURS_KEYWORDS.search(line)]

    return {
        "source": "heuristic_text_scan",
        "note": "No se encontro schema.org/JSON-LD en la pagina - esto es una extraccion heuristica, verificar con el cliente antes de publicar.",
        "possible_phones": phones[:3],
        "possible_hours_lines": hour_lines[:10],
    }


def is_valid_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)
