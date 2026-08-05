"""Al retomar una sesion se reimprime la conversacion previa (continuar 'donde
lo dejaste', no una pantalla vacia). Se comprueba que cada tarea se empareja
con SU respuesta -incluido el caso de varios especialistas intercalados y el de
charla, cuyo turno guarda result="" y la respuesta vive en su hilo-."""
from groq_agent import cli


def _captura(monkeypatch):
    eventos = []
    monkeypatch.setattr(cli.ui, "print_note", lambda *a, **k: None)
    monkeypatch.setattr(cli.ui, "print_task_header", lambda t: eventos.append(("task", t)))
    monkeypatch.setattr(cli.ui, "render_result", lambda t: eventos.append(("resp", t)))
    return eventos


def test_texto_msg_normaliza_lista():
    assert cli._texto_msg({"content": "hola"}) == "hola"
    assert cli._texto_msg({"content": [{"type": "text", "text": "en partes"}]}) == "en partes"
    assert cli._texto_msg({"content": None}) == ""


def test_replay_empareja_tarea_con_su_respuesta(monkeypatch):
    eventos = _captura(monkeypatch)
    conversations = {
        "python-specialist": [
            {"role": "system", "content": "..."},
            {"role": "user", "content": "suma dos numeros"},
            {"role": "assistant", "content": "def suma(a, b): return a + b"},
        ],
    }
    turns = [{"task": "suma dos numeros", "specialist": "python-specialist", "result": "def suma..."}]
    cli._replay_sesion(conversations, turns)
    assert eventos == [
        ("task", "suma dos numeros"),
        ("resp", "def suma(a, b): return a + b"),
    ]


def test_replay_alinea_varios_especialistas_intercalados(monkeypatch):
    eventos = _captura(monkeypatch)
    conversations = {
        "python-specialist": [
            {"role": "user", "content": "t1"},
            {"role": "assistant", "content": "py-1"},
            {"role": "user", "content": "t3"},
            {"role": "assistant", "content": "py-2"},
        ],
        "sql-specialist": [
            {"role": "user", "content": "t2"},
            {"role": "assistant", "content": "sql-1"},
        ],
    }
    turns = [
        {"task": "t1", "specialist": "python-specialist"},
        {"task": "t2", "specialist": "sql-specialist"},
        {"task": "t3", "specialist": "python-specialist"},
    ]
    cli._replay_sesion(conversations, turns)
    # cada tarea con la respuesta que le toca de su especialista, en orden
    assert eventos == [
        ("task", "t1"), ("resp", "py-1"),
        ("task", "t2"), ("resp", "sql-1"),
        ("task", "t3"), ("resp", "py-2"),
    ]


def test_replay_charla_saca_la_respuesta_de_su_hilo(monkeypatch):
    """El turno de charla guarda result="" - la respuesta esta en el hilo."""
    eventos = _captura(monkeypatch)
    conversations = {
        cli._CLAVE_CHARLA: [
            {"role": "user", "content": "hola"},
            {"role": "assistant", "content": "Hola, en que te ayudo?"},
        ],
    }
    turns = [{"task": "hola", "specialist": cli._CLAVE_CHARLA, "result": ""}]
    cli._replay_sesion(conversations, turns)
    assert ("resp", "Hola, en que te ayudo?") in eventos


def test_replay_cae_al_result_si_no_hay_hilo(monkeypatch):
    eventos = _captura(monkeypatch)
    turns = [{"task": "algo", "specialist": "python-specialist", "result": "respuesta guardada"}]
    cli._replay_sesion({}, turns)  # sin conversations
    assert ("resp", "respuesta guardada") in eventos


def test_replay_sin_turnos_no_imprime_nada(monkeypatch):
    eventos = _captura(monkeypatch)
    cli._replay_sesion({}, [])
    assert eventos == []
