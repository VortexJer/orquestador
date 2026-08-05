"""Definicion (JSON schema de function-calling) e implementacion de las
herramientas que la terminal le da al modelo servido por Groq. Todo lo
que toca el filesystem pasa por safety.resolve_within_root - ninguna
herramienta puede salir del --workspace elegido al arrancar. Quedarse
DENTRO del workspace no alcanza solo: si --workspace es la raiz de un
repo (default '.', incluido este mismo repo), ahi adentro hay .env,
.venv/, __pycache__/, etc. - safety.is_hidden_path/is_sensitive_file
los oculta de list_dir/glob_search/grep_search y bloquea read/write/edit
directo sobre ellos, aunque el modelo adivine la ruta exacta.

No hay una herramienta de shell arbitrario a proposito: darle
ejecucion de comandos sin restriccion a un modelo de un tercero (via
API) es un vector de riesgo real. `run_check` cubre el caso de uso real
(verificar codigo Python del workspace) con un allowlist fijo de 3
comandos, no comandos arbitrarios.
"""
from __future__ import annotations

import difflib
import fnmatch
import importlib
import re
import subprocess
import sys
from pathlib import Path

from app.tools.reference_kit import ReferenceNotFoundError
from groq_agent.safety import (
    PathEscapeError,
    is_hidden_path,
    is_sensitive_file,
    is_system_path,
    resolve_within_root,
    resolve_write_destination,
)


# --- Imports perezosos ------------------------------------------------
#
# Estas librerias son caras de importar (qdrant_client + fastembed: 1.3s;
# openpyxl: 0.4s; playwright: 0.26s) y solo hacen falta si la tarea llega a
# usar esa herramienta concreta. A nivel de modulo se pagaban SIEMPRE:
# `orquestador --help` tardaba 2 segundos en abrir una ayuda de texto.
#
# El proxy resuelve el import en la primera LLAMADA, asi que los sitios de
# uso no cambian: `_search_hard_cases(...)` sigue escribiendose igual.
def _perezoso(modulo: str, atributo: str):
    cache: dict[str, object] = {}

    def envoltorio(*args, **kwargs):
        fn = cache.get("fn")
        if fn is None:
            fn = getattr(importlib.import_module(modulo), atributo)
            cache["fn"] = fn
        return fn(*args, **kwargs)

    envoltorio.__name__ = atributo
    return envoltorio


check_docx = _perezoso("app.tools.docx_tool", "check_docx")
check_presentation = _perezoso("app.tools.pptx_tool", "check_presentation")
check_workbook = _perezoso("app.tools.xlsx_tool", "check_workbook")
_eval_workbook = _perezoso("app.tools.xlsx_eval", "eval_workbook")
_run_sql = _perezoso("app.tools.sql_sandbox", "run_sql")
_compile_run_cpp = _perezoso("app.tools.native_run", "compile_run_cpp")
create_docx = _perezoso("app.tools.docx_tool", "create_docx")
create_presentation = _perezoso("app.tools.pptx_tool", "create_presentation")
create_workbook = _perezoso("app.tools.xlsx_tool", "create_workbook")
_analyze_image = _perezoso("app.tools.image_analysis", "analyze_image")
_check_links = _perezoso("app.tools.link_checker", "check_links")
_classify_image_content = _perezoso("app.tools.image_vision", "classify_image")
_critique_screenshot = _perezoso("app.tools.image_vision", "critique_screenshot")
_extract_business_info = _perezoso("app.tools.web_scraper", "extract_business_info")
_extract_menu_text = _perezoso("app.tools.image_vision", "extract_menu_text")
_fetch_business_from_maps = _perezoso("app.tools.maps_scraper", "fetch_business_from_maps")
_fetch_gallery_photos_from_maps = _perezoso("app.tools.maps_scraper", "fetch_gallery_photos_from_maps")
_fetch_menu_and_reviews_from_maps = _perezoso("app.tools.maps_scraper", "fetch_menu_and_reviews_from_maps")
_fetch_page = _perezoso("app.tools.web_scraper", "fetch_page")
_lint_email = _perezoso("app.tools.email_lint", "lint_email")
_lint_html = _perezoso("app.tools.web_lint", "lint_html")
_list_reference_components = _perezoso("app.tools.reference_kit", "list_reference_components")
_query_code_graph = _perezoso("app.tools.code_graph", "query_code_graph")
_read_reference_component = _perezoso("app.tools.reference_kit", "read_reference_component")
_render_check = _perezoso("app.tools.render_check", "render_check")
_search_hard_cases = _perezoso("app.rag.query", "search_hard_cases")
_search_images = _perezoso("app.tools.image_search", "search_images")
_search_web = _perezoso("app.tools.web_search", "search_web")
_deep_research = _perezoso("app.tools.deep_research", "deep_research")
# APIs de referencia: ejemplos de codigo, datos de paquetes, vulns, directorios.
_buscar_ejemplos = _perezoso("app.tools.code_examples", "buscar_ejemplos")
_info_paquete = _perezoso("app.tools.package_info", "info_paquete")
_consultar_vulnerabilidades = _perezoso("app.tools.vuln_lookup", "consultar_vulnerabilidades")
_buscar_codigo = _perezoso("app.tools.code_search", "buscar_codigo")
_buscar_api = _perezoso("app.tools.api_directory", "buscar_api")
_datos_de_ejemplo = _perezoso("app.tools.api_directory", "datos_de_ejemplo")
_consultar_wikipedia = _perezoso("app.tools.wikipedia", "consultar_wikipedia")
# Diseño web: iconos, tipografias, paletas, assets, biblioteca UIverse.
_search_icons = _perezoso("app.tools.web_design", "search_icons")
_search_fonts = _perezoso("app.tools.web_design", "search_fonts")
_color_palette = _perezoso("app.tools.web_design", "color_palette")
_design_assets = _perezoso("app.tools.web_design", "design_assets")
_uiverse = _perezoso("app.tools.web_design", "uiverse")
_search_web_image = _perezoso("app.tools.image_web_search", "search_web_image")
_shortest_path_in_graph = _perezoso("app.tools.code_graph", "shortest_path_in_graph")
_summarize_links = _perezoso("app.tools.link_checker", "summarize")

# Nombres CSS basicos que aparecen a menudo en un color de texto/fondo, para no
# obligar al modelo a convertirlos a hex antes de pasarlos.
_CSS_COLORS = {
    "white": "#ffffff", "black": "#000000", "red": "#ff0000", "green": "#008000",
    "blue": "#0000ff", "gray": "#808080", "grey": "#808080", "silver": "#c0c0c0",
    "yellow": "#ffff00", "orange": "#ffa500", "purple": "#800080", "navy": "#000080",
    "teal": "#008080", "maroon": "#800000", "lime": "#00ff00", "transparent": "#ffffff",
}


def _a_rgb(color: str) -> tuple[int, int, int] | None:
    c = (color or "").strip().lower()
    c = _CSS_COLORS.get(c, c).lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) != 6:
        return None
    try:
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except ValueError:
        return None


def _luminancia_relativa(rgb: tuple[int, int, int]) -> float:
    def canal(v: int) -> float:
        s = v / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * canal(r) + 0.7152 * canal(g) + 0.0722 * canal(b)


def _check_contrast(color_texto: str, color_fondo: str) -> str:
    """Ratio de contraste WCAG 2.1 entre texto y fondo, con el veredicto por
    nivel. El modelo no calcula esto bien de cabeza; esta tool lo hace exacto."""
    fg, bg = _a_rgb(color_texto), _a_rgb(color_fondo)
    if fg is None or bg is None:
        malos = [c for c, ok in [(color_texto, fg), (color_fondo, bg)] if ok is None]
        return (f"ERROR: no entiendo el/los color(es) {malos}. Usa hex ('#bbbbbb', '#bbb') "
                "o un nombre CSS basico (white, black, red...).")
    l1, l2 = _luminancia_relativa(fg), _luminancia_relativa(bg)
    claro, oscuro = max(l1, l2), min(l1, l2)
    ratio = (claro + 0.05) / (oscuro + 0.05)
    r = round(ratio, 2)
    def si_no(cumple: bool) -> str:
        return "CUMPLE" if cumple else "NO cumple"
    return (
        f"Contraste texto {color_texto} sobre fondo {color_fondo}: {r}:1\n"
        f"- AA texto normal (>=4.5:1): {si_no(ratio >= 4.5)}\n"
        f"- AA texto grande >=24px o 18.66px bold, y elementos de UI (>=3:1): {si_no(ratio >= 3.0)}\n"
        f"- AAA texto normal (>=7:1): {si_no(ratio >= 7.0)}"
        + ("" if ratio >= 4.5 else
           "\nArreglo: oscurece el color mas oscuro o aclara el mas claro hasta llegar a 4.5:1.")
    )

# Herramientas que crean o sobreescriben contenido DEL USUARIO - piden
# confirmacion en agent_loop.py salvo que se corra con --yes, porque
# pueden perder algo que ya existia (un archivo, un documento).
#
# run_check y render_check NO estan aca a proposito, aunque ejecutan
# codigo/un navegador: son herramientas de VERIFICACION (ruff/mypy/pytest
# de solo lectura, una captura de pantalla diagnostica), no escriben
# nada que el usuario haya pedido como resultado. Pedir confirmacion ahi
# rompe el ciclo generar -> validar -> corregir que es el punto central
# de este diseño (el especialista tiene que poder re-chequear su propio
# trabajo varias veces sin frenar en un [y/N] cada vez). ask_user tampoco
# esta aca: no escribe nada, ya ES la confirmacion/pregunta en si misma.
WRITE_TOOLS = {
    "write_file", "edit_file", "generate_docx", "generate_xlsx", "generate_pptx",
    # Instalar modifica el entorno del usuario, no solo su workspace.
    "install_dependency",
}


def escribe_fuera_del_workspace(name: str, arguments: dict, root: Path) -> bool:
    """True si esta tool call va a ESCRIBIR fuera del workspace (una
    carpeta conocida o una ruta absoluta que pidio el usuario).

    Lo usa agent_loop para NO duplicar confirmacion: si es fuera, la
    confirmacion (obligatoria, con la ruta absoluta) la hace la propia tool
    dentro de _destino_escritura; si es dentro, agent_loop pide la normal.
    Solo aplica a tools que reciben un `path` de destino."""
    if name not in {"write_file", "generate_docx", "generate_xlsx", "generate_pptx"}:
        return False
    path = arguments.get("path")
    if not isinstance(path, str) or not path:
        return False
    try:
        _destino, fuera = resolve_write_destination(root, path)
    except Exception:  # noqa: BLE001 - una ruta rara la resuelve/rechaza la tool misma
        return False
    return fuera

# --- Tope de salida por herramienta -----------------------------------
#
# Podar el historial recorta lo que YA entro en el contexto. Esto es lo
# otro, y es mas barato: impedir que entre. Un `read_reference_component`
# devuelve ~25.000 caracteres de HTML y `read_file` no tenia ningun limite,
# asi que un archivo de 3.000 lineas se colaba entero de una sentada - y
# despues se reenviaba en cada vuelta hasta que la poda lo alcanzaba.
#
# El tope va en `dispatch`, que es por donde pasan TODAS las llamadas, y no
# en cada tool: asi tambien cubre las que se añadan mañana, que es
# justamente donde fallan los limites puestos uno a uno.
#
# Los numeros no son redondos por gusto: son lo que hace falta para que la
# salida siga siendo util. Un componente de web-kit se lee para copiar su
# estructura, y eso se ve en los primeros miles de caracteres; una busqueda
# se lee para elegir un candidato, no para auditar los veinte.
TOPE_POR_DEFECTO = 6_000
TOPES: dict[str, int] = {
    "read_file": 12_000,               # el modelo tiene que poder leer su propio codigo
    "read_reference_component": 10_000,
    "verificar_web": 8_000,
    "search_images": 5_000,
    "search_web_image": 5_000,
    "search_web": 4_000,
    "web_fetch": 8_000,
    # Ejemplos de codigo reales: se leen para copiar/adaptar, y el codigo
    # ocupa. Un poco mas de holgura que una busqueda normal.
    "code_examples": 8_000,
    "search_code": 6_000,
    "sample_data": 5_000,
    "find_api": 5_000,
    # Wikipedia: articulo entero para documentar un tema. Holgado a proposito -
    # es material para escribir un documento, no un dato suelto.
    "wikipedia": 13_000,
    # Diseño: UIverse trae codigo HTML+CSS de 2 elementos; los demas son listas.
    "uiverse": 9_000,
    "search_icons": 4_000,
    "search_fonts": 4_000,
    "list_dir": 3_000,
    "glob_search": 3_000,
    "grep_search": 4_000,
    "list_reference_components": 5_000,
    # Datos de negocio reales: se recortan tarde y poco, porque lo que se
    # pierde aqui el modelo se lo inventa (horarios, telefonos, precios).
    "fetch_business_from_maps": 20_000,
    "fetch_menu_and_reviews_from_maps": 20_000,
    "ask_user": 20_000,
}

# Como se pide el resto. Sin esta pista el modelo ve el corte y su reflejo
# es volver a llamar a la misma tool igual, que devuelve lo mismo cortado.
_COMO_SEGUIR = {
    "read_file": "vuelve a llamar a read_file con `desde` en la linea donde se corto",
    "read_reference_component": "si necesitas el resto, dilo y pidelo por partes",
    "grep_search": "afina el patron o filtra por carpeta",
    "list_dir": "entra en una subcarpeta concreta",
    "glob_search": "usa un patron mas especifico",
}


def acotar_salida(nombre: str, resultado) -> str:
    """Recorta la salida de una tool al tope de su familia.

    Se avisa SIEMPRE de que hubo corte y de cuanto falta: un recorte
    silencioso es peor que la salida larga, porque el modelo da por completo
    lo que no lo esta y decide sobre datos que no vio."""
    texto = resultado if isinstance(resultado, str) else str(resultado)
    tope = TOPES.get(nombre, TOPE_POR_DEFECTO)
    if len(texto) <= tope:
        return texto
    seguir = _COMO_SEGUIR.get(nombre)
    aviso = (
        f"\n\n… [cortado: se muestran {tope} de {len(texto)} caracteres."
        + (f" Para ver mas, {seguir}.]" if seguir else "]")
    )
    return texto[:tope] + aviso


TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "load_skill_section",
            "description": (
                "Carga una seccion de REFERENCIA de tu propio skill que no viene en el "
                "prompt base. Los skills grandes no se cargan enteros (el de web son "
                "~24K tokens reenviados en cada turno, y un prompt asi de largo hace "
                "que los modelos de este tamaño caigan en defaults genericos), asi que "
                "sus secciones de consulta se piden aqui cuando toca. Si tu prompt "
                "termina con un indice de 'Secciones que NO estan cargadas todavia', "
                "esas son las que se piden con esta tool, en el momento que indica cada "
                "una. Si no tienes ese indice, tu skill ya viene completo y esta tool no "
                "te hace falta."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "section": {
                        "type": "string",
                        "description": (
                            "Prefijo de la seccion tal como aparece en el indice (ej. "
                            "\"2.\") o parte de su titulo (ej. \"Sistema de diseño\")."
                        ),
                    },
                },
                "required": ["section"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_to_specialist",
            "description": (
                "Delega una SUBTAREA concreta a otro especialista del catalogo. El "
                "delegado ejecuta su PROPIO ciclo completo (su skill, sus herramientas, "
                "sus confirmaciones si escribe algo) y devuelve su resultado final para "
                "que lo incorpores al tuyo. Solo se permite UN nivel: el delegado no "
                "puede delegar a su vez."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "specialist": {
                        "type": "string",
                        "description": "Nombre exacto del skill a delegar (ver --list-skills para la lista valida).",
                    },
                    "subtask": {
                        "type": "string",
                        "description": "La subtarea concreta a resolver, en lenguaje claro y autocontenido.",
                    },
                    "context_summary": {
                        "type": "string",
                        "description": (
                            "Resumen breve de lo relevante de la conversacion actual que el "
                            "especialista delegado necesita saber - no mandes el historial "
                            "entero, solo lo que importa para esta subtarea puntual."
                        ),
                    },
                },
                "required": ["specialist", "subtask"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": (
                "Le hace una pregunta directa al usuario y devuelve su respuesta como "
                "texto, dejando el turno abierto. Si la sesion corre con --yes (modo "
                "desatendido) no hay nadie que conteste: aplica tu mejor criterio y "
                "déjalo explicito en la respuesta final."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Opcional: 2-4 opciones concretas y mutuamente excluyentes para que "
                            "el usuario elija por numero en vez de escribir texto libre."
                        ),
                    },
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "peek_file",
            "description": (
                "Esqueleto de un archivo grande al ~5% de tokens: te dice que "
                "funciones/clases/secciones tiene y en que linea empieza cada una, "
                "SIN volcarte el contenido. ÚSALO ANTES de read_file en cualquier "
                "archivo largo: léelo entero solo cuando sepas que rango necesitas. "
                "Cada archivo que lees se reenvia en las vueltas siguientes, asi que "
                "un read_file de 3.000 lineas se paga muchas veces, no una."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Ruta dentro del workspace."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_json",
            "description": (
                "Consulta o agrega un archivo JSON/JSONL grande sin volcarlo entero. "
                "Úsalo en vez de read_file sobre un .json de mas de unas pocas "
                "lineas: leer 2 MB de JSON para mirar tres campos llena el contexto "
                "sin aportar nada."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Ruta del .json o .jsonl."},
                    "expresion": {
                        "type": "string",
                        "description": (
                            "Ruta de campos a extraer, estilo 'items.0.nombre' o "
                            "'usuarios[].email'. Vacio: devuelve la estructura."
                        ),
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Lee un archivo de texto del workspace. Devuelve una VENTANA de 400 "
                "lineas desde `desde`, no el archivo entero, y avisa de cuantas quedan. "
                "Si ya sabes que parte te interesa (por peek_file o grep_search), pide "
                "esa ventana en vez de leer desde el principio."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Ruta relativa al workspace."},
                    "desde": {"type": "integer", "description": "Primera linea a leer (desde 1)."},
                    "lineas": {"type": "integer", "description": "Cuantas leer. Por defecto 400."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Crea o sobreescribe un archivo de texto con el contenido dado. Por defecto "
                "escribe DENTRO del workspace (usa una ruta relativa). Si el USUARIO pide "
                "guardar en otro sitio, puedes pasar en `path` el nombre de una carpeta "
                "conocida ('descargas/informe.txt', 'escritorio/web/index.html', "
                "'documentos/...') o una ruta absoluta que el usuario haya indicado - se le "
                "pedira una confirmacion con la ruta exacta antes de escribir. NO uses una "
                "carpeta de fuera por tu cuenta: solo cuando el usuario lo pidio."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Reemplaza una unica ocurrencia de old_text por new_text dentro de un archivo "
                "existente. Falla si old_text no aparece exactamente una vez."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "Lista archivos y subdirectorios dentro de una ruta del workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Default: raiz del workspace."}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob_search",
            "description": "Busca archivos por patron glob (ej. '**/*.py') dentro del workspace.",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_search",
            "description": "Busca una expresion regular en el contenido de archivos del workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "glob": {"type": "string", "description": "Filtro de archivos, default '**/*'."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_hard_cases",
            "description": (
                "Busca en la base de casos dificiles curada (RAG) por un patron/problema, "
                "opcionalmente filtrando por dominio (python, rust, security, office_email, etc.)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "domain": {"type": "string"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_reference_components",
            "description": (
                "Devuelve TODA la libreria de referencia web-kit en UNA sola llamada: cada "
                "entrada trae 'path' y 'description' (que hace ese archivo), asi que con esta "
                "unica llamada ya puedes decidir cuales 2-4 archivos hacen falta para ESTE "
                "proyecto SIN necesidad de abrir cada uno para ver su contenido. Llámala UNA "
                "vez (sin category, o una vez por category si prefieres mirar por partes), lee "
                "las descripciones, decide la lista corta de lo que vas a usar, y solo entonces ahi "
                "llama a read_reference_component solo para esos - no explores la libreria "
                "entera abriendo archivo por archivo, eso quema turnos sin necesidad."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["", "animaciones", "componentes", "layouts", "datos"],
                        "description": "Filtrar por categoria, o vacio para listar todas.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_reference_component",
            "description": (
                "Devuelve el codigo completo de un archivo de la libreria web-kit (ver "
                "list_reference_components para los nombres exactos, ej. "
                "'animaciones/scroll-reveal.html'). ADAPTA el patron al sistema de diseño del "
                "proyecto (sus custom properties, paleta, tipografia) - no lo copies tal cual con "
                "los colores/valores de demo del archivo."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Ruta relativa, ej. 'componentes/menu-movil.html'."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": (
                "Ejecuta una query SQL contra una base SQLite EN MEMORIA montada al vuelo con "
                "un esquema y unos datos de prueba, y devuelve las FILAS reales y el plan "
                "(EXPLAIN QUERY PLAN). Úsala para VERIFICAR una consulta antes de darla por "
                "buena en vez de afirmar de memoria que 'el plan usa un indice' o que devuelve "
                "lo pedido: monta un dataset chico y mira el resultado. Motor SQLite (no "
                "Postgres/MySQL): el SQL estandar (JOIN, GROUP BY, window functions, CTEs) "
                "corre igual; sintaxis de otro motor dara un error de SQLite, que tambien es "
                "informacion util (la query no es portable)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "La consulta a verificar (una sola sentencia)."},
                    "schema": {
                        "type": "string",
                        "description": "DDL para crear las tablas (CREATE TABLE...; puede traer varias).",
                    },
                    "seed": {
                        "type": "string",
                        "description": "Filas de prueba (INSERT...). Sin datos, una agregacion no revela si cuenta bien.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "eval_xlsx_formulas",
            "description": (
                "CALCULA las formulas de una spec de hoja (la misma que generate_xlsx) y "
                "devuelve el valor real de cada celda de formula, mas avisos de anomalias. "
                "check_workbook solo valida ESTRUCTURA; openpyxl NO evalua formulas, asi que "
                "sin esto no sabes si `=B2/$C$2` da 16,7% o si por escribir `=C2/B2` da 600%. "
                "Marca el error clasico del dominio: la cuota/porcentaje invertida (valor >100%), "
                "la referencia a la hoja/celda equivocada (celda que calcula a vacio) y los "
                "errores de formula (#DIV/0!). Llámala ANTES de dar la hoja por buena."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sheets": {
                        "type": "array",
                        "description": "Las hojas, con la MISMA forma que en generate_xlsx (name, headers, rows, formulas, column_formats).",
                        "items": {"type": "object"},
                    },
                },
                "required": ["sheets"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compile_run_cpp",
            "description": (
                "Compila C++ con warnings altos (-Wall -Wextra -Wpedantic, y UBSan si el "
                "toolchain lo trae) y lo EJECUTA con limite de tiempo. Devuelve warnings de "
                "compilacion, salida del programa, y marca timeout=True si no termina (señal "
                "fuerte de BUCLE INFINITO - ej. 'arreglar' una invalidacion de iterador "
                "reiniciando el iterador). Úsala para VERIFICAR que tu C++ compila y hace lo "
                "que crees, en vez de razonarlo de cabeza: en C++ muchos errores graves "
                "compilan sin un solo aviso. El codigo debe ser una unidad completa con main()."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Codigo C++ completo, con un main()."},
                    "stdin": {"type": "string", "description": "Entrada estandar opcional para el programa."},
                    "timeout_s": {"type": "integer", "description": "Segundos maximos de ejecucion (default 5, max 15)."},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_check",
            "description": "Corre una herramienta de verificacion sobre el workspace (Python).",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string", "enum": ["ruff", "mypy", "pytest"]},
                    "path": {"type": "string", "description": "Opcional, default '.' (todo el workspace)."},
                },
                "required": ["tool"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "install_dependency",
            "description": (
                "Instala una dependencia de Python que le falta al codigo que acabas de "
                "escribir. Úsala cuando una comprobacion falle porque una libreria NO "
                "esta instalada (mypy: 'Cannot find implementation or library stub', o "
                "un ImportError al ejecutar): eso no se arregla escribiendo codigo ni "
                "creando un requirements.txt, hay que instalarla de verdad. Solo el "
                "nombre del paquete, sin versiones con espacios ni URLs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "packages": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": 'Paquetes a instalar, ej. ["matplotlib", "numpy"].',
                    },
                },
                "required": ["packages"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_docx",
            "description": (
                "Genera un documento Word (.docx) con estructura real (headings, listas, tablas "
                "e IMAGENES). Cada seccion puede llevar 'image' con la URL de una foto (de "
                "search_images) y 'caption' de pie/texto-alt - úsalo para ilustrar el documento. "
                "Por defecto en el workspace; si el usuario pide otro sitio, `path` acepta una "
                "carpeta conocida ('descargas/informe.docx') o una ruta absoluta que el usuario "
                "dio (pide confirmacion de la ruta antes de escribir)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "heading": {"type": "string"},
                                "level": {"type": "integer"},
                                "text": {"type": "string"},
                                "list_items": {"type": "array", "items": {"type": "string"}},
                                "ordered": {"type": "boolean"},
                                "table": {
                                    "type": "object",
                                    "description": "Tabla de datos con fila de encabezado",
                                    "properties": {
                                        "headers": {"type": "array", "items": {"type": "string"}},
                                        "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                                    },
                                },
                                "image": {"type": "string", "description": "URL de una foto (de search_images) para incrustar"},
                                "caption": {"type": "string", "description": "Pie de foto visible + texto alternativo (accesibilidad)"},
                            },
                        },
                    },
                },
                "required": ["path", "sections"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_xlsx",
            "description": (
                "Genera una hoja de calculo (.xlsx) con encabezados, filas y formulas reales. "
                "Por defecto en el workspace; si el usuario pide otro sitio, `path` acepta una "
                "carpeta conocida ('descargas/datos.xlsx') o una ruta absoluta que el usuario "
                "dio (pide confirmacion de la ruta antes de escribir)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "sheets": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "headers": {"type": "array", "items": {"type": "string"}},
                                "rows": {"type": "array", "items": {"type": "array"}},
                                # Sin estos tres campos declarados el modelo
                                # no sabia que existian y entregaba totales
                                # calculados a mano, fechas como texto y
                                # euros sin formato - justo lo que el skill
                                # de office-spreadsheet le prohibe.
                                "formulas": {
                                    "type": "object",
                                    "description": 'Formula por celda: {"B10": "=SUM(B2:B9)"}',
                                },
                                "date_columns": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                    "description": "Indices de columna (desde 0) que son fechas reales",
                                },
                                "column_formats": {
                                    "type": "object",
                                    "description": (
                                        'Formato por indice de columna (desde 0): '
                                        '{"1": "$#,##0", "3": "0.0%"}'
                                    ),
                                },
                            },
                        },
                    },
                },
                "required": ["path", "sheets"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_pptx",
            "description": (
                "Genera una presentacion (.pptx) CON DISEÑO y VARIEDAD de diapositivas (no la "
                "misma plana repetida). Cada slide tiene un 'kind' que decide su maqueta: "
                "'cover' (portada: title+subtitle), 'section' (separador: title+subtitle-eyebrow), "
                "'bullets' (title+bullets, +image opcional a la derecha), 'two_column' "
                "(comparacion: left_title/left_bullets vs right_title/right_bullets), 'stat' "
                "(una CIFRA grande: title+stat+caption), 'quote' (cita: quote+author), 'image' "
                "(foto grande: title+image+caption), 'table' (title+table{headers,rows}). ALTERNA "
                "kinds para que no sea monotona: portada, secciones, alguna cifra, una comparacion, "
                "una cita, fotos DISTINTAS (nunca repitas la misma URL). El diseño (tema, color, "
                "tipografia) lo pone la herramienta. Por defecto en el workspace; si el usuario "
                "pide otro sitio, `path` acepta una carpeta conocida o una ruta absoluta (pide "
                "confirmacion antes de escribir)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "theme": {
                        "type": "string",
                        "enum": ["medianoche", "pizarra", "profundo", "bosque", "editorial", "corporativo"],
                        "description": (
                            "Tema visual del deck (elige segun el tono): medianoche (oscuro/ambar, "
                            "versatil), pizarra (oscuro/teal, tech), profundo (oscuro/coral, "
                            "audaz), bosque (oscuro/verde, natural), editorial (claro crema/rojo, "
                            "revista), corporativo (blanco/azul, formal). Default: medianoche."
                        ),
                    },
                    "slides": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": ["cover", "section", "bullets", "two_column", "stat", "quote", "image", "table"],
                                    "description": "Maqueta de la diapositiva. Default: bullets.",
                                },
                                "title": {"type": "string"},
                                "subtitle": {"type": "string", "description": "Para 'cover' (bajada) o 'section' (eyebrow encima del titulo)"},
                                "bullets": {"type": "array", "items": {"type": "string"}},
                                "left_title": {"type": "string"},
                                "left_bullets": {"type": "array", "items": {"type": "string"}},
                                "right_title": {"type": "string"},
                                "right_bullets": {"type": "array", "items": {"type": "string"}},
                                "stat": {"type": "string", "description": "La cifra grande, ej. '3,3 s' o '625 CV' (kind 'stat')"},
                                "caption": {"type": "string", "description": "Pie: de la cifra ('stat'), de la foto ('image')"},
                                "quote": {"type": "string"},
                                "author": {"type": "string"},
                                "image": {"type": "string", "description": "URL de una foto (de search_images). NO repitas la misma en dos slides."},
                                "table": {
                                    "type": "object",
                                    "properties": {
                                        "headers": {"type": "array", "items": {"type": "string"}},
                                        "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                                    },
                                },
                                "speaker_notes": {"type": "string"},
                            },
                        },
                    },
                },
                "required": ["path", "slides"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lint_email",
            "description": "Chequea heuristicamente un correo (asunto + cuerpo): placeholders, saludo/cierre, longitud.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["subject", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_contrast",
            "description": (
                "Calcula el RATIO de contraste WCAG entre dos colores (texto y fondo) y dice si "
                "cumple AA. No lo estimes a ojo: pásale los DOS hex y usa el numero que devuelve. "
                "Acepta '#rrggbb', '#rgb' o nombres CSS basicos (white, black...). Devuelve el ratio "
                "(ej. 1.2:1) y si pasa AA texto normal (>=4.5), AA texto grande/UI (>=3.0) y AAA (>=7)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "color_texto": {"type": "string", "description": "Color del texto (foreground), ej. '#bbbbbb'"},
                    "color_fondo": {"type": "string", "description": "Color de fondo (background), ej. '#cccccc'"},
                },
                "required": ["color_texto", "color_fondo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan",
            "description": (
                "Muestra en pantalla tu PLAN de trabajo: una lista de pasos con su estado, para "
                "dividir una tarea de varios pasos y que el usuario vea el progreso. Llámala AL "
                "EMPEZAR con todos los pasos en 'pendiente', y OTRA VEZ cada vez que termines uno "
                "(márcalo 'hecho' y pon el siguiente en 'en_curso'). Solo UN paso 'en_curso' a la "
                "vez. No la uses para una tarea de un solo paso (una pregunta, un cambio de una "
                "linea): ahi el plan es ruido."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pasos": {
                        "type": "array",
                        "description": "Los pasos, en orden.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "paso": {"type": "string", "description": "Que hay que hacer, en una linea corta"},
                                "estado": {
                                    "type": "string",
                                    "enum": ["pendiente", "en_curso", "hecho"],
                                },
                            },
                            "required": ["paso", "estado"],
                        },
                    },
                },
                "required": ["pasos"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": (
                "Trae el texto y los links de una pagina web real (scraper simple, sin ejecutar JS). "
                "Usar para leer contenido de una URL que dio el usuario (su sitio actual, una referencia, etc.)."
            ),
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_business_info",
            "description": (
                "Extrae horarios/telefono/direccion/valoraciones de la pagina de un negocio (via "
                "datos estructurados schema.org si existen, o una extraccion heuristica de texto si no). "
                "Usar en vez de inventar estos datos cuando el sitio es para un negocio real."
            ),
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_business_from_maps",
            "description": (
                "Busca un negocio REAL en Google Maps por nombre+ciudad (o URL directa de su ficha) y "
                "devuelve nombre, categoria, direccion, telefono, web, rating, numero de reseñas, "
                "horario, coordenadas, y la URL de su foto principal si tiene - datos REALES, no "
                "inventados. Usar esto ANTES que extract_business_info/web_fetch cuando el usuario dio "
                "el nombre de un negocio real (aunque no haya dado una URL) - es la fuente mas directa "
                "para datos de un negocio local. Si no encuentra un resultado especifico (nombre "
                "ambiguo), devuelve el error explicando que hacer. AVISO: esto scrapea Google Maps, lo "
                "cual va contra sus Terminos de Servicio - usar solo para montar la web del propio "
                "negocio (con su consentimiento), no para recoleccion masiva de datos de terceros."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "consulta": {
                        "type": "string",
                        "description": "Nombre y ciudad del negocio (ej. 'Loyal Razor, Valencia'), o URL directa de su ficha de Maps.",
                    },
                },
                "required": ["consulta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_menu_and_reviews_from_maps",
            "description": (
                "Para restaurantes, bares y cafeterias reales: trae dos señales de TEXTO real de la ficha de "
                "Google Maps (no fotos, no necesita vision ni Ollama) - 'items_carta' (nombres de plato de la "
                "pestaña Carta, si el negocio la tiene) y 'resenas' (las mejor valoradas, nunca respuestas "
                "del propio negocio; cada una con 'autor', 'estrellas' y 'texto' reales). PROHIBIDO inventar "
                "un testimonio o firmarlo con un autor ficticio aunque suene plausible: si la web lleva "
                "testimonios, se cita el 'texto' y el 'autor' de aqui tal cual (hard_cases: "
                "web-invented-fake-testimonials). Puede devolver ambos vacios si el negocio no tiene esa "
                "seccion en Maps, y eso no es un error. Resuelve el negocio por su cuenta: basta con pasarle "
                "la misma consulta. Mismo aviso legal que fetch_business_from_maps."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "consulta": {
                        "type": "string",
                        "description": "Nombre y ciudad del negocio, igual que en fetch_business_from_maps.",
                    },
                },
                "required": ["consulta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_menu_photos_from_maps",
            "description": (
                "FALLBACK para cuando fetch_menu_and_reviews_from_maps no encontro 'items_carta': abre la "
                "GALERIA de fotos de la ficha de Google Maps (no solo la principal, a diferencia de "
                "fetch_business_from_maps) y devuelve varias URL en buena resolucion. Muchos negocios suben "
                "una foto de su carta fisica; combínalo con extract_menu_text para leer platos y precios "
                "reales en vez de inventarlos. Es menos fiable que la tool de carta y reseñas porque depende "
                "de un modelo de vision local que puede no estar disponible, y puede no encontrar nada. "
                "Resuelve el negocio por su cuenta con la misma consulta (nombre y ciudad). Mismo aviso legal "
                "que fetch_business_from_maps."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "consulta": {
                        "type": "string",
                        "description": "Nombre y ciudad del negocio, igual que en fetch_business_from_maps.",
                    },
                    "max_fotos": {
                        "type": "integer",
                        "description": "Cuantas fotos traer como maximo (default 6). No pidas mas de 8-10.",
                    },
                },
                "required": ["consulta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_images",
            "description": (
                "Busca fotos ROYALTY-FREE (uso comercial permitido) en Pexels/Unsplash/Pixabay. Requiere una "
                "API key gratis por fuente (PEXELS_API_KEY/UNSPLASH_ACCESS_KEY/PIXABAY_API_KEY); si falta, el "
                "error explica como conseguirla. Nunca scrapea un buscador generico. Cada resultado trae ya "
                "el analisis tecnico ('analisis': nitidez, brillo, resolucion, colores) y, si pasas "
                "`rubro_negocio`, tambien el de contenido con vision ('contenido', lo mismo que "
                "classify_image_content), asi que no hace falta llamar a analyze_image ni a "
                "classify_image_content sobre estos resultados. Incluye ademas 'description', el texto que el "
                "banco de imagenes le puso a la foto. Con `queries` (lista) devuelve {tema: {source, results, "
                "error}}, una entrada por tema."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Un solo tema de busqueda. Usa `queries` en vez de esto si necesitas varios temas distintos."},
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Varios temas de busqueda distintos en una sola llamada (ej. ['interior acogedor', 'plato destacado', 'ambiente barra']). Si se pasa esto, ignora `query`.",
                    },
                    "source": {"type": "string", "enum": ["pexels", "unsplash", "pixabay"]},
                    "count": {"type": "integer"},
                    "rubro_negocio": {
                        "type": "string",
                        "description": "Rubro del negocio (ej. 'restaurante tradicional') - fusiona classify_image_content en cada resultado. Pásalo siempre que lo sepas.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web_image",
            "description": (
                "Busca imagenes en TODA la web (DuckDuckGo Images, sin API key). A diferencia de "
                "search_images, NO tiene ninguna garantia de licencia: sirve solo para un sujeto especifico e "
                "identificable (un poster, una portada, un logo, una persona famosa concreta) en contenido "
                "propio o informal, NUNCA para las fotos genericas del sitio publico de un cliente real - un "
                "poster o la foto de un famoso estan MAS protegidos por derechos de autor que una foto de "
                "stock, no menos (hard_cases: web-scraped-images-copyright-risk). Si dudas de si el contexto "
                "es un negocio real o un proyecto personal, usa search_images o pregunta al usuario. Cada "
                "resultado trae ya el analisis tecnico ('analisis') y una verificacion con vision de que la "
                "imagen muestra el sujeto pedido ('verificacion': 'coincide' bool + 'descripcion'). Con "
                "`queries` (lista) devuelve {sujeto: {results, error}}, una entrada por sujeto."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Un solo sujeto a buscar - lo mas especifico posible (nombre completo, titulo exacto, año si aplica). Usa `queries` en vez de esto si necesitas varios sujetos distintos."},
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Varios sujetos distintos en una sola llamada (ej. ['poster pelicula Inception 2010', 'foto Tom Hanks actor']). Si se pasa esto, ignora `query`.",
                    },
                    "count": {"type": "integer", "description": "Cuantos resultados devolver por sujeto (default 3)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_image",
            "description": (
                "Analiza una imagen (URL o archivo del workspace): color dominante, brillo, si es calida "
                "o fria, nitidez/borrosidad, resolucion y si sirve como fondo a pantalla completa. Los "
                "resultados de search_images YA TRAEN esto incluido (clave 'analisis') - no la llames de "
                "nuevo sobre esas. Úsala para imagenes que no vinieron de search_images: la foto principal "
                "de fetch_business_from_maps, fotos de fetch_menu_photos_from_maps, etc. - elegir una foto "
                "solo por su descripcion, sin analizar como se ve, da resultados horribles (texto "
                "illegible sobre un fondo del color equivocado, fotos pixeladas, etc.)."
            ),
            "parameters": {
                "type": "object",
                "properties": {"url_or_path": {"type": "string"}},
                "required": ["url_or_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "classify_image_content",
            "description": (
                "Analiza el CONTENIDO real de una imagen con un modelo de vision local (Ollama): dice si el "
                "TIPO de foto (fachada, interior, producto, plato, persona...) y su calidad encajan con el "
                "rubro del negocio. analyze_image, en cambio, solo mide brillo, nitidez y resolucion. "
                "FIABILIDAD: acierta ~75% de las veces y la misma foto puede dar otra respuesta en otra "
                "corrida, asi que es una señal mas junto al campo 'description' de search_images, no el "
                "arbitro. Si las dos no coinciden, trata la foto como incierta y prueba otra. Si Ollama no "
                "esta disponible, el error explica como arreglarlo."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url_or_path": {"type": "string"},
                    "rubro_negocio": {
                        "type": "string",
                        "description": "Rubro del negocio (ej. 'restaurante de cocina catalana', 'peluqueria', 'gimnasio').",
                    },
                },
                "required": ["url_or_path", "rubro_negocio"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_menu_text",
            "description": (
                "Usa el MISMO modelo de vision local que classify_image_content, pero para TRANSCRIBIR "
                "texto (OCR) en vez de clasificar - pensado para leer una foto de carta/menu real (ej. de "
                "fetch_menu_photos_from_maps). Primero determina si la imagen es realmente una carta "
                "('es_carta'); si no lo es, 'texto_extraido' viene vacio y hay que probar la siguiente foto "
                "candidata. MISMO AVISO DE CONFIABILIDAD que classify_image_content (~75%, no perfecto): "
                "tratar 'texto_extraido' como un BORRADOR a revisar (puede tener platos/precios mal leidos o "
                "incompletos), nunca copiarlo literal sin sentido critico - si un precio se ve absurdo o un "
                "nombre de plato no tiene sentido, es mas probable que sea un error de lectura que un dato "
                "real."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url_or_path": {"type": "string"},
                },
                "required": ["url_or_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deep_research",
            "description": (
                "Investigacion recursiva y CIBERSEGURA sobre una pregunta O SUJETO: busca, LEE "
                "las paginas top, sigue sus enlaces mas relevantes una o dos capas mas, y devuelve "
                "las FUENTES (url + titulo + pasajes literales + 'anclas') mas un resumen. Úsala en "
                "vez de search_web cuando la duda necesita LEER varias fuentes y contrastarlas (no "
                "solo una lista de links): comparativas, 'como funciona X', estado del arte, o "
                "investigar a una persona/empresa. Para investigar PRODUCTIVAMENTE pasa 'consultas' "
                "(varios angulos del sujeto: nombre exacto, nombre+ciudad, nombre+profesion, "
                "nombre site:linkedin.com...) y 'terminos_clave' (rasgos del sujeto: ciudad, "
                "profesion, empresa) para que cada fuente te diga en 'anclas' que rasgos confirma "
                "-asi separas a dos personas del mismo nombre en vez de fundirlas-. Redacta tu "
                "respuesta a partir de las fuentes y CITA las urls; si devuelve error, dilo, NO "
                "inventes. Todo fetch va con filtro SSRF y tope de tamaño."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pregunta": {"type": "string", "description": "La pregunta o sujeto a investigar, en lenguaje natural."},
                    "profundidad": {"type": "integer", "description": "Capas de enlaces a seguir (1-3, default 2)."},
                    "max_paginas": {"type": "integer", "description": "Tope de paginas a leer (default 6, max 15)."},
                    "urls_semilla": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Opcional: URLs de partida (si ya sabes por donde empezar, o si la busqueda falla).",
                    },
                    "consultas": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Opcional: varias consultas de ANGULO para barrer el sujeto en una sola "
                            "llamada (ej. '\"Juan Perez\" Madrid', '\"Juan Perez\" ingeniero', "
                            "'\"Juan Perez\" site:linkedin.com'). Sus resultados se juntan y deduplican."
                        ),
                    },
                    "terminos_clave": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Opcional: RASGOS que identifican al sujeto (ciudad, profesion, empresa, "
                            "alias). Cada fuente devuelve en 'anclas' cuales de estos rasgos menciona: "
                            "una fuente con el nombre pero sin ningun rasgo esperado puede ser un HOMONIMO."
                        ),
                    },
                },
                "required": ["pregunta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Busqueda web general (DuckDuckGo por default, sin API key) para dudas propias o datos en tiempo real.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "count": {"type": "integer"},
                    "engine": {"type": "string", "enum": ["duckduckgo", "brave"]},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "code_examples",
            "description": (
                "Trae EJEMPLOS DE CODIGO REALES desde Stack Overflow: las respuestas mas "
                "votadas/aceptadas a una duda, con su codigo tal cual. Úsala en vez de "
                "escribir de memoria el uso de una libreria, una firma de funcion o un "
                "patron que no dominas al 100%: en los detalles tu memoria FALLA (nombres de "
                "parametros, orden de argumentos, metodo exacto) y te los inventas con "
                "seguridad. Aqui viene la version que miles de personas votaron como "
                "correcta - adáptala, no la copies con los datos de demo. Gratis (300/dia sin "
                "clave; STACKEXCHANGE_KEY sube a 10.000)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "consulta": {"type": "string", "description": "La duda en lenguaje natural, como la buscarias en Google."},
                    "lenguaje": {"type": "string", "description": "Opcional: tag para filtrar (python, javascript, sql, rust...)."},
                    "limite": {"type": "integer", "description": "Cuantos hilos devolver (1-5, default 3)."},
                },
                "required": ["consulta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": (
                "Busca CODIGO REAL en repositorios publicos de GitHub y devuelve el fragmento "
                "que coincide + el repo + el enlace al archivo. Complementa a code_examples: "
                "SO da la respuesta canonica a un problema, esto muestra como se USA de "
                "verdad una libreria/patron en proyectos reales. Requiere GITHUB_TOKEN (gratis, "
                "sin permisos especiales); si falta, el error lo explica y puedes usar "
                "code_examples mientras tanto."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "consulta": {"type": "string", "description": "Terminos o expresion (ej. 'asyncio.gather timeout')."},
                    "lenguaje": {"type": "string", "description": "Opcional: filtro language: (python, typescript, go...)."},
                    "limite": {"type": "integer", "description": "Cuantos resultados (1-10, default 5)."},
                },
                "required": ["consulta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "package_info",
            "description": (
                "Datos ACTUALES y reales de un paquete desde su registro oficial (PyPI, npm, "
                "crates.io, Go, Packagist, RubyGems, NuGet, Maven): version ultima, "
                "descripcion, repo y licencia. Úsala SIEMPRE antes de fijar una version en un "
                "requirements/package.json o de afirmar 'la ultima es X': los numeros de "
                "version dichos de memoria suelen estar desfasados o no existir. Gratis, sin "
                "clave."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ecosystem": {
                        "type": "string",
                        "enum": ["pypi", "npm", "crates", "go", "packagist", "rubygems", "nuget", "maven"],
                    },
                    "name": {
                        "type": "string",
                        "description": "Nombre del paquete (go = ruta del modulo; maven = 'grupo:artefacto'; packagist = 'vendor/paquete').",
                    },
                },
                "required": ["ecosystem", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vuln_check",
            "description": (
                "Consulta vulnerabilidades conocidas (CVEs) de un paquete en OSV.dev y en que "
                "version se arreglaron. Úsala antes de recomendar/añadir una dependencia o al "
                "revisar seguridad, en vez de opinar 'esa version parece vieja': aqui esta el "
                "dato real (agrega GitHub Advisory, RustSec, PyPA, Go vuln DB...). Con "
                "`version` filtra a las que afectan esa version. Gratis, sin clave."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ecosystem": {
                        "type": "string",
                        "enum": ["pypi", "npm", "crates", "go", "packagist", "rubygems", "nuget", "maven"],
                    },
                    "name": {"type": "string", "description": "Nombre del paquete (maven = 'grupo:artefacto')."},
                    "version": {"type": "string", "description": "Opcional: version concreta a comprobar."},
                },
                "required": ["ecosystem", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_api",
            "description": (
                "Busca en un DIRECTORIO de miles de APIs publicas (APIs.guru) por tema y "
                "devuelve nombre, descripcion, categoria, docs y su OpenAPI. Úsala cuando el "
                "usuario quiere una web/app que muestre datos reales (tiempo, cripto, "
                "divisas, deportes, noticias...) y hay que cablear una API de verdad en vez "
                "de inventar los datos. Gratis, sin clave. (Los terminos en ingles dan mas "
                "resultados.)"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "consulta": {"type": "string", "description": "Tema/categoria (ej. 'weather', 'crypto', 'currency')."},
                    "limite": {"type": "integer", "description": "Cuantas devolver (1-15, default 6)."},
                },
                "required": ["consulta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wikipedia",
            "description": (
                "Trae el ARTICULO REAL de Wikipedia sobre un tema (en texto plano) + su "
                "titulo y URL. Es la MEJOR fuente para un documento/presentacion/web SOBRE "
                "algo del mundo -un coche, una empresa, una persona, un lugar, un hecho-: "
                "en vez de escribir de memoria (y equivocarte en cifras, fechas, modelos, "
                "codigos con total seguridad), sacas los datos de la enciclopedia. Llámala "
                "ANTES de redactar. Gratis, sin clave. Idioma 'es' por defecto (cae a 'en' "
                "si no hay articulo en español). Cita la URL que devuelve."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "consulta": {"type": "string", "description": "El tema a buscar (ej. 'BMW Serie 1 F40', 'Frida Kahlo')."},
                    "idioma": {"type": "string", "description": "'es' (default) o 'en'.", "enum": ["es", "en"]},
                },
                "required": ["consulta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sample_data",
            "description": (
                "Trae DATOS DE EJEMPLO realistas (DummyJSON): productos, usuarios, posts, "
                "carritos, recetas, quotes... Para prototipar una web/demo con contenido "
                "creible SIN inventar un JSON a mano ni usar datos reales de nadie. Gratis, "
                "sin clave."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tipo": {
                        "type": "string",
                        "enum": ["products", "carts", "users", "posts", "comments", "todos", "quotes", "recipes"],
                    },
                    "cantidad": {"type": "integer", "description": "Cuantos registros (1-20, default 5)."},
                },
                "required": ["tipo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_icons",
            "description": (
                "Busca iconos en Iconify (200.000+ de 150+ colecciones: Material, MDI, "
                "Font Awesome, Lucide...) y devuelve su nombre y la URL SVG lista para poner "
                "en un <img>. Úsala en vez de dibujar un SVG de icono a mano (te sale roto) o "
                "de asumir que un set de iconos esta cargado. Gratis, sin clave. (Terminos en "
                "ingles: home, cart, menu, search.)"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Que icono buscas (ej. 'shopping cart')."},
                    "limite": {"type": "integer", "description": "Cuantos (1-30, default 12)."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_fonts",
            "description": (
                "Busca tipografias REALES de Google Fonts y devuelve el <link> listo con los "
                "pesos correctos. Úsala en vez de escribir un font-family de memoria (te "
                "inventas el nombre o unos pesos que la fuente no tiene). Filtra por nombre o "
                "por categoria (serif, sans serif, display, handwriting, monospace); sin "
                "query, las mas populares. Gratis, sin clave."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Parte del nombre (ej. 'montserrat'). Vacio = populares."},
                    "categoria": {"type": "string", "description": "serif, sans serif, display, handwriting, monospace."},
                    "limite": {"type": "integer", "description": "Cuantas (1-20, default 8)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "color_palette",
            "description": (
                "Genera una paleta de color ARMONICA a partir de un color base (The Color "
                "API). Úsala para dar cohesion en vez de elegir colores sueltos a ojo: le das "
                "el color de marca y el modo (analogic, complement, triad, monochrome...) y "
                "devuelve los hex con su nombre. Gratis, sin clave."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "base": {"type": "string", "description": "Color base en hex, con o sin '#' (ej. '#0047AB')."},
                    "modo": {
                        "type": "string",
                        "enum": ["monochrome", "monochrome-dark", "monochrome-light", "analogic",
                                 "complement", "analogic-complement", "triad", "quad"],
                    },
                    "cantidad": {"type": "integer", "description": "Cuantos colores (3-8, default 5)."},
                },
                "required": ["base"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "design_assets",
            "description": (
                "Construye URLs LISTAS PARA INCRUSTAR de servicios de assets gratis que "
                "quiza no conoces: avatares (DiceBear), recuadros de relleno (placehold.co), "
                "fotos de relleno (Lorem Picsum), logos de marca (Simple Icons, se verifica "
                "que exista) y codigos QR (QR Server). Úsala para no dejar huecos ni inventar "
                "un data-uri: pides el tipo y te da la URL bien formada. Todo gratis."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["avatar", "placeholder", "photo", "logo", "qr"]},
                    "seed": {"type": "string", "description": "Semilla del avatar / dato del QR."},
                    "brand": {"type": "string", "description": "Marca para el logo (ej. 'github', 'stripe', 'whatsapp')."},
                    "texto": {"type": "string", "description": "Texto del placeholder o dato del QR."},
                    "width": {"type": "integer", "description": "Ancho (placeholder/photo/qr)."},
                    "height": {"type": "integer", "description": "Alto (placeholder/photo)."},
                    "estilo": {"type": "string", "description": "Estilo del avatar DiceBear (thumbs, avataaars, bottts, initials...)."},
                },
                "required": ["kind"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "uiverse",
            "description": (
                "Trae una MUESTRA de elementos UI de UIverse (la mayor biblioteca "
                "open-source: botones, cards, loaders, toggles, formularios...) CON su "
                "animacion, en HTML+CSS listos para copiar-pegar y adaptar. Devuelve el "
                "codigo completo de los primeros. Úsala para partir de un diseño real y "
                "pulido en vez de uno soso desde cero - luego adapta colores/tipografia a tu "
                "proyecto. Todo MIT, gratis. No busca por estilo (los nombres son aleatorios): "
                "da una muestra variada de la categoria."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "categoria": {
                        "type": "string",
                        "enum": ["buttons", "cards", "checkboxes", "forms", "inputs", "loaders",
                                 "notifications", "patterns", "radio", "toggle", "tooltips"],
                    },
                    "limite": {"type": "integer", "description": "Cuantos elementos (1-8, default 4; codigo de los 2 primeros)."},
                },
                "required": ["categoria"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_links",
            "description": "Verifica que una lista de URLs no este rota (404, timeout, etc.) antes de dar un sitio por terminado.",
            "parameters": {
                "type": "object",
                "properties": {"urls": {"type": "array", "items": {"type": "string"}}},
                "required": ["urls"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verificar_web",
            "description": (
                "Verificacion completa de una pagina en UNA sola llamada: la renderiza "
                "en escritorio y en movil y mide sus defectos. Es la forma normal de "
                "comprobar una web. Devuelve un VEREDICTO DETERMINISTA - 'entregable' "
                "(true/false) y 'bloqueantes' (la lista) - que es lo que DECIDE si la web "
                "esta lista: errores de consola/JS, peticiones rotas, scroll horizontal, "
                "render en blanco, contraste WCAG insuficiente (texto casi del color de su "
                "fondo), imagenes rotas o deformadas, y menu movil que no abre (pulsa el "
                "boton hamburguesa y comprueba que despliega), todo medido por el navegador real. "
                "Ademas 'render'/'lint' con el detalle, y 'critica': una señal ASESORA de "
                "un modelo de vision (~75%) SOLO para lo subjetivo (proporciones, "
                "estetica) que NO bloquea la entrega. Regla: si 'entregable' es true, "
                "entrega; no re-verifiques para perseguir una critica limpia."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Ruta del HTML dentro del workspace (o una URL).",
                    },
                    "screenshot_path": {
                        "type": "string",
                        "description": "Nombre base de las capturas. Por defecto verificacion.png.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_files",
            "description": (
                "Borra varios archivos del workspace en UNA sola llamada (pasa la lista completa, no "
                "llames esto una vez por archivo). Uso principal: limpiar las capturas de render_check/"
                "critique_screenshot despues de revisarlas - son archivos de trabajo para verificar, no "
                "parte del sitio final, no tiene sentido dejarlas en la carpeta del proyecto. Ignora en "
                "silencio los archivos que ya no existan (no es un error). No borra carpetas ni archivos "
                "fuera del workspace."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Rutas relativas al workspace de todos los archivos a borrar.",
                    },
                },
                "required": ["paths"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_code_graph",
            "description": (
                "Consulta el grafo de conocimiento ya construido de ESTE MISMO repositorio "
                "(el orquestador, no un proyecto que estes construyendo para un usuario) - generado "
                "aparte con el skill '/graphify' de Claude Code. Devuelve funciones/clases/archivos "
                "relacionados con la busqueda y sus conexiones reales (quien llama a quien, en que "
                "archivo/linea). Util SOLO para dudas sobre COMO ESTA HECHO este sistema por dentro "
                "(arquitectura, que modulo usa a cual) - nunca para el codigo/contenido de la tarea "
                "que te pidio el usuario. Si el grafo no existe todavia (nadie corrio '/graphify' "
                "sobre este repo), devuelve un error explicandolo - no es algo que esta tool pueda "
                "generar por si sola."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Nombre (parcial) de una funcion/clase/archivo del repo, ej. 'auto_router' o 'run_agent'.",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Cuantos saltos de vecindad explorar desde cada coincidencia (por defecto 2).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shortest_path_in_graph",
            "description": (
                "Camino mas corto entre dos funciones/clases/archivos del grafo de conocimiento de "
                "ESTE repositorio (ver query_code_graph) - responde 'como se conecta X con Y' dentro "
                "del propio codigo del orquestador."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "origen": {"type": "string", "description": "Nombre (parcial) del nodo de origen."},
                    "destino": {"type": "string", "description": "Nombre (parcial) del nodo de destino."},
                },
                "required": ["origen", "destino"],
            },
        },
    },
]


def _run_subprocess(cmd: list[str], cwd: Path, timeout_s: int = 30) -> str:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout_s)
        output = (proc.stdout + "\n" + proc.stderr).strip()
        status = "pass" if proc.returncode == 0 else "fail"
        return f"[{status}] {output[-3000:]}"
    except subprocess.TimeoutExpired:
        return f"[fail] timeout tras {timeout_s}s"
    except FileNotFoundError as exc:
        return f"[fail] herramienta no instalada/no encontrada: {exc}"


_MAX_DELEGATION_DEPTH = 1  # un especialista puede delegar; el delegado ya no puede
_MAX_DELEGATION_TURNS = 8  # presupuesto de turnos mas chico que el de la tarea principal


def _fragmento_mas_parecido(original: str, old_text: str, contexto: int = 2) -> str:
    lineas_archivo = original.splitlines()
    primera_linea_old = next((ln.strip() for ln in old_text.splitlines() if ln.strip()), "")
    if not primera_linea_old or not lineas_archivo:
        return "No se encontro ninguna zona parecida en el archivo."
    coincidencias = difflib.get_close_matches(primera_linea_old, lineas_archivo, n=1, cutoff=0.4)
    if not coincidencias:
        return "No se encontro ninguna zona parecida - revisa que el path/archivo sean correctos."
    idx = lineas_archivo.index(coincidencias[0])
    inicio = max(0, idx - contexto)
    fin = min(len(lineas_archivo), idx + contexto + 1)
    fragmento = "\n".join(lineas_archivo[inicio:fin])
    return (
        f"Zona mas parecida en el archivo real (lineas {inicio + 1}-{fin}):\n---\n{fragmento}\n---\n"
        "Usa ese texto EXACTO (espacios/indentacion incluidos) como old_text."
    )



# --- Filtrado de herramientas por especialista -------------------------

_TOOL_SETS_PATH = Path(__file__).resolve().parent.parent / "config" / "tool_sets.yaml"
_tool_sets_cache: dict | None = None


def _tool_sets() -> dict:
    global _tool_sets_cache
    if _tool_sets_cache is None:
        import yaml

        if _TOOL_SETS_PATH.exists():
            _tool_sets_cache = yaml.safe_load(_TOOL_SETS_PATH.read_text(encoding="utf-8")) or {}
        else:
            _tool_sets_cache = {}
    return _tool_sets_cache


def tool_names_for(specialist: str | None) -> set[str] | None:
    """Nombres de las herramientas que le tocan a un especialista, o None
    si no hay configuracion (entonces se le dan todas, como antes).

    Un especialista que NO aparece en el archivo recibe todas a proposito:
    es lo mismo que hacia el sistema antes de existir este filtro, asi que
    añadir un especialista nuevo no lo deja mudo por olvido. El coste de
    ese olvido son tokens; el de la alternativa seria un agente sin
    herramientas y sin explicacion."""
    cfg = _tool_sets()
    if not cfg:
        return None
    sets = cfg.get("sets") or {}
    por_especialista = cfg.get("especialistas") or {}
    if specialist is None or specialist not in por_especialista:
        return None

    nombres = set(sets.get("core") or [])
    for familia in por_especialista[specialist] or []:
        nombres |= set(sets.get(familia) or [])
    return nombres


def schemas_for(specialist: str | None) -> list[dict]:
    """Los esquemas que se le mandan a ESE especialista.

    Los ~31.000 caracteres (~7.700 tokens) del catalogo completo viajaban
    en cada llamada de todos los especialistas. Un python-specialist
    recibia la descripcion entera de `search_images` y
    `fetch_menu_and_reviews_from_maps` en cada turno."""
    permitidas = tool_names_for(specialist)
    if permitidas is None:
        return TOOL_SCHEMAS
    return [t for t in TOOL_SCHEMAS if t["function"]["name"] in permitidas]


# Nombres coloquiales -> skill real, para que delegate_to_specialist no falle
# cuando el modelo pide "presentaciones"/"word"/"web" en vez del nombre exacto
# ('office-presentation-specialist'...). Es una red de seguridad: si el router
# ya enruta bien, esto casi nunca se usa; cuando un especialista tiene que
# delegar, evita 3 fallos seguidos por adivinar el nombre.
_ALIAS_SKILL = {
    "presentacion": "office-presentation-specialist", "presentaciones": "office-presentation-specialist",
    "powerpoint": "office-presentation-specialist", "power point": "office-presentation-specialist",
    "ppt": "office-presentation-specialist", "pptx": "office-presentation-specialist",
    "diapositivas": "office-presentation-specialist", "deck": "office-presentation-specialist",
    "documento": "office-word-specialist", "documentos": "office-word-specialist",
    "word": "office-word-specialist", "docx": "office-word-specialist", "informe": "office-word-specialist",
    "excel": "office-spreadsheet-specialist", "xlsx": "office-spreadsheet-specialist",
    "hoja de calculo": "office-spreadsheet-specialist", "planilla": "office-spreadsheet-specialist",
    "correo": "office-email-specialist", "email": "office-email-specialist", "mail": "office-email-specialist",
    "web": "web-builder-specialist", "pagina web": "web-builder-specialist", "pagina": "web-builder-specialist",
    "sitio web": "web-builder-specialist", "landing": "web-builder-specialist",
    "diseño web": "web-designer-specialist", "diseñador web": "web-designer-specialist",
}


def resolver_skill(nombre: str, disponibles) -> str | None:
    """Devuelve el nombre de skill REAL para lo que pidio el modelo, o None si
    no hay forma de mapearlo. Acepta el nombre exacto, 'python'->
    'python-specialist', y terminos coloquiales ('presentaciones'->
    office-presentation-specialist)."""
    if not nombre:
        return None
    disp = set(disponibles)
    if nombre in disp:
        return nombre
    n = nombre.strip().lower()
    if n in disp:
        return n
    if f"{n}-specialist" in disp:      # 'python' -> 'python-specialist'
        return f"{n}-specialist"
    if n in _ALIAS_SKILL and _ALIAS_SKILL[n] in disp:
        return _ALIAS_SKILL[n]
    for termino, skill in _ALIAS_SKILL.items():   # 'una presentacion' contiene 'presentacion'
        if termino in n and skill in disp:
            return skill
    return None


def _bloqueantes_de_render(datos: dict) -> list[str]:
    """Extrae del dict de render_check los problemas DETERMINISTAS (medidos por
    el navegador real, no opinados por un modelo): errores de consola/JS, red
    rota, overflow horizontal, render sin texto. Estos son los que SI deciden
    si la web esta lista - a diferencia de la critica visual, que es una señal
    de un modelo de vision ~75% y no debe bloquear la entrega."""
    problemas: list[str] = []
    for pasada in ("escritorio", "movil"):
        r = datos.get(pasada) or {}
        if r.get("navigation_error"):
            problemas.append(f"[{pasada}] Error de navegacion: {r['navigation_error']}")
        for m in (r.get("console_errors") or []):
            problemas.append(f"[{pasada}] Error de consola: {m.get('text', m) if isinstance(m, dict) else m}")
        for e in (r.get("page_errors") or []):
            problemas.append(f"[{pasada}] Error de JavaScript: {e}")
        for fr in (r.get("failed_requests") or []):
            problemas.append(f"[{pasada}] Peticion fallida: {fr.get('url', fr) if isinstance(fr, dict) else fr}")
        if r.get("has_horizontal_overflow"):
            problemas.append(f"[{pasada}] Scroll horizontal: algo se sale del ancho del viewport")
        if r.get("render_sin_texto"):
            problemas.append(f"[{pasada}] La pagina renderiza sin texto visible (probablemente sale en blanco)")
        for c in (r.get("contraste_problemas") or []):
            if isinstance(c, dict):
                problemas.append(
                    f"[{pasada}] Contraste WCAG insuficiente ({c.get('ratio')}:1, minimo "
                    f"{c.get('minimo')}:1): texto '{c.get('texto')}' en {c.get('color')} sobre "
                    f"fondo {c.get('fondo')}"
                )
            else:
                problemas.append(f"[{pasada}] Contraste WCAG insuficiente: {c}")
        for im in (r.get("imagenes_problemas") or []):
            if isinstance(im, dict):
                problemas.append(f"[{pasada}] Imagen {im.get('problema')}: {im.get('src')}")
            else:
                problemas.append(f"[{pasada}] Imagen con problema: {im}")
        mm = r.get("menu_movil") or {}
        if mm.get("encontrado") and mm.get("funciona") is False:
            problemas.append(
                f"[{pasada}] El menu movil no abre al pulsarlo (usuario de movil sin poder "
                f"navegar): {mm.get('detalle')}"
            )
    return problemas


class ToolExecutor:
    def __init__(
        self,
        workspace_root: Path,
        auto_yes: bool = False,
        client: object | None = None,
        delegation_depth: int = 0,
        skill_name: str | None = None,
    ):
        self.root = workspace_root
        # Cual es el skill activo: lo necesita load_skill_section para saber
        # de que documento sacar la seccion. None = no se sabe (ej. un
        # executor creado suelto en un test), y entonces la tool lo dice en
        # vez de adivinar un skill al azar.
        self.skill_name = skill_name
        # Si la sesion corre con --yes (desatendida) no hay nadie del otro
        # lado para responder ask_user - bloquear en input() ahi se
        # colgaria para siempre. Se documenta en la description de la
        # tool para que el modelo no dependa de preguntar en ese modo.
        self.auto_yes = auto_yes
        # Distinto de auto_yes: esto es "hay un usuario presente, pero
        # eligio 'aceptar todas' en el dialogo de confirmacion y no quiere
        # que se le pregunte de nuevo en el resto de la sesion" (ver
        # ui.confirm_action). A diferencia de auto_yes, esto NO debe
        # desactivar ask_user - el usuario sigue ahi para responder
        # preguntas reales, solo dejo de confirmar escrituras rutinarias.
        self.session_accept_all = False
        # Guardar FUERA del workspace (carpetas conocidas / rutas absolutas
        # que pidio el usuario). La CONFIRMACION la hace agent_loop en el
        # HILO PRINCIPAL (prompt_toolkit no dibuja bien desde el hilo de
        # fondo de animate_dispatch: parpadeaba y no llegaba a preguntar).
        # La tool aqui solo ENFORCEA: escribe fuera unicamente si esa
        # escritura fue aprobada antes.
        #  - _escritura_externa_aprobada: el destino puntual que agent_loop
        #    acaba de aprobar, justo antes de despachar esta tool.
        #  - _carpetas_externas_ok: carpetas para las que el usuario eligio
        #    "guardar todo aqui sin volver a preguntar" (una web son muchos
        #    archivos; preguntarle por cada uno seria inusable).
        self._escritura_externa_aprobada: Path | None = None
        self._carpetas_externas_ok: set[Path] = set()
        # Manifiesto de archivos que el agente ha creado/editado en esta
        # tarea: {ruta_absoluta: "creado"|"editado"}, en orden. agent_loop lo
        # inyecta al final del contexto cada turno para que el modelo NUNCA
        # pierda de vista donde estan sus archivos (la poda dispersa los
        # resultados de escritura y el modelo se inventaba otra carpeta).
        self.archivos_tocados: dict[str, str] = {}
        self.plan_actual: list[dict] = []   # el plan visible del especialista
        # Necesarios solo para delegate_to_specialist - client es el
        # GroqClient ya autenticado (evitamos el tipo concreto aca para
        # no importar agent_loop/groq_client a nivel de modulo y crear
        # un ciclo de imports con agent_loop, que ya importa de tools).
        self.client = client
        self.delegation_depth = delegation_depth

    def delegate_to_specialist(self, specialist: str, subtask: str, context_summary: str = "") -> str:
        if self.delegation_depth >= _MAX_DELEGATION_DEPTH:
            return (
                "ERROR: se alcanzo el limite de delegacion (ya se delego una vez en esta "
                "tarea) - resuélvela tú mismo con tus propias herramientas, o déjale al "
                "usuario pedir la parte restante como una tarea aparte."
            )
        if self.client is None:
            return "ERROR: delegacion no disponible (no hay cliente de modelo configurado)."

        # Imports locales: agent_loop/auto_router/skills_loader/ui importan
        # de tools.py (TOOL_SCHEMAS, ToolExecutor) - importarlos a nivel de
        # modulo aca crearia un ciclo. Solo se resuelven al llamar la tool.
        from groq_agent import ui
        from groq_agent.agent_loop import run_agent
        from groq_agent.auto_router import model_for_specialist
        from groq_agent.skills_loader import build_system_prompt, list_available_skills

        resuelto = resolver_skill(specialist, list_available_skills())
        if resuelto is None:
            return (
                f"ERROR: '{specialist}' no es un skill valido. Usa el nombre exacto "
                "(ej. 'office-presentation-specialist', 'office-word-specialist', "
                "'web-builder-specialist'). Ver --list-skills."
            )
        specialist = resuelto

        try:
            system_prompt = build_system_prompt(specialist)
        except FileNotFoundError as exc:
            return f"ERROR: {exc}"

        model = model_for_specialist(specialist)
        ui.print_note(f"delegando a {specialist} (modelo {model}): {subtask[:100]}")

        sub_executor = ToolExecutor(
            self.root,
            auto_yes=self.auto_yes,
            client=self.client,
            delegation_depth=self.delegation_depth + 1,
        )
        # Si el usuario ya eligio "aceptar todas" para el especialista que
        # esta delegando, que valga tambien para el especialista delegado -
        # no tendria sentido volver a preguntar aca. Idem las carpetas
        # externas ya aprobadas.
        sub_executor.session_accept_all = self.session_accept_all
        sub_executor._carpetas_externas_ok = self._carpetas_externas_ok
        user_task = f"{subtask}\n\nContexto relevante de la conversacion previa:\n{context_summary}" if context_summary else subtask

        result, _messages = run_agent(
            client=self.client,
            executor=sub_executor,
            system_prompt=system_prompt,
            user_task=user_task,
            model=model,
            max_turns=_MAX_DELEGATION_TURNS,
        )
        ui.print_note(f"{specialist} termino su parte delegada")
        return result

    def ask_user(self, question: str, options: list[str] | None = None) -> str:
        if self.auto_yes:
            return (
                "(el usuario no esta disponible para responder - la sesion corre con --yes. "
                "Sigue con tu mejor criterio razonable y déjalo explicito en tu respuesta final.)"
            )
        from groq_agent.ui import ask_user_ui

        return ask_user_ui(question, options)

    # Lineas que se leen de una vez cuando no se pide una ventana concreta.
    # Sin este limite un archivo de 3.000 lineas entraba entero en el
    # contexto y despues se reenviaba en cada vuelta: casi siempre para que
    # el modelo mirase una funcion de veinte lineas.
    LINEAS_POR_DEFECTO = 400

    def read_file(self, path: str, desde: int = 1, lineas: int | None = None) -> str:
        """Lee una VENTANA del archivo, no el archivo entero.

        `desde` es la primera linea (empezando en 1) y `lineas` cuantas
        leer. El aviso final dice cuanto queda y como pedirlo, para que
        acotar no se convierta en un callejon sin salida."""
        try:
            target = self._resolver_lectura(path)
        except PathEscapeError as exc:
            return f"ERROR: {exc}"
        if is_hidden_path(target) or is_sensitive_file(target):
            return f"ERROR: '{path}' no es accesible (fuera del alcance del workspace: cache/entorno/secreto)."
        if not target.exists():
            return f"ERROR: '{path}' no existe."

        completo = target.read_text(encoding="utf-8")
        todas = completo.splitlines()
        total = len(todas)
        inicio = max(1, int(desde or 1)) - 1
        cuantas = int(lineas) if lineas else self.LINEAS_POR_DEFECTO
        trozo = todas[inicio:inicio + cuantas]

        fin = inicio + len(trozo)
        if inicio == 0 and fin >= total:
            # Cabe entero: se devuelve TAL CUAL, sin cabecera ni avisos y
            # sin pasar por splitlines - que se come el salto de linea final
            # y cambiaria el contenido de lo que el modelo cree estar leyendo.
            return completo
        texto = "\n".join(trozo)
        return (
            f"[lineas {inicio + 1}-{fin} de {total}]\n{texto}"
            + (f"\n\n… [quedan {total - fin} lineas. Para seguir: "
               f"read_file('{path}', desde={fin + 1})]" if fin < total else "")
        )

    def carpeta_externa_aprobada(self, destino: Path) -> bool:
        """True si `destino` cae dentro de una carpeta para la que el
        usuario ya dijo 'guardar todo aqui sin volver a preguntar'."""
        try:
            d = destino.resolve()
        except OSError:
            d = destino
        for c in self._carpetas_externas_ok:
            if d == c or c in d.parents:
                return True
        return False

    def aprobar_carpeta_externa(self, carpeta: Path) -> None:
        """Recuerda una carpeta externa aprobada para el resto de la sesion
        (la llama agent_loop cuando el usuario elige 'y todo en esta
        carpeta')."""
        try:
            self._carpetas_externas_ok.add(carpeta.resolve())
        except OSError:
            self._carpetas_externas_ok.add(carpeta)

    def _resolver_lectura(self, path: str) -> Path:
        """Resuelve una ruta de LECTURA/verificacion. Permite el workspace
        Y las carpetas externas que el usuario aprobo como area de trabajo:
        una web guardada en Descargas hay que poder RE-LEERLA, renderizarla
        y verificarla ahi, no solo escribirla. Cualquier otra cosa fuera del
        workspace se rechaza (el confinamiento de lectura sigue en pie para
        todo lo que el usuario no aprobo)."""
        destino, fuera = resolve_write_destination(self.root, path)
        if not fuera:
            return destino
        if self.carpeta_externa_aprobada(destino):
            return destino
        raise PathEscapeError(
            f"'{path}' esta fuera del workspace y no es una carpeta que hayas aprobado "
            "para trabajar."
        )

    def _destino_escritura(self, path: str):
        """Resuelve una ruta de ESCRITURA permitiendo carpetas conocidas
        (escritorio/descargas/documentos) y rutas absolutas que pidio el
        usuario, ademas del workspace por defecto.

        NO hace UI: la confirmacion de 'guardar fuera' la hace agent_loop en
        el hilo principal (aqui correria en el hilo de fondo de
        animate_dispatch y parpadearia). Esta funcion solo ENFORCEA: si el
        destino cae fuera del workspace, exige que la escritura ya haya sido
        aprobada (destino puntual o carpeta), y bloquea en duro las rutas de
        sistema pase lo que pase.

        Devuelve un Path listo para escribir, o un str de ERROR."""
        try:
            destino, fuera = resolve_write_destination(self.root, path)
        except Exception as exc:  # noqa: BLE001 - PathEscapeError u otra: se reporta al modelo
            return f"ERROR: {exc}"
        # Nunca escribir en secretos/entorno/control de versiones, ni dentro
        # ni fuera del workspace.
        if is_hidden_path(destino) or is_sensitive_file(destino):
            return f"ERROR: '{path}' no es escribible (cache/entorno/secreto/control de versiones)."
        if fuera:
            # BLOQUEO DURO de rutas del sistema (C:\Windows, Program Files,
            # runtime de Python...): ni la confirmacion lo levanta.
            if is_system_path(destino):
                return (
                    f"ERROR: '{destino}' es una carpeta del SISTEMA (Windows/Program Files/"
                    "runtime de Python). Esta IA nunca escribe ahi, ni con confirmacion. "
                    "Elige una carpeta tuya (Escritorio, Descargas, Documentos, o una ruta "
                    "de tu proyecto)."
                )
            aprobado = (
                self._escritura_externa_aprobada is not None
                and self._escritura_externa_aprobada == destino
            ) or self.carpeta_externa_aprobada(destino)
            if not aprobado:
                if self.auto_yes:
                    return (
                        "ERROR: guardar FUERA del workspace pide confirmacion, y la sesion corre "
                        "desatendida (--yes). Relanza con `--workspace` apuntando a esa carpeta, "
                        "o quita --yes."
                    )
                return (
                    "ERROR: escribir fuera del workspace necesita la confirmacion del usuario y "
                    "no se dio. No se escribio nada."
                )
        destino.parent.mkdir(parents=True, exist_ok=True)
        return destino

    def write_file(self, path: str, content: str) -> str:
        destino = self._destino_escritura(path)
        if isinstance(destino, str):  # ERROR
            return destino
        destino.write_text(content, encoding="utf-8")
        self.archivos_tocados[str(destino)] = "creado"
        return f"Escrito {destino} ({len(content)} caracteres)."

    def edit_file(self, path: str, old_text: str, new_text: str) -> str:
        # _resolver_lectura (no resolve_within_root): editar un archivo que
        # se creo en una carpeta APROBADA (una web en Descargas) tiene que
        # ir a esa carpeta, no a workspace\descargas. La carpeta ya se
        # aprobo al crear el archivo, asi que editar ahi no re-pregunta.
        try:
            target = self._resolver_lectura(path)
        except PathEscapeError as exc:
            return f"ERROR: {exc}"
        if is_hidden_path(target) or is_sensitive_file(target) or is_system_path(target):
            return f"ERROR: '{path}' no es editable (cache/entorno/secreto/sistema)."
        if not target.exists():
            return f"ERROR: '{path}' no existe."
        original = target.read_text(encoding="utf-8")
        count = original.count(old_text)
        if count == 0:
            # Sin esto, un old_text que no matchea (tipico: el modelo
            # "recuerda" el contenido de una edicion previa en vez de
            # leerlo de nuevo) fuerza una llamada aparte a read_file solo
            # para ver que hay realmente - un turno completo perdido. Con
            # el fragmento real mas parecido ya en ESTE mismo error, el
            # modelo puede corregir espacios/indentacion en el intento
            # siguiente sin gastar ese turno extra (visto en produccion:
            # 2 edit_file fallidos + 1 read_file de recuperacion por un
            # solo desajuste de old_text).
            return f"ERROR: old_text no se encontro en el archivo. {_fragmento_mas_parecido(original, old_text)}"
        if count > 1:
            return f"ERROR: old_text aparece {count} veces - hazla unica antes de editar."
        target.write_text(original.replace(old_text, new_text, 1), encoding="utf-8")
        self.archivos_tocados[str(target)] = "editado"
        return f"Editado {target}."

    def list_dir(self, path: str = ".") -> str:
        try:
            target = self._resolver_lectura(path)
        except PathEscapeError as exc:
            return f"ERROR: {exc}"
        if not target.exists():
            return f"ERROR: '{path}' no existe."
        entries = sorted(
            p.name + ("/" if p.is_dir() else "")
            for p in target.iterdir()
            if not is_hidden_path(p) and not is_sensitive_file(p)
        )
        return "\n".join(entries) if entries else "(vacio)"

    def glob_search(self, pattern: str) -> str:
        matches = sorted(
            str(p.relative_to(self.root))
            for p in self.root.glob(pattern)
            if not is_hidden_path(p) and not is_sensitive_file(p)
        )
        return "\n".join(matches) if matches else "(sin resultados)"

    def grep_search(self, pattern: str, glob: str = "**/*") -> str:
        regex = re.compile(pattern)
        hits: list[str] = []
        for path in self.root.glob(glob):
            if not path.is_file() or not fnmatch.fnmatch(path.name, "*"):
                continue
            if is_hidden_path(path) or is_sensitive_file(path):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, PermissionError):
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    hits.append(f"{path.relative_to(self.root)}:{line_no}: {line.strip()[:200]}")
        return "\n".join(hits[:200]) if hits else "(sin resultados)"

    def search_hard_cases(self, query: str, domain: str | None = None) -> str:
        results = _search_hard_cases(query, domain=domain or "", limit=3)
        if not results:
            return "(sin resultados relevantes en la base de casos dificiles)"
        lines = []
        for r in results:
            lines.append(
                f"[{r['id']}] {r['problem_description']}\n  Enfoque correcto: {r['correct_approach']}"
            )
        return "\n\n".join(lines)

    def list_reference_components(self, category: str = "") -> str:
        items = _list_reference_components(category)
        if not items:
            return f"(sin resultados para categoria '{category}')"
        return "\n".join(f"{item['path']} — {item['description']}" for item in items)

    def read_reference_component(self, path: str) -> str:
        try:
            return _read_reference_component(path)
        except ReferenceNotFoundError as exc:
            return f"ERROR: {exc}"

    def run_sql(self, query: str, schema: str = "", seed: str = "") -> str:
        return str(_run_sql(query, schema, seed))

    def eval_xlsx_formulas(self, sheets: list[dict]) -> str:
        return str(_eval_workbook(sheets))

    def compile_run_cpp(self, code: str, stdin: str = "", timeout_s: int = 5) -> str:
        return str(_compile_run_cpp(code, stdin, timeout_s))

    def run_check(self, tool: str, path: str = ".") -> str:
        # path es opcional (default: todo el workspace) - se acepta para
        # no romper si el modelo decide pasarlo (algunos lo hacen por
        # simetria con read_file/write_file), pero pytest siempre
        # descubre tests recursivamente desde el workspace de cualquier
        # forma, asi que ahi se ignora. Un string vacio (algunos modelos
        # mandan "" en vez de omitir el argumento) se trata igual que
        # "no especificado".
        path = path or "."
        commands = {
            "ruff": [sys.executable, "-m", "ruff", "check", path],
            # --ignore-missing-imports: que una libreria de terceros no este
            # instalada NO es un defecto del codigo generado, y el modelo no
            # puede arreglarlo escribiendo mas codigo. Sin esto, "Cannot find
            # implementation or library stub for module named matplotlib"
            # entraba en bucle infinito: el modelo escribia requirements.txt,
            # mypy volvia a fallar igual, y vuelta a empezar hasta que el
            # usuario pulsaba ESC. Para instalar de verdad esta
            # `install_dependency`.
            "mypy": [sys.executable, "-m", "mypy", "--ignore-missing-imports", path],
            "pytest": [sys.executable, "-m", "pytest", "-q"],
        }
        if tool not in commands:
            return f"ERROR: herramienta '{tool}' no soportada (usa ruff, mypy o pytest)."
        return _run_subprocess(commands[tool], cwd=self.root)

    # Un nombre de paquete y nada mas: letras, digitos, guiones, puntos y
    # los extras entre corchetes. Ni espacios, ni `;`, ni `&&`, ni rutas, ni
    # URLs - eso es lo que convertiria un instalador en la shell arbitraria
    # que este sistema evita a proposito (ver la cabecera del modulo).
    _PAQUETE_VALIDO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,._-]+\])?$")
    MAX_PAQUETES_POR_LLAMADA = 5

    def install_dependency(self, packages: list[str] | str) -> str:
        """Instala dependencias que le faltan al codigo generado.

        Existe porque sin ella hay errores que el modelo NO puede arreglar
        escribiendo codigo: "no encuentro matplotlib" no se resuelve
        escribiendo un requirements.txt, y el intento entraba en bucle.

        Se valida el nombre y se pasa a pip como argumentos sueltos (nunca
        por shell), asi que no hay forma de colar un comando: un `;` o un
        espacio hacen que el nombre no pase el filtro."""
        if isinstance(packages, str):
            packages = [packages]
        packages = [p.strip() for p in packages if str(p).strip()]
        if not packages:
            return "ERROR: no se indico ningun paquete."
        if len(packages) > self.MAX_PAQUETES_POR_LLAMADA:
            return (
                f"ERROR: demasiados paquetes de una vez ({len(packages)}). "
                f"Instala los que de verdad importa el codigo, maximo "
                f"{self.MAX_PAQUETES_POR_LLAMADA}."
            )
        malos = [p for p in packages if not self._PAQUETE_VALIDO.match(p)]
        if malos:
            return (
                f"ERROR: nombre de paquete no valido: {malos}. Solo el nombre "
                "(ej. 'matplotlib', 'numpy', 'pandas[excel]'), sin versiones "
                "con espacios, sin rutas y sin URLs."
            )
        return _run_subprocess(
            [sys.executable, "-m", "pip", "install", *packages], cwd=self.root
        )

    def generate_docx(self, path: str, sections: list[dict]) -> str:
        target = self._destino_escritura(path)
        if isinstance(target, str):  # ERROR
            return target
        create_docx(sections, target)
        self.archivos_tocados[str(target)] = "creado"
        result = check_docx(target)
        return f"Generado {target}. Chequeo de estructura: {result}"

    def generate_xlsx(self, path: str, sheets: list[dict]) -> str:
        target = self._destino_escritura(path)
        if isinstance(target, str):  # ERROR
            return target
        create_workbook(sheets, target)
        self.archivos_tocados[str(target)] = "creado"
        result = check_workbook(target)
        return f"Generado {target}. Chequeo de estructura: {result}"

    def generate_pptx(self, path: str, slides: list[dict], theme: str = "medianoche") -> str:
        target = self._destino_escritura(path)
        if isinstance(target, str):  # ERROR
            return target
        create_presentation(slides, target, tema=theme)
        self.archivos_tocados[str(target)] = "creado"
        result = check_presentation(target)
        return f"Generado {target} (tema '{theme}'). Chequeo de estructura: {result}"

    def lint_email(self, subject: str, body: str) -> str:
        return str(_lint_email(subject, body))

    def check_contrast(self, color_texto: str, color_fondo: str) -> str:
        return _check_contrast(color_texto, color_fondo)

    def plan(self, pasos: list[dict]) -> str:
        # El plan se DIBUJA en ui.animate_dispatch (caso especial de 'plan'),
        # aqui solo se guarda y se resume: el resultado que ve el modelo es el
        # conteo, no el dibujo. Guardado por si algo quiere consultarlo.
        self.plan_actual = list(pasos or [])
        hechos = sum(
            1 for p in self.plan_actual
            if str((p or {}).get("estado", "")).strip().lower() in ("hecho", "done", "completado", "ok")
        )
        return f"Plan actualizado: {hechos}/{len(self.plan_actual)} pasos hechos."

    def web_fetch(self, url: str) -> str:
        return str(_fetch_page(url))

    def extract_business_info(self, url: str) -> str:
        return str(_extract_business_info(url))

    def fetch_business_from_maps(self, consulta: str) -> str:
        return str(_fetch_business_from_maps(consulta))

    def fetch_menu_and_reviews_from_maps(self, consulta: str) -> str:
        return str(_fetch_menu_and_reviews_from_maps(consulta))

    def fetch_menu_photos_from_maps(self, consulta: str, max_fotos: int = 6) -> str:
        return str(_fetch_gallery_photos_from_maps(consulta, max_fotos=max_fotos))

    def search_images(
        self,
        query: str | None = None,
        queries: list[str] | None = None,
        source: str = "pexels",
        count: int = 5,
        rubro_negocio: str | None = None,
    ) -> str:
        return str(_search_images(query, queries, source, count, rubro_negocio))

    def search_web_image(self, query: str | None = None, queries: list[str] | None = None, count: int = 3) -> str:
        return str(_search_web_image(query, queries, count))

    def analyze_image(self, url_or_path: str) -> str:
        # Si no es una URL, se trata como ruta del workspace (o de una
        # carpeta externa aprobada - una foto del sitio que se esta armando
        # en Descargas).
        target = url_or_path
        if not url_or_path.startswith(("http://", "https://")):
            try:
                target = str(self._resolver_lectura(url_or_path))
            except PathEscapeError as exc:
                return f"ERROR: {exc}"
        return str(_analyze_image(target))

    def classify_image_content(self, url_or_path: str, rubro_negocio: str) -> str:
        target = url_or_path
        if not url_or_path.startswith(("http://", "https://")):
            target = str(resolve_within_root(self.root, url_or_path))
        return str(_classify_image_content(target, rubro_negocio))

    def extract_menu_text(self, url_or_path: str) -> str:
        target = url_or_path
        if not url_or_path.startswith(("http://", "https://")):
            target = str(resolve_within_root(self.root, url_or_path))
        return str(_extract_menu_text(target))

    def search_web(self, query: str, count: int = 5, engine: str = "duckduckgo") -> str:
        return str(_search_web(query, count, engine))

    def deep_research(
        self,
        pregunta: str,
        profundidad: int = 2,
        max_paginas: int = 6,
        urls_semilla: list[str] | None = None,
        consultas: list[str] | None = None,
        terminos_clave: list[str] | None = None,
    ) -> str:
        return str(_deep_research(
            pregunta, profundidad, max_paginas, urls_semilla,
            consultas=consultas, terminos_clave=terminos_clave,
        ))

    def check_links(self, urls: list[str]) -> str:
        return str(_summarize_links(_check_links(urls)))

    # --- APIs de referencia (ejemplos de codigo, paquetes, vulns, directorios) ---

    def code_examples(self, consulta: str, lenguaje: str = "", limite: int = 3) -> str:
        return str(_buscar_ejemplos(consulta, lenguaje, limite))

    def search_code(self, consulta: str, lenguaje: str = "", limite: int = 5) -> str:
        return str(_buscar_codigo(consulta, lenguaje, limite))

    def package_info(self, ecosystem: str, name: str) -> str:
        return str(_info_paquete(ecosystem, name))

    def vuln_check(self, ecosystem: str, name: str, version: str = "") -> str:
        return str(_consultar_vulnerabilidades(ecosystem, name, version))

    def wikipedia(self, consulta: str, idioma: str = "es") -> str:
        return str(_consultar_wikipedia(consulta, idioma))

    def find_api(self, consulta: str, limite: int = 6) -> str:
        return str(_buscar_api(consulta, limite))

    def sample_data(self, tipo: str = "products", cantidad: int = 5) -> str:
        return str(_datos_de_ejemplo(tipo, cantidad))

    # --- Diseño web (iconos, tipografias, paletas, assets, UIverse) ---

    def search_icons(self, query: str, limite: int = 12) -> str:
        return str(_search_icons(query, limite))

    def search_fonts(self, query: str = "", categoria: str = "", limite: int = 8) -> str:
        return str(_search_fonts(query, categoria, limite))

    def color_palette(self, base: str, modo: str = "analogic", cantidad: int = 5) -> str:
        return str(_color_palette(base, modo, cantidad))

    def design_assets(self, kind: str, seed: str = "", brand: str = "", texto: str = "",
                      width: int = 400, height: int = 300, estilo: str = "") -> str:
        return str(_design_assets(kind, seed, brand, texto, width, height, estilo))

    def uiverse(self, categoria: str, limite: int = 4) -> str:
        return str(_uiverse(categoria, limite))

    def lint_web_page(self, path: str) -> str:
        try:
            target = self._resolver_lectura(path)
        except PathEscapeError as exc:
            return f"ERROR: {exc}"
        if not target.exists():
            return f"ERROR: '{path}' no existe."
        return str(_lint_html(target.read_text(encoding="utf-8")))

    def render_check(self, target: str, screenshot_path: str) -> str:
        resolved_target = target
        try:
            if not target.startswith(("http://", "https://")):
                resolved_target = str(self._resolver_lectura(target))
            # screenshot como Path (no str): _render_check usa su .parent.
            resolved_screenshot = self._resolver_lectura(screenshot_path)
        except PathEscapeError as exc:
            return f"ERROR: {exc}"
        return str(_render_check(resolved_target, resolved_screenshot))

    # Cuantas capturas de cada pasada se mandan al modelo de vision. La
    # primera es el hero, que es donde estan casi todos los defectos que
    # importan; la segunda pilla el primer bloque de contenido. Mas que eso
    # multiplica el coste del modelo de vision sin encontrar mucho mas.
    CAPTURAS_A_CRITICAR_POR_PASADA = 2

    def verificar_web(self, path: str, screenshot_path: str = "verificacion.png") -> str:
        """Renderiza, analiza y critica una pagina en UNA sola llamada.

        Existe por una razon medida, no por comodidad: el skill pide desde
        siempre que `render_check`, `lint_web_page` y `critique_screenshot`
        se llamen JUNTAS en la misma respuesta, y en las sesiones reales el
        modelo hacia UNA llamada por turno, 7 de 7 veces. Cada turno de mas
        reenvia la conversacion entera, asi que pedirlo en prosa costaba
        tres contextos completos. Aqui ya no puede no agruparlas.

        Elige sola que capturas critica (ver
        CAPTURAS_A_CRITICAR_POR_PASADA), que es la otra decision que el
        modelo tenia que acordarse de tomar bien."""
        import ast

        informe: dict = {"pagina": path}

        crudo_render = self.render_check(path, screenshot_path)
        crudo_lint = self.lint_web_page(path)
        informe["render"] = crudo_render
        informe["lint"] = crudo_lint

        # render_check devuelve su dict ya pasado por str(): se vuelve a
        # leer con literal_eval, que no ejecuta nada. Si el formato cambia o
        # el render fallo, se sigue igual con el resto del informe - una
        # verificacion incompleta es mucho mejor que ninguna.
        capturas: list[dict] = []
        bloqueantes: list[str] = []
        try:
            datos = ast.literal_eval(crudo_render)
        except (ValueError, SyntaxError):
            datos = None
        if isinstance(datos, dict):
            bloqueantes.extend(_bloqueantes_de_render(datos))
            for pasada in ("escritorio", "movil"):
                for entrada in (datos.get(pasada) or {}).get("capturas", [])[
                    : self.CAPTURAS_A_CRITICAR_POR_PASADA
                ]:
                    capturas.append({
                        "path_captura": entrada.get("ruta"),
                        "secciones_visibles": entrada.get("secciones_visibles"),
                    })
        else:
            # El render no devolvio un informe (no se pudo abrir el navegador,
            # etc.): sin poder ejecutar la pagina no se puede dar por lista.
            bloqueantes.append(
                f"No se pudo renderizar la pagina para verificarla: {str(crudo_render)[:200]}"
            )

        # lint_web_page devuelve str({'passed':.., 'issues':[..]}); sus issues
        # (falta viewport, sin <h1>, etc.) son estructurales y deterministas.
        try:
            datos_lint = ast.literal_eval(crudo_lint)
        except (ValueError, SyntaxError):
            datos_lint = None
        if isinstance(datos_lint, dict) and not datos_lint.get("passed", True):
            for issue in datos_lint.get("issues", []):
                bloqueantes.append(f"Estructura: {issue}")

        # Veredicto DETERMINISTA: lo unico que decide si la web esta lista.
        # NO depende de la critica visual (asesora), asi un problema inventado
        # por el modelo de vision no puede impedir la entrega ni meter al
        # especialista en un bucle de "arreglar" cosas que no existen.
        informe["bloqueantes"] = bloqueantes
        informe["entregable"] = not bloqueantes

        if capturas:
            informe["critica"] = self.critique_screenshot(capturas=capturas)
        else:
            informe["critica"] = (
                "No se pudo criticar visualmente: el render no devolvio capturas. "
                "Revisa el campo 'render' para ver por que."
            )
        informe["nota"] = (
            "'entregable'/'bloqueantes' son deterministas (los mide el navegador real) y son "
            "los que deciden si la web esta lista. 'critica' es una SEÑAL de un modelo de vision "
            "(~75% de acierto): es ASESORA, NO bloquea la entrega. Aplica de la critica solo lo "
            "que puedas CONFIRMAR en tu propio codigo; si 'entregable' es true, entrega - no "
            "vuelvas a verificar solo para perseguir una critica limpia (nunca responde igual "
            "dos veces: es un bucle sin fin)."
        )
        return str(informe)

    def critique_screenshot(
        self,
        path_captura: str | None = None,
        secciones_visibles: list[str] | None = None,
        capturas: list[dict] | None = None,
    ) -> str:
        # capturas (lote) vs path_captura suelto - mismo patron que
        # search_images(query vs queries): analizar TODAS las capturas de
        # una tanda de render_check en una sola llamada en vez de una por
        # captura. Cada captura se BORRA sola aca mismo apenas se analiza
        # (exito o error, decision explicita: una captura de render_check
        # es descartable, no vale la pena conservarla solo por si el
        # analisis fallo) - asi no hace falta un delete_files aparte
        # despues para limpiarlas.
        items = capturas or ([{"path_captura": path_captura, "secciones_visibles": secciones_visibles}] if path_captura else [])
        if not items:
            return "ERROR: falta 'path_captura' o 'capturas'."
        resultados = []
        for item in items:
            p = item.get("path_captura")
            secciones = item.get("secciones_visibles")
            target = None
            try:
                target = resolve_within_root(self.root, p)
                resultado = dict(_critique_screenshot(str(target), secciones))
            except Exception as exc:  # noqa: BLE001 - una captura que falla no debe tirar el resto del lote
                resultado = {"ok": False, "error": str(exc)}
            resultado["path_captura"] = p
            resultados.append(resultado)
            if target is not None:
                try:
                    if target.exists() and not target.is_dir():
                        target.unlink()
                except Exception:  # noqa: BLE001 - limpieza best-effort, no debe tirar el analisis ya obtenido
                    pass
        return str(resultados if capturas else resultados[0])

    def delete_files(self, paths: list[str]) -> str:
        borrados: list[str] = []
        no_encontrados: list[str] = []
        saltados: list[str] = []
        for path in paths:
            try:
                target = self._resolver_lectura(path)
            except Exception as exc:  # noqa: BLE001 - reportar y seguir con el resto de la lista
                saltados.append(f"{path} (ruta invalida o fuera de alcance: {exc})")
                continue
            if is_hidden_path(target) or is_sensitive_file(target) or is_system_path(target):
                saltados.append(f"{path} (fuera del alcance: cache/entorno/secreto/sistema)")
                continue
            if not target.exists():
                no_encontrados.append(path)
                continue
            if target.is_dir():
                saltados.append(f"{path} (es una carpeta, no un archivo - no se borra)")
                continue
            target.unlink()
            borrados.append(path)

        partes = [f"Borrados: {len(borrados)}"]
        if no_encontrados:
            partes.append(f"ya no existian ({len(no_encontrados)}): {no_encontrados}")
        if saltados:
            partes.append(f"saltados: {saltados}")
        return " | ".join(partes)

    def query_code_graph(self, query: str, max_depth: int = 2) -> str:
        return str(_query_code_graph(query, max_depth))

    def shortest_path_in_graph(self, origen: str, destino: str) -> str:
        return str(_shortest_path_in_graph(origen, destino))

    # --- goldentokens ---------------------------------------------
    #
    # Puente a los CLIs del usuario (~/.claude/bin). Si no estan
    # instalados, la tool lo dice y el modelo puede caer a read_file - no
    # se rompe nada.

    def peek_file(self, path: str) -> str:
        from app.tools.goldentokens import ejecutar

        destino = resolve_within_root(self.root, path)
        return ejecutar("peek", [str(destino)], cwd=self.root)

    def query_json(self, path: str, expresion: str = "") -> str:
        from app.tools.goldentokens import ejecutar

        destino = resolve_within_root(self.root, path)
        args = [str(destino)]
        if expresion:
            args.append(expresion)
        return ejecutar("jsonq", args, cwd=self.root)

    def load_skill_section(self, section: str) -> str:
        """Sirve una seccion a demanda del skill activo (carga progresiva,
        ver groq_agent/skills_loader y config/skill_sections.yaml)."""
        # Import local a proposito: skills_loader no se necesita para el
        # resto de las tools y a nivel de modulo crearia un ciclo con
        # delegate_to_specialist.
        from groq_agent.skills_loader import get_skill_section

        if not self.skill_name:
            return (
                "ERROR: no hay un skill activo del que cargar secciones. Esta tool solo "
                "sirve dentro de una sesion con especialista."
            )
        return get_skill_section(self.skill_name, section)

    def dispatch(self, name: str, arguments: dict) -> str:
        method = getattr(self, name, None)
        if method is None:
            return f"ERROR: herramienta desconocida '{name}'."
        try:
            resultado = method(**arguments)
        except TypeError as exc:
            return f"ERROR: argumentos invalidos para '{name}': {exc}"
        except Exception as exc:  # noqa: BLE001 - se lo devolvemos al modelo como resultado de la tool call
            return f"ERROR ejecutando '{name}': {exc}"
        return acotar_salida(name, resultado)
