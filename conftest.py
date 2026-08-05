"""Configura el entorno de test ANTES de que se importe cualquier
modulo de app/ (los modulos de config usan lru_cache y leen variables
de entorno en la primera llamada, asi que el orden importa)."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("INFERENCE_MODE", "mock")
os.environ.setdefault("ROUTER_IMPL", "heuristic")
os.environ.setdefault("QDRANT_MODE", "embedded")
os.environ["QDRANT_PATH"] = tempfile.mkdtemp(prefix="qdrant-test-")
os.environ.setdefault("API_KEYS", "dev-local-key")
# Aisla el historico global de llamadas por API (usage._ruta_historico) para
# que los tests no escriban en el ~/.orquestador real del usuario.
os.environ.setdefault("ORQUESTADOR_HOME", tempfile.mkdtemp(prefix="orq-home-test-"))

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def ensure_hard_cases_ingested():
    from app.rag.ingest import ingest
    ingest()
