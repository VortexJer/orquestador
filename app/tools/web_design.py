"""Recursos de DISEÑO WEB, todos gratis y sin clave. Le dan al que construye
una web material real -iconos, tipografias, paletas, avatares, logos- en vez
de dejar que el modelo escriba un nombre de fuente que no existe o un SVG de
icono a mano (que le sale roto).

APIs:
  - Iconify        -> 200.000+ iconos de 150+ colecciones (search_icons)
  - Google Fonts   -> catalogo real de fuentes, sin key (search_fonts)
  - The Color API  -> paletas armonicas a partir de un color (color_palette)
  - Simple Icons / DiceBear / Lorem Picsum / placehold.co / QR Server ->
                      URLs listas para incrustar (design_assets)
"""
from __future__ import annotations

import json
import os
from urllib.parse import quote

from app.tools.api_client import get_json, get_text


# --- Iconos: Iconify ---------------------------------------------------

def search_icons(query: str, limite: int = 12) -> dict:
    """Busca iconos en Iconify (200k+ de 150+ sets) y devuelve su nombre y la
    URL SVG lista para incrustar.

    Devuelve {query, resultados:[{nombre, set, url_svg}], total, error}. El
    modelo pone la URL en un <img src=...> o descarga el SVG. `nombre` es
    'prefijo:icono' (ej. 'mdi:home')."""
    if not query or not query.strip():
        return {"query": query, "resultados": [], "total": 0, "error": "Consulta vacia."}
    limite = max(1, min(int(limite or 12), 30))
    r = get_json("https://api.iconify.design/search",
                 params={"query": query.strip(), "limit": max(limite, 32)})
    if not r["ok"]:
        return {"query": query, "resultados": [], "total": 0,
                "error": f"no se pudo consultar Iconify ({r['error']})."}
    iconos = (r["data"] or {}).get("icons", [])
    resultados = []
    for nombre in iconos[:limite]:
        if ":" not in nombre:
            continue
        prefijo, icono = nombre.split(":", 1)
        resultados.append({
            "nombre": nombre, "set": prefijo,
            "url_svg": f"https://api.iconify.design/{prefijo}/{icono}.svg",
        })
    return {
        "query": query, "resultados": resultados,
        "total": (r["data"] or {}).get("total", len(resultados)),
        "error": None if resultados else "Sin iconos. Prueba un termino en ingles (home, cart, menu).",
    }


# --- Tipografias: Google Fonts (metadata sin key) ----------------------

_CACHE_FUENTES: list | None = None


def _cargar_fuentes() -> list:
    """El catalogo entero de Google Fonts (~2,7 MB), una vez por proceso. El
    endpoint lleva un prefijo anti-XSSI )]}' que hay que quitar antes de
    parsear el JSON."""
    global _CACHE_FUENTES
    if _CACHE_FUENTES is None:
        r = get_text("https://fonts.google.com/metadata/fonts")
        if not r["ok"] or not r["text"]:
            return []
        txt = r["text"]
        if txt.startswith(")]}'"):
            txt = txt.split("\n", 1)[1] if "\n" in txt else txt[4:]
        try:
            _CACHE_FUENTES = json.loads(txt).get("familyMetadataList", [])
        except ValueError:
            _CACHE_FUENTES = []
    return _CACHE_FUENTES


def _pesos_de(fuente: dict) -> list[int]:
    """Los pesos numericos disponibles (400, 700...), sin duplicar la variante
    italica ('400i' cuenta como 400)."""
    pesos = set()
    for clave in fuente.get("fonts", {}):
        base = clave[:-1] if clave.endswith("i") else clave
        if base.isdigit():
            pesos.add(int(base))
    return sorted(pesos)


def search_fonts(query: str = "", categoria: str = "", limite: int = 8) -> dict:
    """Busca tipografias REALES de Google Fonts y devuelve el link listo para
    usar. Evita que el modelo invente un nombre de fuente o pesos que no
    existen.

    - query: parte del nombre (ej. 'roboto'); vacio = las mas populares.
    - categoria: 'serif', 'sans serif', 'display', 'handwriting', 'monospace'.
    - limite: cuantas (1-20).

    Devuelve {resultados:[{family, category, pesos, link}], total, error}. El
    `link` es el <link href> de Google Fonts con los pesos correctos."""
    lista = _cargar_fuentes()
    if not lista:
        return {"resultados": [], "total": 0, "error": "No se pudo cargar el catalogo de Google Fonts (reintenta)."}
    limite = max(1, min(int(limite or 8), 20))
    q = (query or "").strip().lower()
    cat = (categoria or "").strip().lower()

    encontradas = []
    for f in lista:
        if q and q not in f.get("family", "").lower():
            continue
        if cat and cat not in f.get("category", "").lower():
            continue
        pesos = _pesos_de(f)
        fam_url = quote(f.get("family", ""))
        link = (f"https://fonts.googleapis.com/css2?family={fam_url}"
                + (f":wght@{';'.join(map(str, pesos))}" if pesos else "") + "&display=swap")
        encontradas.append({
            "family": f.get("family"), "category": f.get("category"),
            "pesos": pesos, "link": link, "_pop": f.get("popularity", 99999),
        })

    # Menor 'popularity' = mas popular: las mejores primero.
    encontradas.sort(key=lambda x: x["_pop"] or 99999)
    for e in encontradas:
        e.pop("_pop", None)
    return {
        "resultados": encontradas[:limite], "total": len(encontradas),
        "error": None if encontradas else "Sin coincidencias. Prueba otro nombre o categoria.",
    }


# --- Paletas de color: The Color API -----------------------------------

_MODOS_COLOR = ("monochrome", "monochrome-dark", "monochrome-light", "analogic",
                "complement", "analogic-complement", "triad", "quad")


def color_palette(base: str, modo: str = "analogic", cantidad: int = 5) -> dict:
    """Genera una paleta armonica a partir de un color base (The Color API).

    - base: color en hex, con o sin '#' (ej. '#0047AB' o '0047AB').
    - modo: monochrome, analogic, complement, analogic-complement, triad, quad.
    - cantidad: cuantos colores (3-8).

    Devuelve {base, modo, colores:[{hex, nombre}], error}. Para dar cohesion
    visual en vez de elegir colores sueltos a ojo."""
    if not base or not base.strip():
        return {"base": base, "modo": modo, "colores": [], "error": "Falta el color base (hex)."}
    hexval = base.strip().lstrip("#")
    modo = modo if modo in _MODOS_COLOR else "analogic"
    cantidad = max(3, min(int(cantidad or 5), 8))
    r = get_json("https://www.thecolorapi.com/scheme",
                 params={"hex": hexval, "mode": modo, "count": cantidad})
    if not r["ok"]:
        return {"base": base, "modo": modo, "colores": [],
                "error": f"no se pudo generar la paleta ({r['error']})."}
    colores = []
    for c in (r["data"] or {}).get("colors", []):
        colores.append({
            "hex": (c.get("hex") or {}).get("value"),
            "nombre": (c.get("name") or {}).get("value"),
        })
    return {"base": f"#{hexval.upper()}", "modo": modo, "colores": colores,
            "error": None if colores else "La API no devolvio colores."}


# --- Assets por URL: avatares, placeholders, logos, QR -----------------

_ASSETS = ("avatar", "placeholder", "photo", "logo", "qr")


def design_assets(kind: str, seed: str = "", brand: str = "", texto: str = "",
                  width: int = 400, height: int = 300, estilo: str = "") -> dict:
    """Construye URLs LISTAS PARA INCRUSTAR de servicios de assets gratis. El
    valor es que el modelo no conoce estas URLs; aqui las obtiene bien formadas.

    kind:
      - 'avatar'      -> DiceBear (avatar SVG por 'seed'; 'estilo' opcional:
                         thumbs, avataaars, bottts, initials, identicon...).
      - 'placeholder' -> placehold.co (recuadro solido width x height, con
                         'texto' opcional).
      - 'photo'       -> Lorem Picsum (foto real de relleno width x height).
      - 'logo'        -> Simple Icons (logo de marca por 'brand', ej. 'github',
                         'stripe', 'whatsapp'); se verifica que exista.
      - 'qr'          -> QR Server (codigo QR de 'texto'/'data', width x width).

    Devuelve {kind, url, existe?, nota}."""
    k = (kind or "").strip().lower()
    if k not in _ASSETS:
        return {"kind": kind, "url": "", "error": f"kind '{kind}' no valido. Usa: {', '.join(_ASSETS)}."}
    w = max(16, min(int(width or 400), 2000))
    h = max(16, min(int(height or 300), 2000))

    if k == "avatar":
        est = (estilo or "thumbs").strip().lower()
        s = quote(seed or "usuario")
        return {"kind": k, "url": f"https://api.dicebear.com/9.x/{est}/svg?seed={s}",
                "nota": "SVG de avatar; cambia 'seed' para otra cara, 'estilo' para otro set."}
    if k == "placeholder":
        url = f"https://placehold.co/{w}x{h}"
        if texto:
            url += f"?text={quote(texto)}"
        return {"kind": k, "url": url, "nota": "Recuadro solido de relleno."}
    if k == "photo":
        return {"kind": k, "url": f"https://picsum.photos/{w}/{h}",
                "nota": "Foto real aleatoria de relleno (Lorem Picsum). Añade '?random=N' para variar."}
    if k == "qr":
        data = quote(texto or seed or "https://example.com")
        return {"kind": k, "url": f"https://api.qrserver.com/v1/create-qr-code/?data={data}&size={w}x{w}",
                "nota": "PNG de codigo QR."}
    # logo: Simple Icons por slug, verificando que la marca exista.
    slug = (brand or seed or "").strip().lower().replace(" ", "").replace(".", "")
    if not slug:
        return {"kind": k, "url": "", "error": "Para un logo indica 'brand' (ej. 'github', 'stripe')."}
    url = f"https://cdn.simpleicons.org/{slug}"
    verif = get_text(url)
    existe = verif["ok"] and "svg" in (verif.get("text") or "").lower()
    return {
        "kind": k, "url": url, "existe": existe,
        "nota": ("Logo SVG de la marca." if existe else
                 f"Simple Icons no tiene la marca '{slug}'. Prueba otro slug o busca su logo de otra forma."),
    }


# --- Biblioteca UIverse (galaxy repo, open-source MIT) -----------------
#
# La mayor libreria open-source de elementos UI (5.800+): botones, cards,
# loaders, toggles, formularios... con sus animaciones, listos para
# copiar-pegar. Su base entera vive en el repo github uiverse-io/galaxy, un
# .html autocontenido (HTML+CSS inline) por elemento, organizado por
# categoria. Se lee de ahi (no hay API oficial, pero el repo ES la base de
# datos, y es MIT).

# Nombre amable -> carpeta EXACTA del repo (ojo: 'loaders' va en minuscula y
# hay dos con guion). Se aceptan sinonimos en es/en.
_UIVERSE_CATS = {
    "buttons": "Buttons", "button": "Buttons", "botones": "Buttons", "boton": "Buttons",
    "cards": "Cards", "card": "Cards", "tarjetas": "Cards", "tarjeta": "Cards",
    "checkboxes": "Checkboxes", "checkbox": "Checkboxes", "casillas": "Checkboxes",
    "forms": "Forms", "form": "Forms", "formularios": "Forms", "formulario": "Forms",
    "inputs": "Inputs", "input": "Inputs", "campos": "Inputs",
    "loaders": "loaders", "loader": "loaders", "spinner": "loaders",
    "spinners": "loaders", "cargando": "loaders", "carga": "loaders",
    "notifications": "Notifications", "notification": "Notifications",
    "notificaciones": "Notifications", "notificacion": "Notifications",
    "patterns": "Patterns", "pattern": "Patterns", "patrones": "Patterns",
    "fondos": "Patterns", "backgrounds": "Patterns", "background": "Patterns",
    "radio-buttons": "Radio-buttons", "radio": "Radio-buttons", "radios": "Radio-buttons",
    "toggle-switches": "Toggle-switches", "toggle": "Toggle-switches",
    "toggles": "Toggle-switches", "switch": "Toggle-switches", "switches": "Toggle-switches",
    "tooltips": "Tooltips", "tooltip": "Tooltips",
}
# Nombres simples para el enum del esquema (uno por carpeta).
UIVERSE_CATEGORIAS = ("buttons", "cards", "checkboxes", "forms", "inputs", "loaders",
                      "notifications", "patterns", "radio", "toggle", "tooltips")

_CACHE_UIVERSE: dict[str, list] = {}


def _github_headers() -> dict | None:
    # La API de contents de GitHub sin token permite 60 peticiones/hora; con
    # GITHUB_TOKEN (el mismo que usa search_code) sube a 5.000. Se listan
    # pocas categorias por sesion y ademas se cachea, asi que sin token
    # tambien alcanza.
    tok = os.environ.get("GITHUB_TOKEN")
    return {"Authorization": f"Bearer {tok}"} if tok else None


def _listar_uiverse(folder: str) -> list:
    if folder not in _CACHE_UIVERSE:
        r = get_json(f"https://api.github.com/repos/uiverse-io/galaxy/contents/{folder}",
                     headers=_github_headers())
        _CACHE_UIVERSE[folder] = r["data"] if r["ok"] and isinstance(r["data"], list) else []
    return _CACHE_UIVERSE[folder]


def uiverse(categoria: str, limite: int = 4) -> dict:
    """Trae una MUESTRA VARIADA de elementos UI de UIverse (botones, cards,
    loaders, toggles...) con su animacion, listos para copiar-pegar (HTML+CSS
    inline). Devuelve el codigo completo de los primeros para que el modelo
    vea diseños reales y adapte uno.

    NO acepta busqueda por estilo a proposito: en el repo los elementos se
    llaman con un slug aleatorio (autor_animal-N), asi que 'neon'/'3d' no
    matchearia nada. En vez de eso se devuelve una muestra repartida por toda
    la categoria (variedad real).

    - categoria: buttons, cards, checkboxes, forms, inputs, loaders,
      notifications, patterns, radio, toggle, tooltips.
    - limite: cuantos elementos (1-8). Se trae el codigo de los 2 primeros.

    Devuelve {categoria, resultados:[{nombre, url, codigo?}],
    total_en_categoria, nota, error}. Adapta el codigo al diseño del proyecto
    (sus colores/tipografia), no lo pegues con los valores de demo."""
    folder = _UIVERSE_CATS.get((categoria or "").strip().lower())
    if not folder:
        return {"categoria": categoria, "resultados": [], "error": (
            f"categoria '{categoria}' no valida. Usa: {', '.join(UIVERSE_CATEGORIAS)}.")}
    limite = max(1, min(int(limite or 4), 8))
    entradas = _listar_uiverse(folder)
    htmls = [e for e in entradas if e.get("name", "").endswith(".html")]
    if not htmls:
        return {"categoria": folder, "resultados": [], "error": (
            "no se pudo listar la biblioteca (posible limite de GitHub: pon GITHUB_TOKEN).")}

    # Muestreo repartido por toda la lista (no los N primeros, que serian del
    # mismo autor alfabetico) para que la muestra sea variada.
    n = len(htmls)
    if limite >= n:
        elegidos = htmls
    else:
        paso = n / limite
        elegidos = [htmls[int(i * paso)] for i in range(limite)]

    resultados = []
    for idx, e in enumerate(elegidos):
        item = {"nombre": e["name"][:-5], "url": e.get("download_url")}
        if idx < 2 and item["url"]:  # el codigo de los 2 primeros
            rc = get_text(item["url"])
            if rc["ok"] and rc["text"]:
                item["codigo"] = rc["text"][:3000]
        resultados.append(item)

    return {
        "categoria": folder, "resultados": resultados, "total_en_categoria": n,
        "nota": ("Muestra variada; el codigo de los demas esta en su 'url'. Todos MIT, "
                 "de UIverse.io. Adapta colores/tipografia a tu proyecto."),
        "error": None,
    }
