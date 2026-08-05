"""Clasificacion de imagenes con un modelo de VISION LOCAL (Ollama) -
adaptacion CASI literal de describir_imagen.py (un script probado en la
practica): mismo formato de campos (TIPO/CALIDAD/ALT/MOTIVO), mismas
CALIDADES (bonita/aceptable/fea), mismo modelo por defecto
(gemma3:4b). El UNICO cambio real es que el rubro del negocio se pasa
como parametro en vez de estar hardcodeado a "restaurante", para que
sirva para cualquier tipo de negocio de este catalogo (peluquerias,
gimnasios, etc.), no solo restaurantes.

Requiere Ollama corriendo local con el modelo descargado:
    ollama pull gemma3:4b

CONFIABILIDAD (documentado para quien lo use, no para bloquearlo):
probado en vivo contra 3 fotos con ground-truth verificado (peluqueria,
calle con rascacielos, calle con auto) - en corridas repetidas, acierta
la mayoria de las veces (~75% en las pruebas hechas) pero no siempre:
la MISMA foto con el MISMO modelo puede dar una respuesta correcta en
una corrida e incorrecta en otra (no hay temperatura fija). Por eso se
usa como SEÑAL ADICIONAL junto con la 'description' de texto de
search_images (ver skills/web-builder-specialist.md), no como unico
arbitro: si esta tool dice que una foto es valida pero la description
de texto no menciona el tema buscado, o viceversa, trátalo como
incierto y sigue buscando en vez de confiar ciegamente en un solo lado.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

MODELO_POR_DEFECTO = os.environ.get("VISION_MODEL", "gemma3:4b")

TIPOS = ["fachada", "interior", "producto", "plato", "persona", "equipo", "logo", "ambiente", "otro"]
CALIDADES = ["bonita", "aceptable", "fea"]

_PROMPT_TEMPLATE = (
    "Eres un director de arte que selecciona fotos de un negocio del rubro "
    "'{rubro}' para su web. Mira la imagen y responde EXACTAMENTE en una linea "
    "con este formato, sin nada mas:\n"
    "TIPO: <una de: " + ", ".join(TIPOS) + "> | "
    "CALIDAD: <una de: " + ", ".join(CALIDADES) + "> | "
    "ALT: <descripcion breve en español, max 12 palabras> | "
    "MOTIVO: <por que esa calidad, max 10 palabras>\n"
    "Criterios de CALIDAD (clave: ¿esta foto parece de un negocio de tipo "
    "'{rubro}' PROFESIONAL, o de otro sitio?). El contenido puede ser atractivo, "
    "pero si el SITIO/CONTEXTO no corresponde a ese rubro, NO sirve:\n"
    "- bonita: parece foto profesional de un negocio de ese rubro - buen "
    "encuadre, ambiente/producto que corresponde, buena luz. Vale para "
    "portada/destacar.\n"
    "- aceptable: es claramente de ese rubro pero sin destacar; luz/encuadre "
    "normales.\n"
    "- fea: NO parece de ese rubro (es de otro tipo de negocio o de un "
    "contexto no comercial) o es amateur/descuidada (fondo desordenado, mala "
    "luz, borrosa). Marca fea tambien si el contenido REAL de la foto "
    "claramente no es lo que se esperaria de ese rubro, aunque la foto en si "
    "sea nitida y este bien iluminada.\n"
    "El ALT describe lo que se ve y sirve como texto alternativo accesible."
)


def _obtener_bytes(url_or_path: str) -> bytes:
    # El cliente de ollama espera bytes o una ruta LOCAL en 'images', no
    # una URL - si nos pasan una URL (el caso mas comun, viene de
    # search_images o fetch_gallery_photos_from_maps) hay que bajar los
    # bytes primero.
    if url_or_path.startswith(("http://", "https://")):
        import httpx
        return httpx.get(url_or_path, timeout=15.0, follow_redirects=True).content
    return Path(url_or_path).read_bytes()


_OLLAMA_START_WAIT_S = 2.0
_OLLAMA_START_MAX_TRIES = 3
# Si ya intentamos levantar el servicio una vez en este proceso, no volver
# a lanzar 'ollama serve' en cada llamada (spawnearia un proceso nuevo por
# cada foto que falle) - una sola vez alcanza, si sigue sin responder
# despues de eso es un problema real (no instalado, puerto bloqueado, etc.)
# que reintentar no va a arreglar.
_ollama_arranque_intentado = False


def _intentar_arrancar_ollama() -> bool:
    """Ollama esta pensado para estar 'siempre disponible' (no es un
    servicio que el usuario prenda/apague a mano cada vez) - si una
    llamada falla porque el servicio no esta corriendo (a diferencia de
    no estar instalado, o de que falte el modelo), lo levantamos
    nosotros mismos en vez de simplemente reportar el error y frenar la
    tarea completa por eso. Devuelve True si el servicio quedo
    respondiendo (ya sea porque lo arrancamos o porque ya estaba
    arrancando), False si no se pudo (binario no instalado, o sigue sin
    responder tras el intento)."""
    global _ollama_arranque_intentado
    import shutil
    import subprocess
    import time

    ollama_bin = shutil.which("ollama")
    if not ollama_bin:
        return False
    if not _ollama_arranque_intentado:
        _ollama_arranque_intentado = True
        try:
            subprocess.Popen(
                [ollama_bin, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:  # noqa: BLE001 - si ni siquiera pudimos lanzar el proceso, no hay nada mas que hacer
            return False
    for _ in range(_OLLAMA_START_MAX_TRIES):
        time.sleep(_OLLAMA_START_WAIT_S)
        try:
            import ollama
            ollama.list()  # ping liviano: si responde, el servicio ya esta arriba
            return True
        except Exception:  # noqa: BLE001 - todavia no arranco, seguir esperando el resto de intentos
            continue
    return False


def _llamar_vision(prompt: str, image_bytes: bytes, modelo: str) -> dict:
    """Wrapper comun para llamar a Ollama con una imagen - devuelve
    {'ok': True, 'texto': ...} o {'ok': False, 'error'/'pista': ...},
    mismo manejo de errores (libreria ausente, Ollama caido, modelo no
    descargado) para classify_image y extract_menu_text. Si el servicio
    no esta corriendo (no si falta la libreria o el modelo), intenta
    levantarlo solo una vez y reintenta ANTES de rendirse - sin romper
    la tarea que se le dio a quien llama."""
    try:
        import ollama
    except ImportError:
        return {
            "ok": False,
            "error": "Falta la libreria 'ollama' en el entorno.",
            "pista": "Instalala con: pip install 'ollama>=0.6.0'",
        }

    def _pedir() -> dict:
        respuesta = ollama.chat(
            model=modelo,
            messages=[{"role": "user", "content": prompt, "images": [image_bytes]}],
        )
        return {"ok": True, "texto": respuesta["message"]["content"].strip()}

    try:
        return _pedir()
    except Exception as primer_error:  # noqa: BLE001 - evaluamos si vale la pena reintentar
        error_final = primer_error
        if _intentar_arrancar_ollama():
            try:
                return _pedir()
            except Exception as segundo_error:  # noqa: BLE001 - se devuelve como resultado, no como crash
                error_final = segundo_error
        return {
            "ok": False,
            "error": f"Fallo al llamar al modelo de vision: {error_final}",
            "pista": f"¿Esta Ollama corriendo y el modelo descargado? ollama pull {modelo}",
        }


def classify_image(url_or_path: str, rubro_negocio: str, modelo: str = MODELO_POR_DEFECTO) -> dict:
    """Clasifica una imagen (URL o archivo local) con un modelo de vision
    local. Devuelve ok=False + error/pista si Ollama no esta disponible o
    el modelo no esta descargado, en vez de fallar con una excepcion
    generica - igual que el resto de tools de este proyecto."""
    try:
        image_bytes = _obtener_bytes(url_or_path)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"No se pudo obtener la imagen: {exc}", "pista": None}

    resultado = _llamar_vision(_PROMPT_TEMPLATE.format(rubro=rubro_negocio), image_bytes, modelo)
    if not resultado["ok"]:
        return resultado
    return _parse(resultado["texto"])


_PROMPT_SUJETO = (
    "Te voy a decir que se supone que muestra esta imagen: '{sujeto}'. Mira la imagen y "
    "responde EXACTAMENTE en este formato, sin nada mas:\n"
    "COINCIDE: <si/no - 'si' SOLO si la imagen realmente muestra ese sujeto especifico "
    "(la persona, pelicula, producto, etc. correcto) - una imagen generica del mismo TEMA "
    "pero de otro sujeto especifico (ej. otra pelicula, otra persona) es 'no'>\n"
    "DESCRIPCION: <que se ve realmente en la imagen, en una frase breve>"
)


def verify_image_subject(url_or_path: str, sujeto: str, modelo: str = MODELO_POR_DEFECTO) -> dict:
    """Verifica que una imagen (de busqueda web general, no de un banco de
    stock) muestre realmente el sujeto especifico que se busco - mismo
    principio que classify_image para fotos de negocio (ver
    hard_cases: web-search-description-text-mismatches-real-photo-
    content), pero con un prompt de VERIFICACION DE IDENTIDAD en vez de
    clasificacion por rubro: 'un poster de Inception' buscado en la web
    general puede devolver el poster de OTRA pelicula, un fan-art, o una
    imagen no relacionada - el titulo/descripcion del resultado de
    busqueda no lo garantiza. Mismas salvedades de confiabilidad que el
    resto de este modulo (~75% en las pruebas hechas) - señal real, no
    arbitro perfecto."""
    try:
        image_bytes = _obtener_bytes(url_or_path)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"No se pudo obtener la imagen: {exc}", "pista": None}

    resultado = _llamar_vision(_PROMPT_SUJETO.format(sujeto=sujeto), image_bytes, modelo)
    if not resultado["ok"]:
        return resultado

    texto = resultado["texto"]
    coincide_match = re.search(r"COINCIDE\s*:\s*(si|no)", texto, re.IGNORECASE)
    coincide = bool(coincide_match and coincide_match.group(1).lower() == "si")
    descripcion_match = re.search(r"DESCRIPCION\s*:\s*(.*)", texto, re.IGNORECASE | re.DOTALL)
    descripcion = descripcion_match.group(1).strip() if descripcion_match else ""

    return {"ok": True, "coincide": coincide, "descripcion": descripcion, "raw_response": texto}


_PROMPT_MENU = (
    "Esta imagen puede ser la foto de una carta/menu fisico de un "
    "negocio (restaurante, bar, cafeteria). Responde EXACTAMENTE en "
    "este formato, sin nada mas:\n"
    "ES_CARTA: <si/no - si claramente NO es una carta/menu (es una foto "
    "de comida, del local, de gente, etc.) responde 'no' y no sigas>\n"
    "TEXTO: <transcribi TODO el texto que puedas leer en la imagen tal "
    "cual aparece, plato por plato con su precio si se ve, uno por "
    "linea. Si el texto esta borroso o incompleto, transcribi lo que "
    "SI se lee con claridad y omite el resto - no inventes platos ni "
    "precios que no puedas leer.>"
)


def extract_menu_text(url_or_path: str, modelo: str = MODELO_POR_DEFECTO) -> dict:
    """Usa el MISMO modelo de vision local que classify_image, pero con
    un prompt de transcripcion/OCR en vez de clasificacion - pensado
    para leer el texto de una foto de carta/menu real (ej. traida por
    fetch_gallery_photos_from_maps), no para describir la imagen.

    Devuelve es_carta=False si el modelo determina que la imagen no es
    una carta (para que quien llama pruebe la siguiente candidata de la
    galeria en vez de asumir que el texto devuelto es un menu real).
    Mismas salvedades de confiabilidad que classify_image (~75% en las
    pruebas hechas, un modelo de vision chico no es perfecto): tratar el
    texto extraido como un borrador a revisar, no como transcripcion
    garantizada."""
    try:
        image_bytes = _obtener_bytes(url_or_path)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"No se pudo obtener la imagen: {exc}", "pista": None}

    resultado = _llamar_vision(_PROMPT_MENU, image_bytes, modelo)
    if not resultado["ok"]:
        return resultado

    texto = resultado["texto"]
    es_carta_match = re.search(r"ES_CARTA\s*:\s*(si|no)", texto, re.IGNORECASE)
    es_carta = bool(es_carta_match and es_carta_match.group(1).lower() == "si")
    contenido_match = re.search(r"TEXTO\s*:\s*(.*)", texto, re.IGNORECASE | re.DOTALL)
    contenido = contenido_match.group(1).strip() if contenido_match else ""

    return {
        "ok": True,
        "es_carta": es_carta,
        "texto_extraido": contenido if es_carta else "",
        "raw_response": texto,
    }


_PROMPT_CRITICA_VISUAL = (
    "Eres un diseñador senior revisando una captura de pantalla REAL de una "
    "web (el tamaño exacto de una pantalla, no la pagina entera reducida). "
    "Responde EXACTAMENTE en este formato, sin nada mas:\n"
    "CAMBIARIAS_ALGO: <si/no>\n"
    "PROBLEMAS: <si CAMBIARIAS_ALGO es 'si', lista cada problema concreto "
    "separado por ' | ', diciendo QUE elemento, EN QUE SECCION (si te "
    "pase nombres de seccion mas abajo, usa uno de esos literalmente) y "
    "DONDE dentro de la imagen (arriba/centro/abajo, izquierda/centro/"
    "derecha). Si es 'no', escribe un solo caracter: '-'>\n"
    "Fíjate especificamente (pero no solo) en: botones desproporcionadamente "
    "grandes o chicos para su contenido; texto que deberia estar centrado y "
    "no lo esta, o que se sale de su contenedor; imagenes que no encajan "
    "bien en su espacio (recortadas de forma rara, estiradas/deformadas, "
    "demasiado grandes o chicas para el hueco que ocupan); elementos "
    "superpuestos entre si; espaciado muy desigual entre secciones; texto "
    "con poco contraste sobre su fondo. Se especifico y concreto, no "
    "generico - si todo se ve bien resuelto, responde que no cambiarias "
    "nada en vez de inventar un problema menor."
)


def critique_screenshot(
    path_captura: str,
    secciones_visibles: list[str] | None = None,
    modelo: str = MODELO_POR_DEFECTO,
) -> dict:
    """Critica visual de UNA captura de render_check (una pantalla, no la
    pagina entera) con el mismo modelo de vision local que classify_image/
    extract_menu_text, pero con un prompt de AUDITORIA DE DISEÑO en vez de
    clasificacion/OCR - pensado para detectar defectos que las heuristicas
    de render_check (overflow horizontal, franjas en blanco) no pueden ver:
    botones mal proporcionados, texto descentrado, imagenes que no encajan,
    etc. Pásale siempre una de las capturas de
    render_check['escritorio'/'movil']['capturas'] (viewport_width x
    viewport_height) - una captura full_page gigante reducida para
    mostrarla hace que estos defectos se vean a una escala distinta a la
    real y sean mas dificiles de detectar, tanto para el modelo de vision
    como para un humano mirandola despues.

    Pásale tambien secciones_visibles (el campo del mismo nombre que trae
    cada entrada de 'capturas') para que el hallazgo diga EN QUE SECCION
    ocurre el problema (ej. 'en la seccion cocido-estrella') y no solo su
    posicion vaga dentro de la imagen - asi quien programa sabe
    exactamente donde mirar en el codigo en vez de tener que adivinar a
    que parte del HTML corresponde la captura.

    Mismas salvedades de confiabilidad que el resto de este modulo - un
    modelo de vision chico no es perfecto, tratar el resultado como una
    señal mas, no como verdad absoluta."""
    try:
        image_bytes = _obtener_bytes(path_captura)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"No se pudo obtener la captura: {exc}", "pista": None}

    prompt = _PROMPT_CRITICA_VISUAL
    if secciones_visibles:
        prompt = (
            f"Esta captura corresponde a estas secciones del HTML, de arriba a "
            f"abajo: {', '.join(secciones_visibles)}. Cuando describas un "
            f"problema, indica en CUAL de estas secciones ocurre (literalmente "
            f"uno de esos nombres), ademas de su posicion dentro de la imagen.\n\n"
        ) + prompt

    resultado = _llamar_vision(prompt, image_bytes, modelo)
    if not resultado["ok"]:
        return resultado

    texto = resultado["texto"]
    cambiaria_match = re.search(r"CAMBIARIAS_ALGO\s*:\s*(si|no)", texto, re.IGNORECASE)
    cambiaria_algo = bool(cambiaria_match and cambiaria_match.group(1).lower() == "si")
    problemas_match = re.search(r"PROBLEMAS\s*:\s*(.*)", texto, re.IGNORECASE | re.DOTALL)
    problemas_raw = problemas_match.group(1).strip() if problemas_match else ""
    problemas = (
        [p.strip() for p in problemas_raw.split("|") if p.strip()]
        if cambiaria_algo and problemas_raw not in ("", "-")
        else []
    )

    return {
        "ok": True,
        "cambiaria_algo": cambiaria_algo,
        "problemas": problemas,
        "raw_response": texto,
    }


_LABELS = ("TIPO", "CALIDAD", "ALT", "MOTIVO")


def _campo(texto: str, etiqueta: str) -> str | None:
    """Extrae el valor de '<ETIQUETA>: valor' donde sea que aparezca en el
    texto (una linea propia, o varias etiquetas separadas por '|' en la
    misma linea) - el modelo de vision no siempre respeta el formato de
    'todo en una linea' pedido en el prompt. Corta el valor en el primer
    '|', o el inicio de la SIGUIENTE etiqueta conocida, lo que aparezca
    antes."""
    match = re.search(rf"{etiqueta}\s*:\s*", texto, re.IGNORECASE)
    if not match:
        return None
    resto = texto[match.end():]
    cortes = [len(resto)]
    for otra in (*_LABELS, "|"):
        m2 = re.search(re.escape(otra) if otra == "|" else rf"{otra}\s*:", resto, re.IGNORECASE)
        if m2:
            cortes.append(m2.start())
    fin = min(cortes)
    return resto[:fin].strip().strip('"').strip()


def _parse(texto: str) -> dict:
    tipo, calidad, alt, motivo = "otro", "aceptable", texto, ""

    cab = _campo(texto, "TIPO")
    if cab is not None:
        tipo = next((t for t in TIPOS if t in cab.lower()), "otro")
    cal = _campo(texto, "CALIDAD")
    if cal is not None:
        calidad = next((c for c in CALIDADES if c in cal.lower()), "aceptable")
    a = _campo(texto, "ALT")
    if a:
        alt = a
    m = _campo(texto, "MOTIVO")
    if m:
        motivo = m

    return {
        "ok": True,
        "tipo": tipo,
        "calidad": calidad,
        "apta_web": calidad != "fea",
        "alt": alt.strip().strip('"').strip(),
        "motivo": motivo,
        # Un modelo de vision chico no siempre respeta el formato pedido -
        # se expone el texto crudo para que quien llama pueda leerlo
        # directo si los campos de arriba parecen incompletos.
        "raw_response": texto,
    }
