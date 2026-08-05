"""Tests de las herramientas del especialista web-builder. Algunas
pegan contra internet real (example.com, DuckDuckGo) a proposito -
estas herramientas SON clientes de servicios externos, mockearlas no
probaria nada real sobre si funcionan."""
import re
from pathlib import Path

from PIL import Image, ImageFilter

from app.tools.image_analysis import analyze_image
from app.tools.image_search import search_images
from app.tools.link_checker import check_links, summarize
from app.tools.web_lint import lint_html
from groq_agent.tools import ToolExecutor
from app.tools.web_scraper import fetch_page, is_valid_url
from app.tools.web_search import search_web


def test_web_lint_flags_missing_essentials():
    html = "<html><head><title>Test</title></head><body><img src='x.jpg'></body></html>"
    result = lint_html(html)
    assert result["passed"] is False
    assert any("viewport" in issue for issue in result["issues"])
    assert any("alt" in issue for issue in result["issues"])


def test_web_lint_passes_well_formed_page():
    html = (
        '<html lang="es"><head><meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Pizzeria Don Mario</title><meta name="description" content="Pizza artesanal.">'
        '</head><body><h1>Don Mario</h1><h2>Menu</h2><img src="x.jpg" alt="Pizza"></body></html>'
    )
    assert lint_html(html)["passed"] is True


def test_image_analysis_distinguishes_dark_and_light(tmp_path):
    dark_path = tmp_path / "dark.png"
    Image.new("RGB", (200, 100), color=(20, 20, 25)).save(dark_path)
    dark_result = analyze_image(str(dark_path))
    assert dark_result["brightness"] == "oscura"
    assert dark_result["suggested_overlay_text_color"] == "clara"

    light_path = tmp_path / "light.png"
    Image.new("RGB", (200, 100), color=(230, 200, 90)).save(light_path)
    light_result = analyze_image(str(light_path))
    assert light_result["brightness"] == "clara"
    assert light_result["warmth"] == "calida"


def test_image_analysis_flags_low_resolution(tmp_path):
    small_path = tmp_path / "small.png"
    Image.new("RGB", (300, 200), color=(100, 100, 100)).save(small_path)
    result = analyze_image(str(small_path))
    assert result["resolution_quality"] == "baja"
    assert result["usable_as_fullwidth_background"] is False
    assert any("pixelada" in w for w in result["quality_warnings"])


def test_image_analysis_flags_blurry_image(tmp_path):
    # Genera una imagen con detalle (grilla de bloques de color) y su version borrosa
    sharp = Image.new("RGB", (600, 600))
    pixels = sharp.load()
    for x in range(600):
        for y in range(600):
            pixels[x, y] = (255, 0, 0) if (x // 20 + y // 20) % 2 == 0 else (0, 0, 255)
    sharp_path = tmp_path / "sharp.png"
    sharp.save(sharp_path)

    blurry = sharp.filter(ImageFilter.GaussianBlur(radius=10))
    blurry_path = tmp_path / "blurry.png"
    blurry.save(blurry_path)

    sharp_result = analyze_image(str(sharp_path))
    blurry_result = analyze_image(str(blurry_path))
    assert sharp_result["sharpness"] == "nitida"
    assert blurry_result["sharpness"] == "borrosa"


def test_image_search_without_api_key_returns_clear_error():
    result = search_images("pizza", source="pexels")
    assert result["results"] == []
    assert "PEXELS_API_KEY" in result["error"]


def test_image_search_unknown_source():
    result = search_images("pizza", source="not-a-real-source")
    assert "desconocida" in result["error"]


def test_is_valid_url():
    assert is_valid_url("https://example.com") is True
    assert is_valid_url("not-a-url") is False


def test_fetch_page_real_request():
    page = fetch_page("https://example.com")
    assert page["status_code"] == 200
    assert "Example Domain" in page["title"]


def test_search_web_duckduckgo_returns_results():
    import pytest

    result = search_web("python programming language", count=3)
    # Es una llamada de RED: si el buscador esta throttleando la IP (tipico
    # en CI/datacenter), no hay resultados y eso NO es un fallo del codigo -
    # se hace skip. Cuando SI responde, se valida la calidad.
    if not result["results"]:
        pytest.skip(f"buscador no disponible desde esta IP: {result['error']}")
    assert all(r["url"].startswith("http") for r in result["results"])
    # Calidad: sin mojibake (encoding correcto) y sin palabras pegadas.
    for r in result["results"]:
        assert "�" not in (r["title"] + r["snippet"])
        assert not any(len(w) > 30 for w in re.findall(r"[A-Za-z]+", r["snippet"]))


def test_link_checker_detects_broken_link():
    results = check_links([
        "https://example.com",
        "https://example.com/esta-ruta-no-existe-de-verdad-12345",
    ])
    summary = summarize(results)
    assert summary["total"] == 2
    assert summary["broken_count"] == 1
    assert summary["passed"] is False


def test_render_check_catches_js_error(tmp_path):
    from app.tools.render_check import render_check

    html_path = tmp_path / "page.html"
    html_path.write_text(
        "<html><body><script>funcionQueNoExiste();</script></body></html>",
        encoding="utf-8",
    )
    result = render_check(str(html_path), tmp_path / "shot.png")
    assert result["passed"] is False
    for pasada in (result["escritorio"], result["movil"]):
        assert len(pasada["page_errors"]) > 0
        assert len(pasada["capturas"]) > 0
        assert all(Path(c["ruta"]).exists() for c in pasada["capturas"])
        assert all(isinstance(c["secciones_visibles"], list) for c in pasada["capturas"])


def test_render_check_avisa_si_no_hay_texto_visible(tmp_path):
    """El punto ciego: HTML valido de origen que renderiza VACIO (aqui un JS que
    borra el body). lint (estatico) no lo veria; render_check si, porque mide el
    texto realmente renderizado."""
    from app.tools.render_check import render_check

    html_path = tmp_path / "vacia.html"
    html_path.write_text(
        "<html lang='es'><body><h1>Hola mundo</h1>"
        "<script>document.body.innerHTML = '';</script></body></html>",
        encoding="utf-8",
    )
    result = render_check(str(html_path), tmp_path / "shot.png")
    assert result["passed"] is False
    for pasada in (result["escritorio"], result["movil"]):
        assert pasada["render_sin_texto"] is True
        assert pasada["texto_visible_len"] < 10
        assert any("no muestra texto visible" in w for w in pasada["blank_region_warnings"])


def test_render_check_no_marca_sin_texto_una_pagina_con_contenido(tmp_path):
    """Contraprueba: una pagina normal con texto NO debe dispararlo."""
    from app.tools.render_check import render_check

    html_path = tmp_path / "buena.html"
    html_path.write_text(
        "<html lang='es'><body><h1>Cerveceria Malta Norte</h1>"
        "<p>Cerveza artesanal en Gijon desde 2019.</p></body></html>",
        encoding="utf-8",
    )
    result = render_check(str(html_path), tmp_path / "shot.png")
    for pasada in (result["escritorio"], result["movil"]):
        assert pasada["render_sin_texto"] is False
        assert pasada["texto_visible_len"] > 10
        # negro-sobre-blanco y sin imagenes: los checks deterministas NO deben
        # inventar nada (precision: cero falsos positivos en una pagina sana).
        assert pasada["contraste_problemas"] == []
        assert pasada["imagenes_problemas"] == []


def test_render_check_detecta_contraste_bajo_sin_ia(tmp_path):
    """El caso 'grave' que antes dependia del modelo de vision ~75%: texto casi
    del color de su fondo. Ahora se mide con WCAG sobre estilos computados."""
    from app.tools.render_check import render_check

    html = tmp_path / "contraste.html"
    html.write_text(
        "<html lang='es'><body style='background:#ffffff'>"
        "<h1 style='color:#eeeeee'>Titulo casi invisible sobre blanco</h1>"
        "<p style='color:#111111'>Este parrafo si tiene contraste de sobra.</p>"
        "</body></html>",
        encoding="utf-8",
    )
    result = render_check(str(html), tmp_path / "s.png")
    assert result["passed"] is False
    problemas = result["escritorio"]["contraste_problemas"]
    assert len(problemas) >= 1
    assert all(p["ratio"] < p["minimo"] for p in problemas)
    # el parrafo de buen contraste NO se marca
    assert not any("parrafo" in p["texto"].lower() for p in problemas)


def test_render_check_no_marca_contraste_sobre_fondo_de_imagen(tmp_path):
    """Politica conservadora: si el fondo es una imagen/gradiente no se puede
    saber el color por CSS -> se OMITE, no se arriesga un falso positivo."""
    from app.tools.render_check import render_check

    html = tmp_path / "grad.html"
    html.write_text(
        "<html lang='es'><body>"
        "<div style='background:linear-gradient(#000,#fff);padding:40px'>"
        "<h1 style='color:#808080'>Texto gris sobre un gradiente</h1></div>"
        "</body></html>",
        encoding="utf-8",
    )
    result = render_check(str(html), tmp_path / "s.png")
    # no debe reportar contraste (fondo indeterminable), aunque el color sea gris
    assert result["escritorio"]["contraste_problemas"] == []


def test_render_check_detecta_imagen_rota_sin_ia(tmp_path):
    from app.tools.render_check import render_check

    html = tmp_path / "rota.html"
    html.write_text(
        "<html lang='es'><body><h1>Pagina con una imagen rota</h1>"
        "<img src='no-existe.png' alt='rota' width='200' height='150'></body></html>",
        encoding="utf-8",
    )
    result = render_check(str(html), tmp_path / "s.png")
    assert result["passed"] is False
    assert any("naturalWidth" in im["problema"] for im in result["escritorio"]["imagenes_problemas"])


def test_render_check_detecta_imagen_deformada_sin_ia(tmp_path):
    from app.tools.render_check import render_check

    Image.new("RGB", (100, 100), (120, 120, 120)).save(tmp_path / "cuadrada.png")
    html = tmp_path / "deforme.html"
    html.write_text(
        "<html lang='es'><body><h1>Imagen deformada de prueba</h1>"
        "<img src='cuadrada.png' style='width:240px;height:60px'></body></html>",
        encoding="utf-8",
    )
    result = render_check(str(html), tmp_path / "s.png")
    assert any("deformada" in im["problema"] for im in result["escritorio"]["imagenes_problemas"])


# Un menu movil que FUNCIONA: el boton (arriba a la derecha) despliega el nav.
_MENU_HTML = """<html lang='es'><head><style>
  nav {{ display: none; }} nav.abierto {{ display: block; }}
  .toggle {{ position: fixed; top: 10px; right: 10px; }}
</style></head><body>
<header>
  <button class='toggle menu-toggle' aria-label='Abrir menu' aria-expanded='false'
    onclick="{accion}">= </button>
  <nav id='menu'><a href='#a'>Inicio</a><a href='#b'>Carta</a><a href='#c'>Contacto</a></nav>
</header>
<h1>Cerveceria Malta Norte en Gijon</h1>
<p>Cerveza artesanal desde 2019, con mucho texto para no salir en blanco.</p>
</body></html>"""

_ACCION_OK = ("var n=document.getElementById('menu');var o=n.classList.toggle('abierto');"
              "this.setAttribute('aria-expanded',String(o));")


def test_render_check_menu_movil_funciona(tmp_path):
    """Abre el menu hamburguesa en movil y confirma que despliega los enlaces."""
    from app.tools.render_check import render_check

    html = tmp_path / "menu_ok.html"
    html.write_text(_MENU_HTML.format(accion=_ACCION_OK), encoding="utf-8")
    result = render_check(str(html), tmp_path / "s.png")

    mm = result["movil"]["menu_movil"]
    assert mm["encontrado"] is True
    assert mm["funciona"] is True
    # y deja una captura del menu abierto para poder verlo
    assert any("menu movil abierto" in c["secciones_visibles"] for c in result["movil"]["capturas"])


def test_render_check_menu_movil_roto_es_bloqueante(tmp_path):
    """El boton existe pero al pulsarlo no pasa nada: usuario de movil atrapado.
    Debe marcarse como fallo (no entregable)."""
    from app.tools.render_check import render_check

    html = tmp_path / "menu_roto.html"
    html.write_text(_MENU_HTML.format(accion="void 0;"), encoding="utf-8")  # el onclick no hace nada
    result = render_check(str(html), tmp_path / "s.png")

    mm = result["movil"]["menu_movil"]
    assert mm["encontrado"] is True
    assert mm["funciona"] is False
    assert result["passed"] is False


# --- verificar_web: las tres comprobaciones en una sola llamada --------
#
# El skill pedia render_check + lint_web_page + critique_screenshot en la
# MISMA respuesta y el modelo hacia una por turno (7 de 7 en las sesiones
# guardadas). Cada turno de mas reenvia la conversacion entera, asi que
# agruparlas dejo de ser una peticion en prosa y paso a ser la herramienta.


class _EjecutorEspia:
    """Sustituye las tres piezas por espias, para comprobar el orquestado
    sin levantar un navegador ni un modelo de vision."""

    def __init__(self, executor, capturas_por_pasada=3):
        self.executor = executor
        self.llamadas = []
        self.capturas_criticadas = None
        render = {
            "escritorio": {"capturas": [
                {"ruta": f"cap_escritorio_{i}.png", "secciones_visibles": [f"s{i}"]}
                for i in range(1, capturas_por_pasada + 1)]},
            "movil": {"capturas": [
                {"ruta": f"cap_movil_{i}.png", "secciones_visibles": [f"m{i}"]}
                for i in range(1, capturas_por_pasada + 1)]},
        }
        executor.render_check = self._espia("render_check", str(render))
        executor.lint_web_page = self._espia("lint_web_page", "sin problemas de estructura")

        def critica(capturas=None, **kwargs):
            self.llamadas.append("critique_screenshot")
            self.capturas_criticadas = capturas
            return "[{'cambiaria_algo': False}]"

        executor.critique_screenshot = critica

    def _espia(self, nombre, devuelve):
        def fn(*args, **kwargs):
            self.llamadas.append(nombre)
            return devuelve
        return fn


def test_verificar_web_hace_las_tres_en_una_llamada(tmp_path):
    ex = ToolExecutor(tmp_path)
    espia = _EjecutorEspia(ex)

    informe = ex.verificar_web("index.html")

    assert espia.llamadas == ["render_check", "lint_web_page", "critique_screenshot"]
    for bloque in ("render", "lint", "critica"):
        assert bloque in informe


def test_critica_las_primeras_capturas_de_CADA_pasada(tmp_path):
    """Movil es donde se rompen las webs que en escritorio estan perfectas:
    criticar solo la primera pasada dejaria fuera justo eso."""
    ex = ToolExecutor(tmp_path)
    espia = _EjecutorEspia(ex, capturas_por_pasada=4)

    ex.verificar_web("index.html")

    rutas = [c["path_captura"] for c in espia.capturas_criticadas]
    assert any("escritorio" in r for r in rutas)
    assert any("movil" in r for r in rutas)
    # el limite existe para no disparar el coste del modelo de vision
    assert len(rutas) == 2 * ex.CAPTURAS_A_CRITICAR_POR_PASADA


def test_las_secciones_visibles_llegan_a_la_critica(tmp_path):
    """Sin ellas los hallazgos dicen "arriba a la derecha" en vez de en que
    seccion del HTML esta el problema."""
    ex = ToolExecutor(tmp_path)
    espia = _EjecutorEspia(ex)

    ex.verificar_web("index.html")

    assert all(c.get("secciones_visibles") for c in espia.capturas_criticadas)


def _render_str(escritorio=None, movil=None):
    """Arma el str(dict) que devuelve render_check, con los campos que mira
    _bloqueantes_de_render. Vacio = pasada limpia con una captura."""
    base = lambda extra: {  # noqa: E731
        "capturas": [{"ruta": "cap_1.png", "secciones_visibles": ["hero"]}],
        "navigation_error": None, "console_errors": [], "page_errors": [],
        "failed_requests": [], "has_horizontal_overflow": False, "render_sin_texto": False,
        **(extra or {}),
    }
    return str({"escritorio": base(escritorio), "movil": base(movil), "passed": True})


def test_entregable_true_aunque_la_critica_invente_problemas(tmp_path):
    """El nucleo del arreglo: un render/lint LIMPIOS = entregable, aunque el
    modelo de vision (asesor, ~75%) se invente pegas. Asi un problema
    alucinado no bloquea la entrega ni mete al especialista en un bucle."""
    ex = ToolExecutor(tmp_path)
    ex.render_check = lambda *a, **k: _render_str()
    ex.lint_web_page = lambda *a, **k: str({"passed": True, "issues": []})
    ex.critique_screenshot = lambda **k: str([{"cambiaria_algo": True, "problemas": ["boton gigante inventado"]}])

    informe = ex.verificar_web("index.html")

    assert "'entregable': True" in informe
    assert "'bloqueantes': []" in informe
    assert "boton gigante inventado" in informe   # la critica se muestra, pero no bloquea


def test_error_de_js_hace_no_entregable(tmp_path):
    ex = ToolExecutor(tmp_path)
    ex.render_check = lambda *a, **k: _render_str(escritorio={"page_errors": ["ReferenceError: x is not defined"]})
    ex.lint_web_page = lambda *a, **k: str({"passed": True, "issues": []})
    ex.critique_screenshot = lambda **k: str([{"cambiaria_algo": False}])

    informe = ex.verificar_web("index.html")

    assert "'entregable': False" in informe
    assert "Error de JavaScript" in informe and "ReferenceError" in informe


def test_render_sin_texto_hace_no_entregable(tmp_path):
    ex = ToolExecutor(tmp_path)
    ex.render_check = lambda *a, **k: _render_str(movil={"render_sin_texto": True})
    ex.lint_web_page = lambda *a, **k: str({"passed": True, "issues": []})
    ex.critique_screenshot = lambda **k: str([{"cambiaria_algo": False}])

    informe = ex.verificar_web("index.html")

    assert "'entregable': False" in informe
    assert "sin texto visible" in informe


def test_issues_de_lint_son_bloqueantes(tmp_path):
    ex = ToolExecutor(tmp_path)
    ex.render_check = lambda *a, **k: _render_str()
    ex.lint_web_page = lambda *a, **k: str({"passed": False, "issues": ["No hay ningun <h1> en la pagina."]})
    ex.critique_screenshot = lambda **k: str([{"cambiaria_algo": False}])

    informe = ex.verificar_web("index.html")

    assert "'entregable': False" in informe
    assert "Estructura: No hay ningun <h1>" in informe


def test_contraste_bajo_del_render_es_bloqueante(tmp_path):
    ex = ToolExecutor(tmp_path)
    ex.render_check = lambda *a, **k: _render_str(escritorio={"contraste_problemas": [
        {"texto": "boton", "ratio": 1.2, "minimo": 4.5, "color": "rgb(80,80,80)", "fondo": "rgb(90,90,90)"}]})
    ex.lint_web_page = lambda *a, **k: str({"passed": True, "issues": []})
    ex.critique_screenshot = lambda **k: str([{"cambiaria_algo": False}])

    informe = ex.verificar_web("index.html")

    assert "'entregable': False" in informe
    assert "Contraste WCAG insuficiente" in informe


def test_si_el_render_falla_igual_se_devuelve_el_resto(tmp_path):
    """Una verificacion incompleta sigue siendo util; perder el informe
    entero por un render roto dejaria al modelo sin ninguna señal."""
    ex = ToolExecutor(tmp_path)
    ex.render_check = lambda *a, **k: "ERROR: no se pudo abrir el navegador"
    ex.lint_web_page = lambda *a, **k: "falta el meta viewport"

    informe = ex.verificar_web("index.html")

    assert "falta el meta viewport" in informe
    assert "no se pudo criticar" in informe.lower()


# --- Seguridad (SSRF) e investigacion recursiva --------------------------

def test_ssrf_bloquea_internas_y_esquemas():
    from app.tools.web_safety import UrlNoSegura, validar_url
    import pytest

    for u in [
        "http://localhost/admin", "http://127.0.0.1:8080", "http://0.0.0.0",
        "http://169.254.169.254/latest/meta-data/",  # metadatos de nube
        "http://10.0.0.5/", "http://192.168.1.1/", "http://[::1]/",
        "file:///etc/passwd", "ftp://x/", "gopher://x",
    ]:
        with pytest.raises(UrlNoSegura):
            validar_url(u)


def test_ssrf_permite_publicas():
    from app.tools.web_safety import validar_url
    for u in ["https://example.com/", "https://www.python.org/downloads/"]:
        assert validar_url(u) == u


def test_fetch_seguro_tope_de_bytes():
    from app.tools.web_safety import fetch_seguro
    r = fetch_seguro("https://www.python.org/downloads/", max_bytes=3000)
    if not r.get("ok"):
        import pytest
        pytest.skip(f"red no disponible: {r.get('error')}")
    assert len(r["content"]) <= 3000
    assert r["truncado"] is True


def test_deep_research_extrae_fuentes_y_sigue_enlaces():
    from app.tools.deep_research import deep_research
    r = deep_research(
        "novedades de python 3.13",
        profundidad=2, max_paginas=4,
        urls_semilla=["https://www.python.org/downloads/release/python-3130/"],
    )
    if r.get("error") and not r["fuentes"]:
        import pytest
        pytest.skip(f"red no disponible: {r['error']}")
    assert r["paginas_leidas"] >= 1
    # fuentes con url + pasajes, sin mojibake, urls unicas (dedup por #fragmento)
    urls = [f["url"] for f in r["fuentes"]]
    assert len(urls) == len(set(urls))
    for f in r["fuentes"]:
        assert f["url"].startswith("http")
        assert all("�" not in p for p in f["pasajes"])


def test_deep_research_rechaza_semilla_ssrf():
    # Una URL semilla interna no debe leerse (se filtra en validar_url).
    from app.tools.deep_research import deep_research
    r = deep_research("lo que sea", urls_semilla=["http://127.0.0.1/secreto"], profundidad=1)
    assert r["paginas_leidas"] == 0


# --- Busqueda multi-motor (menos fragil) ---------------------------------

def test_searxng_parse_mapea_y_dedup():
    from app.tools.web_search import _searxng_parse
    data = {"results": [
        {"title": "A", "url": "https://a.com/", "content": "uno"},
        {"title": "A dup", "url": "https://a.com", "content": "dos"},
        {"title": "B", "url": "https://b.com", "content": "tres"},
        {"sin_url": True},
    ]}
    r = _searxng_parse(data, 5)
    assert [x["url"] for x in r] == ["https://a.com/", "https://b.com"]
    assert r[0]["snippet"] == "uno"


def test_cascada_devuelve_el_primero_con_resultados(monkeypatch):
    from app.tools import web_search as ws
    orden = []
    def vacio(q, c): orden.append("vacio"); return {"engine": "vacio", "results": [], "error": "0"}
    def cae(q, c): orden.append("cae"); raise RuntimeError("caido")
    def ok(q, c): orden.append("ok"); return {"engine": "ok", "results": [{"title": "T", "url": "https://x", "snippet": "s"}], "error": None}
    monkeypatch.setattr(ws, "_CASCADA", [vacio, cae, ok])
    out = ws._buscar_en_cascada("q", 3)
    assert out["engine"] == "ok"
    assert orden == ["vacio", "cae", "ok"]  # un motor que lanza no aborta la cascada


def test_cascada_todos_fallan_avisa_de_no_inventar(monkeypatch):
    from app.tools import web_search as ws
    def vacio(q, c): return {"engine": "v", "results": [], "error": "0"}
    monkeypatch.setattr(ws, "_CASCADA", [vacio])
    out = ws._buscar_en_cascada("q", 3, pasadas=1)
    assert out["results"] == []
    assert "no inventes" in out["error"].lower()


def test_user_agent_rota():
    from app.tools.web_search import _headers, _USER_AGENTS
    vistos = {_headers()["User-Agent"] for _ in range(40)}
    assert len(vistos) > 1  # no siempre el mismo
    assert vistos <= set(_USER_AGENTS)


def test_searxng_url_propia_va_primero(monkeypatch):
    monkeypatch.setenv("SEARXNG_URL", "https://mi-searx.example/")
    from app.tools.web_search import _searxng_instancias
    assert _searxng_instancias()[0] == "https://mi-searx.example"
