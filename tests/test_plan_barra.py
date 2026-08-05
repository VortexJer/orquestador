"""El plan vive en la barra PERMANENTE (donde el gasto/contexto), condensado a
una linea, y DESAPARECE al completarse. Lo pidio asi: "que lo del plan aparezca
donde esta lo de los tokens que es permanente, y que cuando se complete
desaparezca"."""
from types import SimpleNamespace

import pytest

from groq_agent import ui
from groq_agent.tools import ToolExecutor


@pytest.fixture(autouse=True)
def _sin_estado_colgando():
    """El plan/gasto son estado GLOBAL del modulo ui; sin limpiar despues de
    cada test se filtraria a otros modulos (p.ej. la barra del spinner)."""
    yield
    ui.set_plan([])
    ui.set_estado(0, None)
    ui.set_modo_auto(False)
    ui._crono_inicio = None
    ui._crono_fin = None


def test_modo_arranca_en_confirma_y_shift_tab_alterna():
    """shift+tab (que llama a alternar_modo) va CONFIRMA <-> AUTO."""
    ui.set_modo_auto(False)
    assert ui.modo_auto() is False
    assert ui.alternar_modo() is True and ui.modo_auto() is True
    assert ui.alternar_modo() is False and ui.modo_auto() is False


def test_texto_estado_muestra_el_modo():
    """El modo es tan permanente como los tokens: siempre en la barra."""
    ui.set_estado(500, SimpleNamespace(total=1000, tasa_cache=0.0, llamadas=2, entrada=1, salida=1))
    ui.set_modo_auto(False)
    assert "CONFIRMA" in ui.texto_estado(ancho=120)
    ui.set_modo_auto(True)
    assert "AUTO" in ui.texto_estado(ancho=120)


def test_cronometro_vivo_y_congelado():
    """Sin arrancar no aparece; corriendo cuenta; parado se congela."""
    ui._crono_inicio = None
    ui._crono_fin = None
    assert ui._texto_cronometro() == ""          # nunca arranco
    ui._crono_inicio = 100.0                       # como si iniciar_cronometro
    ui._crono_fin = 142.0                           # ...y parar_cronometro
    assert ui._texto_cronometro() == "42s"         # congelado en 42s
    ui._crono_fin = 100.0 + 83                      # 1m23s
    assert ui._texto_cronometro() == "1m23s"


def test_texto_estado_incluye_el_cronometro_congelado():
    ui.set_estado(500, SimpleNamespace(total=1000, tasa_cache=0.0, llamadas=2, entrada=1, salida=1))
    ui._crono_inicio, ui._crono_fin = 0.0, 12.0
    assert "12s" in ui.texto_estado(ancho=120)


def _limpiar():
    ui.set_plan([])
    ui.set_estado(0, None)


def test_set_plan_guarda_uno_a_medias():
    _limpiar()
    ui.set_plan([{"paso": "a", "estado": "hecho"}, {"paso": "b", "estado": "en_curso"}])
    assert len(ui._plan_actual) == 2


def test_set_plan_todo_hecho_desaparece():
    _limpiar()
    ui.set_plan([{"paso": "a", "estado": "hecho"}, {"paso": "b", "estado": "done"}])
    assert ui._plan_actual == []          # plan cumplido -> se borra solo


def test_set_plan_vacio_limpia():
    _limpiar()
    ui.set_plan([{"paso": "a", "estado": "en_curso"}])
    ui.set_plan([])
    assert ui._plan_actual == []


def test_texto_estado_muestra_el_plan_JUNTO_al_gasto():
    """El plan convive con los tokens, no los tapa: los tokens son permanentes."""
    _limpiar()
    ui.set_estado(500, SimpleNamespace(total=90000, tasa_cache=0.0, llamadas=7, entrada=1, salida=1))
    ui.set_plan([
        {"paso": "Investigar el negocio", "estado": "hecho"},
        {"paso": "Escribir el index.html", "estado": "en_curso"},
        {"paso": "Verificar el render", "estado": "pendiente"},
    ])
    barra = ui.texto_estado(ancho=120)
    assert "◆ 1/3" in barra
    assert "▶ Escribir el index.html" in barra   # el paso en curso a la vista
    assert "90.000 tok" in barra                 # y los tokens SIGUEN ahi


def test_al_completarse_vuelve_el_gasto():
    _limpiar()
    ui.set_estado(500, SimpleNamespace(total=1234, tasa_cache=0.0, llamadas=3, entrada=1, salida=1))
    ui.set_plan([{"paso": "a", "estado": "hecho"}])   # todo hecho -> se limpia
    barra = ui.texto_estado(ancho=120)
    assert "1.234 tok" in barra                       # vuelve el contador
    assert "◆" not in barra


def test_resumen_plan_se_recorta_al_ancho():
    _limpiar()
    ui.set_plan([{"paso": "Un paso larguisimo " * 20, "estado": "en_curso"}])
    linea = ui._resumen_plan(40)
    assert len(linea) <= 40 and linea.endswith("…")


def test_primer_pendiente_si_no_hay_en_curso():
    _limpiar()
    ui.set_plan([
        {"paso": "hecha", "estado": "hecho"},
        {"paso": "la siguiente", "estado": "pendiente"},
    ])
    assert "▶ la siguiente" in ui._resumen_plan(120)


def test_load_skill_section_no_vuelca_la_guia_cruda():
    """La seccion del skill (markdown de miles de chars) NO debe dumpearse en la
    terminal recortada a 300 chars: se resume. El modelo si la recibe (mensaje
    tool), esto es solo lo que ve el humano en el scrollback."""
    guia = "## 2. Sistema de diseño\n### 2.1 Color\n- Estructura minima: ..." * 30
    linea = ui._renderable_resultado("load_skill_section", {"section": "2. Sistema de diseño"}, guia[:300])
    assert "Sistema de diseño" in linea.plain          # se nombra la seccion...
    assert "Guia del skill consultada" in linea.plain  # ...con la frase de resumen
    assert "### 2.1 Color" not in linea.plain           # ...NO el volcado crudo


def test_con_barra_pega_el_estado_bajo_el_frame():
    """La barra permanente viaja pegada a los frames de animate_dispatch para
    que NO parpadee mientras se ejecutan herramientas (aparecia solo en el
    spinner de 'pensando', se iba al escribir archivos). `_con_barra` compone el
    frame con la barra debajo; su ultima linea tiene que ser el estado vivo."""
    from rich.text import Text

    _limpiar()
    ui.set_estado(500, SimpleNamespace(total=42000, tasa_cache=0.0, llamadas=4, entrada=1, salida=1))
    grupo = ui._con_barra(Text("  escribiendo…"))
    partes = list(grupo.renderables)
    assert len(partes) == 2                       # frame + barra
    assert "42.000 tok" in partes[1].plain        # la barra vive es el ultimo elemento


def test_animate_dispatch_plan_no_vuelca_scrollback_pero_llena_la_barra(tmp_path, monkeypatch):
    _limpiar()
    lineas = []
    monkeypatch.setattr(ui.console, "print", lambda *a, **k: lineas.append(a[0] if a else ""))
    ex = ToolExecutor(tmp_path, auto_yes=True)
    ui.animate_dispatch("plan", {"pasos": [
        {"paso": "uno", "estado": "en_curso"}, {"paso": "dos", "estado": "pendiente"},
    ]}, ex, root=tmp_path)
    # NO se imprimio el bloque multilinea "◆ plan" en el scrollback...
    assert not any("◆ plan" in str(x) for x in lineas)
    # ...pero el plan quedo en la barra
    assert "◆ 0/2" in ui.texto_estado(ancho=120)
