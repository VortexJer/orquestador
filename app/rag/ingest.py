"""Carga todos los archivos de hard_cases/by_domain/*.json en Qdrant.
Uso:

    python -m app.rag.ingest

Idempotente: recrea la coleccion antes de insertar, para poder
correrlo de nuevo sin duplicar entradas mientras el catalogo de casos
crece a mano (alta curada por humano, ver doc de arquitectura seccion 8).

Cada archivo de by_domain/ corresponde a un lenguaje/dominio (python.json,
rust.json, security.json, office_email.json, etc.) - agregar un dominio
nuevo es agregar un archivo nuevo aca, no tocar este script.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.rag.embedder import embed_texts
from app.rag.store import COLLECTION_NAME, get_client, upsert_entries

BY_DOMAIN_DIR = Path(__file__).resolve().parent.parent.parent / "hard_cases" / "by_domain"

_REQUIRED_FIELDS = (
    "id", "language_or_domain", "tags", "problem_description",
    "why_small_models_fail", "correct_approach", "bad_example", "good_example", "source",
)


def _embedding_text(entry: dict) -> str:
    tags = " ".join(entry.get("tags", []))
    return f"{entry['problem_description']} {tags}"


def _load_all_entries(by_domain_dir: Path) -> list[dict]:
    entries: list[dict] = []
    seen_ids: set[str] = set()
    for path in sorted(by_domain_dir.glob("*.json")):
        for entry in json.loads(path.read_text(encoding="utf-8")):
            missing = [f for f in _REQUIRED_FIELDS if f not in entry]
            if missing:
                raise ValueError(f"{path.name}: entrada '{entry.get('id', '?')}' sin campos {missing}")
            if entry["id"] in seen_ids:
                raise ValueError(f"{path.name}: id duplicado '{entry['id']}'")
            seen_ids.add(entry["id"])
            entries.append(entry)
    return entries


def ingest(by_domain_dir: Path = BY_DOMAIN_DIR) -> int:
    entries = _load_all_entries(by_domain_dir)

    client = get_client()
    if client.collection_exists(COLLECTION_NAME):
        client.delete_collection(COLLECTION_NAME)

    texts = [_embedding_text(entry) for entry in entries]
    vectors = embed_texts(texts)
    upsert_entries(entries, vectors)
    return len(entries)


if __name__ == "__main__":
    n = ingest()
    print(f"Ingeridas {n} entradas en la coleccion '{COLLECTION_NAME}' desde {BY_DOMAIN_DIR}.")
