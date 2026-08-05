"""El submenu de una sesion en `orquestador -r` (flecha derecha): renombrar,
borrar y ver uso. Aqui se prueba la LOGICA de persistencia; el pintado con
flechas (ui.select) no se testea porque necesita un TTY.
"""
from groq_agent import session_store, usage


def _sesion(ws, texto="hazme una web"):
    sid = session_store.new_session_id()
    session_store.save_session(ws, sid, {"python-specialist": []},
                               [{"task": texto}], created_at="2026-08-03T10:00:00")
    return sid


def test_mostrar_api_es_historico_global_de_todos_los_workspaces(tmp_path, monkeypatch):
    """`orquestador -api`: total HISTORICO de llamadas por proveedor, leido del
    historico global - suma sesiones de workspaces DISTINTOS, no solo uno."""
    from groq_agent import cli, ui

    monkeypatch.setenv("ORQUESTADOR_HOME", str(tmp_path / "home"))

    g1 = usage.Gasto()
    g1.sumar({"usage": {"prompt_tokens": 10, "completion_tokens": 1}}, "groq:llama-3.3-70b")
    g1.sumar({}, "groq:llama-3.3-70b")
    usage.guardar(tmp_path / "ws1", "s1", g1)          # 2 llamadas a groq
    g2 = usage.Gasto()
    g2.sumar({"usage": {"prompt_tokens": 5, "completion_tokens": 1}}, "nvidia:qwen")
    usage.guardar(tmp_path / "ws2", "s2", g2)          # OTRO workspace: se suma igual

    lineas = []
    monkeypatch.setattr(ui.console, "print", lambda *a, **k: lineas.append(str(a[0]) if a else ""))
    monkeypatch.setattr(ui, "print_header", lambda *a, **k: None)

    assert cli._mostrar_api(tmp_path) == 0
    texto = "\n".join(lineas)
    assert "groq" in texto and "nvidia" in texto
    assert "llama-3.3-70b" in texto                    # desglose por modelo
    assert "TOTAL: 3 llamada(s)" in texto              # 2 (ws1) + 1 (ws2), historico


def test_el_historico_se_acumula_por_delta_no_cuenta_dos_veces(tmp_path, monkeypatch):
    """Guardar la misma sesion cada turno no debe inflar el historico: solo
    cuentan las llamadas NUEVAS respecto al ultimo guardado."""
    monkeypatch.setenv("ORQUESTADOR_HOME", str(tmp_path / "home"))

    g = usage.Gasto()
    g.sumar({}, "groq:llama")
    usage.guardar(tmp_path / "ws", "s", g)             # sesion: 1 -> historico 1
    g.sumar({}, "groq:llama")
    usage.guardar(tmp_path / "ws", "s", g)             # sesion: 2 -> delta +1 -> historico 2
    assert usage.cargar_historico() == {"groq:llama": 2}


def test_mostrar_api_sin_datos_no_revienta(tmp_path, monkeypatch):
    from groq_agent import cli, ui

    monkeypatch.setenv("ORQUESTADOR_HOME", str(tmp_path / "home-vacio"))
    monkeypatch.setattr(ui.console, "print", lambda *a, **k: None)
    monkeypatch.setattr(ui, "print_header", lambda *a, **k: None)
    assert cli._mostrar_api(tmp_path) == 0             # sin historico: 0, sin crash


def test_rename_aparece_en_el_listado(tmp_path):
    sid = _sesion(tmp_path)
    session_store.rename_session(tmp_path, sid, "Mi proyecto web")
    s = next(s for s in session_store.list_sessions(tmp_path) if s["session_id"] == sid)
    assert s["name"] == "Mi proyecto web"


def test_el_nombre_sobrevive_a_un_guardado_posterior(tmp_path):
    """El bug facil: save_session reescribe el fichero y se cargaria el nombre.
    Tiene que preservarlo."""
    sid = _sesion(tmp_path)
    session_store.rename_session(tmp_path, sid, "Persistente")
    # la sesion sigue trabajando y se vuelve a guardar
    session_store.save_session(tmp_path, sid, {"python-specialist": []},
                               [{"task": "otra tarea"}], created_at="2026-08-03T10:00:00")
    s = next(s for s in session_store.list_sessions(tmp_path) if s["session_id"] == sid)
    assert s["name"] == "Persistente"


def test_rename_vacio_vuelve_al_automatico(tmp_path):
    sid = _sesion(tmp_path)
    session_store.rename_session(tmp_path, sid, "Algo")
    session_store.rename_session(tmp_path, sid, "   ")
    s = next(s for s in session_store.list_sessions(tmp_path) if s["session_id"] == sid)
    assert s["name"] is None


def test_delete_quita_la_sesion(tmp_path):
    sid = _sesion(tmp_path)
    assert session_store.delete_session(tmp_path, sid) is True
    assert all(s["session_id"] != sid for s in session_store.list_sessions(tmp_path))
    # borrar algo que no existe no revienta
    assert session_store.delete_session(tmp_path, sid) is False


def test_delete_tambien_limpia_el_uso(tmp_path):
    sid = _sesion(tmp_path)
    g = usage.Gasto()
    g.sumar({"usage": {"prompt_tokens": 100, "completion_tokens": 40}}, modelo="coder-main")
    usage.guardar(tmp_path, sid, g)
    assert usage.cargar(tmp_path, sid).total == 140
    usage.borrar(tmp_path, sid)
    assert usage.cargar(tmp_path, sid).total == 0
    assert sid not in usage.cargar_todo(tmp_path)


def test_etiqueta_prefiere_el_nombre_a_mano(tmp_path):
    from groq_agent.cli import _etiqueta_sesion
    assert _etiqueta_sesion({"name": "Custom", "preview": "hazme una web"}) == "Custom"
    # sin nombre a mano, cae al auto-generado del preview (no vacio)
    auto = _etiqueta_sesion({"name": None, "preview": "hazme una tienda online de cafe"})
    assert auto and auto != "(sin tareas)"


def test_borrar_es_la_primera_opcion_del_submenu_y_borra(monkeypatch, tmp_path):
    from groq_agent import cli
    sid = _sesion(tmp_path)
    capt = {}

    def fake_select(title, options, **k):
        capt["options"] = options
        capt["right"] = k.get("right_is_enter")
        return 0   # el usuario elige la primera opcion

    monkeypatch.setattr(cli.ui, "select", fake_select)
    monkeypatch.setattr(cli.ui, "confirmar", lambda *a, **k: True)
    monkeypatch.setattr(cli.ui, "print_note", lambda *a, **k: None)

    cli._submenu_sesion(tmp_path, {"session_id": sid, "preview": "x", "name": None})
    assert capt["options"][0] == "Borrar"
    assert capt["right"] is True   # → tambien vale en el submenu
    assert all(s["session_id"] != sid for s in session_store.list_sessions(tmp_path))


def test_confirmar_default_si_y_flecha_derecha(monkeypatch):
    from groq_agent import ui
    capt = {}

    def fake_select(pregunta, opciones, default=0, **k):
        capt["default"] = default
        capt["opciones"] = opciones
        capt["right"] = k.get("right_is_enter")
        return default   # simula elegir el que este por defecto

    monkeypatch.setattr(ui, "select", fake_select)
    # default_si=True -> cursor en la primera (Si), y Si es la primera opcion
    assert ui.confirmar("borrar?", si="Si, borrar", no="No",
                        default_si=True, right_is_enter=True) is True
    assert capt["default"] == 0
    assert capt["opciones"][0] == "Si, borrar"
    assert capt["right"] is True
    # por defecto (sin default_si) el cursor va en No -> False
    assert ui.confirmar("borrar?") is False
    assert capt["default"] == 1
