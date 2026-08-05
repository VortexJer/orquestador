"""Consulta de solo lectura sobre un grafo de conocimiento generado por
graphify (skill de Claude Code, ver ~/.claude/skills/graphify) - un
grafo de nodos (funciones, clases, archivos...) y aristas (contains,
calls, imports...) ya extraido del propio codigo de este repositorio.

No requiere el paquete `graphify` instalado (no es una dependencia de
este proyecto, se instala aparte para el skill): lee directamente
`graphify-out/graph.json` (formato node-link estandar) y hace un BFS
ligero en Python puro - equivalente al fallback que el propio skill de
graphify documenta para cuando su CLI no esta disponible.

Es una herramienta generica de INTROSPECCION DE CODIGO (como esta hecho
este orquestador por dentro), no algo especifico de ningun especialista
- por eso no vive junto a maps_scraper/reference_kit (esas son para
construir sitios de un negocio) sino como una tool mas, disponible para
cualquiera. El grafo hay que regenerarlo aparte, fuera de este agente,
corriendo `/graphify` (o `/graphify --update`) sobre el repo desde
Claude Code - esta tool NUNCA reconstruye el grafo, solo lo lee.
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path

_GRAPH_PATH = Path(__file__).resolve().parent.parent.parent / "graphify-out" / "graph.json"
_MAX_RESULTS = 25
_DEFAULT_DEPTH = 2
_MIN_TERM_LEN = 4
# Palabras conectoras comunes en es/en - filtrarlas evita que una busqueda en
# lenguaje natural ("como funciona esto") de falsos positivos por coincidir
# con texto de docstrings/rationale de nodos totalmente ajenos al tema real.
_STOPWORDS = {
    "esto", "esta", "este", "estos", "estas", "para", "como", "donde", "cuando",
    "existe", "ningun", "ninguna", "algo", "alguna", "algun", "sobre", "entre",
    "desde", "hasta", "solo", "todo", "toda", "todos", "todas", "hace", "tiene",
    "with", "from", "this", "that", "there", "where", "when", "does", "about",
}


class CodeGraphUnavailableError(Exception):
    pass


def _load_graph() -> tuple[dict[str, dict], dict[str, list[dict]]]:
    if not _GRAPH_PATH.exists():
        raise CodeGraphUnavailableError(
            f"No existe {_GRAPH_PATH}. El grafo se genera aparte, fuera de este "
            "agente, corriendo el skill '/graphify' sobre este repo desde Claude "
            "Code - esta tool solo puede LEER un grafo ya construido."
        )
    data = json.loads(_GRAPH_PATH.read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in data.get("nodes", [])}
    adjacency: dict[str, list[dict]] = {}
    for link in data.get("links", []):
        source, target = link.get("source"), link.get("target")
        if source is None or target is None:
            continue
        relation = link.get("relation", "")
        adjacency.setdefault(source, []).append({"to": target, "relation": relation})
        adjacency.setdefault(target, []).append({"to": source, "relation": f"{relation} (inverso)"})
    return nodes, adjacency


def _matching_nodes(nodes: dict[str, dict], query: str, limit: int) -> list[str]:
    q = query.strip().lower()
    terms = [
        t for t in q.replace("_", " ").replace("-", " ").split()
        if len(t) >= _MIN_TERM_LEN and t not in _STOPWORDS
    ]
    # Con varios terminos, exigir que coincidan al menos 2 evita que una sola
    # palabra generica (que sobrevivio al filtro de stopwords) haga matchear
    # nodos sin relacion real entre si solo por compartir una palabra suelta.
    umbral = 1 if len(terms) <= 1 else 2

    scored: list[tuple[int, str]] = []
    for node_id, node in nodes.items():
        haystack = " ".join(
            str(node.get(campo, "")) for campo in ("label", "norm_label", "source_file")
        ).lower() + " " + node_id.lower()
        score = sum(1 for t in terms if t in haystack)
        if q and q in haystack:
            score += 3
        if score >= umbral or (q and q in haystack):
            scored.append((score, node_id))
    scored.sort(key=lambda par: -par[0])
    return [node_id for _, node_id in scored[:limit]]


def query_code_graph(query: str, max_depth: int = _DEFAULT_DEPTH) -> dict:
    """Busca nodos del grafo relacionados con `query` (nombre de una
    funcion/clase/archivo del repo, ej. 'auto_router' o 'run_agent') y
    devuelve, para cada uno, sus vecinos hasta `max_depth` saltos (quien
    lo llama, a que llama, en que archivo/linea esta). Util para
    preguntas sobre COMO ESTA HECHO este mismo sistema (arquitectura,
    que funcion usa a cual), no para el codigo de un proyecto que estes
    construyendo para un usuario."""
    nodes, adjacency = _load_graph()
    seeds = _matching_nodes(nodes, query, limit=8)
    if not seeds:
        return {
            "ok": False,
            "error": f"Ningun nodo del grafo coincide con '{query}'.",
            "sugerencia": "Proba con un nombre de funcion/archivo mas literal (ej. 'run_agent' en vez de 'bucle principal').",
        }

    visited: set[str] = set()
    resultados: list[dict] = []
    cola: deque[tuple[str, int]] = deque((seed, 0) for seed in seeds)
    while cola and len(resultados) < _MAX_RESULTS:
        node_id, depth = cola.popleft()
        if node_id in visited:
            continue
        visited.add(node_id)
        node = nodes.get(node_id)
        if node is None:
            continue
        vecinos = adjacency.get(node_id, [])
        resultados.append({
            "id": node_id,
            "label": node.get("label"),
            "archivo": node.get("source_file"),
            "linea": node.get("source_location"),
            "comunidad": node.get("community_name"),
            "relaciones": [f"{v['relation']} -> {v['to']}" for v in vecinos[:8]],
        })
        if depth < max_depth:
            for vecino in vecinos:
                if vecino["to"] not in visited:
                    cola.append((vecino["to"], depth + 1))

    return {"ok": True, "query": query, "nodos_semilla": seeds, "resultados": resultados}


def shortest_path_in_graph(origen: str, destino: str) -> dict:
    """Camino mas corto (BFS) entre dos nodos del grafo, encontrados por
    nombre parcial (no hace falta el id exacto). Util para preguntas
    tipo 'como se conecta X con Y' dentro del propio codigo del
    orquestador."""
    nodes, adjacency = _load_graph()
    origen_ids = _matching_nodes(nodes, origen, limit=1)
    destino_ids = _matching_nodes(nodes, destino, limit=1)
    if not origen_ids or not destino_ids:
        faltante = origen if not origen_ids else destino
        return {"ok": False, "error": f"Ningun nodo coincide con '{faltante}'."}
    inicio, fin = origen_ids[0], destino_ids[0]

    padres: dict[str, str | None] = {inicio: None}
    cola: deque[str] = deque([inicio])
    while cola:
        actual = cola.popleft()
        if actual == fin:
            break
        for vecino in adjacency.get(actual, []):
            if vecino["to"] not in padres:
                padres[vecino["to"]] = actual
                cola.append(vecino["to"])

    if fin not in padres:
        return {"ok": False, "error": f"No hay camino entre '{origen}' y '{destino}' en el grafo."}

    camino: list[str] = []
    nodo: str | None = fin
    while nodo is not None:
        camino.append(nodo)
        nodo = padres[nodo]
    camino.reverse()
    return {
        "ok": True,
        "camino": [nodes[n].get("label", n) for n in camino],
        "longitud": len(camino) - 1,
    }
