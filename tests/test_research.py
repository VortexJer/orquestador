"""El especialista de investigacion (OSINT) y la busqueda mejorada.

Cubre lo que hace la investigacion PRODUCTIVA y, sobre todo, lo que evita
FUNDIR a dos personas con el mismo nombre (homonimos):

  - deep_research en varios angulos (`consultas`): junta y deduplica.
  - anclas de desambiguacion (`terminos_clave`): cada fuente dice que rasgos
    del sujeto confirma; una fuente sin ninguno es un candidato distinto.
  - rerank de candidatos por relevancia antes de leer (idea de Perplexica).
  - extraccion del contenido principal (trafilatura) con degradado seguro.
  - el patron heuristico del dominio `research` (y que NO se traga un fallo
    de codigo: "investiga por que falla este import de python" -> python).

Todo sin red: se monkeypatchea la busqueda y el fetch (que sigue siendo el
SEGURO, con filtro SSRF - eso se prueba aparte en test_web_tools)."""
from __future__ import annotations

import app.tools.deep_research as dr
from app.tools.deep_research import (
    _anclas_presentes,
    _rerank_candidatos,
    deep_research,
)

# --- Sujeto de prueba: dos "Juan Perez" distintos (el homonimo clasico) ---

_SUBJECT = "https://es.linkedin.com/in/juanperez"
_HOMONIMO = "https://futbol.example/juan-perez"

_PAGINAS = {
    _SUBJECT: (
        "<html><head><title>Juan Perez | LinkedIn</title></head><body>"
        "<nav>Inicio Empleos Mensajes Notificaciones</nav>"
        "<article><h1>Juan Perez</h1>"
        "<p>Juan Perez es ingeniero de software en Madrid y trabaja en la "
        "empresa Acme Robotics desde 2015 liderando el equipo de backend.</p>"
        "<p>Juan Perez estudio Teleco en la Politecnica de Madrid y da charlas "
        "sobre sistemas distribuidos en conferencias del sector.</p></article>"
        "<footer>Aviso legal y cookies de LinkedIn</footer></body></html>"
    ),
    _HOMONIMO: (
        "<html><head><title>Juan Perez, delantero</title></head><body>"
        "<article><h1>Juan Perez</h1>"
        "<p>Juan Perez es un futbolista del Sevilla nacido en 1998 que juega "
        "de delantero centro y ha marcado quince goles esta temporada.</p>"
        "<p>Juan Perez debuto en el filial y aspira a jugar en la seleccion "
        "sub-21 el proximo verano segun su entrenador.</p></article>"
        "</body></html>"
    ),
}


def _instalar_fakes(monkeypatch, resultados_por_consulta):
    """Monkeypatchea search_web (segun la consulta) y fetch_seguro (segun la
    url) para que deep_research no toque la red."""

    def fake_search(query, count=5, engine="duckduckgo"):
        for aguja, urls in resultados_por_consulta.items():
            if aguja in query:
                return {"engine": "fake", "error": None, "results": [
                    {"url": u, "title": "Juan Perez", "snippet": "Juan Perez"} for u in urls
                ]}
        return {"engine": "fake", "error": None, "results": []}

    def fake_fetch(url, *args, **kwargs):
        html = _PAGINAS.get(url)
        if html is None:
            return {"ok": False, "content": b"", "url_final": url}
        return {"ok": True, "content": html.encode("utf-8"), "url_final": url}

    # search_web se importa dentro de deep_research desde su modulo de origen.
    monkeypatch.setattr("app.tools.web_search.search_web", fake_search)
    # fetch_seguro esta ligado en el namespace de deep_research (import arriba).
    monkeypatch.setattr(dr, "fetch_seguro", fake_fetch)


def test_multi_angulo_junta_y_deduplica(monkeypatch):
    """Varias consultas de angulo -> una sola llamada que junta y deduplica
    las fuentes, y registra TODAS las consultas usadas."""
    _instalar_fakes(monkeypatch, {
        "Juan Perez": [_SUBJECT, _HOMONIMO],   # la pregunta base trae los dos
        "linkedin": [_SUBJECT],                # este angulo repite el sujeto
    })
    r = deep_research(
        "Juan Perez",
        consultas=['"Juan Perez" Madrid ingeniero', '"Juan Perez" site:linkedin.com'],
        terminos_clave=["Madrid", "ingeniero", "Acme"],
        profundidad=1, max_paginas=6,
    )
    assert r["error"] is None
    # Las 3 consultas (pregunta + 2 angulos) quedan registradas.
    assert r["consultas"] == [
        "Juan Perez",
        '"Juan Perez" Madrid ingeniero',
        '"Juan Perez" site:linkedin.com',
    ]
    urls = [f["url"] for f in r["fuentes"]]
    assert _SUBJECT in urls and _HOMONIMO in urls
    assert len(urls) == len(set(urls))  # el sujeto salio en 2 consultas -> 1 sola vez


def test_anclas_separan_al_sujeto_del_homonimo(monkeypatch):
    """La fuente del sujeto confirma sus rasgos; la del homonimo no confirma
    NINGUNO -> es la señal para no fundirlos."""
    _instalar_fakes(monkeypatch, {"Juan Perez": [_SUBJECT, _HOMONIMO]})
    r = deep_research(
        "Juan Perez", terminos_clave=["Madrid", "ingeniero", "Acme"],
        profundidad=1, max_paginas=6,
    )
    por_url = {f["url"]: f for f in r["fuentes"]}
    # El sujeto real menciona sus rasgos.
    assert set(por_url[_SUBJECT]["anclas"]) >= {"Madrid", "ingeniero"}
    # El homonimo (mismo nombre, futbolista) no confirma ninguna ancla.
    assert por_url[_HOMONIMO]["anclas"] == []


def test_anclas_presentes_es_case_insensitive():
    assert _anclas_presentes(["Vive en MADRID y es Ingeniero"], ["madrid", "abogado"]) == ["madrid"]
    assert _anclas_presentes(["texto"], []) == []


def test_rerank_pone_primero_lo_mas_relevante():
    """Se lee primero la fuente que mas solapa y que menciona una ancla, no la
    que llego antes en la lista."""
    claves = {"juan", "perez", "ingeniero", "madrid"}
    candidatos = [
        {"url": "u_ruido", "title": "Noticias varias", "snippet": "deporte y ocio"},
        {"url": "u_bueno", "title": "Juan Perez ingeniero", "snippet": "Juan Perez en Madrid"},
        {"url": "u_medio", "title": "Juan Perez", "snippet": "un tal Juan"},
    ]
    orden = [c["url"] for c in _rerank_candidatos(candidatos, claves, ["Madrid"])]
    assert orden[0] == "u_bueno"
    assert orden[-1] == "u_ruido"


def test_extraccion_quita_el_ruido_de_layout(monkeypatch):
    """El texto extraido de la pagina del sujeto contiene el cuerpo del
    articulo y NO el menu/footer (via trafilatura, o el fallback)."""
    _instalar_fakes(monkeypatch, {"Juan Perez": [_SUBJECT]})
    r = deep_research("Juan Perez", profundidad=1, max_paginas=1)
    texto = " ".join(p for f in r["fuentes"] for p in f["pasajes"])
    assert "Acme Robotics" in texto
    assert "cookies de LinkedIn" not in texto  # el footer no es contenido


def test_extraer_texto_degrada_sin_trafilatura(monkeypatch):
    """Si trafilatura no esta instalada, el texto sale igual por BeautifulSoup."""
    from bs4 import BeautifulSoup
    monkeypatch.setattr(dr, "_trafilatura", None)
    html = "<html><body><p>Contenido principal de la pagina de prueba aqui.</p></body></html>"
    soup = BeautifulSoup(html, "html.parser")
    assert "Contenido principal" in dr._extraer_texto(html, soup)


def test_ninguna_consulta_devuelve_nada_avisa_de_no_inventar(monkeypatch):
    _instalar_fakes(monkeypatch, {})  # search_web siempre vacio
    r = deep_research("Juan Perez", consultas=['"Juan Perez" Madrid'], profundidad=1)
    assert r["fuentes"] == []
    assert "inventes" in r["error"].lower()


# --- Enrutamiento del dominio research ------------------------------------

def test_heuristico_reconoce_una_investigacion():
    from app.router.heuristic_router import match_domain_or_none
    assert match_domain_or_none("investiga la huella digital de Juan Perez") == "research"
    assert match_domain_or_none("averigua todo sobre la empresa Acme") == "research"
    assert match_domain_or_none("hazme un dossier de este proveedor") == "research"
    assert match_domain_or_none("que se sabe de Fulano de Tal") == "research"


def test_heuristico_no_confunde_un_fallo_de_codigo_con_investigacion():
    """'investiga' sobre un bug de un lenguaje NO es research: va a su
    lenguaje/debugging (el verbo apunta a 'por que falla', no a un sujeto)."""
    from app.router.heuristic_router import match_domain_or_none
    assert match_domain_or_none("investiga por que falla este import de python") == "python"


def test_el_schema_de_deep_research_expone_los_angulos_y_anclas():
    from groq_agent.tools import TOOL_SCHEMAS
    dr_schema = next(
        t for t in TOOL_SCHEMAS if t["function"]["name"] == "deep_research"
    )
    props = dr_schema["function"]["parameters"]["properties"]
    assert "consultas" in props and "terminos_clave" in props
