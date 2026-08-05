from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
AUTH = {"Authorization": "Bearer dev-local-key"}


def test_generate_requires_auth():
    r = client.post("/v1/generate", json={"task": "hola"})
    assert r.status_code == 401


def test_generate_python_task_end_to_end_mock():
    r = client.post(
        "/v1/generate",
        headers=AUTH,
        json={"task": "Escribe is_valid_email(value) que valide el formato de un email, con tests."},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("completed", "partial")
    assert body["trace"]["model_used"]["specialist"] == "python-specialist"
    assert body["cost"]["gpu_seconds"] >= 0


def test_catalog_lists_python_specialist_enabled():
    r = client.get("/v1/catalog", headers=AUTH)
    assert r.status_code == 200
    specialists = r.json()["specialists"]
    assert specialists["python-specialist"]["enabled"] is True


def test_health_no_auth_required():
    r = client.get("/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
