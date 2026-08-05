"""El selector de flechas es un ADORNO: nunca puede tumbar el programa.

Regresion de un fallo real: los estilos usaban nombres de color de rich
("grey58"), que prompt_toolkit no entiende, y la Application se construia
FUERA del try - asi que `orquestador -r` moria con un traceback en vez de
caer al comportamiento simple.
"""
import pytest

from groq_agent import ui


def test_los_colores_del_selector_los_entiende_prompt_toolkit():
    """prompt_toolkit solo acepta colores ANSI con nombre o hex. Este test
    falla en el momento en que alguien meta un 'grey58' de rich."""
    pt_style = pytest.importorskip("prompt_toolkit.styles")
    import inspect

    fuente = inspect.getsource(ui.select)
    ini = fuente.index("_PTStyle.from_dict({")
    fin = fuente.index("})", ini)
    bloque = fuente[ini:fin]

    estilos = {}
    for linea in bloque.splitlines():
        if ":" in linea and '"' in linea:
            partes = [p.strip().strip('",') for p in linea.split(":", 1)]
            if len(partes) == 2 and partes[0].strip('"'):
                estilos[partes[0].strip('"')] = partes[1]
    assert estilos, "no se pudo leer el bloque de estilos"

    # Si algun color no es valido, from_dict levanta ValueError - que es
    # exactamente lo que rompio en produccion.
    pt_style.Style.from_dict(estilos)


def test_select_sin_tty_devuelve_el_default_sin_bloquear(monkeypatch):
    """En una tuberia o en CI no hay nadie para pulsar una tecla: esperar
    una que nunca llega es peor que elegir lo razonable."""
    monkeypatch.setattr(ui.sys.stdin, "isatty", lambda: False, raising=False)
    assert ui.select("Titulo", ["a", "b", "c"], default=2) == 2


def test_select_con_lista_vacia_no_revienta():
    assert ui.select("Titulo", []) is None


def test_select_cae_al_default_si_prompt_toolkit_falla(monkeypatch):
    """Cualquier fallo construyendo o corriendo el menu tiene que degradar,
    nunca propagar: es un selector, no la tarea del usuario."""
    monkeypatch.setattr(ui, "_pt_select_broken", False)
    monkeypatch.setattr(ui.sys.stdin, "isatty", lambda: True, raising=False)

    import prompt_toolkit.application as pta

    def _explota(*a, **k):
        raise ValueError("Wrong color format 'grey58'")

    monkeypatch.setattr(pta, "Application", _explota)
    assert ui.select("Titulo", ["a", "b"], default=1) == 1


def test_errores_largos_se_reducen_a_su_frase_util():
    """Los proveedores devuelven el motivo dentro de un JSON; mostrar el
    objeto entero es el muro de texto que sobra."""
    largo = (
        'HTTP 402: {"message":"Payment required to access this resource. '
        'Visit your billing tab.","type":"payment_required_error",'
        '"param":"quota","code":"payment_required"}'
    )
    corto = ui._mensaje_util(largo)
    assert "Payment required" in corto
    assert "payment_required_error" not in corto
    assert len(corto) < len(largo)


@pytest.mark.parametrize(
    "crudo",
    [
        'HTTP 403: {"code":30001,"message":"Sorry, your balance is low","data":null}',
        'HTTP 402: {"error":{"message":"A payment method is required","type":"x"}}',
        'HTTP 404: {"detail":"model not found"}',
    ],
)
def test_mensaje_util_soporta_las_formas_de_error_de_cada_proveedor(crudo):
    corto = ui._mensaje_util(crudo)
    assert "{" not in corto, f"quedo JSON crudo: {corto}"


def test_mensaje_util_deja_pasar_un_texto_normal():
    assert ui._mensaje_util("No existe el skill 'x'") == "No existe el skill 'x'"


# --- la barra de estado tiene que estar SIEMPRE abajo -------------------
#
# Fallo reportado: "lo del contexto sigue sin funcionar como quiero, lo
# quiero siempre visible, abajo, y actualizandose". Estaba solo en el
# `bottom_toolbar` del prompt, que existe unicamente mientras el prompt
# esta abierto: desaparecia justo cuando hay algo que mirar, durante el
# trabajo del modelo.


def test_el_spinner_lleva_la_barra_pegada_debajo():
    from rich.console import Group

    from groq_agent import ui

    vivo = ui.spinner("pensando…")
    dibujo = vivo.get_renderable()

    assert isinstance(dibujo, Group), "el spinner solo ya no basta"
    assert len(dibujo.renderables) == 2, "spinner arriba, barra debajo"


def test_la_barra_del_spinner_se_redibuja_con_cifras_nuevas():
    """`get_renderable` se llama en cada refresco: si devolviera un texto
    fijo, el contador se quedaria congelado en el primer valor."""
    from groq_agent import ui

    class _Gasto:
        total, llamadas, tasa_cache = 1000, 1, 0.0

    gasto = _Gasto()
    ui.set_estado(5_000, gasto)
    vivo = ui.spinner("trabajando")
    primero = vivo.get_renderable().renderables[1].plain

    gasto.total, gasto.llamadas = 90_000, 7
    ui.set_estado(120_000, gasto)
    segundo = vivo.get_renderable().renderables[1].plain

    assert primero != segundo
    assert "90.000 tok" in segundo


def test_ya_no_se_imprime_una_copia_congelada_por_turno(capsys):
    """Una linea por vuelta llena el historial de contadores viejos, que es
    lo contrario de una barra permanente."""
    from groq_agent import ui

    ui.print_status_bar(12_345, None)

    assert capsys.readouterr().out == ""
