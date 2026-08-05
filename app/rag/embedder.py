"""Embedder para la base de casos dificiles. fastembed (ONNX, CPU,
sin torch) en vez de sentence-transformers: mismo modelo
(BAAI/bge-small-en-v1.5, 384 dims) pero sin arrastrar una dependencia
de GBs - corre comodo en la maquina de desarrollo sin GPU y en el
container liviano de Qdrant en el servidor (ver doc arquitectura,
seccion 6 y 10).
"""
from __future__ import annotations

from functools import lru_cache

from fastembed import TextEmbedding

from app.config import get_embedder_config


@lru_cache
def get_embedder() -> TextEmbedding:
    cfg = get_embedder_config()
    return TextEmbedding(model_name=cfg["model_name"])


def embed_text(text: str) -> list[float]:
    embedder = get_embedder()
    return list(next(embedder.embed([text])))


def embed_texts(texts: list[str]) -> list[list[float]]:
    embedder = get_embedder()
    return [list(vec) for vec in embedder.embed(texts)]
