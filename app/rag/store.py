"""Wrapper sobre Qdrant para la base de casos dificiles. Busqueda
hibrida: similitud semantica (embedding de problem_description+tags)
combinada con filtro estructurado por language_or_domain/tags - ver
doc de arquitectura, seccion 8.

Modo embebido (QDRANT_MODE=embedded, default): no requiere Docker, usa
un archivo local (qdrant_local_data/) - ideal para desarrollo sin GPU
ni infraestructura. Modo remoto (QDRANT_MODE=remote): apunta a un
container qdrant/qdrant real, para el despliegue en el servidor (donde
el contenedor de Qdrant vive separado del contenedor con GPU).

Nota: el modo embebido bloquea el path a un solo proceso a la vez. Si
la API y un script de ingesta corren al mismo tiempo contra el mismo
QDRANT_PATH van a chocar - en ese caso usar QDRANT_MODE=remote incluso
en desarrollo (docker-compose ya trae el servicio `qdrant`).
"""
from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from app.config import get_embedder_config, get_settings

if TYPE_CHECKING:  # solo para el type checker; en runtime se importa diferido
    from qdrant_client import QdrantClient

COLLECTION_NAME = "hard_cases"

# Import DIFERIDO de qdrant_client: tarda ~1.1s en cargar. Arriba haría lento el
# arranque de la terminal aunque no se use el RAG; aquí solo se paga al usarlo.


@lru_cache
def get_client() -> "QdrantClient":
    from qdrant_client import QdrantClient
    settings = get_settings()
    if settings.qdrant_mode == "remote":
        return QdrantClient(url=settings.qdrant_url)
    return QdrantClient(path=settings.qdrant_path)


def ensure_collection() -> None:
    from qdrant_client.http import models as qm
    client = get_client()
    dim = get_embedder_config()["dim"]
    if not client.collection_exists(COLLECTION_NAME):
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
        )


def upsert_entries(entries: list[dict], vectors: list[list[float]]) -> None:
    from qdrant_client.http import models as qm
    ensure_collection()
    client = get_client()
    points = [
        qm.PointStruct(id=idx, vector=vector, payload=entry)
        for idx, (entry, vector) in enumerate(zip(entries, vectors))
    ]
    client.upsert(collection_name=COLLECTION_NAME, points=points)


def search(
    query_vector: list[float],
    domain: str | None = None,
    tags: list[str] | None = None,
    limit: int = 3,
    score_threshold: float = 0.35,
) -> list[dict]:
    from qdrant_client.http import models as qm
    ensure_collection()
    client = get_client()
    must: list[qm.FieldCondition] = []
    if domain:
        must.append(qm.FieldCondition(key="language_or_domain", match=qm.MatchValue(value=domain)))
    should: list[qm.FieldCondition] = []
    if tags:
        should.append(qm.FieldCondition(key="tags", match=qm.MatchAny(any=tags)))

    query_filter = qm.Filter(must=must or None, should=should or None) if (must or should) else None

    hits = client.search(
        collection_name=COLLECTION_NAME,
        query_vector=query_vector,
        query_filter=query_filter,
        limit=limit,
        score_threshold=score_threshold,
    )
    return [{"score": hit.score, **hit.payload} for hit in hits]


def count() -> int:
    ensure_collection()
    client = get_client()
    return client.count(COLLECTION_NAME).count
