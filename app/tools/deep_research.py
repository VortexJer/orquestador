"""Investigacion recursiva CIBERSEGURA para el especialista.

Dada una pregunta, hace un barrido en anchura y acotado: busca, lee las
paginas top, extrae los pasajes relevantes, y sigue los enlaces mas
prometedores de esas paginas una o dos capas mas (eso es lo "recursivo").
Devuelve una lista de FUENTES con sus pasajes + un texto consolidado, para
que el modelo redacte la respuesta CON CITAS en vez de inventar.

Conceptos tomados de los agentes de investigacion abiertos (gpt-researcher,
Perplexica, "deep research"): descomponer en varias consultas de angulo,
rerankear los resultados por relevancia antes de leer, y extraer el contenido
principal de cada pagina con calidad. Aqui, todo sin LLM ni dependencia
obligatoria y sin renunciar a la seguridad.

Decisiones:
  - Multi-angulo (`consultas`): en vez de una busqueda generica, barre el
    sujeto desde varios flancos (nombre exacto, +ciudad, +profesion,
    site:...) y junta/deduplica los resultados. Es la "descomposicion de
    consulta" de gpt-researcher, hecha por el especialista que llama.
  - Rerank antes de leer (`_rerank_candidatos`): se leen primero las fuentes
    cuyo titulo+snippet mas solapan con la pregunta y las anclas del sujeto,
    para gastar el presupuesto de paginas en lo mejor (idea de Perplexica),
    de forma lexica y barata - sin embeddings, no puede fallar en caliente.
  - Extraccion del contenido principal con trafilatura (opcional): quita
    menus/anuncios/pies mucho mejor que un get_text() crudo -> mejores
    pasajes y mejores citas. Si no esta instalada, degrada a BeautifulSoup.
  - Desambiguacion (`terminos_clave`/`anclas`): cada fuente reporta que
    rasgos del sujeto confirma, para que el modelo NO funda a dos personas
    con el mismo nombre.
  - Sin LLM dentro: la relevancia se decide por solape de palabras clave
    (heuristica barata y robusta). El modelo bueno sintetiza despues; asi
    esta tool no gasta tokens ni depende de ningun proveedor.
  - TODO fetch pasa por web_safety.fetch_seguro: filtro SSRF (nada de
    localhost/IPs internas/metadatos de nube), tope de bytes, solo texto.
    trafilatura recibe el HTML YA descargado asi; nunca descarga por su
    cuenta (saltaria el filtro SSRF).
  - Presupuesto duro: max_paginas totales, profundidad de capas y un tope
    por dominio, para que no se convierta en un crawler infinito.
"""
from __future__ import annotations

import re
from collections import deque
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

from app.tools.web_safety import UrlNoSegura, decodificar, fetch_seguro, validar_url

# Extraccion del contenido PRINCIPAL de una pagina (quita menus, anuncios,
# pies, "articulos relacionados"). trafilatura es el estandar del sector para
# esto (mejor F1 que readability/newspaper en los benchmarks abiertos), y es
# lo que dispara la calidad de los pasajes: sin ella, un get_text() crudo
# arrastra el ruido del layout y ensucia tanto el ranking como las citas.
# OPCIONAL y SEGURO: se le pasa el HTML que YA descargamos con filtro SSRF;
# NUNCA se usa su descargador propio (saltaria el filtro). Si no esta
# instalada, se degrada al metodo de BeautifulSoup de siempre.
try:
    import trafilatura as _trafilatura
except ImportError:  # pragma: no cover - degradado si no esta la dependencia
    _trafilatura = None

# Palabras vacias es/en: no aportan a la relevancia y ensucian el ranking.
_STOPWORDS = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "a", "al",
    "en", "y", "o", "u", "que", "como", "para", "por", "con", "su", "sus", "se",
    "es", "son", "the", "a", "an", "of", "to", "in", "on", "and", "or", "for",
    "with", "is", "are", "how", "what", "when", "where", "who", "why", "which",
    "cual", "cuales", "cuando", "donde", "quien", "quienes", "cuanto", "mejor",
    "mejores", "hoy", "actual", "sobre",
}

_ENLACE_BASURA = re.compile(
    r"(login|signin|signup|register|privacy|cookie|terms|contact|about|"
    r"advertis|subscribe|newsletter|/tag/|/category/|/author/|facebook\.com|"
    r"twitter\.com|instagram\.com|linkedin\.com|youtube\.com|/feed|\.rss)", re.I,
)


def _palabras_clave(texto: str) -> set[str]:
    palabras = re.findall(r"[a-zA-ZÁÉÍÓÚÜÑáéíóúüñ0-9]{3,}", texto.lower())
    return {p for p in palabras if p not in _STOPWORDS}


def _norm(url: str) -> str:
    """Clave de deduplicacion: sin fragmento #ancla (no cambia la pagina) y
    sin barra final. Asi la misma pagina con dos anclas no se lee dos veces."""
    return urldefrag(url)[0].rstrip("/")


def _dominio(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except ValueError:
        return ""


def _extraer_texto(html: str, soup_limpio: BeautifulSoup) -> str:
    """El texto PRINCIPAL de la pagina. Primero trafilatura (quita el ruido de
    layout con mucho mas acierto); si no esta instalada o no saca nada util,
    cae al get_text() del soup ya limpio de nav/footer. Recibe el HTML que ya
    se descargo con filtro SSRF - trafilatura NO vuelve a la red."""
    if _trafilatura is not None:
        try:
            texto = _trafilatura.extract(
                html,
                include_comments=False,   # los comentarios no son el contenido
                include_tables=True,      # las tablas si: llevan datos/hechos
                favor_recall=True,        # ante la duda, conserva texto (no lo tira)
                deduplicate=True,
                output_format="txt",
            )
            if texto and texto.strip():
                return re.sub(r"\n{3,}", "\n\n", texto).strip()
        except Exception:  # noqa: BLE001 - cualquier fallo de extraccion cae al fallback
            pass
    return re.sub(r"\n{3,}", "\n\n", soup_limpio.get_text("\n")).strip()


def _leer_pagina(url: str) -> dict | None:
    """Trae una pagina de forma segura y devuelve titulo, texto limpio y
    enlaces (con su texto de ancla). None si no se pudo o no es HTML util."""
    res = fetch_seguro(url)
    if not res.get("ok") or not res.get("content"):
        return None
    html = decodificar(res["content"])
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "form"]):
        tag.decompose()
    # Texto principal via trafilatura (con el soup ya limpio como fallback).
    texto = _extraer_texto(html, soup)
    base = res.get("url_final") or url
    enlaces = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        absoluto = urljoin(base, href)
        if not absoluto.startswith(("http://", "https://")):
            continue
        enlaces.append((absoluto, a.get_text(" ", strip=True)[:120]))
    return {"url": base, "title": title, "texto": texto, "enlaces": enlaces}


def _pasajes_relevantes(texto: str, claves: set[str], n: int = 3) -> list[str]:
    """Los n parrafos con mas solape de palabras clave. Es lo que hace que
    la tool devuelva la parte UTIL de una pagina larga, no la pagina entera."""
    parrafos = [p.strip() for p in re.split(r"\n{2,}|(?<=[.!?])\s{2,}", texto) if len(p.strip()) > 60]
    puntuados = []
    for p in parrafos:
        pal = _palabras_clave(p)
        solape = len(pal & claves)
        if solape:
            puntuados.append((solape, len(p), p))
    # mas solape primero; a igualdad, el parrafo mas corto (mas denso).
    puntuados.sort(key=lambda t: (-t[0], t[1]))
    vistos, salida = set(), []
    for _, _, p in puntuados:
        firma = p[:80]
        if firma in vistos:
            continue
        vistos.add(firma)
        # Colapsar saltos de linea y espacios repetidos: un pasaje es una
        # frase, no un trozo de layout.
        limpio = re.sub(r"\s+", " ", p).strip()
        salida.append(limpio[:600])
        if len(salida) >= n:
            break
    return salida


def _ordenar_enlaces(enlaces: list[tuple[str, str]], claves: set[str]) -> list[str]:
    """Enlaces ordenados por relevancia: solape de las palabras clave con el
    texto de ancla + la propia URL. Descarta enlaces de navegacion/basura."""
    puntuados = []
    for url, ancla in enlaces:
        if _ENLACE_BASURA.search(url):
            continue
        pal = _palabras_clave(ancla + " " + url.replace("/", " ").replace("-", " "))
        solape = len(pal & claves)
        if solape:
            puntuados.append((solape, url))
    puntuados.sort(key=lambda t: -t[0])
    # dedup conservando orden
    vistos, salida = set(), []
    for _, url in puntuados:
        c = _norm(url)
        if c not in vistos:
            vistos.add(c)
            salida.append(url)
    return salida


def _rerank_candidatos(
    candidatos: list[dict], claves: set[str], anclas: list[str],
) -> list[dict]:
    """Ordena los resultados de busqueda por relevancia ANTES de leerlos, por
    solape de palabras clave (y de anclas del sujeto) con titulo+snippet. Asi
    se leen primero las fuentes mas prometedoras y las max_paginas del
    presupuesto se gastan en lo mejor, no en lo que llego antes. Es lo que hace
    Perplexica al rerankear los resultados de SearXNG - aqui de forma lexica y
    barata (sin embeddings ni dependencia externa), que para titulo+snippet es
    suficiente y no puede fallar en caliente."""
    anclas_low = [a.strip().lower() for a in anclas if a.strip()]

    def puntua(c: dict) -> tuple[int, int]:
        texto = f"{c.get('title', '')} {c.get('snippet', '')}"
        solape = len(_palabras_clave(texto) & claves)
        # Una ancla del sujeto en el snippet es señal fuerte de que es EL
        # sujeto y no un homonimo: pesa mas que una palabra clave suelta.
        bonus = sum(2 for a in anclas_low if a in texto.lower())
        return (solape + bonus, len(c.get("snippet", "")))

    # Mas puntuacion primero; a igualdad, el de snippet mas largo (mas contexto).
    return sorted(candidatos, key=lambda c: (-puntua(c)[0], -puntua(c)[1]))


def _anclas_presentes(pasajes: list[str], anclas: list[str]) -> list[str]:
    """De la lista de rasgos del sujeto (`anclas`: p.ej. una ciudad, una
    profesion, una empresa), cuales aparecen en los pasajes de esta fuente.
    Es la señal que deja al modelo DESAMBIGUAR: una fuente que menciona el
    nombre pero NINGUN rasgo esperado puede ser un homonimo, no el sujeto."""
    if not anclas:
        return []
    texto = " ".join(pasajes).lower()
    return [a for a in anclas if a.strip() and a.strip().lower() in texto]


def deep_research(
    pregunta: str,
    profundidad: int = 2,
    max_paginas: int = 6,
    urls_semilla: list[str] | None = None,
    max_por_dominio: int = 2,
    consultas: list[str] | None = None,
    terminos_clave: list[str] | None = None,
) -> dict:
    """Investiga `pregunta` de forma recursiva y segura.

    - profundidad: cuantas CAPAS de enlaces seguir (1 = solo las paginas de
      la busqueda; 2 = tambien sus enlaces relevantes; etc.). Se acota a 3.
    - max_paginas: tope DURO de paginas que se llegan a leer.
    - urls_semilla: puntos de partida explicitos (si se dan, no hace falta
      buscar - util si la busqueda esta caida o para acotar el arranque).
    - max_por_dominio: no leer mas de N paginas del mismo sitio (diversidad).
    - consultas: BUSQUEDA EN VARIOS ANGULOS. En vez de una sola busqueda de
      `pregunta`, se buscan TODAS estas consultas (nombre exacto, nombre+ciudad,
      nombre+profesion, nombre site:linkedin.com, ...) y se juntan/deduplican
      sus resultados. Es lo que hace la busqueda PRODUCTIVA: barrer el sujeto
      desde muchos flancos en una sola llamada, no una consulta generica.
    - terminos_clave: RASGOS del sujeto para desambiguar (ciudad, profesion,
      empresa, edad, alias). Suben la relevancia de los pasajes que los citan
      y, sobre todo, cada fuente devuelve `anclas` = cuales de estos rasgos
      aparecen en ella -> el modelo ve si una fuente es del sujeto o de un
      HOMONIMO (mismo nombre, otra persona) antes de fundir sus datos.

    Devuelve {pregunta, fuentes:[{url,title,pasajes,anclas}], resumen,
    consultas, paginas_leidas, error}. El modelo redacta la respuesta a partir
    de las fuentes, citando las URLs, y NO mezcla fuentes de sujetos distintos.
    """
    if not pregunta or not pregunta.strip():
        return {"error": "Pregunta vacia.", "fuentes": []}
    profundidad = max(1, min(int(profundidad or 2), 3))
    max_paginas = max(1, min(int(max_paginas or 6), 15))
    anclas = [t for t in (terminos_clave or []) if t and t.strip()]
    # Las claves de relevancia salen de la pregunta + las consultas de angulo +
    # los rasgos del sujeto: asi un pasaje que menciona el nombre Y un rasgo
    # esperado puntua alto (justo el que quiero conservar de una pagina larga).
    claves = _palabras_clave(pregunta)
    for extra in (consultas or []) + anclas:
        claves |= _palabras_clave(extra)

    # Import perezoso para no crear un ciclo con web_search en tiempo de carga.
    from app.tools.web_search import search_web

    consultas_usadas: list[str] = []
    # Frontera BFS: (url, profundidad_actual). Se siembra con urls_semilla o
    # con los resultados de buscar la pregunta / las consultas de angulo.
    frontera: deque[tuple[str, int]] = deque()
    if urls_semilla:
        for u in urls_semilla:
            try:
                frontera.append((validar_url(u), 1))
            except UrlNoSegura:
                continue
    else:
        # Barrido multi-angulo: la pregunta + cada consulta extra (deduplicadas,
        # sin vacias). Con una sola pregunta el comportamiento es el de antes.
        queries: list[str] = []
        for q in [pregunta, *(consultas or [])]:
            q = (q or "").strip()
            if q and q not in queries:
                queries.append(q)
        errores: list[str] = []
        # Se juntan y deduplican los resultados de TODAS las consultas antes de
        # decidir que leer (dict por url normalizada = dedup conservando el
        # primero, que suele ser el mejor rankeado de esa consulta).
        candidatos: dict[str, dict] = {}
        for q in queries:
            consultas_usadas.append(q)
            r = search_web(q, count=max_paginas)
            if r.get("error") and not r.get("results"):
                errores.append(f"'{q}': {r['error']}")
                continue
            for res in r.get("results", []):
                clave = _norm(res.get("url", ""))
                if not clave or clave in candidatos:
                    continue
                candidatos[clave] = {
                    "url": res["url"], "title": res.get("title", ""),
                    "snippet": res.get("snippet", ""),
                }
        # Solo es error si NINGUNA consulta trajo nada (con multi-angulo, que
        # una falle no invalida la investigacion si otra encontro fuentes).
        if not candidatos:
            return {
                "pregunta": pregunta, "fuentes": [], "paginas_leidas": 0,
                "consultas": consultas_usadas,
                "error": f"Ninguna busqueda devolvio resultados ({'; '.join(errores)}). "
                "No inventes la respuesta: dilo, o reintenta, o pasa urls_semilla.",
            }
        # Rerank: leer primero lo mas relevante, no lo que llego antes.
        for cand in _rerank_candidatos(list(candidatos.values()), claves, anclas):
            frontera.append((cand["url"], 1))

    fuentes: list[dict] = []
    vistos_url: set[str] = set()
    por_dominio: dict[str, int] = {}
    leidas = 0

    while frontera and leidas < max_paginas:
        url, capa = frontera.popleft()
        clave_url = _norm(url)
        if clave_url in vistos_url:
            continue
        vistos_url.add(clave_url)
        dom = _dominio(url)
        if por_dominio.get(dom, 0) >= max_por_dominio:
            continue

        pagina = _leer_pagina(url)
        if pagina is None:
            continue
        leidas += 1
        por_dominio[dom] = por_dominio.get(dom, 0) + 1

        pasajes = _pasajes_relevantes(pagina["texto"], claves, n=3)
        if pasajes:
            fuentes.append({
                "url": pagina["url"], "title": pagina["title"], "pasajes": pasajes,
                # Que rasgos del sujeto confirma esta fuente (para desambiguar).
                "anclas": _anclas_presentes(pasajes, anclas),
            })

        # Recursion: encolar los enlaces mas relevantes una capa mas abajo.
        if capa < profundidad:
            for enlace in _ordenar_enlaces(pagina["enlaces"], claves)[:3]:
                if _norm(enlace) not in vistos_url:
                    frontera.append((enlace, capa + 1))

    resumen = "\n\n".join(
        f"[{i + 1}] {f['title'] or f['url']} — {f['url']}"
        + (f"  (rasgos: {', '.join(f['anclas'])})" if f.get("anclas") else "")
        + "\n" + "\n".join(f"  · {p}" for p in f["pasajes"])
        for i, f in enumerate(fuentes)
    )
    return {
        "pregunta": pregunta,
        "fuentes": fuentes,
        "paginas_leidas": leidas,
        "consultas": consultas_usadas,
        "resumen": resumen,
        "error": None if fuentes else "No se extrajo ningun pasaje relevante de las paginas leidas.",
    }
