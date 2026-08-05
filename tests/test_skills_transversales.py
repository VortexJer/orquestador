"""Los tres especialistas transversales: testing, refactor y docs.

Cubren dominios que el router YA clasificaba bien pero que no tenian
especialista: `testing` y `refactor` caian en python-specialist (pedir
tests para Java daba el especialista de Python) y `docs` caia en
generalist-tiny, o sea documentacion tecnica escrita por el modelo mas
pequeño del catalogo.
"""
import pytest

from app.router.heuristic_router import DOMAIN_TO_SPECIALIST_HINT, classify_domain
from groq_agent.auto_router import model_for_specialist
from groq_agent.skills_loader import build_system_prompt, list_available_skills
from groq_agent.tools import tool_names_for

NUEVOS = ("testing-specialist", "refactor-specialist", "docs-specialist")


def test_ningun_dominio_se_queda_sin_especialista():
    """La comprobacion que destapo los tres huecos. Si vuelve a fallar es
    que se añadio un dominio sin su skill."""
    skills = set(list_available_skills())
    huerfanos = {d: e for d, e in DOMAIN_TO_SPECIALIST_HINT.items() if e not in skills}
    assert not huerfanos, f"dominios que apuntan a un skill inexistente: {huerfanos}"


@pytest.mark.parametrize("nombre", NUEVOS)
def test_el_skill_existe_y_se_puede_construir_su_prompt(nombre):
    assert nombre in list_available_skills()
    assert len(build_system_prompt(nombre)) > 1000


@pytest.mark.parametrize(
    ("tarea", "esperado"),
    [
        ("escribe los tests para validators.py", "testing-specialist"),
        ("necesito unit tests de esta clase", "testing-specialist"),
        ("refactoriza este codigo", "refactor-specialist"),
        ("elimina duplicacion en el modulo", "refactor-specialist"),
        ("escribe un readme del proyecto", "docs-specialist"),
        ("documenta estas funciones con docstrings", "docs-specialist"),
    ],
)
def test_las_tareas_llegan_al_especialista_correcto(tarea, esperado):
    dominio, _ = classify_domain(tarea)
    assert DOMAIN_TO_SPECIALIST_HINT[dominio] == esperado


def test_docs_ya_no_cae_en_el_modelo_mas_chico():
    """Escribir la documentacion tecnica de un proyecto con el tier de
    clasificacion era el peor emparejamiento del catalogo."""
    assert model_for_specialist("docs-specialist") != "router-tiny"


def test_refactor_usa_el_tier_agentico():
    """Refactorizar es leer mucho (localizar usos, comprobar dependencias)
    antes de escribir poco: es exploracion con herramientas, que es lo que
    distingue a ese tier."""
    assert model_for_specialist("refactor-specialist") == "devstral-agentic"


@pytest.mark.parametrize("nombre", ("testing-specialist", "refactor-specialist"))
def test_testing_y_refactor_pueden_verificar_codigo(nombre):
    nombres = tool_names_for(nombre)
    assert "run_check" in nombres, "sin run_check no pueden comprobar nada"
    assert "grep_search" in nombres, "necesitan localizar usos antes de tocar"


def test_docs_no_recibe_herramientas_de_ejecucion():
    """Documentar no ejecuta nada; darle run_check solo invita a usarlo."""
    assert "run_check" not in tool_names_for("docs-specialist")


@pytest.mark.parametrize("nombre", NUEVOS)
def test_avisan_de_que_run_check_es_solo_python(nombre):
    """Son transversales pero la herramienta solo cubre Python. Prometer
    que se ejecutaron unos tests de Java seria mentir sobre el resultado."""
    texto = build_system_prompt(nombre).lower()
    if nombre == "docs-specialist":
        pytest.skip("docs no ejecuta nada")
    assert "solo" in texto and "python" in texto


@pytest.mark.parametrize("nombre", NUEVOS)
def test_mantienen_la_convencion_de_la_casa(nombre):
    texto = build_system_prompt(nombre)
    # Normalizar espacios: los skills van con las lineas cortadas a ~72
    # columnas, asi que una frase puede quedar partida por un salto.
    plano = " ".join(texto.lower().split())
    assert "sistema de orquestacion" in plano
    assert "formato de salida" in plano
    # La regla de emojis del sistema llega por el preambulo compartido.
    assert "emoji" in plano
