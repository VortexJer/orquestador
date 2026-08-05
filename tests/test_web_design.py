"""Recursos de diseño web gratis: iconos (Iconify), tipografias (Google
Fonts), paletas (The Color API), assets por URL y la biblioteca UIverse.

Offline (monkeypatch del cliente HTTP): lo que se fija es NUESTRA logica -
construir la URL SVG del icono, el <link> de la fuente con sus pesos, el
muestreo variado de UIverse, el parseo del color anidado- no que los
servidores esten arriba.
"""
import json

import pytest

from app.tools import web_design


# --- search_icons (Iconify) --------------------------------------------

def test_search_icons_construye_la_url_svg(monkeypatch):
    monkeypatch.setattr(web_design, "get_json", lambda url, **k: {
        "ok": True, "data": {"icons": ["mdi:home", "material-symbols:shopping-cart"], "total": 2}})
    out = web_design.search_icons("home", 5)
    assert out["resultados"][0]["nombre"] == "mdi:home"
    assert out["resultados"][0]["set"] == "mdi"
    assert out["resultados"][0]["url_svg"] == "https://api.iconify.design/mdi/home.svg"
    assert out["resultados"][1]["url_svg"] == "https://api.iconify.design/material-symbols/shopping-cart.svg"


def test_search_icons_consulta_vacia():
    assert web_design.search_icons("")["error"]


# --- search_fonts (Google Fonts) ---------------------------------------

_GF_META = ")]}'\n" + json.dumps({"familyMetadataList": [
    {"family": "Roboto", "category": "Sans Serif", "popularity": 1,
     "fonts": {"100": {}, "400": {}, "400i": {}, "700": {}}},
    {"family": "Lora", "category": "Serif", "popularity": 50,
     "fonts": {"400": {}, "700": {}}},
    {"family": "Open Sans", "category": "Sans Serif", "popularity": 2,
     "fonts": {"400": {}, "600": {}}},
]})


def test_search_fonts_construye_el_link_con_pesos(monkeypatch):
    web_design._CACHE_FUENTES = None
    monkeypatch.setattr(web_design, "get_text", lambda url, **k: {"ok": True, "text": _GF_META})
    out = web_design.search_fonts("roboto")
    web_design._CACHE_FUENTES = None
    f = out["resultados"][0]
    assert f["family"] == "Roboto"
    assert f["pesos"] == [100, 400, 700]          # '400i' no duplica el 400
    assert "family=Roboto:wght@100;400;700" in f["link"]
    assert "display=swap" in f["link"]


def test_search_fonts_ordena_por_popularidad_y_filtra_categoria(monkeypatch):
    web_design._CACHE_FUENTES = None
    monkeypatch.setattr(web_design, "get_text", lambda url, **k: {"ok": True, "text": _GF_META})
    out = web_design.search_fonts(categoria="sans")   # solo las sans serif
    web_design._CACHE_FUENTES = None
    familias = [r["family"] for r in out["resultados"]]
    assert "Lora" not in familias
    assert familias[0] == "Roboto"     # popularity 1 antes que Open Sans (2)


def test_search_fonts_espacio_en_el_nombre_va_url_encoded(monkeypatch):
    web_design._CACHE_FUENTES = None
    monkeypatch.setattr(web_design, "get_text", lambda url, **k: {"ok": True, "text": _GF_META})
    out = web_design.search_fonts("open sans")
    web_design._CACHE_FUENTES = None
    assert "Open%20Sans" in out["resultados"][0]["link"]


# --- color_palette (The Color API) -------------------------------------

def test_color_palette_parsea_el_color_anidado(monkeypatch):
    captura = {}

    def fake_get(url, params=None, **k):
        captura["params"] = params
        return {"ok": True, "data": {"colors": [
            {"hex": {"value": "#04429A"}, "name": {"value": "Congress Blue"}},
            {"hex": {"value": "#0309A2"}, "name": {"value": "Blue Gray"}}]}}

    monkeypatch.setattr(web_design, "get_json", fake_get)
    out = web_design.color_palette("#0047AB", "triad", 2)
    assert captura["params"]["hex"] == "0047AB"      # sin '#'
    assert captura["params"]["mode"] == "triad"
    assert out["colores"][0] == {"hex": "#04429A", "nombre": "Congress Blue"}


def test_color_palette_modo_invalido_cae_a_analogic(monkeypatch):
    captura = {}
    monkeypatch.setattr(web_design, "get_json", lambda url, params=None, **k: (
        captura.update(params=params) or {"ok": True, "data": {"colors": []}}))
    web_design.color_palette("abc123", "arcoiris")
    assert captura["params"]["mode"] == "analogic"


def test_color_palette_sin_base():
    assert web_design.color_palette("")["error"]


# --- design_assets (constructor de URLs) -------------------------------

def test_design_assets_avatar_y_qr_y_placeholder_no_tocan_la_red(monkeypatch):
    monkeypatch.setattr(web_design, "get_text",
                        lambda *a, **k: pytest.fail("estos no deben hacer red"))
    av = web_design.design_assets("avatar", seed="Maria")
    assert av["url"] == "https://api.dicebear.com/9.x/thumbs/svg?seed=Maria"
    qr = web_design.design_assets("qr", texto="hola mundo")
    assert qr["url"].startswith("https://api.qrserver.com/") and "hola%20mundo" in qr["url"]
    ph = web_design.design_assets("placeholder", width=600, height=200, texto="Logo")
    assert "600x200" in ph["url"] and "text=Logo" in ph["url"]


def test_design_assets_logo_verifica_existencia(monkeypatch):
    monkeypatch.setattr(web_design, "get_text",
                        lambda url, **k: {"ok": True, "text": "<svg>...</svg>"})
    out = web_design.design_assets("logo", brand="GitHub")
    assert out["url"] == "https://cdn.simpleicons.org/github"   # slug normalizado
    assert out["existe"] is True


def test_design_assets_logo_inexistente(monkeypatch):
    monkeypatch.setattr(web_design, "get_text",
                        lambda url, **k: {"ok": False, "text": None})
    out = web_design.design_assets("logo", brand="marca-que-no-existe")
    assert out["existe"] is False
    assert "no tiene" in out["nota"]


def test_design_assets_kind_invalido():
    assert web_design.design_assets("dragon")["error"]


# --- uiverse (biblioteca de componentes) -------------------------------

def _entradas(n):
    return [{"name": f"autor_animal-{i}.html",
             "download_url": f"https://raw.githubusercontent.com/uiverse-io/galaxy/main/Buttons/autor_animal-{i}.html"}
            for i in range(n)]


def test_uiverse_muestrea_variado_y_trae_codigo_de_los_dos_primeros(monkeypatch):
    web_design._CACHE_UIVERSE.clear()
    monkeypatch.setattr(web_design, "get_json", lambda url, **k: {"ok": True, "data": _entradas(100)})
    codigos = {"n": 0}

    def fake_text(url, **k):
        codigos["n"] += 1
        return {"ok": True, "text": "<button>anim</button>"}

    monkeypatch.setattr(web_design, "get_text", fake_text)
    out = web_design.uiverse("buttons", 4)
    web_design._CACHE_UIVERSE.clear()

    assert out["total_en_categoria"] == 100
    assert len(out["resultados"]) == 4
    # muestreo repartido: no son los 4 primeros consecutivos (0,1,2,3).
    nums = [int(r["nombre"].split("-")[-1]) for r in out["resultados"]]
    assert nums == [0, 25, 50, 75]
    # codigo solo de los 2 primeros (2 llamadas a get_text).
    assert codigos["n"] == 2
    assert out["resultados"][0]["codigo"] == "<button>anim</button>"
    assert "codigo" not in out["resultados"][2]


def test_uiverse_mapea_sinonimos_de_categoria(monkeypatch):
    web_design._CACHE_UIVERSE.clear()
    urls = []
    monkeypatch.setattr(web_design, "get_json",
                        lambda url, **k: (urls.append(url) or {"ok": True, "data": _entradas(3)}))
    monkeypatch.setattr(web_design, "get_text", lambda url, **k: {"ok": True, "text": "x"})
    web_design.uiverse("botones")   # sinonimo español -> carpeta 'Buttons'
    web_design._CACHE_UIVERSE.clear()
    assert urls[0].endswith("/contents/Buttons")


def test_uiverse_categoria_invalida():
    assert web_design.uiverse("dragones")["error"]
