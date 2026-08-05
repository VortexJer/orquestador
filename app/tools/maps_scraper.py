"""Datos REALES de un negocio desde Google Maps (sin API key) -
adaptador sobre el paquete `google-maps-scraper` (gmaps_scraper), que
usa un navegador headless (Firefox via Playwright) por debajo.
Normaliza su salida a un dict estable.

AVISO LEGAL: scrapear Google Maps va contra sus Terminos de Servicio y
puede romperse cuando Google cambie su HTML. Es para montar la web del
propio negocio (con su consentimiento), no para recoleccion masiva -
decision consciente del usuario de este proyecto, ver conversacion de
diseño de web-builder-specialist.

Instalacion (una vez):
    pip install google-maps-scraper
    playwright install firefox

`fetch_business_from_maps` devuelve solo la foto PRINCIPAL (limite del
paquete de base). `fetch_gallery_photos_from_maps` (mas abajo) cubre
ese hueco con scraping propio (no del paquete): abre la galeria de
fotos del negocio en el mismo navegador headless y devuelve varias URL
reales - util sobre todo para encontrar una foto de la CARTA/MENU
fisica que el propio negocio subio a su ficha, mas fiable que
inventarla o buscarla en bancos de imagenes genericos.
"""
from __future__ import annotations

import re
import unicodedata
import urllib.parse

_CAMPOS = [
    "name", "rating", "reviews", "review_count", "reviews_count", "category",
    "address", "phone", "website", "hours", "opening_hours", "latitude",
    "longitude", "place_id", "url", "image", "image_url", "thumbnail", "photo_count",
]


def _a_dict(obj) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return dict(obj)
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "__dict__"):
        return dict(vars(obj))
    return {c: getattr(obj, c, None) for c in _CAMPOS if hasattr(obj, c)}


def _maps_url(consulta: str, idioma: str = "es") -> str:
    q = urllib.parse.quote_plus(consulta)
    # hl=<idioma> le pide a Google Maps la interfaz/datos en ese idioma -
    # sin esto, el scraper puede devolver categoria/horario en el idioma
    # que Google le asigne por default al navegador headless (visto en
    # la practica: aleman, sin relacion con el idioma de la consulta).
    return f"https://www.google.com/maps/search/?api=1&query={q}&hl={idioma}"


def _limpiar(v):
    """Quita iconos de Maps (caracteres de uso privado) y caracteres de
    control, colapsa espacios/saltos sobrantes."""
    if not isinstance(v, str):
        return v
    v = "".join(
        ch for ch in v
        if not (0xE000 <= ord(ch) <= 0xF8FF) and unicodedata.category(ch)[0] != "C"
    )
    return " ".join(v.split()).strip() or None


def _coords(d: dict) -> dict | None:
    lat, lon = d.get("latitude"), d.get("longitude")
    if lat is not None and lon is not None:
        return {"lat": lat, "lon": lon}
    return None


def fetch_business_from_maps(consulta: str, idioma: str = "es") -> dict:
    """Devuelve dict con datos del negocio (sin descargar fotos - eso lo
    hace el caller via image_search/web_fetch si hace falta, para no
    escribir archivos fuera del control del ToolExecutor). ok=False +
    error/pista si el paquete falta o el scraping fallo."""
    try:
        from gmaps_scraper import scrape_place
    except ImportError:
        return {
            "ok": False,
            "error": "Falta el paquete 'google-maps-scraper'.",
            "pista": "Instalalo con: pip install google-maps-scraper && playwright install firefox",
        }

    url = consulta if consulta.strip().startswith("http") else _maps_url(consulta, idioma)
    try:
        resultado = scrape_place(url)
    except Exception as exc:  # noqa: BLE001 - se devuelve como resultado, no como crash
        return {
            "ok": False,
            "error": f"Fallo al scrapear Maps: {exc}",
            "pista": (
                "Revisa conexion / que playwright tenga Firefox instalado "
                "(playwright install firefox). Google tambien puede estar limitando."
            ),
        }

    place = getattr(resultado, "place", None) or resultado
    d = _a_dict(place)

    nombre = _limpiar(d.get("name"))
    if not nombre or nombre.lower() in ("resultados", "results") or not d.get("address"):
        return {
            "ok": False,
            "error": f"La busqueda '{consulta}' no resolvio a un negocio concreto.",
            "pista": (
                "Usa un nombre mas especifico (con la ciudad correcta), o pega la URL "
                "exacta de la ficha de Google Maps del negocio."
            ),
        }

    return {
        "ok": True,
        "consulta": consulta,
        "nombre": nombre,
        "categoria": _limpiar(d.get("category")),
        "direccion": _limpiar(d.get("address")),
        "telefono": _limpiar(d.get("phone")),
        "web": d.get("website"),
        "rating": d.get("rating"),
        "n_resenas": d.get("review_count") or d.get("reviews_count") or d.get("reviews"),
        "horario": d.get("opening_hours") or d.get("hours"),
        "coordenadas": _coords(d),
        "google_maps_url": d.get("url"),
        "foto_principal_url": d.get("image") or d.get("image_url") or d.get("thumbnail"),
    }


_SIZE_SUFFIX_RE = re.compile(r"=w\d+-h\d+[\w-]*$|=s\d+[\w-]*$")


def _en_resolucion_alta(url: str, lado: int = 1000) -> str:
    """Las URL de fotos de Maps vienen con un sufijo de tamaño
    ('=w203-h152-k-no', '=s338-k-no') que se puede reemplazar por uno
    mas grande para pedirle a Google la misma foto en mejor resolucion
    (necesario para que el modelo de vision pueda leer texto chico de
    una carta/menu fotografiada)."""
    base = _SIZE_SUFFIX_RE.sub("", url)
    return f"{base}=w{lado}-h{lado}-k-no"


def _abrir_ficha_negocio(page, url_negocio: str, idioma: str) -> None:
    """Navega a la ficha del negocio y la deja lista para interactuar
    (cookies aceptadas/rechazadas, panel principal cargado). Boilerplate
    compartido por todas las funciones que abren Playwright directo
    sobre una ficha ya resuelta (galeria de fotos, menu, reseñas)."""
    page.goto(url_negocio, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(1200)

    # Dialogo de consentimiento de cookies (varia segun region/idioma) -
    # sin esto la pagina se queda en el aviso y nunca llega a la ficha.
    for texto in ("Rechazar todo", "Aceptar todo", "Reject all", "Accept all"):
        boton = page.query_selector(f'button:has-text("{texto}")')
        if boton and boton.is_visible():
            boton.click()
            page.wait_for_timeout(1200)
            break

    page.wait_for_selector("h1", timeout=15000)
    page.wait_for_timeout(800)


def _resolver_url_negocio(consulta: str, idioma: str) -> tuple[dict | None, str | None]:
    """Corre fetch_business_from_maps y devuelve (datos, url) - o
    (resultado_error, None) si no se pudo resolver, para que el caller
    lo propague tal cual."""
    datos = fetch_business_from_maps(consulta, idioma)
    if not datos.get("ok"):
        return datos, None
    url_negocio = datos.get("google_maps_url")
    if not url_negocio:
        return {
            "ok": False,
            "error": "Se encontro el negocio pero no su URL de Maps, no se puede abrir la ficha.",
            "pista": None,
        }, None
    return datos, url_negocio


def fetch_gallery_photos_from_maps(consulta: str, idioma: str = "es", max_fotos: int = 6) -> dict:
    """Abre la galeria de fotos del negocio (scraping propio, NO del
    paquete gmaps_scraper - ese solo expone la foto principal) y
    devuelve varias URLs reales en buena resolucion. Pensado sobre todo
    para encontrar la foto de la carta/menu que muchos negocios suben a
    su ficha de Maps: combinar con `extract_menu_text` (vision local)
    sobre cada candidata para leer el texto.

    Requiere resolver primero el negocio via `fetch_business_from_maps`
    internamente (reusa esa misma logica probada) y despues abre la
    galeria con Playwright directo - la galeria en si no la cubre el
    paquete, asi que es codigo propio y mas fragil ante cambios de
    Google. Ante cualquier fallo, devuelve ok=False en vez de crashear."""
    datos, url_negocio = _resolver_url_negocio(consulta, idioma)
    if url_negocio is None:
        return datos

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "ok": False,
            "error": "Falta Playwright en el entorno.",
            "pista": "Instalalo con: pip install playwright && playwright install firefox",
        }

    try:
        with sync_playwright() as p:
            browser = p.firefox.launch(headless=True)
            context = browser.new_context(viewport={"width": 1600, "height": 1000}, locale=idioma)
            page = context.new_page()
            try:
                from playwright_stealth import stealth_sync
                stealth_sync(page)
            except ImportError:
                pass

            _abrir_ficha_negocio(page, url_negocio, idioma)

            boton_fotos = page.query_selector(
                'button[aria-label*="foto"], button[aria-label*="Foto"], button[jsaction*="photo"]'
            )
            if not boton_fotos:
                browser.close()
                return {
                    "ok": False,
                    "error": "No se encontro el boton de fotos en la ficha del negocio.",
                    "pista": "Puede que este negocio no tenga fotos publicas en Maps.",
                }
            boton_fotos.click()
            page.wait_for_timeout(2000)

            # Las miniaturas de la galeria se pintan como background-image
            # en divs (NO como <img src>) - de ahi que haya que leer el
            # computed style en vez de buscar elementos <img>. Se filtran
            # las muy chicas (avatares de reseñadores, ~24-48px) quedandonos
            # solo con fotos reales del negocio.
            urls_crudas = page.evaluate(
                """() => {
                    const vistos = new Set();
                    const out = [];
                    document.querySelectorAll('div').forEach((el) => {
                        const bg = getComputedStyle(el).backgroundImage;
                        if (!bg || !bg.includes('googleusercontent')) return;
                        const rect = el.getBoundingClientRect();
                        if (rect.width < 100 || rect.height < 100) return;
                        const m = bg.match(/url\\("?(.*?)"?\\)/);
                        if (!m) return;
                        const base = m[1].split('=')[0];
                        if (vistos.has(base)) return;
                        vistos.add(base);
                        out.push(m[1]);
                    });
                    return out;
                }"""
            )
            browser.close()
    except Exception as exc:  # noqa: BLE001 - se devuelve como resultado, no como crash
        return {
            "ok": False,
            "error": f"Fallo al abrir la galeria de fotos: {exc}",
            "pista": "Google tambien puede estar limitando/bloqueando el scraping en este momento.",
        }

    fotos = [_en_resolucion_alta(u) for u in urls_crudas[:max_fotos]]
    if not fotos:
        return {
            "ok": False,
            "error": "Se abrio la galeria pero no se pudo extraer ninguna foto.",
            "pista": "El DOM de Google Maps puede haber cambiado.",
        }
    return {"ok": True, "nombre": datos["nombre"], "fotos_galeria": fotos}


_JS_EXTRAER_RESENAS = """() => {
    // Cada reseña real es una tarjeta 'div.jftiEf[aria-label]' - el
    // aria-label ES el nombre real del autor (dato estable, a
    // diferencia de la clase '.wiI7pd' del texto en si, que Google
    // reusa TAMBIEN para la respuesta del propietario dentro de la
    // misma tarjeta cuando el negocio contesta). Por eso: agarrar SOLO
    // el primer '.wiI7pd' de cada tarjeta (indice 0, el texto de la
    // reseña real) e ignorar el resto (respuestas), y leer la
    // valoracion del span de estrellas dentro de la misma tarjeta -
    // scrapear ambos datos juntos evita mezclar reseñas de clientes
    // con contestaciones del propio negocio, y permite priorizar las
    // mejor valoradas en vez de tomar las primeras que aparezcan.
    const tarjetas = document.querySelectorAll('div.jftiEf[aria-label]');
    const out = [];
    tarjetas.forEach((tarjeta) => {
        const autor = tarjeta.getAttribute('aria-label') || '';
        const textos = tarjeta.querySelectorAll('.wiI7pd');
        if (textos.length === 0) return;
        const texto = textos[0].textContent.trim();
        if (!texto) return;
        const starEl = tarjeta.querySelector('span[role="img"][aria-label*="estrella"]');
        const match = starEl ? starEl.getAttribute('aria-label').match(/(\\d+)/) : null;
        const estrellas = match ? parseInt(match[1], 10) : null;
        out.push({autor, estrellas, texto});
    });
    return out;
}"""

# Marcadores de UI de Maps (nav, footer) que rodean el texto real del
# menu cuando se vuelca todo el texto de la pestaña "Carta" como lista
# de nodos hoja - no hay un selector CSS estable para "nombre de plato"
# (a diferencia de las reseñas, que si tienen la clase '.wiI7pd'), asi
# que se recorta heuristicamente entre el marcador de inicio ('Popular'
# o 'Menú') y el primer marcador de cierre conocido.
_CARTA_INICIO = ("Popular", "Menú")
_CARTA_FIN = (
    "Iniciar sesión", "No disponible", "Aplicaciones de Google", "Capas",
    "Ocultar el panel lateral", "Mostrar el panel lateral",
)


def _recortar_items_carta(nodos: list[str]) -> list[str]:
    inicio = None
    for marcador in _CARTA_INICIO:
        if marcador in nodos:
            inicio = nodos.index(marcador) + 1
            break
    if inicio is None:
        return []
    fin = len(nodos)
    for marcador in _CARTA_FIN:
        if marcador in nodos[inicio:]:
            fin = min(fin, inicio + nodos[inicio:].index(marcador))
    return [n for n in nodos[inicio:fin] if n != "Popular"]


def fetch_menu_and_reviews_from_maps(
    consulta: str, idioma: str = "es", max_items: int = 25, max_resenas: int = 6
) -> dict:
    """Trae DOS señales reales para decidir el contenido/color de la web
    (ver skills/web-builder-specialist.md seccion 2.1): los nombres de
    plato de la pestaña "Carta" de Maps (si el negocio la tiene - texto
    real, no una foto que haya que OCRear) y un puñado de reseñas REALES
    de clientes (nunca respuestas del propio negocio, que Google mezcla
    en el mismo bloque de texto - ver _JS_EXTRAER_RESENAS), ordenadas de
    mejor a peor valoracion (mas estrellas primero) y recortadas a
    max_resenas DESPUES de ordenar, asi las que devuelve son las mejores
    disponibles, no las primeras que aparecen en la pagina. Cada reseña
    trae 'autor' (nombre real del cliente en Maps), 'estrellas' (int o
    None si no se pudo leer) y 'texto'. Scraping propio (no lo cubre el
    paquete gmaps_scraper), asi que ambas listas pueden venir vacias si
    el negocio no tiene esa seccion o Google cambio el HTML - eso no es
    un error, es normal: 'items_carta'/'reseñas' vacios simplemente
    significa que no hay esa señal para este negocio en particular.

    IMPORTANTE para quien construye la web con esto: 'texto' es la
    reseña REAL, no un resumen - si se usa como testimonio en la web,
    tiene que citarse tal cual (o recortada con "...", nunca reescrita
    ni parafraseada) y atribuirse al 'autor' real que trae esta funcion,
    NUNCA a un nombre inventado (ver
    hard_cases/by_domain/web.json: web-invented-fake-testimonials).

    A diferencia de fetch_menu_photos_from_maps (que depende del modelo
    de vision local para leer una foto y es menos confiable, ~75% en
    pruebas), esto es texto real de Maps cuando existe - pruébalo
    PRIMERO, y usa fetch_menu_photos_from_maps/extract_menu_text solo si
    el negocio no tiene pestaña de Carta."""
    datos, url_negocio = _resolver_url_negocio(consulta, idioma)
    if url_negocio is None:
        return datos

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "ok": False,
            "error": "Falta Playwright en el entorno.",
            "pista": "Instalalo con: pip install playwright && playwright install firefox",
        }

    try:
        with sync_playwright() as p:
            browser = p.firefox.launch(headless=True)
            context = browser.new_context(viewport={"width": 1600, "height": 1000}, locale=idioma)
            page = context.new_page()
            try:
                from playwright_stealth import stealth_sync
                stealth_sync(page)
            except ImportError:
                pass

            _abrir_ficha_negocio(page, url_negocio, idioma)

            resenas_todas = page.evaluate(_JS_EXTRAER_RESENAS)
            # Mejor valoracion primero (None al final, tratado como "sin dato" -
            # no como 0, para no hundir reseñas cuyo rating no se pudo leer).
            resenas_todas.sort(key=lambda r: r["estrellas"] if r["estrellas"] is not None else -1, reverse=True)
            resenas = resenas_todas[:max_resenas]

            items_carta: list[str] = []
            carta_tab = page.query_selector('button[role="tab"]:has-text("Carta")')
            if carta_tab:
                carta_tab.click()
                page.wait_for_timeout(2000)
                nodos = page.evaluate(
                    """() => {
                        const out = [];
                        document.querySelectorAll('body *').forEach((el) => {
                            if (el.children.length === 0) {
                                const t = el.textContent.trim();
                                if (t.length > 1 && t.length < 40) out.push(t);
                            }
                        });
                        return out;
                    }"""
                )
                items_carta = _recortar_items_carta(nodos)[:max_items]

            browser.close()
    except Exception as exc:  # noqa: BLE001 - se devuelve como resultado, no como crash
        return {
            "ok": False,
            "error": f"Fallo al abrir la ficha del negocio: {exc}",
            "pista": "Google tambien puede estar limitando/bloqueando el scraping en este momento.",
        }

    return {
        "ok": True,
        "nombre": datos["nombre"],
        "items_carta": items_carta,
        "resenas": resenas,
    }
