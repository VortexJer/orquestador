"""Bateria de enrutamiento POR ESPECIALISTA (gratis, local, 0 tokens).

Muchos prompts naturales por cada dominio routable (eval/router_eval_
specialists.json). El router es HIBRIDO: el heuristico propone una PISTA
(match_domain_or_none) y el LLM confirma/corrige. Asi que lo que se puede
comprobar sin gastar un token de proveedor es la parte del heuristico, y
lo que importa de esa parte es LA PRECISION DE LA PISTA:

  - pista = dominio esperado  -> señal fuerte, el heuristico ya acierta.
  - pista = None              -> INOCUO: no vio señal y no propone nada; en
                                 produccion decide el LLM con su glosa. Es el
                                 caso normal de los dominios que se distinguen
                                 por intencion, no por una palabra clave
                                 (office narrativo, debugging, refactor...).
  - pista = OTRO dominio      -> BUG: mete al LLM un prior falso. Cero
                                 tolerancia, salvo los pocos casos de
                                 ambiguedad multi-lenguaje documentados abajo.

Disciplina de la casa (feedback del usuario): un misrouting que decide la
IA se arregla en la GLOSA del router LLM, NUNCA ampliando el regex del
heuristico. Por eso aqui NO se exige que el heuristico clasifique cada
frase natural (eso empujaria a inflar el regex): se exige que no MIENTA.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from app.router.heuristic_router import match_domain_or_none

_DATASET = Path(__file__).resolve().parents[1] / "eval" / "router_eval_specialists.json"
_CASES = json.loads(_DATASET.read_text(encoding="utf-8"))["cases"]

_MIN_POR_DOMINIO = 8

# Prompts donde dos lenguajes comparten palabras clave y el heuristico no
# puede desempatar sin semantica: "una interface EN Go" (interface tambien
# es de TypeScript/Java), "convierte este codigo Java A Kotlin" (nombra los
# dos; el destino es el que cuenta). La pista queda imperfecta A PROPOSITO:
# NO se infla el regex para esto - lo resuelve la glosa del LLM, que si
# entiende "en Go" y "a Kotlin". Se listan para que la limitacion sea
# VISIBLE, no un fallo escondido subiendo un umbral.
_AMBIGUO_MULTILENGUAJE = {"go-07", "kt-04"}

# Dominios que el heuristico SI debe reconocer por su palabra clave (el
# nombre del lenguaje, o un termino inequivoco). Para estos se exige recall,
# no solo precision. Se excluyen a proposito: go (su nombre es demasiado
# corto/ambiguo, depende del LLM - decision del autor), y los dominios que
# se distinguen por INTENCION y no por una palabra (testing, refactor, docs,
# code_review, debugging, office_*, trivial_text).
_DOMINIOS_CON_SEÑAL_FUERTE = {
    "python", "typescript", "javascript", "java", "csharp", "cpp", "sql",
    "rust", "php", "ruby", "kotlin", "security", "iac", "web",
    "accessibility", "research",
}


def _hint(task: str) -> str | None:
    return match_domain_or_none(task)


@pytest.mark.parametrize(
    "case",
    [c for c in _CASES if c["id"] not in _AMBIGUO_MULTILENGUAJE],
    ids=[c["id"] for c in _CASES if c["id"] not in _AMBIGUO_MULTILENGUAJE],
)
def test_el_heuristico_nunca_da_una_pista_equivocada(case):
    """Precision: la pista del heuristico es el dominio esperado o None,
    jamas un tercer dominio (eso arrastraria al LLM a lo que no es)."""
    hint = _hint(case["task"])
    assert hint in (case["expected_domain"], None), (
        f"[{case['id']}] PISTA EQUIVOCADA: esperado={case['expected_domain']} "
        f"hint={hint}\n  tarea: {case['task']}\n  (arreglar quitando el falso "
        f"positivo del regex o, si es ambiguedad multi-lenguaje real, documentarlo "
        f"en _AMBIGUO_MULTILENGUAJE y dejarselo a la glosa del LLM)"
    )


def test_los_casos_ambiguos_multilenguaje_estan_documentados():
    """Los pocos casos ambiguos existen y su pista imperfecta la corrige el
    LLM: aqui solo se fija que siguen siendo un puñado y que caen en un
    dominio RELACIONADO (un lenguaje), no en cualquier cosa."""
    assert len(_AMBIGUO_MULTILENGUAJE) <= 3, (
        "crecen los casos que el heuristico no puede desempatar: revisa si es "
        "ambiguedad real o un regex que se puede afinar sin inflarlo."
    )
    ids = {c["id"] for c in _CASES}
    assert _AMBIGUO_MULTILENGUAJE <= ids, "un id documentado ya no existe en la bateria"


def test_cada_dominio_tiene_muchos_prompts():
    """'Para cada especialista muchos prompts': cobertura minima por dominio."""
    por_dominio = Counter(c["expected_domain"] for c in _CASES)
    flojos = {d: n for d, n in por_dominio.items() if n < _MIN_POR_DOMINIO}
    assert not flojos, (
        f"dominios con menos de {_MIN_POR_DOMINIO} prompts: {flojos}. "
        "Un especialista con un solo prompt de prueba no esta cubierto."
    )


@pytest.mark.parametrize("dominio", sorted(_DOMINIOS_CON_SEÑAL_FUERTE))
def test_el_patron_de_cada_dominio_con_señal_fuerte_dispara(dominio):
    """Anti-regresion del REGEX (no del fraseo de los prompts): un dominio con
    palabra clave propia (el nombre del lenguaje, 'wcag', 'huella digital'...)
    tiene que pillar AL MENOS un par de sus prompts. Si cae a cero es que su
    patron se rompio (paso real: un patron con acentos mal escritos compilaba
    pero no matcheaba NUNCA). No se exige mayoria: muchos prompts se escriben
    en lenguaje llano a proposito y esos los resuelve la glosa del LLM, no el
    regex - contarlos como fallo empujaria justo a inflar el patron."""
    casos = [c for c in _CASES if c["expected_domain"] == dominio]
    exactos = sum(1 for c in casos if _hint(c["task"]) == dominio)
    assert exactos >= 2, (
        f"'{dominio}': el heuristico NO acerto ninguno (o casi) de sus "
        f"{len(casos)} prompts con palabra clave -> su patron esta roto."
    )
