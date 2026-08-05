"""APIs de referencia gratuitas: ejemplos de codigo (Stack Overflow), datos
de paquetes (PyPI/npm/...), vulnerabilidades (OSV), directorios y datos de
ejemplo.

Se prueban OFFLINE (monkeypatch del cliente HTTP) porque lo que hay que
fijar no es que Stack Overflow este arriba, sino la LOGICA nuestra: la
allowlist de hosts (defensa central), la extraccion de bloques de codigo, el
parseo de cada registro, el mapeo de ecosistemas a OSV y la degradacion
limpia cuando falta una clave. Un unico test toca la red real, marcado para
poder saltarlo sin conexion.
"""
import pytest

from app.tools import (
    api_directory,
    code_examples,
    code_search,
    package_info,
    vuln_lookup,
)
from app.tools.api_client import HostNoPermitido, _validar_host


# --- Cliente/allowlist: la defensa central -----------------------------

def test_allowlist_deja_pasar_los_hosts_conocidos():
    for url in ("https://pypi.org/pypi/requests/json",
                "https://api.stackexchange.com/2.3/search",
                "https://api.osv.dev/v1/query"):
        _validar_host(url)  # no lanza


@pytest.mark.parametrize("url", [
    "https://localhost/x",
    "https://127.0.0.1/x",
    "https://169.254.169.254/latest/meta-data/",  # metadatos de la nube
    "https://internal.corp/x",
    "https://evil.com/pypi.org",                   # el host es evil.com, no pypi
])
def test_allowlist_bloquea_hosts_no_permitidos(url):
    """El modelo aporta el termino de busqueda, no la URL: el host lo fija el
    codigo. Cualquier host fuera de la lista se rechaza -> sin SSRF."""
    with pytest.raises(HostNoPermitido):
        _validar_host(url)


def test_allowlist_exige_https():
    with pytest.raises(HostNoPermitido):
        _validar_host("http://pypi.org/pypi/requests/json")


# --- code_examples (Stack Overflow) ------------------------------------

_SO_BUSQUEDA = {
    "ok": True, "status": 200, "error": None,
    "data": {
        "quota_remaining": 297,
        "items": [
            {"question_id": 1, "title": "How to flatten a list in Python",
             "score": 4200, "link": "https://stackoverflow.com/q/1",
             "tags": ["python", "list"], "answer_count": 12,
             "accepted_answer_id": 11, "body": "<p>pregunta</p>"},
            {"question_id": 2, "title": "Otra sin aceptar", "score": 5,
             "link": "https://stackoverflow.com/q/2", "tags": ["python"],
             "answer_count": 1, "body": "<p>q2</p>"},
        ],
    },
}
_SO_RESPUESTAS = {
    "ok": True, "status": 200, "error": None,
    "data": {"items": [
        {"answer_id": 11, "question_id": 1, "score": 8000, "is_accepted": True,
         "body": "<p>Usa una comprension:</p><pre><code>flat = [x for sub in lst for x in sub]\n</code></pre>"},
    ]},
}


def test_code_examples_extrae_el_bloque_de_codigo(monkeypatch):
    llamadas = []

    def fake_get(url, params=None, **kw):
        llamadas.append(url)
        return _SO_BUSQUEDA if "search/advanced" in url else _SO_RESPUESTAS

    monkeypatch.setattr(code_examples, "get_json", fake_get)
    out = code_examples.buscar_ejemplos("flatten list", lenguaje="python", limite=1)

    assert out["error"] is None
    assert out["cuota_restante"] == 297
    top = out["resultados"][0]
    assert top["aceptada"] is True
    assert top["votos_respuesta"] == 8000
    assert any("comprension" in top["codigo"][0] for _ in [0]) or "for x in sub" in top["codigo"][0]
    assert "for x in sub" in top["codigo"][0]  # el codigo real, no la prosa
    # 2 peticiones: buscar + traer respuestas en lote.
    assert len(llamadas) == 2


def test_code_examples_consulta_vacia_no_llama_a_la_red(monkeypatch):
    monkeypatch.setattr(code_examples, "get_json",
                        lambda *a, **k: pytest.fail("no debio llamar a la red"))
    assert code_examples.buscar_ejemplos("  ")["error"]


def test_code_examples_error_de_cuota_se_reporta(monkeypatch):
    monkeypatch.setattr(code_examples, "get_json",
                        lambda *a, **k: {"ok": False, "status": 400, "data": None, "error": "HTTP 400"})
    out = code_examples.buscar_ejemplos("algo")
    assert out["resultados"] == []
    assert "STACKEXCHANGE_KEY" in out["error"]  # pista accionable, no solo el fallo


# --- package_info ------------------------------------------------------

def test_package_info_pypi_saca_la_version(monkeypatch):
    monkeypatch.setattr(package_info, "get_json", lambda url, **k: {
        "ok": True, "data": {"info": {
            "version": "2.34.2", "summary": "HTTP for Humans", "license": "Apache-2.0",
            "home_page": "", "project_urls": {"Source": "https://github.com/psf/requests"}}}})
    out = package_info.info_paquete("pypi", "requests")
    assert out["version"] == "2.34.2"
    assert out["repo"].endswith("requests")
    assert out["licencia"] == "Apache-2.0"


def test_package_info_npm_usa_el_manifiesto_latest(monkeypatch):
    # Se pide /{name}/latest (manifiesto de la ultima version, unos KB), no el
    # packument completo (react entero pesa ~7 MB y se cortaba).
    urls = []

    def fake_get(url, **k):
        urls.append(url)
        return {"ok": True, "data": {
            "version": "19.2.8", "description": "React",
            "homepage": "https://react.dev",
            "repository": {"url": "git+https://github.com/facebook/react.git"},
            "license": "MIT"}}

    monkeypatch.setattr(package_info, "get_json", fake_get)
    out = package_info.info_paquete("npm", "react")
    assert urls[0].endswith("/react/latest")     # no descarga todas las versiones
    assert out["version"] == "19.2.8"
    assert out["repo"] == "https://github.com/facebook/react"  # sin git+ ni .git


def test_package_info_npm_licencia_objeto(monkeypatch):
    """npm permite license como string O como {"type": "MIT"}."""
    monkeypatch.setattr(package_info, "get_json", lambda url, **k: {
        "ok": True, "data": {"version": "1.0.0", "license": {"type": "BSD-3-Clause"}}})
    assert package_info.info_paquete("npm", "x")["licencia"] == "BSD-3-Clause"


def test_package_info_ecosistema_desconocido():
    out = package_info.info_paquete("cargo", "serde")  # 'cargo' no es 'crates'
    assert out["version"] is None
    assert "no soportado" in out["error"]


def test_package_info_escapa_mayusculas_de_go():
    assert package_info._escape_go("github.com/BurntSushi/toml") == "github.com/!burnt!sushi/toml"


def test_package_info_maven_usa_el_cdn_no_el_solr(monkeypatch):
    """Con 'grupo:artefacto' se va a repo1.maven.org (maven-metadata.xml, RAPIDO
    y fiable), no al buscador solr (intermitente: 40s/30s/0.5s)."""
    llamadas = {}

    def fake_text(url, **k):
        llamadas["url"] = url
        return {"ok": True, "text": (
            "<metadata><groupId>com.google.guava</groupId><artifactId>guava</artifactId>"
            "<versioning><latest>33.2.0-jre</latest><release>33.2.0-jre</release></versioning></metadata>")}

    def fake_get(*a, **k):
        pytest.fail("no debio caer al solr con una coordenada valida")

    monkeypatch.setattr(package_info, "get_text", fake_text)
    monkeypatch.setattr(package_info, "get_json", fake_get)
    out = package_info.info_paquete("maven", "com.google.guava:guava")
    assert "repo1.maven.org/maven2/com/google/guava/guava/maven-metadata.xml" in llamadas["url"]
    assert out["version"] == "33.2.0-jre"


def test_package_info_packagist_elige_la_ultima_estable(monkeypatch):
    monkeypatch.setattr(package_info, "get_json", lambda url, **k: {
        "ok": True, "data": {"package": {"description": "Logs", "repository": "https://github.com/Seldaek/monolog",
                             "versions": {"3.5.0": {}, "3.10.0": {}, "2.9.1": {}, "dev-main": {}}}}})
    out = package_info.info_paquete("packagist", "monolog/monolog")
    assert out["version"] == "3.10.0"  # ni la dev, ni la 3.5, ni la 2.x


# --- vuln_check (OSV) --------------------------------------------------

def test_vuln_check_mapea_ecosistema_y_saca_el_fix(monkeypatch):
    capturado = {}

    def fake_post(url, body, **k):
        capturado["body"] = body
        return {"ok": True, "data": {"vulns": [
            {"id": "GHSA-jfh8-c2jp-5v3q", "aliases": ["CVE-2021-44228"],
             "summary": "Log4Shell", "database_specific": {"severity": "CRITICAL"},
             "affected": [{"ranges": [{"events": [{"introduced": "0"}, {"fixed": "2.15.0"}]}]}]}]}}

    monkeypatch.setattr(vuln_lookup, "post_json", fake_post)
    out = vuln_lookup.consultar_vulnerabilidades("maven", "org.apache.logging.log4j:log4j-core", "2.14.1")

    assert capturado["body"]["package"]["ecosystem"] == "Maven"  # mapeo pypi->PyPI, maven->Maven...
    assert capturado["body"]["version"] == "2.14.1"
    v = out["vulnerabilidades"][0]
    assert v["aliases"] == ["CVE-2021-44228"]
    assert v["corregido_en"] == ["2.15.0"]
    assert "CRITICAL" in v["severidad"]


def test_vuln_check_sin_vulnerabilidades_lo_dice(monkeypatch):
    monkeypatch.setattr(vuln_lookup, "post_json", lambda *a, **k: {"ok": True, "data": {}})
    out = vuln_lookup.consultar_vulnerabilidades("pypi", "requests")
    assert out["total"] == 0
    assert "Sin vulnerabilidades" in out["nota"]


# --- search_code (GitHub) ----------------------------------------------

def test_search_code_sin_token_degrada_con_pista(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    out = code_search.buscar_codigo("asyncio gather")
    assert out["resultados"] == []
    assert "GITHUB_TOKEN" in out["error"]
    assert "code_examples" in out["error"]  # ofrece la alternativa sin clave


def test_search_code_con_token_devuelve_fragmentos(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_falso")
    monkeypatch.setattr(code_search, "get_json", lambda url, **k: {
        "ok": True, "status": 200, "data": {"total_count": 1, "items": [
            {"path": "app/main.py", "html_url": "https://github.com/x/y/blob/main/app/main.py",
             "repository": {"full_name": "x/y"},
             "text_matches": [{"fragment": "await asyncio.gather(*tasks)"}]}]}})
    out = code_search.buscar_codigo("asyncio gather", lenguaje="python")
    assert out["resultados"][0]["repo"] == "x/y"
    assert "gather" in out["resultados"][0]["fragmentos"][0]


# --- find_api / sample_data -------------------------------------------

def test_find_api_filtra_por_palabra_clave(monkeypatch):
    api_directory._CACHE_DIRECTORIO = None  # limpiar cache entre tests
    directorio = {
        "openweathermap.org": {"preferred": "2.5", "versions": {"2.5": {"info": {
            "title": "OpenWeather", "description": "Current weather and forecast",
            "x-apisguru-categories": ["weather"]}}}},
        "stripe.com": {"preferred": "1", "versions": {"1": {"info": {
            "title": "Stripe", "description": "Payments API", "x-apisguru-categories": ["financial"]}}}},
    }
    monkeypatch.setattr(api_directory, "get_json",
                        lambda url, **k: {"ok": True, "data": directorio})
    out = api_directory.buscar_api("weather")
    assert out["total"] == 1
    assert out["resultados"][0]["nombre"] == "OpenWeather"
    api_directory._CACHE_DIRECTORIO = None


def test_sample_data_tipo_invalido():
    out = api_directory.datos_de_ejemplo("dragones")
    assert out["items"] == []
    assert "no disponible" in out["error"]


def test_sample_data_devuelve_items(monkeypatch):
    monkeypatch.setattr(api_directory, "get_json", lambda url, params=None, **k: {
        "ok": True, "data": {"products": [{"id": 1, "title": "Phone"}], "total": 100}})
    out = api_directory.datos_de_ejemplo("products", 1)
    assert out["items"][0]["title"] == "Phone"
    assert out["total"] == 100
