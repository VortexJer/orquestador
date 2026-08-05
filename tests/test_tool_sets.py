"""El filtrado de herramientas por especialista.

Los esquemas de las 35 tools son ~31.000 caracteres (~7.700 tokens) que
viajaban en cada llamada de todos los especialistas. Estos tests fijan las
dos garantias del filtro: que nadie recibe herramientas que no puede usar,
y - mas importante - que nadie se queda sin las que necesita.
"""
import json

import pytest
import yaml

from groq_agent.tools import TOOL_SCHEMAS, schemas_for, tool_names_for
from groq_agent.skills_loader import SKILLS_DIR

CFG = yaml.safe_load(
    (SKILLS_DIR.parent / "config" / "tool_sets.yaml").read_text(encoding="utf-8")
)
TODOS = {t["function"]["name"] for t in TOOL_SCHEMAS}


def test_toda_tool_esta_en_algun_set():
    """Una tool que no este en ningun set NO se le entrega a nadie. Mejor
    que salte aca que descubrirlo porque un especialista no puede hacer su
    trabajo."""
    en_sets = {n for grupo in CFG["sets"].values() for n in grupo}
    huerfanas = TODOS - en_sets
    assert not huerfanas, (
        f"tools sin set (nadie las recibe): {sorted(huerfanas)}. "
        "Añadilas a config/tool_sets.yaml."
    )


def test_ningun_set_menciona_una_tool_inexistente():
    """Un nombre mal escrito en el yaml se traduce en una herramienta que
    el especialista cree tener y no tiene."""
    en_sets = {n for grupo in CFG["sets"].values() for n in grupo}
    fantasmas = en_sets - TODOS
    assert not fantasmas, f"el yaml menciona tools que no existen: {sorted(fantasmas)}"


def test_los_especialistas_referencian_sets_reales():
    familias = set(CFG["sets"])
    for especialista, usados in CFG["especialistas"].items():
        desconocidos = set(usados or []) - familias
        assert not desconocidos, f"'{especialista}' usa sets inexistentes: {desconocidos}"


@pytest.mark.parametrize("especialista", sorted(CFG["especialistas"]))
def test_todos_reciben_el_nucleo(especialista):
    """Sin leer ni escribir archivos no hay agente. Ni el tier mas chico
    puede quedarse sin eso."""
    nombres = tool_names_for(especialista)
    for imprescindible in ("read_file", "write_file", "edit_file", "ask_user"):
        assert imprescindible in nombres, f"'{especialista}' se quedo sin {imprescindible}"


def test_el_especialista_de_codigo_no_recibe_herramientas_de_web():
    nombres = tool_names_for("python-specialist")
    for ajena in ("search_images", "verificar_web", "fetch_business_from_maps",
                  "read_reference_component"):
        assert ajena not in nombres


def test_el_de_webs_si_recibe_lo_suyo():
    """El contrapeso: si el filtro fuera demasiado agresivo, el test de
    arriba pasaria igual y web-builder no podria trabajar."""
    nombres = tool_names_for("web-builder-specialist")
    for propia in ("verificar_web", "search_images",
                   "list_reference_components", "fetch_business_from_maps"):
        assert propia in nombres, f"web-builder se quedo sin {propia}"


def test_un_especialista_desconocido_recibe_todas():
    """Añadir un especialista nuevo y olvidar configurarlo cuesta tokens;
    dejarlo mudo costaria que no funcione y sin explicacion."""
    assert tool_names_for("especialista-inventado") is None
    assert schemas_for("especialista-inventado") == TOOL_SCHEMAS


def test_el_filtrado_ahorra_de_verdad():
    completo = len(json.dumps(TOOL_SCHEMAS))
    for especialista in ("python-specialist", "office-email-specialist", "generalist-tiny"):
        filtrado = len(json.dumps(schemas_for(especialista)))
        ahorro = (completo - filtrado) / completo
        assert ahorro > 0.4, (
            f"'{especialista}' solo ahorra {ahorro:.0%} - revisa si sus sets "
            "crecieron de mas"
        )


def test_goldentokens_estan_en_el_nucleo():
    """Leer un archivo grande o un JSON pasa en cualquier dominio, no solo
    en web o en codigo."""
    for especialista in ("python-specialist", "web-builder-specialist", "generalist-tiny"):
        nombres = tool_names_for(especialista)
        assert "peek_file" in nombres
        assert "query_json" in nombres


def test_las_tres_de_verificacion_no_se_ofrecen_sueltas():
    """`verificar_web` existe porque el skill pedia render_check +
    lint_web_page + critique_screenshot JUNTAS y, en las sesiones reales,
    el modelo hacia UNA llamada por turno las 7 veces que se midio. Cada
    turno de mas reenvia la conversacion entera. Si vuelven al catalogo,
    vuelve el problema."""
    for suelta in ("render_check", "critique_screenshot", "lint_web_page"):
        assert suelta not in TODOS, (
            f"{suelta} volvio a ofrecerse suelta: el modelo la llamara en su "
            "propio turno en vez de agrupar. Usa verificar_web."
        )
