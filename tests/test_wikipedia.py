"""Tool de Wikipedia: se prueba OFFLINE (monkeypatch de get_json). Lo que se
fija es la LOGICA nuestra - resolver el articulo, extraer el texto, el
fallback es->en, el recorte y que una consulta vacia no toque la red -, no que
Wikipedia este arriba."""
from app.tools import api_client, wikipedia


def _pagina(titulo, extracto, url):
    return {"ok": True, "status": 200, "error": None,
            "data": {"query": {"pages": {"1": {"title": titulo, "extract": extracto, "fullurl": url}}}}}


def _sin_paginas():
    return {"ok": True, "status": 200, "error": None, "data": {"query": {"pages": {}}}}


def test_wikipedia_devuelve_el_articulo(monkeypatch):
    monkeypatch.setattr(api_client, "get_json", lambda url, **k: _pagina(
        "BMW Serie 1", "El BMW Serie 1 es un automovil compacto.", "https://es.wikipedia.org/wiki/BMW_Serie_1"))
    r = wikipedia.consultar_wikipedia("BMW Serie 1 F40")
    assert r["ok"] is True
    assert r["titulo"] == "BMW Serie 1"
    assert r["url"] == "https://es.wikipedia.org/wiki/BMW_Serie_1"
    assert "automovil" in r["extracto"]
    assert r["idioma"] == "es"


def test_wikipedia_cae_a_ingles_si_no_hay_en_espanol(monkeypatch):
    """Un tema sin pagina en es (un modelo de coche, por ejemplo) se busca en
    en antes de rendirse."""
    def fake(url, **k):
        return _pagina("Some Topic", "Only in English.", "https://en.wikipedia.org/wiki/Some_Topic") \
            if "en.wikipedia.org" in url else _sin_paginas()
    monkeypatch.setattr(api_client, "get_json", fake)
    r = wikipedia.consultar_wikipedia("some obscure topic")
    assert r["ok"] is True
    assert r["idioma"] == "en"
    assert r["url"].startswith("https://en.wikipedia.org")


def test_wikipedia_consulta_vacia_no_llama_a_la_red(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no deberia llamar a la red con consulta vacia")
    monkeypatch.setattr(api_client, "get_json", boom)
    r = wikipedia.consultar_wikipedia("   ")
    assert r["ok"] is False
    assert r["extracto"] is None


def test_wikipedia_recorta_un_articulo_largo(monkeypatch):
    largo = "dato. " * 5000
    monkeypatch.setattr(api_client, "get_json", lambda url, **k: _pagina(
        "Tema", largo, "https://es.wikipedia.org/wiki/Tema"))
    r = wikipedia.consultar_wikipedia("tema", limite_chars=500)
    assert r["ok"] is True
    assert len(r["extracto"]) < 700          # 500 + la marca de recorte
    assert "recortado" in r["extracto"]


def test_wikipedia_idioma_invalido_cae_a_es(monkeypatch):
    vistos = []
    def fake(url, **k):
        vistos.append(url)
        return _pagina("X", "texto", "https://es.wikipedia.org/wiki/X")
    monkeypatch.setattr(api_client, "get_json", fake)
    r = wikipedia.consultar_wikipedia("x", idioma="fr")
    assert r["idioma"] == "es"
    assert all("es.wikipedia.org" in u for u in vistos)


def test_wikipedia_hosts_en_la_allowlist():
    from app.tools.api_client import _validar_host
    _validar_host("https://es.wikipedia.org/w/api.php")   # no lanza
    _validar_host("https://en.wikipedia.org/w/api.php")   # no lanza
