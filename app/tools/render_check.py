"""El "ejecutador que comprueba" del especialista web-builder: renderiza
la pagina en un navegador real (headless Chromium via Playwright) y
devuelve una captura + errores de consola/JS/requests fallidos. Un HTML
que "se ve bien" leyendo el codigo puede tener JS roto que solo aparece
al ejecutarlo de verdad - esto es el equivalente, para web, de correr
pytest en vez de solo leer el codigo.

Requiere el navegador instalado una vez:
    pip install playwright
    playwright install chromium
"""
from __future__ import annotations

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

_DEFAULT_TIMEOUT_MS = 15_000


class _SilentHandler(SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler escribe cada GET en stderr - aqui se
    sirve una pagina entera con decenas de assets y ese ruido enterraria
    el resultado real de la verificacion."""

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - firma heredada
        pass
# Detecta que secciones semanticas (header/footer/section/article - la
# estructura que la seccion 12 de web-builder-specialist.md exige usar
# siempre) estan al menos parcialmente visibles en la posicion de scroll
# actual, para poder decirle a quien lee el resultado ("en la seccion
# X") en vez de solo una posicion vaga dentro de la imagen (arriba/
# centro/abajo). Se identifica cada una por su id si lo tiene, o si no
# por el texto de su primer h1/h2/h3.
_JS_SECCIONES_VISIBLES = """() => {
    const vh = window.innerHeight;
    const candidatos = document.querySelectorAll('header, footer, section, article');
    const visibles = [];
    for (const el of candidatos) {
        const r = el.getBoundingClientRect();
        if (r.bottom <= 0 || r.top >= vh) continue;
        let nombre = el.id || '';
        if (!nombre) {
            const h = el.querySelector('h1, h2, h3');
            nombre = h ? h.textContent.trim().slice(0, 50) : ('<' + el.tagName.toLowerCase() + '>');
        }
        visibles.push(nombre);
    }
    return visibles;
}"""

# Contraste WCAG DETERMINISTA (sin modelo de vision): mide el color del texto
# contra su fondo EFECTIVO leyendo los estilos computados del navegador real, y
# calcula el ratio WCAG. Atrapa el caso "grave" que antes dependia de un modelo
# de vision ~75% que se inventaba pegas: texto invisible o casi (mismo color que
# su fondo, gris sobre gris). Politica CONSERVADORA (precision sobre
# exhaustividad, para no reintroducir falsos positivos): si el fondo es una
# imagen o un gradiente NO se puede saber el color exacto por CSS -> se OMITE
# ese elemento en vez de arriesgar un aviso falso. Umbrales WCAG AA: 4.5:1
# normal, 3:1 para texto grande (>=24px, o >=18.66px en negrita).
_JS_CONTRASTE = """() => {
    const parseRGB = (s) => {
        const m = s.match(/rgba?\\(([^)]+)\\)/);
        if (!m) return null;
        const p = m[1].split(',').map(x => parseFloat(x));
        return {r: p[0], g: p[1], b: p[2], a: (p[3] === undefined ? 1 : p[3])};
    };
    const lum = (c) => {
        const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
        return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
    };
    const ratio = (a, b) => {
        const L1 = Math.max(lum(a), lum(b)), L2 = Math.min(lum(a), lum(b));
        return (L1 + 0.05) / (L2 + 0.05);
    };
    const fondoEfectivo = (el) => {
        let node = el;
        while (node && node.nodeType === 1) {
            const cs = getComputedStyle(node);
            if (cs.backgroundImage && cs.backgroundImage !== 'none') return null; // indeterminable -> omitir
            const bg = parseRGB(cs.backgroundColor);
            if (bg && bg.a > 0) return bg;
            node = node.parentElement;
        }
        return {r: 255, g: 255, b: 255, a: 1}; // sin fondo declarado: lienzo blanco
    };
    const problemas = [];
    const vistos = new Set();
    for (const el of document.querySelectorAll('body *')) {
        const propio = Array.from(el.childNodes)
            .filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join('');
        if (propio.length < 2) continue;  // solo elementos con texto propio directo
        const cs = getComputedStyle(el);
        if (cs.visibility === 'hidden' || cs.display === 'none' || parseFloat(cs.opacity) === 0) continue;
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;
        const color = parseRGB(cs.color);
        if (!color || color.a === 0) continue;
        const fondo = fondoEfectivo(el);
        if (!fondo) continue;  // fondo imagen/gradiente: no se puede medir -> omitir
        const cr = ratio(color, fondo);
        const fs = parseFloat(cs.fontSize);
        const grande = fs >= 24 || (fs >= 18.66 && parseInt(cs.fontWeight) >= 700);
        const minimo = grande ? 3.0 : 4.5;
        if (cr < minimo) {
            const clave = cs.color + '|' + fondo.r + ',' + fondo.g + ',' + fondo.b;
            if (vistos.has(clave)) continue;   // un aviso por combinacion color/fondo
            vistos.add(clave);
            problemas.push({
                texto: propio.slice(0, 40),
                ratio: Math.round(cr * 100) / 100,
                minimo: minimo,
                color: cs.color,
                fondo: 'rgb(' + fondo.r + ', ' + fondo.g + ', ' + fondo.b + ')',
            });
        }
    }
    return problemas;
}"""

# Imagenes rotas o DEFORMADAS, deterministas: una que no cargo tiene
# naturalWidth===0; una deformada tiene un aspecto renderizado que no coincide
# con su aspecto natural. object-fit cover/contain/scale-down NO deforman (la
# imagen se recorta o encaja sin estirarse), asi que se omiten - solo 'fill'
# (el default) y 'none' pueden estirar.
_JS_IMAGENES = """() => {
    const problemas = [];
    for (const img of document.querySelectorAll('img')) {
        const cs = getComputedStyle(img);
        if (cs.display === 'none' || cs.visibility === 'hidden') continue;
        const r = img.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;
        const src = (img.currentSrc || img.src || '').slice(-70);
        if (!img.complete || img.naturalWidth === 0) {
            problemas.push({src: src, problema: 'no cargo (naturalWidth=0)'});
            continue;
        }
        if (['cover', 'contain', 'scale-down'].includes(cs.objectFit)) continue;
        const arRender = r.width / r.height;
        const arNatural = img.naturalWidth / img.naturalHeight;
        const desvio = Math.abs(arRender - arNatural) / arNatural;
        if (desvio > 0.15) {
            problemas.push({
                src: src,
                problema: 'deformada (aspecto ' + arRender.toFixed(2) + ' vs natural ' + arNatural.toFixed(2) + ')',
            });
        }
    }
    return problemas;
}"""

# Menu movil (hamburguesa) DETERMINISTA, sin modelo de vision: en la pasada
# movil, el nav de escritorio suele estar oculto y hay un boton (normalmente
# arriba a la derecha) que lo despliega. Un menu que NO abre al pulsarlo deja
# al usuario de movil sin poder navegar - un bug grave que ni las capturas
# estaticas ni el lint detectan (el boton se ve, pero no hace nada). Este JS
# localiza el candidato mas probable y lo ETIQUETA; el click y la comprobacion
# de "antes/despues" se hacen desde Python (necesitan esperar la animacion).
# Helpers compartidos por deteccion y comprobacion.
_JS_MENU_HELPERS = """
    const _vis = (el) => {
        if (!el) return false;
        const cs = getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && r.top < window.innerHeight;
    };
    const _contarLinksNav = () => {
        let n = 0;
        for (const a of document.querySelectorAll('header a, nav a')) if (_vis(a)) n++;
        return n;
    };
"""
_JS_MENU_DETECTAR = "() => {" + _JS_MENU_HELPERS + """
    const puntua = (el) => {
        if (!_vis(el)) return -1;
        let s = 0;
        const cls = (el.className && el.className.toString ? el.className.toString() : '').toLowerCase();
        const al = (el.getAttribute('aria-label') || '').toLowerCase();
        if (el.hasAttribute('aria-expanded')) s += 5;
        if (el.hasAttribute('aria-controls')) s += 4;
        if (/menu|nav|burger|hamburg|toggle/.test(cls)) s += 3;
        if (/men[uú]|navigation|abrir/.test(al)) s += 3;
        if (el.closest('header, nav')) s += 2;
        const r = el.getBoundingClientRect();
        if (r.top < 120 && r.left > window.innerWidth / 2) s += 2;  // arriba a la derecha
        if (el.querySelector('svg') && (el.textContent || '').trim().length < 3) s += 1;  // icono hamburguesa
        return s;
    };
    let mejor = null, mejorP = 0;
    for (const el of document.querySelectorAll('button, a, [role="button"]')) {
        const p = puntua(el);
        if (p > mejorP) { mejor = el; mejorP = p; }
    }
    if (!mejor || mejorP < 3) return {encontrado: false};
    mejor.setAttribute('data-rc-menu', '1');
    return {
        encontrado: true,
        links_antes: _contarLinksNav(),
        expanded_antes: (mejor.getAttribute('aria-expanded') || '').toLowerCase(),
    };
}"""
_JS_MENU_DESPUES = "() => {" + _JS_MENU_HELPERS + """
    const el = document.querySelector('[data-rc-menu]');
    return {
        links_despues: _contarLinksNav(),
        expanded_despues: el ? (el.getAttribute('aria-expanded') || '').toLowerCase() : '',
    };
}"""
# Tope de capturas por pantalla en una sola corrida - una pagina
# patologicamente larga no debe generar decenas de archivos; si se
# llega al tope, se avisa en el resultado en vez de seguir sin limite.
# 25 en vez de un numero mas chico porque la pasada MOVIL de la misma
# pagina puede necesitar bastantes mas folds que la de escritorio (el
# contenido se apila verticalmente al angostarse - un caso real medido
# aca: 11 folds en 1280px vs 17 folds en 375px para el mismo sitio) - un
# tope ajustado a escritorio cortaba silenciosamente el final de la
# version movil (horario/contacto/footer nunca llegaban a capturarse).
_MAX_FOLDS = 25

# Umbral para el chequeo de "franjas en blanco" - si una franja
# horizontal de la captura es casi un unico color solido en un
# porcentaje muy alto de su ancho, es senial fuerte de contenido que
# deberia verse pero no se ve (ej. reveal-on-scroll roto, seccion sin
# estilos, imagen que no cargo). No es perfecto (un hero con fondo
# solido de un color da un falso positivo ahi), por eso se reporta
# como advertencia con la posicion, no como fallo automatico duro.
_BLANK_STRIP_HEIGHT_PX = 40
_BLANK_STRIP_UNIFORMITY_THRESHOLD = 0.985
_BLANK_STRIP_MIN_CONSECUTIVE = 3  # al menos ~120px seguidos para no gatillar con un separador angosto

# Minimo de caracteres de texto VISIBLE (innerText renderado, ya sin espacios)
# por debajo del cual se considera que la pagina salio vacia. Cualquier pagina
# real tiene al menos un titular de hero muy por encima de esto; que salga por
# debajo casi siempre significa que un JS fallo en silencio, un fetch no cargo,
# o el contenido quedo en opacity:0 - roto para un usuario real aunque el HTML
# de origen (lo que ve lint_web_page) tuviera texto de sobra.
_MIN_TEXTO_VISIBLE = 10


def _detect_large_blank_regions(screenshot_path: Path, y_offset: int = 0) -> list[str]:
    """Heuristica barata (sin modelo de vision): recorre la captura en
    franjas horizontales y marca corridas largas de franjas casi
    uniformes en color - la firma tipica de una seccion "reveal on
    scroll" que quedo en opacity:0, o contenido que no se renderizo.
    y_offset desplaza las coordenadas reportadas a la posicion absoluta
    en la pagina completa cuando la imagen es una captura por pantalla
    (ver render_check) y no la pagina entera."""
    image = Image.open(screenshot_path).convert("RGB")
    width, height = image.size
    warnings: list[str] = []
    consecutive = 0
    run_start = 0

    def _is_uniform(y0: int, y1: int) -> bool:
        strip = image.crop((0, y0, width, y1))
        colors = strip.getcolors(maxcolors=width * (y1 - y0))
        if colors is None:
            return False
        dominant_count = max(c for c, _ in colors)
        return (dominant_count / (width * (y1 - y0))) >= _BLANK_STRIP_UNIFORMITY_THRESHOLD

    for y in range(0, height, _BLANK_STRIP_HEIGHT_PX):
        y_end = min(y + _BLANK_STRIP_HEIGHT_PX, height)
        if _is_uniform(y, y_end):
            if consecutive == 0:
                run_start = y
            consecutive += 1
        else:
            if consecutive >= _BLANK_STRIP_MIN_CONSECUTIVE:
                warnings.append(
                    f"Franja sospechosamente uniforme/vacia entre y={run_start + y_offset}px e "
                    f"y={y + y_offset}px ({y - run_start}px de alto) - revisar si deberia haber "
                    "contenido ahi (seccion con reveal-on-scroll roto, imagen que no cargo, "
                    "estilos faltantes)."
                )
            consecutive = 0
    if consecutive >= _BLANK_STRIP_MIN_CONSECUTIVE:
        warnings.append(
            f"Franja sospechosamente uniforme/vacia entre y={run_start + y_offset}px y el final "
            f"de esta captura ({height - run_start}px de alto) - revisar si deberia haber "
            "contenido ahi."
        )
    return warnings


# Ancho/alto de referencia para la pasada "movil" - 375px es el mismo
# ancho que ya exige el checklist de web-builder-specialist.md (seccion
# 2.7/14), asi que la pasada movil de esta tool usa literalmente ese
# valor en vez de otro numero desalineado con el resto de la guia.
_MOBILE_VIEWPORT = {"width": 375, "height": 812}


def _scroll_instant(page, y: int) -> None:
    """Scrollea a la posicion Y de forma INSTANTANEA. Un 'window.scrollTo(x, y)'
    de dos argumentos posicionales hereda el 'scroll-behavior' CSS del
    sitio - si es 'smooth' (comun, y algo que este mismo catalogo genera
    via JS de scroll suave en varios patrones), la posicion real tarda
    varios cientos de ms en asentarse. Capturar la pantalla antes de eso
    produce capturas desalineadas entre si (huecos o partes repetidas
    entre una y la siguiente) en vez de avanzar en coordenadas exactas y
    continuas. La forma con objeto de opciones y 'behavior: instant'
    fuerza el salto inmediato sin animacion, sea cual sea el CSS del sitio."""
    page.evaluate(f"window.scrollTo({{top: {y}, left: 0, behavior: 'instant'}})")
    page.wait_for_timeout(200)  # margen para que listeners de scroll (ej. header .scrolled) asienten


def _probar_menu_movil(page, screenshot_path: Path, prefijo: str, capturas: list) -> dict:
    """Pulsa el menu hamburguesa y comprueba que ABRE de verdad (aparecen los
    enlaces de nav, o `aria-expanded` pasa a true). Determinista, sin modelo de
    vision. Deja una captura del menu abierto si funciona, para poder verlo."""
    _scroll_instant(page, 0)
    antes = page.evaluate(_JS_MENU_DETECTAR)
    if not antes.get("encontrado"):
        return {"encontrado": False, "funciona": None, "detalle": "no se encontro boton de menu movil"}
    try:
        page.evaluate("() => { const el = document.querySelector('[data-rc-menu]'); if (el) el.click(); }")
    except Exception:  # noqa: BLE001 - un click que falla es, en si, un menu que no responde
        pass
    page.wait_for_timeout(450)  # margen para la animacion de apertura
    despues = page.evaluate(_JS_MENU_DESPUES)
    links_a, links_d = antes.get("links_antes", 0), despues.get("links_despues", 0)
    exp_a, exp_d = antes.get("expanded_antes", ""), despues.get("expanded_despues", "")
    funciona = (links_d > links_a) or (exp_a != "true" and exp_d == "true")
    if funciona:
        stem, suffix = screenshot_path.stem, screenshot_path.suffix or ".png"
        ruta = screenshot_path.with_name(f"{stem}_{prefijo}_menu{suffix}")
        page.screenshot(path=str(ruta))
        capturas.append({"ruta": str(ruta), "secciones_visibles": ["menu movil abierto"]})
    return {
        "encontrado": True,
        "funciona": bool(funciona),
        "detalle": f"enlaces de nav visibles {links_a}->{links_d}, aria-expanded '{exp_a}'->'{exp_d}'",
    }


def _capture_viewport(
    playwright,
    url: str,
    viewport_width: int,
    viewport_height: int,
    wait_ms: int,
    screenshot_path: Path,
    prefijo: str,
    probar_menu: bool = False,
) -> dict:
    """Corre la navegacion + captura por pantalla completa para UN
    tamaño de viewport (ver render_check, que llama esto dos veces:
    escritorio y movil)."""
    console_messages: list[dict] = []
    page_errors: list[str] = []
    failed_requests: list[dict] = []

    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={"width": viewport_width, "height": viewport_height})

    page.on("console", lambda msg: console_messages.append({"type": msg.type, "text": msg.text}))
    page.on("pageerror", lambda exc: page_errors.append(str(exc)))
    # req.failure es un string directo en la API de Python de Playwright
    # (a diferencia de la API de JS, donde es un objeto {errorText: ...})
    # - indexarlo como dict tira TypeError.
    page.on("requestfailed", lambda req: failed_requests.append({
        "url": req.url,
        "failure": req.failure or "unknown",
    }))

    status_code = None
    navigation_error = None
    try:
        response = page.goto(url, wait_until="networkidle", timeout=_DEFAULT_TIMEOUT_MS)
        status_code = response.status if response else None
    except Exception as exc:  # noqa: BLE001 - se lo devolvemos como parte del resultado, no como crash
        navigation_error = str(exc)

    page.wait_for_timeout(wait_ms)

    # document.documentElement.scrollWidth > clientWidth detecta overflow
    # horizontal de LA PAGINA ENTERA (un elemento empuja el body a ser mas
    # ancho que el viewport). NO detecta el caso mas comun en la
    # practica: un <nav>/<header> con `overflow: hidden` (o simplemente
    # angosto) que recorta sus propios hijos por dentro sin afectar el
    # ancho del documento - ahi el link "Contacto" desaparece
    # silenciosamente en vez de causar scroll. Por eso se chequea AMBOS:
    # el documento completo, y cada nav/header por separado contra su
    # propio scrollWidth interno.
    has_horizontal_overflow = bool(page.evaluate(
        """() => {
            if (document.documentElement.scrollWidth > document.documentElement.clientWidth) return true;
            const candidates = document.querySelectorAll('nav, header');
            for (const el of candidates) {
                if (el.scrollWidth > el.clientWidth + 2) return true;
            }
            return false;
        }"""
    ))

    page_height = page.evaluate("document.body.scrollHeight")

    # Texto VISIBLE realmente renderizado (innerText respeta display:none,
    # visibility, opacity:0 en cadena, etc.), no el del codigo fuente. Es lo
    # que atrapa una pagina que "se ve bien en el HTML" pero sale en blanco al
    # ejecutarse - el punto ciego que ni lint (estatico) ni el chequeo de
    # errores de consola cubren (un fetch que devuelve vacio no tira error).
    texto_visible_len = int(page.evaluate(
        "document.body ? document.body.innerText.replace(/\\s+/g, '').length : 0"
    ))
    render_sin_texto = texto_visible_len < _MIN_TEXTO_VISIBLE

    # Recorrido previo (coordenadas exactas, scroll instantaneo) ANTES de
    # empezar a capturar: si el sitio usa revelado por scroll (opacity:0
    # + IntersectionObserver - la tecnica de animacion que este mismo
    # catalogo instruye usar por defecto, ver hard_cases/by_domain/web.json),
    # un fold capturado sin haber pasado por ahi antes muestra ese
    # contenido con opacity:0 - es decir, en blanco. Eso es un falso
    # positivo grave: "renderiza bien" en realidad esconde un sitio que
    # se ve vacio/roto para cualquier usuario real. Scrollear de verdad
    # (en vez de forzar la clase via JS) tambien deja que esto detecte si
    # el observer esta genuinamente roto.
    position = 0
    while position < page_height:
        position += viewport_height
        _scroll_instant(page, position)

    # Chequeos DETERMINISTAS del DOM ya renderizado (sin modelo de vision), una
    # vez recorrida la pagina (asi el contenido con revelado-por-scroll ya esta
    # visible y no da falsos negativos): contraste WCAG texto/fondo e imagenes
    # rotas/deformadas. Miden estilos computados y tamaños reales, no opinan.
    contraste_problemas = list(page.evaluate(_JS_CONTRASTE))
    imagenes_problemas = list(page.evaluate(_JS_IMAGENES))

    # Captura POR PANTALLA (no un unico archivo full_page gigante): cada
    # imagen mide exactamente viewport_width x viewport_height, el mismo
    # tamaño en el que un visitante real la ve. Arranca de nuevo desde
    # arriba con coordenadas EXACTAS (fold * viewport_height) para que
    # cada captura empiece justo donde termino la anterior, sin huecos ni
    # partes repetidas entre una y la siguiente. Una sola captura
    # full_page reducida para mostrarla esconde ademas defectos de
    # PROPORCION (un boton demasiado grande, texto descentrado, una
    # imagen que no encaja en su hueco) porque todo se encoge por igual
    # al verla - a esa escala un boton "gigante" se ve del mismo tamaño
    # relativo que uno normal. Por pantalla, el defecto se nota igual que
    # lo notaria un usuario real con su navegador.
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    stem, suffix = screenshot_path.stem, screenshot_path.suffix or ".png"
    n_folds = max(1, min(-(-page_height // viewport_height), _MAX_FOLDS))
    capturas: list[dict] = []
    blank_region_warnings: list[str] = []
    for fold in range(n_folds):
        fold_position = fold * viewport_height
        _scroll_instant(page, fold_position)
        fold_path = screenshot_path.with_name(f"{stem}_{prefijo}_{fold + 1}{suffix}")
        page.screenshot(path=str(fold_path))
        # Que secciones se ven en ESTA captura puntual - se pide DESPUES
        # de scrollear a esta posicion exacta, asi queda emparejado con
        # lo que la imagen realmente muestra, no con la posicion anterior.
        secciones_visibles = page.evaluate(_JS_SECCIONES_VISIBLES)
        capturas.append({"ruta": str(fold_path), "secciones_visibles": secciones_visibles})
        blank_region_warnings.extend(_detect_large_blank_regions(fold_path, y_offset=fold_position))
    screenshots_truncated = (-(-page_height // viewport_height)) > _MAX_FOLDS

    # Menu movil: se prueba DESPUES de capturar (el click cambia el estado de
    # la pagina) y solo en la pasada movil, que es donde hay hamburguesa.
    menu_movil = {"encontrado": False, "funciona": None, "detalle": "no aplica"}
    if probar_menu:
        menu_movil = _probar_menu_movil(page, screenshot_path, prefijo, capturas)

    browser.close()

    console_errors = [m for m in console_messages if m["type"] == "error"]
    if render_sin_texto:
        blank_region_warnings.insert(0, (
            f"La pagina no muestra texto visible al renderizar (solo "
            f"{texto_visible_len} caracteres) - probablemente salio en blanco: "
            "revisa si un JS fallo, un fetch no cargo, o el contenido quedo oculto "
            "(opacity:0 / display:none). El HTML de origen puede tener texto igual."
        ))
    return {
        "viewport": f"{viewport_width}x{viewport_height}",
        "status_code": status_code,
        "navigation_error": navigation_error,
        "capturas": capturas,
        "screenshots_truncated": screenshots_truncated,
        "console_errors": console_errors,
        "page_errors": page_errors,
        "failed_requests": failed_requests,
        "has_horizontal_overflow": has_horizontal_overflow,
        "texto_visible_len": texto_visible_len,
        "render_sin_texto": render_sin_texto,
        "contraste_problemas": contraste_problemas,
        "imagenes_problemas": imagenes_problemas,
        "menu_movil": menu_movil,
        "blank_region_warnings": blank_region_warnings,
    }


def render_check(
    target: str,
    screenshot_path: Path,
    viewport_width: int = 1280,
    viewport_height: int = 800,
    wait_ms: int = 500,
) -> dict:
    """target: URL (http/https) o ruta a un archivo HTML local.

    Corre la captura DOS VECES por dentro - escritorio
    (viewport_width x viewport_height, default 1280x800) y movil
    (375x812 fijo, el mismo ancho de 375px que ya exige el checklist de
    web-builder-specialist.md) - un sitio que se ve bien en escritorio
    puede romperse en movil (grids que no colapsan, texto que se sale,
    botones sin area de toque decente) y antes había que acordarse de
    llamar esto una segunda vez con otros argumentos para chequearlo;
    ahora ambas pasadas son automaticas y vienen en la misma respuesta,
    bajo las claves 'escritorio' y 'movil'.

    Una ruta local se sirve por HTTP (mini-servidor efimero en un puerto
    libre, raiz = la carpeta del archivo) en vez de navegarse como
    file://: fetch() directamente NO soporta file:// en Chromium, asi
    que cualquier pagina que cargue assets propios via fetch (modelos
    .gltf/.hdr, JSON de datos, fuentes...) fallaria SOLO en esta
    verificacion y funcionaria bien en produccion - falso negativo.
    Servir por http reproduce lo que hara el hosting real."""
    url = target
    servidor = None
    if not target.startswith(("http://", "https://", "file://")):
        ruta = Path(target).resolve()
        handler = partial(_SilentHandler, directory=str(ruta.parent))
        servidor = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=servidor.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{servidor.server_address[1]}/{ruta.name}"

    try:
        with sync_playwright() as playwright:
            escritorio = _capture_viewport(
                playwright, url, viewport_width, viewport_height, wait_ms, screenshot_path, "escritorio"
            )
            movil = _capture_viewport(
                playwright, url, _MOBILE_VIEWPORT["width"], _MOBILE_VIEWPORT["height"], wait_ms,
                screenshot_path, "movil", probar_menu=True,
            )
    finally:
        if servidor is not None:
            servidor.shutdown()
            servidor.server_close()

    def _menu_roto(r: dict) -> bool:
        mm = r.get("menu_movil") or {}
        return bool(mm.get("encontrado") and mm.get("funciona") is False)

    passed = all(
        not r["navigation_error"]
        and not r["console_errors"]
        and not r["page_errors"]
        and not r["failed_requests"]
        and not r["has_horizontal_overflow"]
        and not r["render_sin_texto"]
        and not r["contraste_problemas"]
        and not r["imagenes_problemas"]
        and not _menu_roto(r)
        for r in (escritorio, movil)
    )

    return {
        "url": url,
        "escritorio": escritorio,
        "movil": movil,
        "passed": passed,
    }
