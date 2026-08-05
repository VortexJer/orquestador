"""Punto de entrada de alto nivel que usan los especialistas: texto de
query -> embedding -> busqueda hibrida en Qdrant. Implementa las
heuristicas de guide.md a nivel de codigo (cuando SI consultar) para
que no dependa de que el modelo "decida bien" - la decision de
consultar o no queda en manos del especialista (heuristica de riesgo),
pero la mecanica de la consulta en si es determinista.
"""
from __future__ import annotations

from app.rag.embedder import embed_text
from app.rag.store import search

RISK_KEYWORDS_REQUIRING_RAG = {
    "concurrency", "auth", "sql_write",
}


def should_consult_hard_cases(risk_signals: list[str], task_text: str) -> bool:
    if any(signal in RISK_KEYWORDS_REQUIRING_RAG for signal in risk_signals):
        return True
    tutorial_patterns = ("promise.all", "not in (select", "count =", "default=[]", "default={}")
    return any(p in task_text.lower() for p in tutorial_patterns)


def search_hard_cases(query_text: str, domain: str, tags: list[str] | None = None, limit: int = 3) -> list[dict]:
    vector = embed_text(query_text)
    return search(query_vector=vector, domain=domain, tags=tags, limit=limit)
