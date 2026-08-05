"""session_store.py es la base de -c/--continue y -r/--resume - sin
red, toda la logica es manejo de archivos JSON dentro del workspace."""
import json

import pytest

from groq_agent import session_store


def test_new_session_ids_are_unique():
    ids = {session_store.new_session_id() for _ in range(5)}
    assert len(ids) == 5


def test_save_and_load_roundtrip(tmp_path):
    session_id = session_store.new_session_id()
    conversations = {"python-specialist": [{"role": "system", "content": "skill..."}]}
    turns = [{"task": "hacer algo", "specialist": "python-specialist", "model": "x", "domain": "python", "result": "ok"}]

    session_store.save_session(tmp_path, session_id, conversations, turns, "2026-01-01T00:00:00")
    loaded = session_store.load_session(tmp_path, session_id)

    assert loaded["session_id"] == session_id
    assert loaded["conversations"] == conversations
    assert loaded["turns"] == turns


def test_load_missing_session_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        session_store.load_session(tmp_path, "sess_no_existe")


def test_list_sessions_orders_most_recent_first(tmp_path):
    # Escribe los archivos directamente (en vez de via save_session) para
    # controlar 'updated_at' de forma determinista - save_session siempre
    # timestampea con la hora actual, que no alcanza a diferenciar entre
    # dos llamadas seguidas en el mismo test.
    old_id = "sess_20260101_000000_aaaa"
    (tmp_path / session_store.SESSIONS_DIRNAME).mkdir(parents=True, exist_ok=True)
    session_store.session_path(tmp_path, old_id).write_text(
        json.dumps({
            "session_id": old_id, "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00", "turns": [{"task": "primera tarea"}], "conversations": {},
        }),
        encoding="utf-8",
    )

    new_id = "sess_20260102_000000_bbbb"
    session_store.session_path(tmp_path, new_id).write_text(
        json.dumps({
            "session_id": new_id, "created_at": "2026-01-02T00:00:00",
            "updated_at": "2026-01-02T00:00:00", "turns": [{"task": "segunda tarea"}], "conversations": {},
        }),
        encoding="utf-8",
    )

    sessions = session_store.list_sessions(tmp_path)
    assert sessions[0]["session_id"] == new_id
    assert sessions[0]["preview"] == "segunda tarea"
    assert sessions[1]["session_id"] == old_id


def test_find_latest_session_id_none_when_empty(tmp_path):
    assert session_store.find_latest_session_id(tmp_path) is None


def test_find_latest_session_id_returns_most_recent(tmp_path):
    session_id = session_store.new_session_id()
    session_store.save_session(tmp_path, session_id, {}, [{"task": "x"}], "2026-01-01T00:00:00")
    assert session_store.find_latest_session_id(tmp_path) == session_id
