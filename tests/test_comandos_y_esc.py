"""Comandos guardados, autocorreccion de un tiro y parada con ESC."""
import pytest

from groq_agent import commands, interrupt
from groq_agent.agent_loop import _es_error, _mensaje_de_correccion


# --- comandos guardados ------------------------------------------------


@pytest.fixture(autouse=True)
def _aislar(tmp_path, monkeypatch):
    """Nunca tocar el config real del usuario en los tests."""
    monkeypatch.setattr(commands, "RUTA", tmp_path / "comandos.json")


def test_guardar_y_expandir():
    commands.guardar("web", "hazme una web para $1 en $2")
    assert commands.expandir("web", ["el bar lucio", "madrid"]) == (
        "hazme una web para el bar lucio en madrid"
    )


def test_arguments_junta_todo():
    commands.guardar("revisa", "revisa esto: $ARGUMENTS")
    assert commands.expandir("revisa", ["a.py", "b.py"]) == "revisa esto: a.py b.py"


def test_un_hueco_sin_argumento_queda_vacio_no_literal():
    """Dejar un '$1' crudo hace que el modelo lo interprete como texto."""
    commands.guardar("web", "web para $1 en $2")
    assert "$" not in commands.expandir("web", ["lucio"])


def test_dos_digitos_no_los_pisa_el_de_uno():
    commands.guardar("x", "$1 y $2 y $3")
    assert commands.expandir("x", ["a", "b", "c"]) == "a y b y c"


def test_expandir_algo_que_no_existe_devuelve_none():
    assert commands.expandir("inventado", []) is None


def test_no_se_aceptan_nombres_raros():
    assert commands.guardar("con espacios", "x").startswith("ERROR")
    assert commands.guardar("", "x").startswith("ERROR")


def test_no_se_acepta_plantilla_vacia():
    assert commands.guardar("x", "   ").startswith("ERROR")


def test_guardar_dos_veces_reemplaza_sin_duplicar():
    commands.guardar("web", "v1")
    commands.guardar("web", "v2")
    assert commands.listar() == {"web": "v2"}


def test_borrar():
    commands.guardar("web", "x")
    assert "borrado" in commands.borrar("web")
    assert commands.listar() == {}
    assert commands.borrar("web").startswith("ERROR")


def test_placeholders_dice_que_espera():
    assert commands.placeholders("web para $1 en $2") == ["$1", "$2"]
    assert commands.placeholders("revisa $ARGUMENTS") == ["$ARGUMENTS"]


def test_un_json_corrupto_no_revienta(tmp_path, monkeypatch):
    ruta = tmp_path / "comandos.json"
    ruta.write_text("{esto no es json", encoding="utf-8")
    monkeypatch.setattr(commands, "RUTA", ruta)
    assert commands.listar() == {}


# --- autocorreccion de un tiro ----------------------------------------


def test_detecta_los_errores_de_herramienta():
    assert _es_error("ERROR: no existe el archivo")
    assert not _es_error("archivo escrito correctamente")
    assert not _es_error("")


def test_la_correccion_incluye_los_argumentos_no_solo_el_error():
    """El patron a detectar casi siempre esta en los argumentos (la misma
    ruta mal escrita); sin ellos el modelo ve tres errores identicos sin
    saber que cambio entre uno y otro."""
    msg = _mensaje_de_correccion("edit_file", [
        ({"path": "estilo.css", "old_text": "body{}"}, "ERROR: no se encontro"),
        ({"path": "estilos.css", "old_text": "body{}"}, "ERROR: no existe"),
    ])
    assert "estilo.css" in msg
    assert "estilos.css" in msg
    assert "edit_file" in msg


def test_la_correccion_pide_cambiar_de_enfoque_no_reintentar():
    msg = _mensaje_de_correccion("edit_file", [({"path": "a"}, "ERROR: x")])
    bajo = msg.lower()
    assert "cambia de enfoque" in bajo
    assert "ask_user" in bajo, "tiene que ofrecer preguntar como salida"


# --- ESC ---------------------------------------------------------------


def test_sin_tty_no_se_vigila_el_teclado():
    """Leer stdin sin TTY robaria la entrada de una tuberia."""
    interrupt.dejar_de_vigilar()
    interrupt.empezar_a_vigilar()   # stdin no es tty en pytest
    assert interrupt.pedido() is False


def test_la_bandera_se_puede_poner_y_limpiar():
    interrupt.limpiar()
    assert not interrupt.pedido()
    interrupt._bandera.set()
    assert interrupt.pedido()
    interrupt.limpiar()
    assert not interrupt.pedido()


def test_la_nota_de_parada_dice_que_no_de_por_hecho_continuar():
    """Sin esto el modelo retoma su plan como si nada, justo cuando lo
    pararon para cambiarlo."""
    envuelto = interrupt.envolver_tras_parada("mejor hazlo en azul")
    bajo = envuelto.lower()
    assert "mejor hazlo en azul" in envuelto
    assert "no des por hecho" in bajo
    assert "esc" in bajo


# --- ESC devuelve el turno, no cierra el programa -----------------------
#
# Fallo real reportado: "le doy a ESC y se corta del todo y sale
# orquestador entero, no puedo seguir prompteando". Con una tarea lanzada
# desde la linea de comandos la lista de tareas tiene UN elemento, asi que
# al pararla se acababa el bucle y con el la sesion.


def test_tras_una_parada_se_devuelve_el_turno_al_usuario():
    from groq_agent import cli

    escritas = iter(["y ahora hazlo en azul", ""])
    cli._interactive_tasks = lambda: (t for t in escritas if t)

    estado = {"parada": False}
    salidas = []
    for tarea in cli._tareas_de_la_sesion(["hazme una web"], estado, interactivo=False):
        salidas.append(tarea)
        if len(salidas) == 1:
            estado["parada"] = True      # el usuario pulso ESC

    assert salidas == ["hazme una web", "y ahora hazlo en azul"]


def test_sin_parada_una_tarea_suelta_termina():
    """Lo de siempre: `orquestador "haz X"` hace X y sale."""
    from groq_agent import cli

    estado = {"parada": False}
    assert list(cli._tareas_de_la_sesion(["haz X"], estado, interactivo=False)) == ["haz X"]


def test_en_modo_interactivo_salir_sigue_saliendo():
    """Si el generador interactivo termino es porque el usuario escribio
    'salir' o una linea vacia - eso si es irse, aunque hubiera parada."""
    from groq_agent import cli

    estado = {"parada": True}
    assert list(cli._tareas_de_la_sesion(iter(["una tarea"]), estado, interactivo=True)) == ["una tarea"]
