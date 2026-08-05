"""Invariante estructural del enrutamiento: TODO dominio del enum `Domain`
(salvo `multi`, que no tiene un especialista propio) tiene que estar
cableado de punta a punta. Sin este test, agregar un dominio nuevo al enum
y olvidarse de una de las piezas es un fallo SILENCIOSO:

  - falta la glosa en auto_router._DOMAIN_GLOSS -> el router-tiny nunca ve
    esa etiqueta en su prompt y JAMAS puede elegir ese especialista (bug
    real: code_review/debugging/accessibility estaban en el enum, tenian
    especialista y regex, pero no glosa -> 3 de 26 especialistas eran
    inalcanzables desde la terminal en produccion, y nada lo delataba);
  - falta la entrada en DOMAIN_TO_SPECIALIST_HINT -> cae al fallback;
  - el especialista apuntado no existe en specialists.yaml -> KeyError en
    caliente;
  - falta el patron regex -> el router heuristico (fallback y baseline de
    eval) nunca clasifica ahi.

Este test convierte los 4 fallos silenciosos en un fallo ruidoso de CI.
"""
from __future__ import annotations

from typing import get_args

import pytest

from app.config import load_catalog
from app.router.heuristic_router import DOMAIN_TO_SPECIALIST_HINT, _DOMAIN_PATTERNS
from app.schemas import Domain
from groq_agent.auto_router import (
    _DOMAIN_GLOSS,
    _VALID_DOMAINS,
    _domain_list_con_glosas,
)

# `multi` es el unico dominio sin especialista dedicado: significa "la tarea
# mezcla varios dominios", el orquestador lo descompone, no lo enruta a un
# skill. Todo lo demas SI tiene que estar cableado.
_SIN_ESPECIALISTA = {"multi"}
ROUTABLE_DOMAINS = [d for d in get_args(Domain) if d not in _SIN_ESPECIALISTA]

_HEURISTIC_DOMAINS = {domain for domain, _pattern in _DOMAIN_PATTERNS}


def test_el_enum_y_los_dominios_validos_del_router_coinciden():
    """_VALID_DOMAINS (lo que el router-tiny puede devolver) ES el enum, sin
    deriva. Si divergen, el resto de asserts miden la cosa equivocada."""
    assert set(_VALID_DOMAINS) == set(get_args(Domain))


@pytest.mark.parametrize("domain", ROUTABLE_DOMAINS)
def test_cada_dominio_tiene_glosa_para_el_router_llm(domain):
    """Sin glosa, el dominio no aparece en el prompt del router-tiny y su
    especialista es inalcanzable en produccion."""
    assert domain in _DOMAIN_GLOSS, (
        f"'{domain}' no tiene glosa en _DOMAIN_GLOSS: el router-tiny nunca lo "
        f"ofrecera y su especialista sera inalcanzable"
    )


@pytest.mark.parametrize("domain", ROUTABLE_DOMAINS)
def test_cada_dominio_aparece_en_el_prompt_del_clasificador(domain):
    """La prueba de fuego end-to-end: la etiqueta sale REALMENTE en el texto
    que se le manda al modelo (no basta con estar en el dict si el render la
    filtra)."""
    prompt_lines = _domain_list_con_glosas()
    assert f"- {domain}:" in prompt_lines, (
        f"'{domain}' no se renderiza en el prompt del clasificador"
    )


@pytest.mark.parametrize("domain", ROUTABLE_DOMAINS)
def test_cada_dominio_mapea_a_un_especialista(domain):
    assert domain in DOMAIN_TO_SPECIALIST_HINT, (
        f"'{domain}' no tiene entrada en DOMAIN_TO_SPECIALIST_HINT"
    )


@pytest.mark.parametrize("domain", ROUTABLE_DOMAINS)
def test_el_especialista_apuntado_existe_en_el_catalogo(domain):
    """Un hint que apunta a un nombre que no esta en specialists.yaml
    revienta con KeyError en get_specialist() en caliente."""
    specialist = DOMAIN_TO_SPECIALIST_HINT[domain]
    catalogo = load_catalog()["specialists"]
    assert specialist in catalogo, (
        f"'{domain}' -> '{specialist}', que no existe en specialists.yaml"
    )


@pytest.mark.parametrize("domain", ROUTABLE_DOMAINS)
def test_cada_dominio_tiene_patron_en_el_router_heuristico(domain):
    """El heuristico es el fallback y el baseline de eval: un dominio sin
    patron nunca se clasifica ahi por regex (solo por default a python)."""
    assert domain in _HEURISTIC_DOMAINS, (
        f"'{domain}' no tiene patron regex en _DOMAIN_PATTERNS"
    )


def test_no_hay_glosas_huerfanas():
    """Al reves: una glosa para un dominio que ya no esta en el enum es
    codigo muerto que confunde al modelo con una opcion imposible."""
    huerfanas = set(_DOMAIN_GLOSS) - set(get_args(Domain))
    assert not huerfanas, f"glosas sin dominio en el enum: {huerfanas}"


def test_no_hay_hints_huerfanos():
    huerfanos = set(DOMAIN_TO_SPECIALIST_HINT) - set(get_args(Domain))
    assert not huerfanos, f"hints sin dominio en el enum: {huerfanos}"


# --- Prueba de comportamiento end-to-end del camino del router LLM --------
#
# Los asserts de arriba comprueban las PIEZAS (glosa, hint, patron). Este
# comprueba el RESULTADO observable: metido cada dominio por auto_route_llm
# (el router de produccion de la terminal), sale el especialista correcto.
# Es la prueba que habria cazado en rojo el bug original: con code_review/
# debugging/accessibility fuera de la glosa, el modelo real nunca devolvia
# esas etiquetas y su especialista era inalcanzable - aqui lo forzamos con
# un cliente falso para verificar que el CABLEADO (no el juicio del modelo)
# lleva cada etiqueta a su skill.


class _ClienteQueDevuelve:
    """Cliente falso que responde siempre la etiqueta de dominio dada, como
    haria el router-tiny si clasificara ese dominio."""

    def __init__(self, etiqueta: str):
        self._etiqueta = etiqueta

    def chat(self, messages, model=None, temperature=None, **kwargs):
        return {"choices": [{"message": {"content": self._etiqueta}}]}


@pytest.mark.parametrize("domain", ROUTABLE_DOMAINS)
def test_cada_dominio_enruta_a_su_especialista_por_el_camino_llm(domain):
    """Si el router-tiny clasifica <domain>, auto_route_llm devuelve el
    especialista que le corresponde - incluidos los 3 (code_review,
    debugging, accessibility) que antes eran inalcanzables."""
    from groq_agent.auto_router import auto_route_llm

    routed = auto_route_llm(_ClienteQueDevuelve(domain), "una tarea cualquiera")
    assert routed is not None and not isinstance(routed, str), (
        f"'{domain}' no produjo un enrutamiento (salio charla/continuacion)"
    )
    specialist, _model, detected = routed
    assert detected == domain
    assert specialist == DOMAIN_TO_SPECIALIST_HINT[domain], (
        f"'{domain}' enruto a '{specialist}', se esperaba "
        f"'{DOMAIN_TO_SPECIALIST_HINT[domain]}'"
    )


@pytest.mark.parametrize("domain", ["code_review", "debugging", "accessibility"])
def test_los_tres_dominios_antes_inalcanzables_ahora_salen_en_el_prompt(domain):
    """Regresion explicita del bug corregido: estos 3 estaban en el enum,
    tenian especialista y patron, pero faltaban en la glosa -> el modelo
    nunca los veia. Este test falla si alguien vuelve a quitar la glosa."""
    assert domain in _DOMAIN_GLOSS
    assert f"- {domain}:" in _domain_list_con_glosas()
