"""Reparacion de tool_calls malformados.

Bug real (visto en vivo con web-builder-large construyendo 'Malta Norte'):
el modelo metio el JSON de ARGUMENTOS en el campo `name` del tool_call y dejo
`arguments` vacio, asi que el dispatch lo trataba como 'herramienta desconocida'
y NO escribia el index.html. `_reparar_tool_call` lo recompone: infiere la tool
por las claves del dict (path+content -> write_file).
"""
from groq_agent.agent_loop import _reparar_tool_call, _tool_por_claves
from groq_agent.tools import ToolExecutor, schemas_for


def _ex(tmp_path):
    return ToolExecutor(tmp_path, auto_yes=True)


def test_recupera_write_file_con_el_json_en_el_name(tmp_path):
    schemas = schemas_for("web-builder-specialist")
    name = '{"path": "malta-norte/index.html", "content": "<!DOCTYPE html>..."}'
    n, a = _reparar_tool_call(name, {}, _ex(tmp_path), schemas)
    assert n == "write_file"
    assert a["path"] == "malta-norte/index.html"
    assert a["content"] == "<!DOCTYPE html>..."


def test_no_toca_un_nombre_valido(tmp_path):
    schemas = schemas_for("web-builder-specialist")
    n, a = _reparar_tool_call("write_file", {"path": "x", "content": "y"}, _ex(tmp_path), schemas)
    assert n == "write_file" and a == {"path": "x", "content": "y"}


def test_nombre_desconocido_que_no_es_json_se_deja_igual(tmp_path):
    schemas = schemas_for("web-builder-specialist")
    n, a = _reparar_tool_call("herramienta_inventada", {}, _ex(tmp_path), schemas)
    assert n == "herramienta_inventada" and a == {}


def test_json_troceado_en_el_name_no_revienta(tmp_path):
    schemas = schemas_for("web-builder-specialist")
    roto = '{"path": "a.html", "content": "<div'  # sin cerrar
    n, a = _reparar_tool_call(roto, {}, _ex(tmp_path), schemas)
    assert n == roto and a == {}  # no se pudo reparar: se devuelve tal cual


def test_respeta_args_sueltos_validos_al_reparar(tmp_path):
    schemas = schemas_for("web-builder-specialist")
    name = '{"path": "a.html"}'
    # llega tambien un arg suelto valido en arguments: no se pisa
    n, a = _reparar_tool_call(name, {"content": "hola"}, _ex(tmp_path), schemas)
    assert n == "write_file"
    assert a == {"path": "a.html", "content": "hola"}


def test_tool_por_claves_prefiere_la_que_las_exige():
    schemas = schemas_for("web-builder-specialist")
    # path+content son los `required` de write_file
    assert _tool_por_claves({"path", "content"}, schemas) == "write_file"


def test_tool_por_claves_sin_coincidencia_devuelve_none():
    schemas = schemas_for("web-builder-specialist")
    assert _tool_por_claves({"clave_que_no_existe_en_ninguna"}, schemas) is None
