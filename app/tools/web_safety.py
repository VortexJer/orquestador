"""Capa de fetch CIBERSEGURA para todo lo que trae paginas de internet
(busqueda, web_fetch, investigacion recursiva).

El riesgo principal al dejar que un modelo elija que URL pedir es SSRF
(Server-Side Request Forgery): que consiga que ESTE proceso pida
`http://localhost`, una IP interna (10.x/192.168.x), o el endpoint de
metadatos de la nube `http://169.254.169.254/` para robar credenciales.
Un fetch "a pelo" con follow_redirects=True es justo el vector: una URL
publica puede redirigir a una interna.

Defensas que aplica `fetch_seguro`:
  - Solo http/https (nada de file://, gopher://, ftp://...).
  - Resuelve el host y EXIGE que TODAS sus IPs sean publicas (rechaza
    loopback, privadas, link-local -incluye la de metadatos-, reservadas,
    multicast). Una sola IP interna basta para bloquear.
  - Sigue redirecciones a MANO revalidando cada salto (una publica que
    redirige a una interna se corta).
  - Tope de bytes (evita respuestas gigantes / zip bombs) y de tiempo.
  - Solo content-type de texto/HTML (no binarios).
  - No reenvia cookies ni cabeceras de autenticacion a terceros.

Queda un hueco teorico de DNS-rebinding (la IP podria cambiar entre la
validacion y la conexion real de httpx); para el modelo de amenaza de
esta herramienta -un scraper de investigacion, no un proxy expuesto- es
aceptable y se documenta aqui.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_TIMEOUT_S = 15.0
_MAX_BYTES = 2_000_000  # 2 MB: de sobra para el texto de una pagina
_MAX_REDIRECTS = 4
_CONTENT_TYPES_OK = ("text/html", "text/plain", "application/xhtml")


class UrlNoSegura(Exception):
    """La URL no pasa el filtro SSRF (esquema, host o IP no permitidos)."""


def _ip_es_publica(ip_txt: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_txt)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local  # incluye 169.254.0.0/16 (metadatos de la nube)
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validar_url(url: str) -> str:
    """Lanza UrlNoSegura si la URL no es fetcheable con seguridad. Devuelve
    la URL tal cual si pasa. Resuelve el host y comprueba TODAS sus IPs."""
    if not isinstance(url, str) or not url.strip():
        raise UrlNoSegura("URL vacia.")
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise UrlNoSegura(f"esquema no permitido: '{parsed.scheme}' (solo http/https).")
    host = parsed.hostname
    if not host:
        raise UrlNoSegura("URL sin host.")
    # Una IP literal se valida directa; un nombre se resuelve.
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UrlNoSegura(f"no se pudo resolver el host '{host}': {exc}") from exc
    ips = {info[4][0] for info in infos}
    if not ips:
        raise UrlNoSegura(f"el host '{host}' no resolvio a ninguna IP.")
    internas = [ip for ip in ips if not _ip_es_publica(ip)]
    if internas:
        raise UrlNoSegura(
            f"el host '{host}' resuelve a una IP interna/privada ({internas[0]}) - "
            "bloqueado por seguridad (SSRF)."
        )
    return url.strip()


def fetch_seguro(
    url: str,
    max_bytes: int = _MAX_BYTES,
    timeout: float = _TIMEOUT_S,
    max_redirects: int = _MAX_REDIRECTS,
) -> dict:
    """Trae una URL con todas las defensas SSRF. Devuelve
    {ok, url_final, status, content_type, content(bytes), error}.

    Sigue redirecciones a mano revalidando cada salto - por eso el cliente
    va con follow_redirects=False."""
    actual = url
    with httpx.Client(
        follow_redirects=False,
        timeout=timeout,
        headers={"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml,text/plain"},
    ) as client:
        for _ in range(max_redirects + 1):
            try:
                validar_url(actual)
            except UrlNoSegura as exc:
                return {"ok": False, "url_final": actual, "error": str(exc)}
            try:
                with client.stream("GET", actual) as resp:
                    if resp.is_redirect:
                        location = resp.headers.get("location")
                        if not location:
                            return {"ok": False, "url_final": actual, "error": "redireccion sin destino."}
                        actual = urljoin(actual, location)
                        continue
                    ct = resp.headers.get("content-type", "").lower()
                    if not any(t in ct for t in _CONTENT_TYPES_OK):
                        return {
                            "ok": False, "url_final": str(resp.url), "status": resp.status_code,
                            "error": f"content-type no permitido: '{ct or 'desconocido'}' (solo texto/HTML).",
                        }
                    trozos, total = [], 0
                    for chunk in resp.iter_bytes():
                        trozos.append(chunk)
                        total += len(chunk)
                        if total > max_bytes:
                            break  # se corta la descarga, no se lee mas
                    contenido = b"".join(trozos)[:max_bytes]
                    return {
                        "ok": resp.status_code < 400,
                        "url_final": str(resp.url),
                        "status": resp.status_code,
                        "content_type": ct,
                        "content": contenido,
                        "truncado": total > max_bytes,
                        "error": None if resp.status_code < 400 else f"HTTP {resp.status_code}",
                    }
            except httpx.HTTPError as exc:
                return {"ok": False, "url_final": actual, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": False, "url_final": actual, "error": f"demasiadas redirecciones (>{max_redirects})."}


def decodificar(contenido: bytes) -> str:
    """Texto de bytes cuyo charset declarado no es de fiar (varios sitios
    dicen UTF-8 y sirven latin-1). Prueba UTF-8 y, si aparece el caracter
    de reemplazo, reinterpreta como latin-1."""
    try:
        texto = contenido.decode("utf-8")
        if "�" not in texto:
            return texto
    except UnicodeDecodeError:
        pass
    return contenido.decode("latin-1", errors="replace")
