"""Ejemplos de codigo REALES desde Stack Overflow (Stack Exchange API).

Por que esto y no "de memoria": un modelo de tamaño medio se inventa la
firma de una funcion o el nombre de un parametro con total seguridad. Aqui
traemos la respuesta que MILES de personas votaron como correcta, con su
codigo tal cual, y el modelo la adapta en vez de inventarla.

Gratis: la Stack Exchange API da 300 peticiones/dia por IP sin clave. Con
una clave gratuita (env STACKEXCHANGE_KEY, se saca en
https://stackapps.com/apps/oauth/register) suben a 10.000/dia. Cada llamada
a esta tool gasta 2 peticiones: una para buscar y otra (en lote) para traer
las respuestas.
"""
from __future__ import annotations

import os

from bs4 import BeautifulSoup

from app.tools.api_client import get_json

_BASE = "https://api.stackexchange.com/2.3"
# 'withbody' es un filtro incorporado de la API que añade el campo .body
# (HTML de la pregunta/respuesta) al resultado - sin el no vendria el codigo.
_FILTRO = "withbody"
_MAX_BLOQUES = 3          # bloques de codigo por respuesta
_MAX_CHARS_BLOQUE = 1600  # cada bloque se recorta para no volar el contexto


def _clave() -> dict:
    k = os.environ.get("STACKEXCHANGE_KEY")
    return {"key": k} if k else {}


def _bloques_codigo(html_body: str) -> list[str]:
    """Los bloques <pre> del cuerpo (el codigo en SO va en <pre><code>...).
    get_text() sin separador conserva los saltos de linea reales del codigo."""
    soup = BeautifulSoup(html_body or "", "html.parser")
    bloques = []
    for pre in soup.find_all("pre"):
        codigo = pre.get_text().strip()
        if codigo:
            bloques.append(codigo[:_MAX_CHARS_BLOQUE])
        if len(bloques) >= _MAX_BLOQUES:
            break
    return bloques


def _texto_explicativo(html_body: str, limite: int = 320) -> str:
    """El primer parrafo de prosa de la respuesta (sin el codigo): el
    'por que', que suele ser lo que le falta al modelo."""
    soup = BeautifulSoup(html_body or "", "html.parser")
    for pre in soup.find_all("pre"):
        pre.decompose()
    texto = " ".join(soup.get_text(" ", strip=True).split())
    return texto[:limite]


def buscar_ejemplos(consulta: str, lenguaje: str = "", limite: int = 3) -> dict:
    """Busca en Stack Overflow y devuelve las mejores respuestas con su codigo.

    - consulta: en lenguaje natural, como lo buscarias en Google.
    - lenguaje/tag: opcional, filtra por etiqueta (python, javascript, sql...).
    - limite: cuantos hilos devolver (1-5).

    Devuelve {consulta, resultados:[{titulo, link, votos_pregunta, tags,
    votos_respuesta, aceptada, explicacion, codigo:[...]}], cuota_restante,
    error}."""
    if not consulta or not consulta.strip():
        return {"consulta": consulta, "resultados": [], "error": "Consulta vacia."}
    limite = max(1, min(int(limite or 3), 5))

    params = {
        "order": "desc", "sort": "votes", "q": consulta.strip(),
        "site": "stackoverflow", "filter": _FILTRO,
        "pagesize": min(limite * 2, 10), "answers": 1,
    }
    if lenguaje.strip():
        params["tagged"] = lenguaje.strip().lower()
    params.update(_clave())

    r = get_json(f"{_BASE}/search/advanced", params=params)
    if not r["ok"]:
        return {"consulta": consulta, "resultados": [], "error": (
            f"No se pudo consultar Stack Overflow ({r['error']}). "
            "Puede ser un limite de cuota (300/dia sin clave): pon STACKEXCHANGE_KEY. "
            "No inventes el codigo: dilo o reintenta."
        )}
    datos = r["data"] or {}
    preguntas = [q for q in datos.get("items", []) if q.get("answer_count", 0) > 0]
    if not preguntas:
        return {"consulta": consulta, "resultados": [],
                "cuota_restante": datos.get("quota_remaining"),
                "error": "Sin resultados con respuesta para esa consulta. Prueba otros terminos o quita el tag."}

    # Preferimos las que tienen respuesta aceptada; nos quedamos con `limite`.
    preguntas.sort(key=lambda q: (0 if q.get("accepted_answer_id") else 1, -q.get("score", 0)))
    preguntas = preguntas[:limite]

    # Traer las respuestas en LOTE (1 sola peticion): la aceptada si existe,
    # si no la mejor votada del hilo. Pedimos por id de respuesta cuando hay
    # aceptada; para el resto, por hilo.
    ids_respuesta = [str(q["accepted_answer_id"]) for q in preguntas if q.get("accepted_answer_id")]
    cuerpo_por_respuesta: dict[int, dict] = {}
    if ids_respuesta:
        ra = get_json(f"{_BASE}/answers/{';'.join(ids_respuesta)}",
                      params={"site": "stackoverflow", "filter": _FILTRO,
                              "order": "desc", "sort": "votes", **_clave()})
        if ra["ok"]:
            for a in (ra["data"] or {}).get("items", []):
                cuerpo_por_respuesta[a["answer_id"]] = a

    resultados = []
    for q in preguntas:
        aid = q.get("accepted_answer_id")
        ans = cuerpo_por_respuesta.get(aid) if aid else None
        # Si no hubo respuesta aceptada (o no vino), usamos el cuerpo de la
        # pregunta como ultimo recurso para al menos dar el contexto y el link.
        body = (ans or {}).get("body") or q.get("body", "")
        resultados.append({
            "titulo": q.get("title", ""),
            "link": q.get("link", ""),
            "votos_pregunta": q.get("score", 0),
            "tags": q.get("tags", []),
            "votos_respuesta": (ans or {}).get("score"),
            "aceptada": bool(ans and ans.get("is_accepted")),
            "explicacion": _texto_explicativo(body),
            "codigo": _bloques_codigo(body),
        })

    return {
        "consulta": consulta,
        "resultados": resultados,
        "cuota_restante": datos.get("quota_remaining"),
        "error": None,
    }
