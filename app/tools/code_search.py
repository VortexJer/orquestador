"""Codigo REAL desde repositorios de GitHub (GitHub code search API).

Complementa a code_examples: Stack Overflow da la respuesta canonica a un
problema; esto muestra como se USA de verdad una libreria o un patron en
proyectos publicos. Devuelve el fragmento que coincide + el repo + el enlace
al archivo.

Requiere un token de GitHub (env GITHUB_TOKEN) porque la busqueda de codigo
SIEMPRE pide autenticacion; el token es gratis y no necesita ningun permiso
especial (un classic PAT sin scopes, o un fine-grained de solo lectura
publica, sirve): https://github.com/settings/tokens . Sin token, la tool lo
dice y el modelo puede tirar de code_examples.
"""
from __future__ import annotations

import os

from app.tools.api_client import get_json


def buscar_codigo(consulta: str, lenguaje: str = "", limite: int = 5) -> dict:
    """Busca codigo en repos publicos de GitHub.

    - consulta: terminos o una expresion (ej. 'asyncio.gather timeout').
    - lenguaje: opcional (python, typescript, go...) -> filtro language:.
    - limite: cuantos resultados (1-10).

    Devuelve {consulta, resultados:[{repo, archivo, link, fragmentos:[...]}],
    total, error}."""
    if not consulta or not consulta.strip():
        return {"consulta": consulta, "resultados": [], "total": 0, "error": "Consulta vacia."}
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return {"consulta": consulta, "resultados": [], "total": 0, "error": (
            "Falta GITHUB_TOKEN (gratis, sin permisos especiales, en "
            "https://github.com/settings/tokens). La busqueda de codigo de GitHub exige "
            "autenticacion. Mientras tanto usa code_examples (Stack Overflow, sin clave)."
        )}

    limite = max(1, min(int(limite or 5), 10))
    q = consulta.strip()
    if lenguaje.strip():
        q += f" language:{lenguaje.strip().lower()}"

    r = get_json(
        "https://api.github.com/search/code",
        params={"q": q, "per_page": limite},
        headers={
            "Authorization": f"Bearer {token}",
            # Media type text-match: hace que cada item traiga 'text_matches'
            # con el FRAGMENTO que coincidio, no solo la ruta del archivo.
            "Accept": "application/vnd.github.text-match+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    if not r["ok"]:
        pista = ""
        if r.get("status") == 401:
            pista = " (token invalido o caducado)"
        elif r.get("status") == 403:
            pista = " (limite de peticiones: la busqueda de codigo permite ~10/min)"
        return {"consulta": consulta, "resultados": [], "total": 0,
                "error": f"GitHub code search fallo ({r['error']}){pista}."}

    datos = r["data"] or {}
    resultados = []
    for item in datos.get("items", [])[:limite]:
        repo = item.get("repository", {})
        fragmentos = [m.get("fragment", "").strip()
                      for m in item.get("text_matches", []) if m.get("fragment")]
        resultados.append({
            "repo": repo.get("full_name", ""),
            "archivo": item.get("path", ""),
            "link": item.get("html_url", ""),
            "fragmentos": fragmentos[:3],
        })

    return {
        "consulta": consulta, "resultados": resultados,
        "total": datos.get("total_count", len(resultados)),
        "error": None if resultados else "Sin coincidencias. Prueba otros terminos.",
    }
