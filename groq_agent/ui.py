"""Capa de presentacion de la terminal: logo, caja de input, colores,
spinners, confirmaciones y renderizado del resultado final. Separada de
agent_loop.py/cli.py/tools.py para que la logica del agente no dependa
de como se ve la terminal - si mañana esto corre headless (tests, CI,
un futuro modo --json), esas otras piezas no cambian.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

if sys.platform == "win32":
    # La consola legacy de Windows usa cp1252 y no soporta bien unicode -
    # el texto del modelo (via OpenRouter) puede traer cualquier caracter y no
    # esta bajo nuestro control. Forzar utf-8 evita un UnicodeEncodeError
    # a mitad de una respuesta.
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import random
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

console = Console(legacy_windows=False)


def _esc(value: str | None) -> str:
    """Escapa corchetes antes de interpolar texto no confiable (tareas del
    usuario, argumentos/resultados de tools, texto del modelo) dentro de un
    f-string pasado a console.print - sin esto, un '[algo]' literal se
    interpreta como marcado de rich y puede desaparecer o romper el render."""
    return escape(value) if value else ""

C_SYSTEM = "cyan"
C_DIM = "grey58"
C_OK = "green3"
C_FAIL = "red3"
C_ACCENT = "bold cyan"
C_ASK = "magenta"

_ARCHIVO_TOOLS = {"write_file", "edit_file", "read_file"}


def _nombre_archivo(path: str | None) -> str:
    if not path:
        return "un archivo"
    return path.replace("\\", "/").rsplit("/", 1)[-1]


# Frase amigable por herramienta - lo que ve el usuario en vez del nombre
# tecnico de la funcion y sus argumentos en crudo (ver Claude Code: "Editing
# styles.css", no "edit_file({...})"). Cada entrada es una funcion
# (arguments: dict) -> str para poder incluir detalles (que archivo, que
# se busca) cuando aporta algo.
_ACCIONES: dict[str, object] = {
    "write_file": lambda a: f"Creando {_nombre_archivo(a.get('path'))}",
    "edit_file": lambda a: f"Editando {_nombre_archivo(a.get('path'))}",
    "read_file": lambda a: f"Leyendo {_nombre_archivo(a.get('path'))}",
    "delete_files": lambda a: f"Borrando {len(a.get('paths') or [])} archivo(s) temporal(es)",
    "search_images": lambda a: "Buscando fotos",
    "search_web_image": lambda a: (
        f"Buscando {len(a['queries'])} imagenes en internet" if a.get("queries")
        else f"Buscando una imagen de {a.get('query') or 'algo especifico'} en internet"
    ),
    "analyze_image": lambda a: "Analizando una foto",
    "classify_image_content": lambda a: "Revisando el contenido de una foto",
    "critique_screenshot": lambda a: (
        f"Revisando {len(a['capturas'])} capturas de la web" if a.get("capturas") else "Revisando una captura de la web"
    ),
    "verificar_web": lambda a: "Verificando la web",
    "render_check": lambda a: "Renderizando y comprobando la web",
    "lint_web_page": lambda a: "Revisando la estructura de la pagina",
    "check_links": lambda a: "Comprobando enlaces",
    "fetch_business_from_maps": lambda a: "Consultando datos del negocio en Maps",
    "fetch_menu_and_reviews_from_maps": lambda a: "Trayendo carta y reseñas de Maps",
    "fetch_menu_photos_from_maps": lambda a: "Buscando fotos del menu en Maps",
    "extract_menu_text": lambda a: "Leyendo el texto de una foto de carta",
    "list_reference_components": lambda a: "Mirando la biblioteca de componentes",
    "read_reference_component": lambda a: "Leyendo un componente de referencia",
    "list_dir": lambda a: "Mirando archivos",
    "glob_search": lambda a: "Buscando archivos",
    "grep_search": lambda a: "Buscando texto en archivos",
    "search_hard_cases": lambda a: "Consultando casos dificiles conocidos",
    "search_web": lambda a: "Buscando en internet",
    "web_fetch": lambda a: "Abriendo una pagina web",
    "extract_business_info": lambda a: "Extrayendo datos del negocio",
    "generate_docx": lambda a: "Generando un documento Word",
    "generate_xlsx": lambda a: "Generando una hoja de calculo",
    "generate_pptx": lambda a: "Generando una presentacion",
    "lint_email": lambda a: "Revisando la estructura del email",
    "run_check": lambda a: "Corriendo comprobaciones sobre el codigo",
    "query_code_graph": lambda a: "Consultando el mapa del codigo",
    "shortest_path_in_graph": lambda a: "Consultando el mapa del codigo",
    "ask_user": lambda a: "Preguntandote algo",
    "delegate_to_specialist": lambda a: f"Pasando parte de la tarea a {a.get('specialist') or 'otro especialista'}",
}


def _accion_amigable(name: str, arguments: dict) -> str:
    fn = _ACCIONES.get(name)
    if fn is None:
        return name.replace("_", " ").capitalize()
    try:
        return fn(arguments)
    except Exception:  # noqa: BLE001 - una frase generica es mejor que romper la UI
        return name.replace("_", " ").capitalize()


# Contraparte en pasado de _ACCIONES - que decir cuando la tool YA
# TERMINO (ej. "Datos del negocio en Maps consultados" en vez de dejar
# "Consultando datos del negocio en Maps..." como si siguiera en curso,
# o peor, tapar la frase con el volcado crudo del resultado). Escrito a
# mano por tool (no derivado automaticamente del gerundio de _ACCIONES)
# porque el español tiene demasiadas conjugaciones irregulares
# (abrir -> abierto, no "abrido") para que valga la pena una
# transformacion generica - ver write_file/edit_file/read_file, que ya
# resuelven esto aparte via _VERBO_ARCHIVO + link clickeable, asi que no
# estan en este dict.
_ACCIONES_LISTO: dict[str, object] = {
    "delete_files": lambda a: f"Borrados {len(a.get('paths') or [])} archivo(s) temporal(es)",
    "search_images": lambda a: "Fotos encontradas",
    "search_web_image": lambda a: "Imagen encontrada",
    "analyze_image": lambda a: "Foto analizada",
    "classify_image_content": lambda a: "Contenido de la foto revisado",
    "critique_screenshot": lambda a: (
        f"{len(a['capturas'])} capturas revisadas" if a.get("capturas") else "Captura revisada"
    ),
    "verificar_web": lambda a: "Web verificada (render, estructura y vista)",
    "render_check": lambda a: "Web renderizada y comprobada",
    "lint_web_page": lambda a: "Estructura de la pagina revisada",
    "check_links": lambda a: "Enlaces comprobados",
    "fetch_business_from_maps": lambda a: "Datos del negocio en Maps consultados",
    "fetch_menu_and_reviews_from_maps": lambda a: "Carta y reseñas de Maps traidas",
    "fetch_menu_photos_from_maps": lambda a: "Fotos del menu en Maps encontradas",
    "extract_menu_text": lambda a: "Texto de la carta leido",
    "list_reference_components": lambda a: "Biblioteca de componentes revisada",
    "read_reference_component": lambda a: "Componente de referencia leido",
    "list_dir": lambda a: "Archivos listados",
    "glob_search": lambda a: "Archivos encontrados",
    "grep_search": lambda a: "Texto encontrado",
    "search_hard_cases": lambda a: "Casos dificiles consultados",
    # Sin frase propia caia al volcado crudo: la seccion del skill (markdown de
    # varios miles de chars, ej. "## 2. Sistema de diseño") se imprimia recortada
    # a 300 chars en la terminal. El modelo SI la recibe (va en el mensaje tool);
    # aca solo se resume que se consulto, sin ensuciar el scrollback.
    "load_skill_section": lambda a: (
        f"Guia del skill consultada: {a['section']}" if a.get("section") else "Guia del skill consultada"
    ),
    "search_web": lambda a: "Busqueda en internet lista",
    "deep_research": lambda a: (
        f"Investigacion lista: {a['pregunta']}" if a.get("pregunta") else "Investigacion en varias fuentes lista"
    ),
    "wikipedia": lambda a: (
        f"Wikipedia consultada: {a['consulta']}" if a.get("consulta") else "Wikipedia consultada"
    ),
    "code_examples": lambda a: "Ejemplos de codigo buscados",
    "search_code": lambda a: "Codigo de repos buscado",
    "package_info": lambda a: (
        f"Paquete consultado: {a['name']}" if a.get("name") else "Datos del paquete consultados"
    ),
    "vuln_check": lambda a: (
        f"Vulnerabilidades consultadas: {a['name']}" if a.get("name") else "Vulnerabilidades del paquete consultadas"
    ),
    "find_api": lambda a: "Directorio de APIs consultado",
    "sample_data": lambda a: "Datos de ejemplo traidos",
    "search_icons": lambda a: "Iconos buscados",
    "search_fonts": lambda a: "Tipografias buscadas",
    "color_palette": lambda a: "Paleta de color generada",
    "design_assets": lambda a: "Recursos de diseño traidos",
    "uiverse": lambda a: "Componentes UIverse traidos",
    "web_fetch": lambda a: "Pagina web abierta",
    "extract_business_info": lambda a: "Datos del negocio extraidos",
    "generate_docx": lambda a: "Documento Word generado",
    "generate_xlsx": lambda a: "Hoja de calculo generada",
    "generate_pptx": lambda a: "Presentacion generada",
    "lint_email": lambda a: "Estructura del email revisada",
    "run_check": lambda a: "Comprobaciones sobre el codigo listas",
    "query_code_graph": lambda a: "Mapa del codigo consultado",
    "shortest_path_in_graph": lambda a: "Mapa del codigo consultado",
    "ask_user": lambda a: "Pregunta respondida",
    "delegate_to_specialist": lambda a: f"Tarea resuelta por {a.get('specialist') or 'el otro especialista'}",
}

_MARCAS_DE_ERROR = ("ERROR", "[fail]")


def _accion_lista(name: str, arguments: dict, preview: str) -> str | None:
    """None si esta tool no tiene frase en pasado propia, O si el
    resultado real es un error - un ✓ verde tapando un fallo real seria
    peor que el volcado crudo de toda la vida (falsa sensacion de que
    funciono)."""
    if preview.startswith(_MARCAS_DE_ERROR):
        return None
    fn = _ACCIONES_LISTO.get(name)
    if fn is None:
        return None
    try:
        return fn(arguments)
    except Exception:  # noqa: BLE001 - un error armando el texto no debe tapar el resultado real
        return None

# Estado de prompt_toolkit para boxed_input (la caja de texto libre).
# Perezoso: la sesion se crea en el primer uso, y si esta terminal no lo
# soporta (Git Bash/mintty - no tienen buffer de consola nativo de
# Windows) se cae a un input() simple y no se reintenta.
#
# confirm_action ya NO tiene su propio selector de teclas: usa el menu de
# flechas `select()` como el resto de la terminal, asi que desaparecieron
# _pt_confirm_session/_pt_confirm_broken. De paso se elimino un bug real
# que tenian: prompt_toolkit dejaba los key_bindings 'y'/'a' pegados al
# objeto PromptSession, y si confirm_action y boxed_input compartian
# sesion, una tarea que empezaba por 'a' ("a ver que tal...") disparaba la
# vieja binding y cortaba la caja devolviendo "yes_session". `select` no
# bindea letras a resultados, asi que eso ya no puede pasar.
_pt_session = None
_pt_style = None
_pt_input_broken = False


def print_logo(subtitle: str) -> None:
    console.print()
    console.print(Text(" ◆ ", style=C_ACCENT) + Text("orquestador", style="bold white"))
    console.print(Text(f"   {subtitle}", style="grey50 italic"))
    console.print()


# Estado que se pinta encima del prompt en cada vuelta. Lo setea el CLI al
# terminar cada tarea; asi la linea de tokens/contexto esta SIEMPRE a la
# vista cuando el turno es del usuario, no solo al cerrar una vuelta del
# agente.
_estado_actual: tuple[int, object] | None = None


def set_estado(chars: int, gasto=None) -> None:
    global _estado_actual
    _estado_actual = (chars, gasto)


# ── Modo de confirmacion de escrituras ──────────────────────────────────────
# Con shift+tab (en el prompt) se alterna entre:
#   - CONFIRMA (por defecto): cada escritura de archivo pide confirmacion.
#   - AUTO: las escrituras DENTRO del workspace se aplican solas (las de FUERA
#     del workspace siguen preguntando: eso es seguridad, no comodidad).
# El CLI sincroniza executor.session_accept_all con esto antes de cada tarea.
_modo_auto: bool = False


def modo_auto() -> bool:
    return _modo_auto


def set_modo_auto(v: bool) -> None:
    global _modo_auto
    _modo_auto = bool(v)


def alternar_modo() -> bool:
    """Alterna auto/confirma y devuelve el nuevo estado. Lo llama el binding de
    shift+tab del prompt."""
    global _modo_auto
    _modo_auto = not _modo_auto
    return _modo_auto


def _etiqueta_modo() -> str:
    return "» AUTO" if _modo_auto else "CONFIRMA"


# ── Cronometro prompt -> resultado ──────────────────────────────────────────
# Cuenta lo que tarda la IA desde que se manda el prompt hasta que llega el
# resultado. Vive contando mientras trabaja (_crono_fin None) y se CONGELA al
# terminar, para que en el prompt siguiente quede a la vista cuanto tardo.
_crono_inicio: float | None = None
_crono_fin: float | None = None


def iniciar_cronometro() -> None:
    global _crono_inicio, _crono_fin
    _crono_inicio = time.monotonic()
    _crono_fin = None


def parar_cronometro() -> None:
    global _crono_fin
    if _crono_inicio is not None:
        _crono_fin = time.monotonic()


def _fmt_duracion(seg: float) -> str:
    seg = int(seg)
    return f"{seg}s" if seg < 60 else f"{seg // 60}m{seg % 60:02d}s"


def _texto_cronometro() -> str:
    if _crono_inicio is None:
        return ""
    fin = _crono_fin if _crono_fin is not None else time.monotonic()
    return _fmt_duracion(fin - _crono_inicio)


# ── Barra FIJA al fondo (un unico Live persistente) ─────────────────────────
# La barra tiene que quedar SIEMPRE como ultima linea, con el contenido nuevo
# scrolleando por ENCIMA y nada por debajo. Se logra con un UNICO Live de Rich
# que se mantiene abierto durante todo el trabajo: console.print escribe por
# encima de la region viva y el scrollback se conserva (NO se usa pantalla
# alternativa, que si romperia el historial - por eso antes se habia evitado
# una barra fija). El spinner y animate_dispatch NO abren su propio Live (Rich
# prohibe dos a la vez): ponen su contenido en `_trabajo_actual` y este Live lo
# pinta encima de la barra. Los menus/inputs de prompt_toolkit NO conviven con
# un Live -> lo pausan (ver _barra_en_pausa) o lo cierran (boxed_input).
_barra_viva: Live | None = None
_trabajo_actual = None  # renderable de la actividad en curso (spinner/animacion) o None


def _render_barra():
    """Lo que dibuja el Live fijo: la actividad en curso (si la hay) con la
    barra permanente pegada debajo, o solo la barra."""
    barra = Text(texto_estado(), style=C_DIM)
    if _trabajo_actual is not None:
        from rich.console import Group
        return Group(_trabajo_actual, barra)
    return barra


def _refrescar_barra() -> None:
    if _barra_viva is not None:
        try:
            _barra_viva.refresh()
        except Exception:  # noqa: BLE001 - un refresco fallido no debe cortar el trabajo
            pass


def abrir_barra_fija() -> None:
    """Ancla la barra al fondo. Idempotente. No hace nada sin TTY (una tuberia
    no tiene donde fijar nada)."""
    global _barra_viva
    if _barra_viva is not None or not sys.stdout.isatty():
        return
    try:
        _barra_viva = Live(
            _render_barra(), console=console, get_renderable=_render_barra,
            refresh_per_second=15,
            # transient: al pararlo (para abrir un prompt, o al cerrar la sesion)
            # se BORRA, en vez de dejar una copia congelada de la barra pegada en
            # el scrollback - justo el anti-patron que se venia evitando.
            transient=True,
        )
        _barra_viva.start()
    except Exception:  # noqa: BLE001 - si el Live no arranca, se sigue sin barra fija
        _barra_viva = None


def cerrar_barra_fija() -> None:
    """Suelta la barra fija (para cederle la terminal a un prompt de
    prompt_toolkit, o al cerrar la sesion). Idempotente."""
    global _barra_viva, _trabajo_actual
    if _barra_viva is None:
        return
    _trabajo_actual = None
    try:
        _barra_viva.stop()
    except Exception:  # noqa: BLE001
        pass
    _barra_viva = None


@contextmanager
def _barra_en_pausa():
    """Cierra la barra fija mientras corre un prompt de prompt_toolkit (que no
    convive con un Live) y la vuelve a abrir despues, SOLO si estaba abierta.
    Anidable: el interior ve que ya no hay Live y no hace nada."""
    estaba = _barra_viva is not None
    if estaba:
        cerrar_barra_fija()
    try:
        yield
    finally:
        if estaba:
            abrir_barra_fija()


class _Trabajo:
    """Lo que devuelve spinner(): abre la barra fija y coloca su contenido como
    actividad en curso, encima de la barra. Se usa como `with ui.spinner(...)`.
    Mantiene get_renderable() -> Group(contenido, barra) porque los tests (y
    antes el Live propio del spinner) lo consultan."""

    def __init__(self, contenido):
        self._contenido = contenido

    def get_renderable(self):
        return _con_barra(self._contenido)

    def __enter__(self):
        global _trabajo_actual
        abrir_barra_fija()
        _trabajo_actual = self._contenido
        _refrescar_barra()
        return self

    def __exit__(self, *_exc):
        # NO cierra la barra: la deja abierta (solo limpia la actividad) para que
        # siga fija durante la ejecucion de herramientas, no solo al 'pensar'.
        global _trabajo_actual
        _trabajo_actual = None
        _refrescar_barra()
        return False


# El plan VIVO del especialista, condensado a una linea, se pinta en la MISMA
# barra permanente que el gasto/contexto (bottom_toolbar del prompt + Live del
# spinner). Mientras hay plan a medias, la izquierda de la barra lo muestra; en
# cuanto esta todo hecho (o se limpia) DESAPARECE y la barra vuelve al gasto.
_plan_actual: list = []


def set_plan(pasos: list) -> None:
    """Fija el plan que pinta la barra de estado. Si todos los pasos estan
    hechos (o la lista viene vacia), lo BORRA: el plan cumplido desaparece de
    la barra y esta vuelve a mostrar solo gasto/contexto."""
    global _plan_actual
    pasos = list(pasos or [])
    _plan_actual = [] if (not pasos or _plan_todo_hecho(pasos)) else pasos



def texto_estado(ancho: int | None = None) -> str:
    """El contenido de la barra: gasto a la izquierda, contexto a la
    derecha, separados hasta ocupar el ancho.

    Lee `_estado_actual`, que apunta al objeto Gasto VIVO del cliente (se
    muta en su sitio), asi que cada llamada devuelve cifras al dia sin que
    nadie tenga que refrescarlo a mano."""
    from groq_agent.usage import barra_contexto

    try:
        chars, gasto = _estado_actual if _estado_actual else (0, None)
        der = f"contexto {barra_contexto(chars, ancho=16)}"
        total = ancho or console.width

        # El gasto (tokens) es PERMANENTE: siempre esta, no lo tapa el plan.
        if gasto is not None and getattr(gasto, "llamadas", 0):
            tok = f"{gasto.total:,} tok".replace(",", ".")
            if gasto.tasa_cache > 0.05:
                tok += f" · cache {int(gasto.tasa_cache * 100)}%"
            tok += f" · {gasto.llamadas} llamadas"
        else:
            tok = "sin gasto todavia"

        # Nucleo permanente: modo · [cronometro ·] gasto. El modo y el
        # cronometro son tan fijos como los tokens - siempre a la vista.
        base = _etiqueta_modo()
        crono = _texto_cronometro()
        if crono:
            base += f" · {crono}"
        base += f"  ·  {tok}"

        izq = base
        if _plan_actual:
            # El plan va a la IZQUIERDA de todo, sin taparlo; se recorta a lo
            # que sobre para que modo+gasto+contexto siempre quepan enteros. Al
            # completarse se limpia solo -> queda solo el nucleo.
            espacio = total - len(base) - len(der) - 5
            if espacio >= 8:
                izq = f"{_resumen_plan(espacio)}  ·  {base}"

        relleno = max(1, total - len(izq) - len(der))
        return f"{izq}{' ' * relleno}{der}"
    except Exception:  # noqa: BLE001 - la barra NUNCA debe tumbar el prompt ni el spinner
        return ""


def boxed_input(label: str = "tarea") -> str:
    """Prompt de una sola linea, SIN recuadro, con edicion completa
    (flechas, borrado, unicode, ctrl+c) via prompt_toolkit. Si la
    terminal no soporta eso, cae a un input() simple sin crashear.

    Se probaron DOS variantes de caja con borde (console.print manual, y
    despues PromptSession(show_frame=True)) y las dos fueron rechazadas
    en uso real: la primera se rompia con el resize de la terminal (texto
    estatico, no se puede redibujar), y la segunda arreglaba eso pero
    dibujaba una barra de ancho COMPLETO de la terminal apenas arrancaba
    el input, ocupando toda la pantalla sin necesidad. Un prompt simple
    (sin ningun caracter de borde dibujado) no tiene ese problema en
    absoluto: no hay nada que desalinear con un resize, y el ancho que
    ocupa es literalmente el del texto tipeado, ni un caracter mas -
    crece con lo que escribes en vez de ocupar la terminal entera desde
    el primer momento."""
    global _pt_session, _pt_style, _pt_input_broken
    # Es el turno del usuario: se suelta la barra fija (el Live) para que el
    # prompt tome la terminal. Durante el prompt la barra la pinta el
    # bottom_toolbar de prompt_toolkit (misma info via texto_estado), asi que no
    # hay hueco. El proximo trabajo (spinner/animate_dispatch) la reabre solo.
    cerrar_barra_fija()
    try:
        if _pt_input_broken:
            raise RuntimeError("prompt_toolkit deshabilitado en esta terminal")
        from prompt_toolkit import PromptSession
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.styles import Style as PTStyle

        if _pt_session is None:
            # shift+tab alterna AUTO/CONFIRMA sin tener que enviar nada. El
            # invalidate() repinta el bottom_toolbar al instante, asi el modo
            # nuevo se ve apenas se pulsa. Enter NO se toca: sigue enviando.
            _kb = KeyBindings()

            @_kb.add("s-tab")
            def _(event):  # noqa: ANN001 - firma que exige prompt_toolkit
                alternar_modo()
                event.app.invalidate()

            # erase_when_done=True: apenas se aprieta Enter, prompt_toolkit
            # borra la linea que se estaba tipeando en vez de dejarla
            # pegada en el scrollback - sin esto quedaba DUPLICADA (esta
            # linea + el "› tarea" que imprime print_task_header justo
            # despues en cli.py, mismo texto dos veces con estilos
            # distintos).
            _pt_session = PromptSession(erase_when_done=True, key_bindings=_kb)
            _pt_style = PTStyle.from_dict({
                "prompt": "bold cyan",
                # La barra en gris sobre el fondo de la terminal: tiene que
                # leerse sin competir con lo que se esta escribiendo.
                "bottom-toolbar": "#8a8a8a bg:default noreverse",
            })

        # bottom_toolbar recibe una FUNCION, no un texto: prompt_toolkit la
        # llama en cada refresco, asi que las cifras suben solas mientras el
        # prompt esta abierto. refresh_interval marca cada cuanto repinta.
        text = _pt_session.prompt(
            HTML(f"<prompt>{label}› </prompt>"),
            style=_pt_style,
            bottom_toolbar=lambda: texto_estado(),
            refresh_interval=0.5,
        )
    except (KeyboardInterrupt, EOFError):
        raise
    except Exception:
        if not _pt_input_broken:
            console.print(f"[{C_DIM}]  (edicion de linea simple en esta terminal)[/{C_DIM}]")
        _pt_input_broken = True
        from groq_agent.interrupt import turno_del_usuario

        with turno_del_usuario():
            text = console.input(f"[{C_ACCENT}]{label}›[/{C_ACCENT}] ")
    return text


def print_task_header(task: str) -> None:
    console.print(f"\n[bold white]› {_esc(task)}[/bold white]")


def print_routing(tag: str, specialist: str, model: str, domain: str | None = None) -> None:
    domain_part = f"dominio={_esc(domain)} -> " if domain else ""
    console.print(
        f"  [{C_OK}]\\[{_esc(tag)}][/{C_OK}] "
        f"[{C_DIM}]{domain_part}skill={_esc(specialist)} -> modelo={_esc(model)}[/{C_DIM}]"
    )


def print_fixed_route(specialist: str, model: str) -> None:
    console.print(f"  [{C_DIM}]skill fijo={_esc(specialist)} -> modelo={_esc(model)}[/{C_DIM}]")


def print_tool_call(name: str, arguments: dict) -> None:
    """Frase amigable en vez del nombre tecnico + argumentos en crudo
    (ej. "Creando index.html" en vez de "write_file({'path': 'index.html',
    'content': '<!DOCTYPE...'})") - el volcado tecnico completo es ruido
    para el usuario, no informacion util (ver Claude Code: nunca muestra
    la llamada cruda a una tool, solo un resumen de una linea)."""
    console.print(f"  [{C_SYSTEM}]› {_esc(_accion_amigable(name, arguments))}...[/{C_SYSTEM}]")


_VERBO_ARCHIVO = {"write_file": "Creado", "edit_file": "Editado", "read_file": "Leído"}


def _renderable_resultado(name: str, arguments: dict, preview: str, root: Path | None = None) -> Text:
    """Arma el renderable de la linea final SIN imprimirlo - separado de
    print_tool_result para poder usar el MISMO contenido como ultimo
    frame de la animacion de animate_dispatch (ver ahi) en vez de un
    console.print() aparte despues de que la animacion ya termino. Dos
    escrituras separadas (Live borra su contenido, despues un print
    nuevo pone la linea final) dejaban un instante en blanco entre
    medio - se veia como un parpadeo justo al terminar (reportado).

    Para write_file/edit_file/read_file con `root` (la raiz del
    workspace) disponible, muestra la ruta ABSOLUTA del archivo como un
    link clickeable (soportado por terminales modernas - Windows
    Terminal, iTerm2, etc. via hyperlinks OSC 8; en una terminal sin
    soporte se ve igual mas sin poder clickearse) en vez del mensaje
    tecnico de la tool - asi el usuario puede abrir el archivo con un
    click en vez de ir a buscarlo el mismo por la ruta relativa.

    Para read_file en particular, `preview` seria el CONTENIDO del
    archivo (eso es lo que devuelve la tool) - deliberadamente nunca se
    muestra: el usuario ya conoce el contenido de un archivo que le
    pidio al modelo leer, lo que quiere saber es CUAL archivo se leyo,
    no una repeticion de algo que ya tiene delante."""
    if name in _ARCHIVO_TOOLS and root is not None and arguments.get("path"):
        try:
            # resolve_write_destination (no `root / path` a secas) para que
            # una ruta a carpeta conocida ('descargas/...') muestre su
            # destino REAL (Downloads\...), no el workspace + esa ruta.
            from groq_agent.safety import resolve_write_destination
            abs_path, _ = resolve_write_destination(root, arguments["path"])
            uri = abs_path.as_uri()
            return (
                Text(f"    ✓ {_VERBO_ARCHIVO[name]}: ", style=C_OK)
                + Text(str(abs_path), style=f"underline {C_DIM} link {uri}")
            )
        except Exception:  # noqa: BLE001 - si algo falla al armar el link, mostrar el preview normal
            pass
    listo = _accion_lista(name, arguments, preview)
    if listo is not None:
        return Text(f"    ✓ {listo}", style=C_OK)
    return Text(f"    {preview}", style=C_DIM)


def print_tool_result(name: str, arguments: dict, preview: str, root: Path | None = None) -> None:
    console.print(_renderable_resultado(name, arguments, preview, root))


# Puntos suspensivos que "saltan" DESPUES del texto (". "/".. "/"..."/
# " .."/"  ."/"   ", en loop) - mismo patron que el spinner 'simpleDots
# Scrolling' de Rich, pero armado a mano: el spinner nativo de Rich
# (console.status(spinner=...)) dibuja su animacion ANTES del texto
# ("<frame> Consultando..."), y lo que se pidio es al reves (el texto
# fijo, los puntos animados despues: "Consultando...").
_DOTS_FRAMES = [".  ", ".. ", "...", " ..", "  .", "   "]

# Frases EXTRA que rotan con la frase principal de _ACCIONES mientras se
# espera una tool lenta - solo los puntos moviendose y el texto siempre
# igual se siente estatico/aburrido de mirar en una espera larga
# (reportado). Solo tiene sentido para tools que en la practica pueden
# tardar unos cuantos segundos (red, un modelo de vision, un browser
# headless) - una tool casi instantanea ni llega a mostrar la segunda
# frase, no hace falta variarla. Son la etapa de "INICIO" (ver
# _FRASES_FINAL_GENERICAS para la etapa de cierre, mas abajo) - varias
# por tool para que la eleccion al azar (ver _elegir_frase) no se sienta
# repetitiva en una espera larga.
_FRASES_ROTACION: dict[str, list[str]] = {
    "write_file": ["Guardando cambios", "Escribiendo en disco"],
    "edit_file": ["Aplicando el cambio", "Guardando"],
    "search_images": [
        "Comparando candidatas", "Revisando cual encaja mejor",
        "Mirando nitidez y encuadre", "Descartando las que no sirven",
    ],
    "search_web_image": [
        "Revisando que sea la imagen correcta", "Comparando con lo que pediste",
    ],
    "fetch_business_from_maps": [
        "Extrayendo nombre y direccion", "Leyendo la ficha del negocio", "Confirmando los datos",
    ],
    "fetch_menu_and_reviews_from_maps": [
        "Ordenando las mejores reseñas", "Revisando la carta", "Leyendo opiniones de clientes",
    ],
    "fetch_menu_photos_from_maps": ["Filtrando las mejores fotos", "Revisando la galeria del negocio"],
    "verificar_web": [
        "Sacando capturas de escritorio", "Sacando capturas de movil",
        "Revisando errores de consola", "Revisando la estructura de la pagina",
        "Mirando cada captura con detalle", "Comprobando proporciones y colores",
    ],
    "render_check": [
        "Sacando capturas de escritorio", "Sacando capturas de movil",
        "Revisando errores de consola", "Comprobando que no se rompa nada",
    ],
    "critique_screenshot": [
        "Mirando cada captura con detalle", "Comparando con lo esperado", "Revisando proporciones y colores",
    ],
    "classify_image_content": ["Describiendo lo que se ve en la foto", "Verificando el contenido real"],
    "extract_menu_text": ["Leyendo plato por plato", "Transcribiendo precios"],
    "web_fetch": ["Descargando la pagina", "Leyendo el contenido", "Procesando el HTML"],
    "search_web": ["Revisando los mejores resultados", "Comparando fuentes"],
    "generate_docx": ["Dando formato al documento", "Armando los parrafos"],
    "generate_xlsx": ["Armando las formulas", "Dando formato a las celdas"],
    "generate_pptx": ["Maquetando las diapositivas", "Ajustando el diseño"],
    "run_check": ["Corriendo ruff", "Corriendo los tests", "Revisando el codigo"],
    "delegate_to_specialist": ["Esperando la respuesta del otro especialista", "Coordinando la tarea"],
}

# Etapa de "CIERRE" - generica (no por tool, son frases razonables para
# cualquier espera larga) y se usa solo entonces despues de superar un umbral
# de tiempo (ver _en_etapa_final) - asi la sensacion de "ya casi" llega
# cuando de verdad lleva un rato esperando, no desde el primer segundo.
_FRASES_FINAL_GENERICAS = ["Ya casi", "Un momento mas", "Terminando", "Casi listo"]
_UMBRAL_FINAL_FALLBACK_S = 18.0  # antes 6.0 - con _ROTACION_INTERVALO_S mas lento (8.0s) un umbral
# corto pasaba a la etapa de cierre casi en la primera rotacion, sin dar tiempo a ver variedad
# del pool de inicio; sin historial de esta tool todavia, a partir de aca se asume "ya deberia
# estar por terminar"
_ROTACION_INTERVALO_S = 8.0  # antes 2.2 -> 4.5 -> 8.0 - la idea es que se sienta como que esta
# "pensando" (contemplativo), no como texto parpadeando rapido; cada cuanto cambia la FRASE,
# independiente del ritmo de los puntos, y NO instantaneo a proposito


def _frases_para(name: str, arguments: dict) -> list[str]:
    return [_accion_amigable(name, arguments), *_FRASES_ROTACION.get(name, [])]


def _en_etapa_final(name: str, transcurrido: float) -> bool:
    historial = _HISTORIAL_DURACIONES.get(name)
    if historial:
        umbral = (sum(historial) / len(historial)) * 0.75
    else:
        umbral = _UMBRAL_FINAL_FALLBACK_S
    return transcurrido >= umbral


def _elegir_frase(frases_inicio: list[str], tick: int, en_final: bool, anterior: str) -> str:
    # El primer tick (solo entonces arranca la espera) siempre muestra la frase
    # base (la de _ACCIONES) - un arranque predecible y sensato antes de
    # empezar a variar al azar. De ahi en mas, al azar dentro de la etapa
    # que corresponda, evitando repetir la MISMA frase dos ticks seguidos
    # (se notaria como que no cambio nada).
    if tick <= 0:
        return frases_inicio[0]
    pool = _FRASES_FINAL_GENERICAS if en_final else frases_inicio
    opciones = [f for f in pool if f != anterior] or pool
    return random.choice(opciones)


# El ritmo de los puntos NO es fijo - se estima cuanto suele tardar CADA
# tool (por nombre) a partir de lo observado en esta misma sesion, y se
# usa eso para decidir la cadencia: una tool que en la practica tarda
# varios segundos (red, un modelo de vision, un browser headless) anima
# mas lento/relajado, total hay tiempo de sobra que llenar; una que
# suele ser casi instantanea (grep_search, list_dir) anima mas rapido,
# para que se alcance a ver movimiento antes de que ya este el
# resultado, en vez de un solo frame estatico que se siente trabado. Sin
# historial todavia (primera vez que se llama esa tool en la sesion) se
# usa una cadencia neutra de arranque.
_DOTS_INTERVAL_RAPIDA_S = 0.12
_DOTS_INTERVAL_LENTA_S = 0.38
_DOTS_INTERVAL_DEFAULT_S = 0.2
_HISTORIAL_DURACIONES: dict[str, list[float]] = {}
_HISTORIAL_VENTANA = 5  # ventana movil chica: importa como viene tardando ULTIMAMENTE, no todo el historico

# Cada cuanto se revisa si el dispatch REAL ya termino - mucho mas
# seguido que cualquier ritmo visual (puntos o frases), para notar el
# resultado real apenas llega y cortar ahi (en vez de quedar atado a
# esperar el resto de un intervalo visual mas largo antes de darse
# cuenta - "acelerar la espera" si la tanda ya volvio).
_POLL_COMPLETADO_S = 0.05

# Frames de remate MUY rapidos que se muestran justo antes de cerrar la
# animacion (ver el "for" al final del loop en animate_dispatch) - un
# corte instantaneo de la animacion a la linea final se ve mal
# (reportado), esto da una sensacion de "acelerando" antes de terminar
# en vez de desaparecer de golpe. Dura una fraccion de segundo (3
# frames a 0.05s = 0.15s) - no es "esperar de mas", el resultado real ya
# esta listo en ese punto, es puramente cosmetico.
_REMATE_FRAMES = 3
_REMATE_INTERVALO_S = 0.05


def _registrar_duracion(name: str, segundos: float) -> None:
    historial = _HISTORIAL_DURACIONES.setdefault(name, [])
    historial.append(segundos)
    del historial[:-_HISTORIAL_VENTANA]


def _intervalo_puntos(name: str) -> float:
    historial = _HISTORIAL_DURACIONES.get(name)
    if not historial:
        return _DOTS_INTERVAL_DEFAULT_S
    promedio = sum(historial) / len(historial)
    return max(_DOTS_INTERVAL_RAPIDA_S, min(_DOTS_INTERVAL_LENTA_S, promedio / 15))


_LEXER_POR_EXTENSION = {
    "html": "html", "css": "css", "js": "javascript", "py": "python",
    "json": "json", "md": "markdown", "yaml": "yaml", "yml": "yaml",
}

# Igual que los puntos: nada de duracion fija. Se estima un tiempo total
# a partir del TAMAÑO real del contenido (mas texto, mas rapido por
# caracter para que no se eternice; menos texto, mas lento por caracter
# para que no sea un parpadeo imperceptible), acotado entre un piso y un
# techo para que ningun extremo se sienta raro. Van dos rondas de "mas
# lento" reportadas en uso real - primero x5, despues otra vez porque
# seguia sintiendose rapido.
_ESCRITURA_CPS_OBJETIVO = 90  # antes 900 -> 180 -> 90
_ESCRITURA_DURACION_MIN_S = 3.5  # antes 0.35 -> 1.75 -> 3.5
_ESCRITURA_DURACION_MAX_S = 16.0  # antes 1.6 -> 8.0 -> 16.0
# Un tope de frames FIJO (la version anterior, 60) da un paso entre
# frames que crece muchisimo apenas el archivo supera esa cantidad de
# caracteres - un archivo de 700 caracteres saltaba de a ~12 por frame,
# se sentia "a los trompicones" en vez de letra por letra (reportado).
# En cambio, un ritmo de refresco FIJO (frames por segundo) hace que el
# paso salga de cuanto dura la animacion, no de un numero inventado -
# archivos chicos (que ya de por si duran varios segundos, ver el piso
# de arriba) terminan revelando de a UN caracter por frame de verdad, y
# solo los archivos grandes agrupan mas caracteres por frame para no
# exceder un ritmo de refresco que la terminal pueda sostener sin
# flickering.
_ESCRITURA_REFRESCOS_POR_SEGUNDO = 35
_ESCRITURA_LINEAS_VISIBLES_MIN = 14
_ESCRITURA_LINEAS_VISIBLES_MARGEN = 6  # filas de la terminal que se dejan libres para el resto de la UI


def _lineas_visibles() -> int:
    """Antes un tope fijo de 14 lineas dejaba la 'ventana' de codigo
    chica en terminales grandes (reportado: 'la ventana no se ve casi').
    Usa el alto REAL de la terminal (como ya se hace con console.width
    para el input) menos un margen para el resto de lo que se imprime
    alrededor, en vez de un numero inventado - asi se ve tanto del
    archivo como entra de verdad en pantalla."""
    return max(_ESCRITURA_LINEAS_VISIBLES_MIN, console.height - _ESCRITURA_LINEAS_VISIBLES_MARGEN)


def _lexer_por_archivo(path: str | None) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if path and "." in path else ""
    return _LEXER_POR_EXTENSION.get(ext, "text")


def _duracion_escritura(contenido: str) -> float:
    estimada = len(contenido) / _ESCRITURA_CPS_OBJETIVO
    return max(_ESCRITURA_DURACION_MIN_S, min(_ESCRITURA_DURACION_MAX_S, estimada))


def _animar_escritura(contenido: str, path: str | None) -> None:
    """Revelado progresivo tipo 'maquina de escribir' del contenido a
    escribir - se conoce COMPLETO de antemano (viene en `arguments`,
    write_file/edit_file no tocan el disco hasta que el dispatch real
    corre), asi que se anima ANTES de ese dispatch en vez de intentar
    sincronizar con el. La duracion total y el paso entre frames salen
    de `_duracion_escritura` (escala con el tamaño real del contenido,
    ver comentario ahi) en vez de ser un numero fijo. Solo se muestran
    las ultimas ~14 lineas del fragmento revelado: lo que importa aca es
    la SENSACION de progreso, no que se alcance a leer cada linea de un
    archivo de cientos.

    Cada frame se pone como actividad de la barra fija (_trabajo_actual) y se
    fuerza un refresco a mano (_refrescar_barra): sin ese refresco, el Live solo
    saca lo ultimo cada 1/refresh_per_second, y un `intervalo` mas corto haria
    que varios frames se pisen sin verse (revelacion a trompicones, bug real
    reportado). Con el refresco por frame, cada uno se pinta sincronizado con el
    sleep.

    El paso a paso es por CARACTER, no por linea - revelar linea por
    linea completa se ve como bloques de texto apareciendo de golpe,
    nada parecido a estar tipeando de verdad. Cortando por caracter el
    borde del fragmento visible cae A MITAD de la ultima linea (como
    alguien escribiendo en vivo), y para archivos grandes el paso entre
    frames crece (mas caracteres por frame) para entrar en la duracion
    total - se ve como si estuviera tipeando muy rapido, en vez de
    tardar una eternidad letra por letra en un archivo de miles de
    caracteres.

    background_color="default": el tema 'monokai' trae su PROPIO color
    de fondo (un gris oscuro) y por default Syntax lo pinta como un
    bloque solido detras del texto - repintar ese bloque completo en
    cada frame, muchas veces por segundo, es lo que se vio como
    flickering (reportado). Con "default" se usa el fondo real de la
    terminal en vez de pintar uno nuevo encima cada vez, sin perder el
    resaltado de sintaxis (que es lo que importa aca)."""
    # Import local: rich.markdown/markdown_it cuestan ~180ms
    # y solo hacen falta al renderizar, no en cada arranque.
    from rich.syntax import Syntax

    global _trabajo_actual
    duracion = _duracion_escritura(contenido)
    total = len(contenido)
    frames_por_ritmo = max(1, round(duracion * _ESCRITURA_REFRESCOS_POR_SEGUNDO))
    total_frames = max(1, min(total, frames_por_ritmo))
    paso = max(1, -(-total // total_frames))  # division hacia arriba
    intervalo = duracion / total_frames
    lexer = _lexer_por_archivo(path)
    lineas_visibles = _lineas_visibles()
    for i in range(paso, total + paso, paso):
        fragmento = contenido[:i]
        visible = "\n".join(fragmento.splitlines()[-lineas_visibles:])
        _trabajo_actual = Syntax(
            visible, lexer, theme="monokai", background_color="default",
            line_numbers=False, word_wrap=False,
        )
        _refrescar_barra()
        time.sleep(intervalo)


def _con_barra(contenido):
    """Pega la barra permanente (gasto/contexto/plan) DEBAJO de un frame vivo.

    La barra tenia que estar SIEMPRE abajo, pero solo aparecia en el spinner de
    'pensando' (el client.chat) y en el bottom_toolbar del prompt. Durante la
    ejecucion de herramientas manda animate_dispatch, cuyo Live no la pintaba,
    asi que la barra parpadeaba: presente al pensar, ausente al escribir
    archivos - y en una web los edit_file/write_file son casi todo el tiempo.
    Con esto la barra viaja pegada a cada frame intermedio de animate_dispatch.

    El frame FINAL (el que se persiste, transient=False) NO la lleva: si la
    llevara, quedaria una copia CONGELADA de la barra por cada tool en el
    scrollback - justo el anti-patron que se elimino al dejar de imprimirla."""
    from rich.console import Group
    return Group(contenido, Text(texto_estado(), style=C_DIM))


def animate_dispatch(name: str, arguments: dict, executor, root: Path | None = None) -> str:
    """Corre executor.dispatch EN UN HILO aparte mientras el hilo
    principal anima algo en pantalla - pensado para rellenar el tiempo
    muerto real entre llamadas (una tool como fetch_business_from_maps o
    critique_screenshot puede tardar varios segundos: red, un modelo de
    vision local, un browser headless) en vez de dejar la terminal
    congelada y en silencio hasta que vuelve el resultado.

    write_file/edit_file son un caso especial: el contenido a escribir
    ya se conoce ANTES de llamar a dispatch (viene en `arguments`), asi
    que en vez de puntos sueltos se anima el propio codigo apareciendo
    poco a poco (ver _animar_escritura) - el dispatch real (I/O local) es
    casi instantaneo, entra comodo mientras dura esa animacion.

    El ritmo de los puntos (para el resto de las tools) se ajusta con lo
    observado en ESTA sesion (ver _intervalo_puntos/_registrar_duracion) -
    por eso se mide el tiempo real que tardo el dispatch antes de
    devolver el resultado. Si la tool tiene frases alternativas
    (_FRASES_ROTACION), el TEXTO tambien va cambiando cada
    _ROTACION_INTERVALO_S segundos, al azar y en dos etapas (inicio vs
    cierre, ver _elegir_frase/_en_etapa_final) - solo los puntos
    moviendose con el mismo texto fijo se siente estatico en una espera
    larga.

    NADA de esto le agrega duracion artificial a una tool que ya de por
    si tarda (una busqueda, un scraping) - el loop de abajo solo dibuja
    mientras `future` (el dispatch real, corriendo en su propio hilo)
    sigue sin terminar; el chequeo de `future.done()` se hace a un ritmo
    RAPIDO fijo (bastante mas seguido que el ritmo visual de puntos o de
    frases) para notar apenas el resultado real ya esta listo y cortar
    ahi mismo, en vez de quedar atado a esperar el resto de un intervalo
    visual mas largo antes de darse cuenta."""
    if name == "plan":
        # El plan NO se vuelca al scrollback: vive en la barra permanente (donde
        # el gasto/contexto), condensado a una linea, y desaparece al cumplirse.
        # Asi no se repite un bloque enorme cada vez que se marca un paso.
        result = executor.dispatch(name, arguments)
        set_plan(arguments.get("pasos") or [])
        return result

    global _trabajo_actual
    frases = _frases_para(name, arguments)
    inicio = time.monotonic()
    # La animacion se pinta como actividad de la barra FIJA (un unico Live que ya
    # esta abierto durante el trabajo; abrir_barra_fija es no-op si ya lo esta o
    # si no hay TTY). Asi la barra no parpadea entre herramienta y herramienta:
    # es la misma region viva, siempre al fondo.
    abrir_barra_fija()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(executor.dispatch, name, arguments)
        if name == "write_file" and arguments.get("content"):
            _animar_escritura(arguments["content"], arguments.get("path"))
        elif name == "edit_file" and arguments.get("new_text"):
            _animar_escritura(arguments["new_text"], arguments.get("path"))
        intervalo_puntos = _intervalo_puntos(name)
        i = 0
        tick_actual = -1
        frase_actual = frases[0]
        ultimo_render = 0.0
        while not future.done():
            ahora = time.monotonic()
            transcurrido = ahora - inicio
            tick = int(transcurrido / _ROTACION_INTERVALO_S)
            if tick != tick_actual:
                tick_actual = tick
                frase_actual = _elegir_frase(frases, tick, _en_etapa_final(name, transcurrido), frase_actual)
            if ahora - ultimo_render >= intervalo_puntos:
                ultimo_render = ahora
                i += 1
                _trabajo_actual = Text(f"  {frase_actual}{_DOTS_FRAMES[i % len(_DOTS_FRAMES)]}", style=C_SYSTEM)
                _refrescar_barra()
            time.sleep(_POLL_COMPLETADO_S)
        # Remate: un corte INSTANTANEO de la animacion a la linea final
        # se ve mal (reportado) - unos pocos frames finales bien rapidos
        # ("acelerando" antes de terminar) en vez de desaparecer de
        # golpe. Nada que ver con esperar de mas: el resultado real ya
        # esta listo (future.done()==True aca), esto es puramente cosmetico.
        for _ in range(_REMATE_FRAMES):
            i += 1
            _trabajo_actual = Text(f"  {frase_actual}{_DOTS_FRAMES[i % len(_DOTS_FRAMES)]}", style=C_SYSTEM)
            _refrescar_barra()
            time.sleep(_REMATE_INTERVALO_S)
        result = future.result()
    # La actividad termino: se limpia y la linea final del resultado se imprime
    # POR ENCIMA de la barra fija (queda en el scrollback; la barra sigue abajo,
    # sin dejar una copia congelada de si misma en el historial).
    _trabajo_actual = None
    console.print(_renderable_resultado(name, arguments, result[:300], root))
    _refrescar_barra()
    _registrar_duracion(name, time.monotonic() - inicio)
    return result



def print_context_saving(informe: dict) -> None:
    """Una linea discreta con lo que se recorto del historial.

    Se muestra porque es la unica señal de que la gestion de contexto esta
    trabajando: sin ella, "por que esta tarea gasta la mitad" queda como
    magia. Va en gris y en una sola linea - es telemetria, no un evento.
    """
    ahorrado = informe["antes"] - informe["despues"]
    if ahorrado < 2000:
        # Por debajo de ~500 tokens no vale la pena la linea de ruido.
        return
    partes = []
    if informe.get("podado"):
        partes.append(f"poda {informe['podado'] // 1000}k")
    if informe.get("compactado"):
        partes.append(f"resumen {informe['compactado'] // 1000}k")
    detalle = " + ".join(partes) if partes else f"{ahorrado // 1000}k"
    console.print(
        f"[{C_DIM}]  contexto: {informe['antes'] // 1000}k -> "
        f"{informe['despues'] // 1000}k chars  ({detalle})[/{C_DIM}]"
    )


# --- Selector de flechas -----------------------------------------------
#
# Tecnica portada de src/menu.js de kimi-cli-upgrade: NO se borra la
# pantalla en cada pulsacion, se sube el cursor N lineas y se reescriben
# solo las opciones. Eso es lo que quita el parpadeo, y ademas permite que
# el menu viva en medio de la salida en vez de exigir estar arriba del
# todo.
#
# Se usa prompt_toolkit (ya es dependencia y ya funciona en esta terminal,
# ver boxed_input) en vez de leer stdin en crudo: en Windows el manejo de
# secuencias de flechas a mano es una fuente conocida de rarezas segun la
# consola (cmd, Windows Terminal, VS Code) y no vale la pena reimplementarlo.

_HIDE_CURSOR = "\x1b[?25l"
_SHOW_CURSOR = "\x1b[?25h"

_pt_select_broken = False


def _ventana_scroll(i: int, top: int, n: int, max_visibles: int) -> tuple[int, int]:
    """Ventana deslizante del selector: dado el indice elegido `i`, el `top`
    actual (primera opcion visible), el total `n` y cuantas caben, devuelve
    `(nuevo_top, fin)` -la franja [top, fin) que hay que pintar- deslizando lo
    MINIMO para que `i` quede dentro. Es lo que hace que al bajar la seleccion
    no se salga de pantalla."""
    if i < top:                          # subio por encima de la ventana
        top = i
    elif i >= top + max_visibles:        # bajo por debajo de la ventana
        top = i - max_visibles + 1
    top = max(0, min(top, max(0, n - max_visibles)))
    return top, min(n, top + max_visibles)


def select(
    title: str,
    options: list[str],
    default: int = 0,
    hints: list[str] | None = None,
    submenu_on_right: bool = False,
    right_is_enter: bool = False,
) -> int | None | tuple[str, int]:
    """Selector con flechas arriba/abajo. Devuelve el indice elegido, o
    None si se cancela con Esc/Ctrl-C.

    `hints` es una segunda linea opcional por opcion (gris), para explicar
    sin ensuciar la linea principal.

    `submenu_on_right=True` habilita la flecha DERECHA: en vez de elegir,
    sale devolviendo `("__right__", i)` para que el llamador abra un
    submenu sobre esa opcion (ej. borrar/renombrar/ver uso de una sesion).
    Los llamadores que no lo pasan nunca reciben la tupla: compatible.

    `right_is_enter=True` hace que la flecha DERECHA elija (como enter): para
    un si/no rapido en cadena con las flechas. Excluyente con submenu_on_right.

    Sin TTY (tuberia, CI) devuelve el default sin dibujar nada: bloquear
    esperando una tecla que nunca llega es peor que elegir lo razonable.
    """
    global _pt_select_broken

    if not options:
        return None
    if not sys.stdin.isatty() or _pt_select_broken:
        return default

    try:
        from prompt_toolkit.application import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import Layout
        from prompt_toolkit.layout.containers import HSplit, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
    except Exception:
        _pt_select_broken = True
        return default

    # "top" = primera opcion visible: una VENTANA que sigue a la seleccion.
    # Sin esto, una lista mas larga que la pantalla se dibujaba entera y al
    # bajar la opcion elegida se iba fuera de pantalla sin que nada scrollara.
    estado = {"i": max(0, min(default, len(options) - 1)), "top": 0}
    con_hint = bool(hints)

    def _lineas():
        pie = "\n  ↑↓ mover · enter elegir · esc cancelar"
        if submenu_on_right:
            pie = "\n  ↑↓ mover · enter elegir · → opciones · esc cancelar"
        elif right_is_enter:
            pie = "\n  ↑↓ mover · enter/→ elegir · esc cancelar"

        # Cuantas opciones caben: alto de la terminal menos titulo, pie y un
        # margen para los avisos de "N mas arriba/abajo". Cada opcion ocupa 2
        # filas cuando lleva hint (linea + gris), 1 si no.
        filas_por_opcion = 2 if con_hint else 1
        alto = shutil.get_terminal_size(fallback=(80, 24)).lines
        util = max(filas_por_opcion, alto - 5)          # titulo(1)+pie(2)+avisos(2)
        max_visibles = max(1, util // filas_por_opcion)

        top, fin = _ventana_scroll(estado["i"], estado["top"], len(options), max_visibles)
        estado["top"] = top
        n = len(options)

        out = [("class:title", f"{title}\n")]
        if top > 0:
            out.append(("class:hint", f"  ↑ {top} mas arriba\n"))
        for idx in range(top, fin):
            elegido = idx == estado["i"]
            marca = "› " if elegido else "  "
            estilo = "class:sel" if elegido else "class:opt"
            out.append((estilo, f"{marca}{options[idx]}\n"))
            if hints and idx < len(hints) and hints[idx]:
                out.append(("class:hint", f"    {hints[idx]}\n"))
        if fin < n:
            out.append(("class:hint", f"  ↓ {n - fin} mas abajo\n"))
        out.append(("class:hint", pie))
        return out

    kb = KeyBindings()

    @kb.add("up")
    @kb.add("k")
    def _arriba(event):
        # Circular: bajar desde el ultimo lleva al primero. En una lista
        # corta se navega mas rapido asi que topando en los extremos.
        estado["i"] = (estado["i"] - 1) % len(options)

    @kb.add("down")
    @kb.add("j")
    def _abajo(event):
        estado["i"] = (estado["i"] + 1) % len(options)

    @kb.add("enter")
    def _elegir(event):
        event.app.exit(result=estado["i"])

    if submenu_on_right:
        @kb.add("right")
        @kb.add("l")
        def _submenu(event):
            event.app.exit(result=("__right__", estado["i"]))
    elif right_is_enter:
        @kb.add("right")
        def _derecha_elige(event):
            event.app.exit(result=estado["i"])

    @kb.add("escape")
    @kb.add("c-c")
    def _cancelar(event):
        event.app.exit(result=None)

    # OJO: prompt_toolkit NO entiende los nombres de color de rich
    # ("grey58", "grey42") - solo colores ANSI con nombre o hex. Mezclarlos
    # levanta ValueError("Wrong color format") al CONSTRUIR la Application.
    # Por eso van en hex, y por eso la construccion esta dentro del try: un
    # selector es un adorno, y un adorno nunca puede tumbar el programa.
    # Antes estaba fuera y `orquestador -r` moria con un traceback.
    from groq_agent.interrupt import turno_del_usuario

    try:
        from prompt_toolkit.styles import Style as _PTStyle

        app = Application(
            layout=Layout(
                HSplit([Window(FormattedTextControl(_lineas), dont_extend_height=True)])
            ),
            key_bindings=kb,
            style=_PTStyle.from_dict({
                "title": "bold #4fc1ff",
                "sel": "bold #4ec9b0",
                "opt": "#9a9a9a",
                "hint": "italic #6a6a6a",
            }),
            full_screen=False,
            erase_when_done=True,   # al elegir, el menu desaparece: la
                                    # terminal queda limpia, sin rastro.
        )
        # El vigilante de ESC consume las teclas: sin cederle el teclado,
        # las flechas no llegan al selector. Y la barra fija (un Live) NO
        # convive con un Application de prompt_toolkit: se pausa mientras dura
        # el menu y se reanuda al salir (no-op si no habia barra abierta).
        with _barra_en_pausa(), turno_del_usuario():
            return app.run()
    except (KeyboardInterrupt, EOFError):
        return None
    except Exception:
        _pt_select_broken = True
        return default


def clear_screen() -> None:
    """Deja la terminal limpia de verdad, como hace Claude Code al arrancar:
    sin el banner de la consola, sin el comando que la lanzo, y sin lo que
    hubiera ejecutado antes.

    POR QUE NO BASTA CON LAS SECUENCIAS ANSI
    La escapada \\x1b[3J (borrar scrollback) NO la implementa el conhost
    clasico, que es donde corre Windows PowerShell 5.1 - probado: el banner
    de PowerShell y la linea del propio comando seguian ahi. En Windows hay
    que pasar por `cls`, que llama a la API de consola y si vacia el buffer.
    Las ANSI se mandan igual DESPUES, porque en Windows Terminal, VS Code y
    cualquier terminal POSIX si funcionan y cubren el caso en que `cls` no
    limpie el scrollback.
    """
    if not sys.stdout.isatty():
        return
    if os.name == "nt":
        # subprocess seria mas cuidado, pero `cls` necesita heredar el
        # handle de consola real para vaciar su buffer: lanzado en un
        # proceso hijo con las tuberias redirigidas no limpia nada.
        os.system("cls")  # noqa: S605, S607
    else:
        os.system("clear")  # noqa: S605, S607
    sys.stdout.write("\x1b[3J\x1b[H\x1b[2J")
    sys.stdout.flush()


# El personaje. Deliberadamente tipografico, no un emoji: el usuario los
# tiene prohibidos en todo el sistema (ver el preambulo de skills_loader) y
# ademas los emojis se rompen segun la fuente de la consola.
_PERSONAJE = [
    "   ╭───────╮",
    "   │ ◠   ◠ │",
    "   │   ▿   │",
    "   ╰───┬───╯",
]


def print_splash(
    subtitle: str,
    datos: list[tuple[str, str]] | None = None,
    pie: str | None = None,
) -> None:
    """Pantalla de inicio: limpia la terminal, dibuja el personaje y TODO el
    estado de arranque en una sola tabla etiqueta+valor.

    `pie` es una linea final de ayuda. Va aca y no como un print aparte
    para que el arranque sea un unico bloque: cada linea de prosa suelta
    debajo ensucia la pantalla que se acaba de limpiar, que es justo lo
    contrario de lo que se busca."""
    clear_screen()
    console.print()
    for i, linea in enumerate(_PERSONAJE):
        if i == 1:
            console.print(
                Text(linea, style=C_ACCENT)
                + Text("   orquestador", style="bold white")
            )
        elif i == 2:
            console.print(Text(linea, style=C_ACCENT) + Text(f"   {subtitle}", style="grey50 italic"))
        else:
            console.print(Text(linea, style=C_ACCENT))
    console.print()
    for etiqueta, valor in datos or []:
        console.print(
            f"   [{C_DIM}]{etiqueta:<12}[/{C_DIM}] {escape(valor)}", highlight=False
        )
    if datos:
        console.print()
    if pie:
        console.print(f"   [{C_DIM}]{escape(pie)}[/{C_DIM}]", highlight=False)
        console.print()


def _mensaje_util(texto: str) -> str:
    """Saca la frase legible de un error de proveedor.

    Los proveedores devuelven el motivo dentro de un JSON
    ({"error":{"message":"..."}}, {"message":"..."}, {"detail":"..."}).
    Mostrar el objeto entero es exactamente el muro de texto que sobra:
    de 300 caracteres, los 40 utiles son el valor de 'message'.
    """
    texto = texto.strip().replace("\n", " ")
    inicio = texto.find("{")
    if inicio != -1:
        cabecera = texto[:inicio].strip().rstrip(":")
        try:
            datos = json.loads(texto[inicio:])
        except (ValueError, TypeError):
            datos = None
        if isinstance(datos, dict):
            for clave in ("message", "detail", "error_description", "msg"):
                valor = datos.get(clave)
                if isinstance(valor, str) and valor:
                    return f"{cabecera} · {valor}" if cabecera else valor
            anidado = datos.get("error")
            if isinstance(anidado, dict):
                valor = anidado.get("message") or anidado.get("detail")
                if isinstance(valor, str) and valor:
                    return f"{cabecera} · {valor}" if cabecera else valor
            if isinstance(anidado, str) and anidado:
                return f"{cabecera} · {anidado}" if cabecera else anidado
    return texto


def print_error_corto(exc: Exception | str, sugerencia: str = "") -> None:
    """Un error en una linea, no un muro de texto.

    Quien usa la terminal necesita saber QUE paso y QUE hacer; el cuerpo
    JSON del proveedor o un traceback no le dicen ninguna de las dos cosas.
    highlight=False porque si no rich colorea numeros, rutas y comillas por
    su cuenta y convierte una linea sobria en un arcoiris.
    """
    texto = _mensaje_util(str(exc))
    if len(texto) > 110:
        texto = texto[:107] + "…"
    console.print(f"  [{C_FAIL}]×[/{C_FAIL}] {escape(texto)}", highlight=False)
    if sugerencia:
        console.print(f"    [{C_DIM}]{escape(sugerencia)}[/{C_DIM}]", highlight=False)



def print_status_bar(chars: int, gasto=None) -> None:
    """Actualiza el estado que pinta la barra viva. YA NO imprime una linea.

    La barra existe en dos sitios y los dos se refrescan solos: el `Live`
    del spinner mientras el modelo trabaja, y el `bottom_toolbar` del
    prompt mientras escribes tú. Entre los dos esta siempre abajo y siempre
    al dia.

    Imprimirla ademas en cada vuelta dejaba una copia CONGELADA por turno
    en el historial - una lista de contadores viejos por la que hay que
    hacer scroll, que es justo lo contrario de "una barra permanente".
    `_print_status_bar` se conserva para el resumen del final de sesion,
    donde una foto fija si tiene sentido."""
    set_estado(chars, gasto)


def _print_status_bar(chars: int, gasto=None) -> None:
    """Barra de estado: gasto a la IZQUIERDA, contexto a la DERECHA.

    Ocupa el ancho completo de la terminal, con las dos cifras en los
    extremos, para que se lean de un vistazo sin buscarlas entre el resto
    de la salida. Es la unica linea de la UI que se alinea a los bordes:
    eso es lo que la hace reconocible como "estado" y no como un mensaje
    mas del agente.

    No es una barra fija de terminal (eso exigiria tomar la pantalla en
    modo alternativo y romperia el scroll del historial, que es justo lo
    que el usuario mira cuando quiere revisar lo que hizo el agente). Se
    imprime al cerrar cada vuelta, que es cuando las cifras cambian.
    """
    from groq_agent.usage import barra_contexto

    izquierda = ""
    if gasto is not None and gasto.llamadas:
        izquierda = f"{gasto.total:,} tok".replace(",", ".")
        if gasto.tasa_cache > 0.05:
            izquierda += f" · cache {int(gasto.tasa_cache * 100)}%"
        izquierda += f" · {gasto.llamadas} llamadas"
    else:
        izquierda = "sin gasto registrado"

    derecha = f"contexto {barra_contexto(chars, ancho=16)}"

    ancho = max(40, console.width)
    relleno = ancho - len(izquierda) - len(derecha)
    if relleno < 2:
        # Terminal estrecha: apilar en vez de solapar.
        console.print(f"[{C_DIM}]{izquierda}[/{C_DIM}]", highlight=False)
        console.print(f"[{C_DIM}]{derecha}[/{C_DIM}]", highlight=False)
        return
    console.print(
        f"[{C_DIM}]{izquierda}{' ' * relleno}{derecha}[/{C_DIM}]", highlight=False
    )


def print_context_meter(chars: int, gasto=None) -> None:
    """Una linea con el llenado del contexto y el gasto acumulado.

    Va junta a proposito: las dos cifras solo significan algo la una junto a
    la otra. Un contexto al 80% con poco gasto es una tarea que arranca
    cargada; al 80% con mucho gasto es una tarea que lleva rato y conviene
    vigilar."""
    from groq_agent.usage import barra_contexto

    partes = [f"contexto {barra_contexto(chars)}"]
    if gasto is not None and gasto.llamadas:
        partes.append(f"{gasto.total // 1000}k tok")
        if gasto.tasa_cache > 0.05:
            partes.append(f"cache {int(gasto.tasa_cache * 100)}%")
    console.print(f"  [{C_DIM}]{' · '.join(partes)}[/{C_DIM}]", highlight=False)


def print_usage_panel(titulo: str, gasto, extra: list[tuple[str, str]] | None = None) -> None:
    """Panel de gasto de una sesion."""
    console.print(f"[{C_ACCENT}]{escape(titulo)}[/{C_ACCENT}]")
    if not gasto.llamadas:
        console.print(f"  [{C_DIM}]sin llamadas registradas[/{C_DIM}]")
        console.print()
        return
    filas = [
        ("llamadas", f"{gasto.llamadas:,}"),
        ("entrada", f"{gasto.entrada:,} tok"),
        ("salida", f"{gasto.salida:,} tok"),
        ("total", f"{gasto.total:,} tok"),
    ]
    if gasto.cache:
        filas.append(("de cache", f"{gasto.cache:,} tok  ({int(gasto.tasa_cache * 100)}%)"))
    if gasto.sin_datos:
        # Se dice explicitamente: un total al que le faltan llamadas no debe
        # parecer exacto.
        filas.append(("sin informar", f"{gasto.sin_datos} llamada(s) sin usage"))
    for etiqueta, valor in filas + (extra or []):
        console.print(f"  [{C_DIM}]{etiqueta:<14}[/{C_DIM}] {escape(valor)}", highlight=False)
    if gasto.por_modelo:
        console.print(f"  [{C_DIM}]{'por modelo':<14}[/{C_DIM}]")
        for modelo, n in sorted(gasto.por_modelo.items(), key=lambda x: -x[1]):
            console.print(
                f"  {'':<14} [{C_DIM}]{escape(modelo):<46}[/{C_DIM}] {n:>9,} tok",
                highlight=False,
            )
    console.print()



def print_header(titulo: str, datos: list[tuple[str, str]] | None = None) -> None:
    """Cabecera ligera para los SUBCOMANDOS (--usage, --doctor, --keys...).

    Sin el personaje y sin limpiar la pantalla: el dibujo es la bienvenida
    de la terminal, y repetirlo en cada consulta lo convierte en ruido. Y
    borrar la pantalla en un subcomando se llevaria por delante justo lo que
    el usuario acaba de mirar.
    """
    console.print()
    console.print(Text(" ◆ ", style=C_ACCENT) + Text(titulo, style="bold white"))
    for etiqueta, valor in datos or []:
        console.print(
            f"   [{C_DIM}]{etiqueta:<12}[/{C_DIM}] {escape(valor)}", highlight=False
        )
    console.print()


def print_note(text: str) -> None:
    console.print(f"  [{C_DIM}]{_esc(text)}[/{C_DIM}]")


# Estado de un paso -> (simbolo, color). Se aceptan variantes en es/en porque
# el modelo no siempre respeta el enum del schema.
_PLAN_ESTADOS = {
    "hecho": ("✓", C_OK), "done": ("✓", C_OK), "completado": ("✓", C_OK), "ok": ("✓", C_OK),
    "en_curso": ("▶", "bold cyan"), "en curso": ("▶", "bold cyan"),
    "in_progress": ("▶", "bold cyan"), "actual": ("▶", "bold cyan"), "haciendo": ("▶", "bold cyan"),
}
_PLAN_PENDIENTE = ("○", C_DIM)


def _paso_plan(p) -> tuple[str, str]:
    """Normaliza un paso (dict con claves variables, o string pelado) a
    (texto, estado). El modelo no siempre respeta las claves del schema."""
    if isinstance(p, str):
        return p, "pendiente"
    texto = (p.get("paso") or p.get("texto") or p.get("descripcion")
             or p.get("step") or p.get("tarea") or "")
    estado = p.get("estado") or p.get("status") or "pendiente"
    return texto, estado


def _simbolo_paso(estado: str) -> str:
    return _PLAN_ESTADOS.get(str(estado).strip().lower(), _PLAN_PENDIENTE)[0]


def _plan_todo_hecho(pasos: list) -> bool:
    """True si TODOS los pasos estan marcados como hechos (plan cumplido)."""
    return bool(pasos) and all(_simbolo_paso(e) == "✓" for _, e in map(_paso_plan, pasos))


def _resumen_plan(limite: int) -> str:
    """El plan condensado a UNA linea para la barra permanente:
    `◆ hechos/total · ▶ <paso en curso>`. Muestra el paso en curso, o si no
    hay ninguno marcado, el primer pendiente. Se recorta a `limite` chars."""
    total = len(_plan_actual)
    hechos = 0
    en_curso = primer_pend = None
    for p in _plan_actual:
        texto, estado = _paso_plan(p)
        simbolo = _simbolo_paso(estado)
        if simbolo == "✓":
            hechos += 1
        elif simbolo == "▶" and en_curso is None:
            en_curso = texto
        elif simbolo == "○" and primer_pend is None:
            primer_pend = texto
    etiqueta = f"◆ {hechos}/{total}"
    actual = en_curso or primer_pend
    if actual:
        etiqueta += f" · ▶ {actual}"
    if limite and len(etiqueta) > limite:
        etiqueta = etiqueta[: max(1, limite - 1)].rstrip() + "…"
    return etiqueta


def print_plan(pasos: list) -> None:
    """El plan del especialista: una lista de pasos con su estado, igual que la
    lista de tareas que se ve cuando el asistente principal divide un problema.
    Se re-dibuja entero cada vez que el especialista lo actualiza (marca uno
    hecho y pone el siguiente en curso)."""
    if not pasos:
        return
    console.print(f"\n  [{C_ACCENT}]◆ plan[/{C_ACCENT}]")
    hechos = 0
    for p in pasos:
        texto, estado = _paso_plan(p)
        simbolo, color = _PLAN_ESTADOS.get(str(estado).strip().lower(), _PLAN_PENDIENTE)
        if simbolo == "✓":
            hechos += 1
            estilo = C_DIM               # lo hecho se atenua
        elif simbolo == "▶":
            estilo = "bold white"        # lo que se esta haciendo, resaltado
        else:
            estilo = "white"
        console.print(
            f"    [{color}]{simbolo}[/{color}] [{estilo}]{_esc(texto)}[/{estilo}]",
            highlight=False,
        )
    console.print(f"    [{C_DIM}]{hechos}/{len(pasos)} hechos[/{C_DIM}]")


def confirmar_ruta_externa(destino) -> str:
    """Confirmacion para escribir FUERA del workspace, con la ruta absoluta
    a la vista y el MISMO menu de flechas que el resto de la terminal.
    Devuelve 'no' | 'once' (solo este archivo) | 'folder' (y todo lo de esa
    carpeta sin volver a preguntar - pensado para una web, que son muchos
    ficheros en la misma carpeta).

    Sin TTY (CI/tuberia) devuelve 'no': sin nadie que confirme, lo seguro es
    NO escribir fuera del workspace."""
    from pathlib import Path

    if not sys.stdin.isatty():
        return "no"
    carpeta = Path(str(destino)).parent
    console.print()
    console.print(
        Panel(
            Text(f"Vas a guardar FUERA del workspace, en:\n{destino}", style="white"),
            border_style=C_ASK, title="guardar fuera del workspace", title_align="left",
        )
    )
    elegido = select(
        "",
        [
            "No, cancelar",
            "Si, solo este archivo",
            f"Si, y todo en '{carpeta.name or carpeta}' sin volver a preguntar",
        ],
        default=0,
        hints=[
            "no se escribe nada",
            f"se escribe {destino}",
            f"se guarda todo lo de {carpeta} el resto de la sesion",
        ],
    )
    return {1: "once", 2: "folder"}.get(elegido, "no")


def confirm_action(name: str, arguments: dict) -> str:
    """Devuelve 'yes' | 'no' | 'yes_session' ('aceptar todas' - no volver
    a preguntar en el resto de la sesion, ver agent_loop.py).

    Usa el MISMO menu de flechas ↑/↓ que las preguntas del modelo
    (ask_user_ui), la confirmacion de guardar fuera (confirmar_ruta_externa)
    y el selector de sesiones: una sola forma de decir si/no en toda la
    terminal, en vez de un atajo de teclas propio aqui. Sin TTY devuelve
    'no' (lo seguro cuando no hay quien decida)."""
    print_tool_call(name, arguments)
    if not sys.stdin.isatty():
        return "no"

    elegido = select(
        "¿Aplicar esta accion?",
        ["Si, hazlo", "Si, y no preguntar mas en esta sesion", "No, cancelar"],
        default=0,
        hints=[
            "se ejecuta esta vez",
            "se ejecuta y no se vuelve a preguntar en toda la sesion",
            "no se ejecuta",
        ],
    )
    return {0: "yes", 1: "yes_session"}.get(elegido, "no")


def ask_user_ui(question: str, options: list[str] | None = None) -> str:
    """Pregunta del modelo al usuario.

    Con opciones se usa el selector de flechas, igual que el de sesiones:
    escribir "2" y darle a enter es mas trabajo, se puede teclear mal, y
    obliga a leer la lista dos veces (una para elegir, otra para encontrar
    el numero). Con el selector, mover y elegir es la misma accion.

    question/options se pasan por Text() y no por markup: vienen del modelo
    (contenido no confiable) y asi se muestran literales, sin que un
    '[algo]' se interprete como marcado de rich.
    """
    console.print()
    console.print(
        Panel(Text(question, style="white"), border_style=C_ASK, title="?", title_align="left")
    )

    if options:
        elegido = select(
            "",   # la pregunta ya esta en el panel de arriba
            list(options),
            default=0,
        )
        if elegido is not None:
            console.print(f"  [{C_ASK}]›[/{C_ASK}] {escape(options[elegido])}")
            return options[elegido]
        # Cancelado con Esc, o terminal sin soporte: se cae al texto libre
        # en vez de dar por elegida la primera opcion. Elegir por el usuario
        # algo que no eligio es peor que preguntarle otra vez.
        console.print(f"  [{C_DIM}](escribe tu respuesta)[/{C_DIM}]")

    from groq_agent.interrupt import turno_del_usuario

    # console.input NO convive con la barra fija (un Live): se pausa mientras el
    # usuario teclea la respuesta y se reanuda al terminar.
    with _barra_en_pausa(), turno_del_usuario():
        answer = console.input(f"  [{C_ASK}]›[/{C_ASK}] ").strip()
    if options:
        # Por si el usuario escribe el numero igualmente (costumbre del
        # comportamiento anterior).
        try:
            idx = int(answer)
            if 1 <= idx <= len(options):
                return options[idx - 1]
        except ValueError:
            pass
    return answer or "(el usuario no respondio nada)"


def confirmar(
    pregunta: str,
    si: str = "Si",
    no: str = "No",
    default_si: bool = False,
    right_is_enter: bool = False,
) -> bool:
    """Si/No con el mismo selector de flechas del resto de la terminal. `Si`
    es SIEMPRE la primera opcion; `default_si` pone el cursor en ella (por
    defecto en `No`, lo seguro, que es lo que sale sin TTY o al cancelar con
    Esc). `right_is_enter` deja confirmar con la flecha derecha."""
    return select(
        pregunta, [si, no], default=0 if default_si else 1,
        right_is_enter=right_is_enter,
    ) == 0


def leer_linea(prompt: str, actual: str = "") -> str:
    """Una linea de texto libre (ej. renombrar). Muestra el valor actual como
    referencia. Sin TTY devuelve el actual (no hay quien teclee)."""
    if actual:
        console.print(f"  [{C_DIM}]actual:[/{C_DIM}] {escape(actual)}")
    if not sys.stdin.isatty():
        return actual
    from groq_agent.interrupt import turno_del_usuario
    with turno_del_usuario():
        return console.input(f"  [{C_ACCENT}]›[/{C_ACCENT}] {escape(prompt)}: ").strip()


def pausa_enter(mensaje: str = "enter para volver") -> None:
    """Espera a que el usuario pulse enter, para que pueda leer lo que se
    acaba de imprimir antes de que el menu lo tape. Sin TTY, no espera."""
    if not sys.stdin.isatty():
        return
    from groq_agent.interrupt import turno_del_usuario
    with turno_del_usuario():
        try:
            console.input(f"  [{C_DIM}]{escape(mensaje)}[/{C_DIM}]")
        except (EOFError, KeyboardInterrupt):
            pass


def render_result(text: str) -> None:
    # Import local: rich.markdown arrastra markdown_it y cuesta ~180ms.
    # Solo hace falta al renderizar la respuesta final, no en cada arranque
    # del CLI - `orquestador --help` no tiene por que pagarlo.
    from rich.markdown import Markdown

    console.print()
    if text.strip():
        console.print(Markdown(text))
    else:
        console.print(f"[{C_DIM} italic](respuesta vacia)[/{C_DIM} italic]")
    console.print()


def spinner(label: str):
    """Spinner CON la barra de estado pegada debajo, mientras el agente trabaja.

    Ya NO crea su propio Live: pone el spinner como actividad en curso de la
    barra FIJA (un unico Live que se mantiene abierto durante todo el trabajo,
    ver abrir_barra_fija) y esta lo pinta con la barra debajo. Asi la barra no
    parpadea al pasar de 'pensar' a 'ejecutar herramientas': es la MISMA region
    viva. Devuelve un objeto usable como `with ui.spinner(...)` que ademas
    expone get_renderable() -> Group(spinner, barra) (lo comprueban los tests).

    `texto_estado()` se re-lee en cada refresco del Live, asi que las cifras
    (tokens, cronometro) suben solas sin que nadie las refresque a mano."""
    from rich.spinner import Spinner

    return _Trabajo(Spinner("dots", text=Text(label, style=C_SYSTEM)))
