"""Cliente HTTP para las APIs de REFERENCIA (registros de paquetes, Stack
Overflow, OSV, directorios de APIs, datos de ejemplo).

Por que no reusa fetch_seguro (web_safety):
  fetch_seguro filtra SSRF porque ahi el MODELO elige la URL - podria pedir
  http://localhost o la IP de metadatos. Aqui es al reves: el host lo fija
  ESTE codigo (una constante por API), y el modelo solo aporta el termino de
  busqueda o el nombre del paquete. Con eso, la defensa natural y mas
  estricta no es un filtro de IPs sino una ALLOWLIST de hosts: si un host no
  esta en la lista, no se llama, punto. El modelo no puede desviar la
  peticion a un sitio interno porque no controla el host.

Ademas, como toda capa de red del proyecto: solo https, timeout, tope de
bytes (respuestas gigantes / zip bombs) y User-Agent propio con contacto
(crates.io, por ejemplo, RECHAZA peticiones sin un UA identificable).
"""
from __future__ import annotations

from urllib.parse import urlparse

import httpx

# UA identificable con contacto: es un requisito de crates.io (devuelve 403
# sin el) y buena educacion con el resto de APIs publicas.
_USER_AGENT = "orquestador-modelos/1.0 (herramienta de referencia; +https://github.com/VortexJer)"
_TIMEOUT_S = 15.0
# 6 MB: los metadatos de un paquete son KB, pero algunos registros devuelven
# TODA la historia de versiones (PyPI/Packagist de un paquete muy veterano),
# y un JSON cortado a la mitad no parsea. El tope sigue protegiendo contra
# respuestas absurdas; solo se descarga lo que el servidor mande (streaming).
_MAX_BYTES = 6_000_000

# Hosts que ESTE proyecto sabe que va a llamar. Cada tool de referencia usa
# uno o varios de estos y nada mas. Ampliar la lista es una decision
# explicita y revisable, no algo que el modelo pueda forzar en runtime.
HOSTS_PERMITIDOS = frozenset({
    # Registros de paquetes (package_info)
    "pypi.org",
    "registry.npmjs.org",
    "crates.io",
    "proxy.golang.org",
    "packagist.org",
    "rubygems.org",
    "api.nuget.org",
    "azuresearch-usnc.nuget.org",
    "search.maven.org",
    "repo1.maven.org",       # CDN de artefactos (maven-metadata.xml): fiable, el solr no
    # Ejemplos de codigo / Q&A (code_examples)
    "api.stackexchange.com",
    # Codigo real de repos (code_search) + biblioteca UIverse (web_design)
    "api.github.com",
    "raw.githubusercontent.com",   # codigo crudo de elementos UIverse (galaxy repo)
    # Vulnerabilidades (vuln_check)
    "api.osv.dev",
    # Directorio de APIs publicas + datos de ejemplo (find_api / sample_data)
    "api.apis.guru",
    "dummyjson.com",
    # Diseño web (web_design): iconos, tipografias, paletas, logos de marca
    "api.iconify.design",     # 200k+ iconos de 150+ sets
    "fonts.google.com",       # metadata de Google Fonts (sin key)
    "www.thecolorapi.com",    # esquemas de color / paletas armonicas
    "cdn.simpleicons.org",    # logos de marca (SVG por slug) - verificar existencia
    # Enciclopedia (wikipedia): datos reales para documentos/presentaciones
    # sobre un tema del mundo (un coche, una empresa, una persona...). Solo
    # es/en: el usuario es de España, con ingles de respaldo.
    "es.wikipedia.org",
    "en.wikipedia.org",
})


class HostNoPermitido(Exception):
    """Se intento llamar a un host que no esta en la allowlist."""


def _validar_host(url: str) -> None:
    host = (urlparse(url).hostname or "").lower()
    if host not in HOSTS_PERMITIDOS:
        raise HostNoPermitido(
            f"host '{host}' no esta en la allowlist de APIs de referencia. "
            "Estas tools solo hablan con un conjunto fijo de APIs conocidas."
        )
    if not url.lower().startswith("https://"):
        raise HostNoPermitido(f"solo https en las APIs de referencia (era: {url[:40]}...).")


def _headers(extra: dict | None = None) -> dict:
    h = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    if extra:
        h.update(extra)
    return h


def get_json(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = _TIMEOUT_S,
    max_bytes: int = _MAX_BYTES,
) -> dict:
    """GET a un host de la allowlist y parsea JSON.

    Devuelve {ok, status, data, error}. Nunca lanza por un fallo de red o un
    JSON invalido: eso se reporta en 'error' para que la tool lo convierta en
    algo util para el modelo. Solo HostNoPermitido se propaga (es un bug de
    programacion de la tool, no un fallo de runtime esperable).

    No se usa streaming a proposito: algunos de estos servidores (Maven
    Central) no mandan Content-Length ni cierran la conexion, y un lector por
    trozos se quedaba esperando hasta el timeout aunque el cuerpo ya hubiera
    llegado entero (30 s por 682 bytes). httpx lee el cuerpo completo y
    devuelve; el tope de bytes se aplica al recortar, no durante la descarga -
    aceptable porque los hosts son una allowlist de APIs reputadas, no
    cualquier URL."""
    import json as _json

    _validar_host(url)
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout, headers=_headers(headers)) as client:
            resp = client.get(url, params=params)
            cuerpo = resp.content[:max_bytes]
            if resp.status_code >= 400:
                return {"ok": False, "status": resp.status_code, "data": None,
                        "error": f"HTTP {resp.status_code}"}
            try:
                return {"ok": True, "status": resp.status_code,
                        "data": _json.loads(cuerpo.decode("utf-8")), "error": None}
            except (ValueError, UnicodeDecodeError) as exc:
                return {"ok": False, "status": resp.status_code, "data": None,
                        "error": f"respuesta no era JSON valido: {exc}"}
    except httpx.HTTPError as exc:
        return {"ok": False, "status": None, "data": None,
                "error": f"{type(exc).__name__}: {exc}"}


def get_text(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = _TIMEOUT_S,
    max_bytes: int = _MAX_BYTES,
) -> dict:
    """GET texto CRUDO (XML, etc.) de un host de la allowlist. Mismo contrato
    de red que get_json pero devuelve {ok, status, text, error} sin parsear.
    Lo usa Maven (su metadata fiable es maven-metadata.xml, no JSON)."""
    _validar_host(url)
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout, headers=_headers(headers)) as client:
            resp = client.get(url, params=params)
            texto = resp.content[:max_bytes].decode("utf-8", errors="replace")
            if resp.status_code >= 400:
                return {"ok": False, "status": resp.status_code, "text": None,
                        "error": f"HTTP {resp.status_code}"}
            return {"ok": True, "status": resp.status_code, "text": texto, "error": None}
    except httpx.HTTPError as exc:
        return {"ok": False, "status": None, "text": None,
                "error": f"{type(exc).__name__}: {exc}"}


def post_json(
    url: str,
    body: dict,
    headers: dict | None = None,
    timeout: float = _TIMEOUT_S,
    max_bytes: int = _MAX_BYTES,
) -> dict:
    """POST con cuerpo JSON a un host de la allowlist. Mismo contrato de
    retorno que get_json. Lo usa OSV (su query es un POST)."""
    import json as _json

    _validar_host(url)
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout, headers=_headers(headers)) as client:
            resp = client.post(url, json=body)
            cuerpo = resp.content[:max_bytes]
            if resp.status_code >= 400:
                return {"ok": False, "status": resp.status_code, "data": None,
                        "error": f"HTTP {resp.status_code}"}
            try:
                return {"ok": True, "status": resp.status_code,
                        "data": _json.loads(cuerpo.decode("utf-8")), "error": None}
            except (ValueError, UnicodeDecodeError) as exc:
                return {"ok": False, "status": resp.status_code, "data": None,
                        "error": f"respuesta no era JSON valido: {exc}"}
    except httpx.HTTPError as exc:
        return {"ok": False, "status": None, "data": None,
                "error": f"{type(exc).__name__}: {exc}"}
