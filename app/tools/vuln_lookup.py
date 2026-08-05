"""Vulnerabilidades conocidas de un paquete, desde OSV.dev (la base de datos
de vulnerabilidades open-source de Google, gratis y sin clave).

Para el especialista de seguridad y para cualquiera que añada una
dependencia: en vez de opinar "esa version parece vieja", se consulta si
tiene CVEs reales y en que version se arreglaron. OSV agrega GitHub Advisory,
RustSec, PyPA, Go vuln DB, etc. en un formato unico.
"""
from __future__ import annotations

from app.tools.api_client import post_json

# Nuestro nombre de ecosistema -> el nombre EXACTO que espera OSV (distingue
# mayusculas). Mismos ecosistemas que package_info, misma nomenclatura de
# entrada, para que el modelo no tenga que aprender dos vocabularios.
_A_OSV = {
    "pypi": "PyPI", "npm": "npm", "crates": "crates.io", "go": "Go",
    "packagist": "Packagist", "rubygems": "RubyGems", "nuget": "NuGet", "maven": "Maven",
}


def _corregido_en(vuln: dict) -> list[str]:
    """Versiones donde el fallo quedo arreglado (eventos 'fixed' de los
    rangos afectados). Es el dato accionable: 'sube a esta'."""
    fixes: list[str] = []
    for afectado in vuln.get("affected", []):
        for rango in afectado.get("ranges", []):
            for evento in rango.get("events", []):
                if "fixed" in evento:
                    fixes.append(evento["fixed"])
    return sorted(set(fixes))


def _severidad(vuln: dict) -> str:
    sev = vuln.get("severity") or []
    if sev:
        return ", ".join(f"{s.get('type', '')}:{s.get('score', '')}" for s in sev)
    # Muchos advisories llevan la severidad cualitativa aca.
    return (vuln.get("database_specific") or {}).get("severity", "") or "sin clasificar"


def consultar_vulnerabilidades(ecosystem: str, name: str, version: str = "") -> dict:
    """Busca vulnerabilidades conocidas de un paquete en OSV.dev.

    - ecosystem: pypi/npm/crates/go/packagist/rubygems/nuget/maven.
    - name: nombre del paquete (maven = 'grupo:artefacto').
    - version: opcional. Con version, OSV filtra a las que afectan ESA
      version; sin ella, devuelve todas las del paquete."""
    eco = (ecosystem or "").strip().lower()
    if eco not in _A_OSV:
        return {"ecosystem": ecosystem, "name": name, "vulnerabilidades": [], "total": 0,
                "error": f"ecosistema '{ecosystem}' no soportado. Usa: {', '.join(_A_OSV)}."}
    if not name or not name.strip():
        return {"ecosystem": eco, "name": name, "vulnerabilidades": [], "total": 0,
                "error": "Falta el nombre del paquete."}

    body: dict = {"package": {"ecosystem": _A_OSV[eco], "name": name.strip()}}
    if version and version.strip():
        body["version"] = version.strip()

    r = post_json("https://api.osv.dev/v1/query", body)
    if not r["ok"]:
        return {"ecosystem": eco, "name": name, "vulnerabilidades": [], "total": 0,
                "error": f"no se pudo consultar OSV ({r['error']})."}

    vulns = (r["data"] or {}).get("vulns", []) or []
    salida = []
    for v in vulns:
        salida.append({
            "id": v.get("id", ""),
            "aliases": v.get("aliases", []),  # normalmente el/los CVE
            "resumen": v.get("summary", "") or (v.get("details", "") or "")[:200],
            "severidad": _severidad(v),
            "corregido_en": _corregido_en(v),
            "link": f"https://osv.dev/vulnerability/{v.get('id', '')}",
        })

    return {
        "ecosystem": eco, "name": name.strip(),
        "version": version.strip() or None,
        "vulnerabilidades": salida, "total": len(salida),
        "error": None,
        "nota": "Sin vulnerabilidades conocidas en OSV." if not salida else None,
    }
