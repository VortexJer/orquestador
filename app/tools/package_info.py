"""Datos REALES y actuales de un paquete, desde el registro oficial de cada
ecosistema. Todos gratis y sin clave.

El problema que resuelve: un modelo de tamaño medio inventa numeros de
version ("usa requests 2.28") y nombres de import con total seguridad, y a
menudo estan desfasados o no existen. Aqui se consulta la fuente de verdad
-PyPI, npm, crates.io, etc.- y se devuelve la version actual, la
descripcion, el repo y la licencia. El modelo cita el dato, no lo adivina.

Ecosistemas: pypi, npm, crates, go, packagist, rubygems, nuget, maven.
"""
from __future__ import annotations

import re

from app.tools.api_client import get_json, get_text

ECOSISTEMAS = ("pypi", "npm", "crates", "go", "packagist", "rubygems", "nuget", "maven")


def _salida(ecosystem, name, version=None, descripcion="", homepage="", repo="",
            licencia="", fecha="", error=None) -> dict:
    return {
        "ecosystem": ecosystem, "name": name, "version": version,
        "descripcion": (descripcion or "")[:400], "homepage": homepage or "",
        "repo": repo or "", "licencia": licencia or "", "fecha": fecha or "",
        "error": error,
    }


def _escape_go(mod: str) -> str:
    """El proxy de Go exige minusculas: cada mayuscula va como '!' + minuscula
    (github.com/BurntSushi -> github.com/!burnt!sushi)."""
    return "".join("!" + c.lower() if c.isupper() else c for c in mod)


def _clave_version(v: str) -> tuple:
    """Orden de versiones toscamente semantico, para elegir la 'ultima' cuando
    el registro no la marca (packagist). No pretende ser PEP440/semver
    completo: separa por no-digitos y compara numericamente lo que sea numero."""
    partes = re.split(r"[.\-+]", v)
    return tuple(int(p) if p.isdigit() else -1 for p in partes)


def _pypi(name: str) -> dict:
    r = get_json(f"https://pypi.org/pypi/{name}/json")
    if not r["ok"]:
        return _salida("pypi", name, error=f"no encontrado en PyPI ({r['error']}).")
    info = (r["data"] or {}).get("info", {})
    urls = info.get("project_urls") or {}
    repo = urls.get("Source") or urls.get("Repository") or urls.get("Homepage") or ""
    return _salida("pypi", name, info.get("version"), info.get("summary"),
                   info.get("home_page") or urls.get("Homepage", ""), repo,
                   info.get("license"))


def _npm(name: str) -> dict:
    # /latest (el manifiesto de la ultima version) en vez de /{name} (el
    # packument COMPLETO): el de un paquete popular como react pesa ~7 MB
    # -todas sus versiones historicas- y se cortaba contra el tope de bytes,
    # dejando un JSON invalido. El manifiesto de una version pesa unos KB y
    # ya trae version, descripcion, repo y licencia.
    r = get_json(f"https://registry.npmjs.org/{name}/latest")
    if not r["ok"]:
        return _salida("npm", name, error=f"no encontrado en npm ({r['error']}).")
    d = r["data"] or {}
    repo = d.get("repository") or {}
    repo_url = repo.get("url", "") if isinstance(repo, dict) else str(repo)
    lic = d.get("license")
    if isinstance(lic, dict):  # npm permite {"type": "MIT"} ademas del string
        lic = lic.get("type", "")
    return _salida("npm", name, d.get("version"), d.get("description"),
                   d.get("homepage", ""), repo_url.replace("git+", "").replace(".git", ""),
                   lic)


def _crates(name: str) -> dict:
    r = get_json(f"https://crates.io/api/v1/crates/{name}")
    if not r["ok"]:
        return _salida("crates", name, error=f"no encontrado en crates.io ({r['error']}).")
    c = (r["data"] or {}).get("crate", {})
    return _salida("crates", name, c.get("max_stable_version") or c.get("newest_version"),
                   c.get("description"), c.get("homepage") or c.get("documentation", ""),
                   c.get("repository", ""), "", c.get("updated_at", ""))


def _go(name: str) -> dict:
    r = get_json(f"https://proxy.golang.org/{_escape_go(name)}/@latest")
    if not r["ok"]:
        return _salida("go", name, error=(
            f"no encontrado en el proxy de Go ({r['error']}). Usa la ruta completa "
            "del modulo (ej. github.com/gin-gonic/gin)."))
    d = r["data"] or {}
    return _salida("go", name, d.get("Version"), "", "",
                   name if "." in name else "", "", d.get("Time", ""))


def _packagist(name: str) -> dict:
    r = get_json(f"https://packagist.org/packages/{name}.json")
    if not r["ok"]:
        return _salida("packagist", name, error=(
            f"no encontrado en Packagist ({r['error']}). Usa 'vendor/paquete' (ej. monolog/monolog)."))
    p = (r["data"] or {}).get("package", {})
    versiones = [v for v in (p.get("versions") or {}) if "dev" not in v.lower()]
    ultima = max(versiones, key=_clave_version) if versiones else None
    return _salida("packagist", name, ultima, p.get("description"),
                   "", p.get("repository", ""), "")


def _rubygems(name: str) -> dict:
    r = get_json(f"https://rubygems.org/api/v1/gems/{name}.json")
    if not r["ok"]:
        return _salida("rubygems", name, error=f"no encontrado en RubyGems ({r['error']}).")
    d = r["data"] or {}
    lic = d.get("licenses") or []
    return _salida("rubygems", name, d.get("version"), d.get("info"),
                   d.get("homepage_uri", ""), d.get("source_code_uri") or d.get("project_uri", ""),
                   ", ".join(lic) if lic else "")


def _nuget(name: str) -> dict:
    r = get_json("https://azuresearch-usnc.nuget.org/query",
                 params={"q": f"packageid:{name}", "take": 1})
    if not r["ok"]:
        return _salida("nuget", name, error=f"no se pudo consultar NuGet ({r['error']}).")
    datos = (r["data"] or {}).get("data", [])
    if not datos:
        return _salida("nuget", name, error="no encontrado en NuGet.")
    d = datos[0]
    return _salida("nuget", name, d.get("version"), d.get("description"),
                   d.get("projectUrl", ""), d.get("projectUrl", ""),
                   ", ".join(d.get("licenseExpression", "").split()) if d.get("licenseExpression") else "")


def _maven(name: str) -> dict:
    # Con 'grupo:artefacto' vamos al CDN de artefactos (repo1.maven.org):
    # maven-metadata.xml da la version en <release>/<latest> y es RAPIDO Y
    # FIABLE. El buscador solr (search.maven.org) es intermitente -medido: 40s,
    # 30s y 0.5s en tres intentos seguidos-, asi que solo se usa como ultimo
    # recurso para el caso de 'artefacto' suelto (sin grupo), que el CDN no
    # puede resolver porque necesita la ruta del grupo.
    if ":" in name:
        grupo, artefacto = name.split(":", 1)
        ruta = grupo.replace(".", "/")
        r = get_text(f"https://repo1.maven.org/maven2/{ruta}/{artefacto}/maven-metadata.xml")
        if r["ok"] and r["text"]:
            m = (re.search(r"<release>([^<]+)</release>", r["text"])
                 or re.search(r"<latest>([^<]+)</latest>", r["text"]))
            if m:
                return _salida("maven", name, m.group(1), f"{grupo}:{artefacto}", "",
                               f"https://central.sonatype.com/artifact/{grupo}/{artefacto}", "")
        # Sin metadata (grupo/artefacto mal escrito): caemos al solr por si el
        # nombre existe con otra coordenada.
        q = f'g:"{grupo}" AND a:"{artefacto}"'
    else:
        q = name

    r = get_json("https://search.maven.org/solrsearch/select",
                 params={"q": q, "rows": 1, "wt": "json"}, timeout=30.0)
    if not r["ok"]:
        return _salida("maven", name, error=(
            f"no se pudo consultar Maven Central ({r['error']}). El CDN de artefactos "
            "es mas fiable: pasa la coordenada completa 'grupo:artefacto'."))
    docs = (r["data"] or {}).get("response", {}).get("docs", [])
    if not docs:
        return _salida("maven", name, error="no encontrado en Maven Central. Prueba 'grupo:artefacto'.")
    d = docs[0]
    coord = f"{d.get('g', '')}:{d.get('a', '')}"
    return _salida("maven", name, d.get("latestVersion") or d.get("v"), coord, "", "", "")


_HANDLERS = {
    "pypi": _pypi, "npm": _npm, "crates": _crates, "go": _go,
    "packagist": _packagist, "rubygems": _rubygems, "nuget": _nuget, "maven": _maven,
}


def info_paquete(ecosystem: str, name: str) -> dict:
    """Metadatos actuales de un paquete en su registro oficial.

    ecosystem: uno de pypi/npm/crates/go/packagist/rubygems/nuget/maven.
    name: nombre del paquete (go = ruta del modulo; maven = 'grupo:artefacto';
    packagist = 'vendor/paquete')."""
    eco = (ecosystem or "").strip().lower()
    if eco not in _HANDLERS:
        return _salida(ecosystem, name, error=(
            f"ecosistema '{ecosystem}' no soportado. Usa uno de: {', '.join(ECOSISTEMAS)}."))
    if not name or not name.strip():
        return _salida(eco, name, error="Falta el nombre del paquete.")
    return _HANDLERS[eco](name.strip())
