"""Terminal agentica de pruebas, al estilo Claude Code, para ejercitar
los skills + base de casos dificiles + herramientas de este repo contra
modelos servidos por OpenRouter (o cualquier otro endpoint OpenAI-
compatible - ver LLM_PROVIDER en groq_agent/groq_client.py).

Modo AUTO (default, recomendado): no elegis skill ni modelo, cada tarea
se clasifica sola (misma logica que el router de produccion) y se le
asigna el skill Y un modelo de tamaño comparable al que le tocaria en
el catalogo real (ver groq_agent/auto_router.py) - nunca un modelo
frontera de 70B/120B, el objetivo sigue siendo validar contra algo del
orden de lo que correria en una GPU de 24GB.

Sesiones (como `claude -c` / `claude -r`): cada tarea queda guardada en
`<workspace>/.orquestador_sessions/`. `--continue` retoma la sesion mas
reciente de ESE workspace; `--resume` (sin valor) te deja elegir de una
lista, o `--resume <id>` retoma una puntual.

Uso:
    orquestador
    (o: python -m groq_agent.cli)
        -> modo interactivo, auto-enrutado, workspace = carpeta "workspace" en
           tu Escritorio (se crea sola si no existe; cámbiala con --workspace)

    orquestador --workspace ./mi_proyecto "Revisa validators.py y agrega tests"
    orquestador --skill web-builder-specialist "Landing page para mi restaurante"
        -> fuerza un skill especifico (el modelo se sigue eligiendo automatico
           segun el tamaño que le corresponde a ese skill, salvo --model)

    Guardar en otro sitio: por defecto todo va al workspace, pero puedes
    pedir en la propia tarea "guárdalo en Descargas" o dar una ruta ("...
    en C:\\Users\\yo\\informes"). Se te confirma la ruta exacta antes de
    escribir fuera del workspace. Nunca escribe en carpetas del sistema.

    orquestador -c                 # continua la sesion mas reciente de este workspace
    orquestador -r                 # elegir una sesion anterior de una lista
    orquestador -r sess_20260703_...  # retomar una sesion puntual por id

    orquestador --list-skills
    orquestador --list-models

Corre SIEMPRE desde la raiz de orquestador-modelos/ (para que `import app.*`
resuelva) - o usa el lanzador ./orquestador.ps1, que ya se para ahi.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from groq_agent import checkpoint as _checkpoint
from groq_agent import interrupt
from groq_agent import prompt_classifier, prompt_refs
from groq_agent import providers as prov
from groq_agent import session_naming, session_store, ui
from groq_agent.providers import DEFAULT_TIER

# La reconfiguracion de encoding para Windows (utf-8, por si el texto del
# modelo trae caracteres fuera de cp1252) vive en groq_agent.ui y se
# aplica al importarlo arriba - no se repite aca.



# IMPORTS DIFERIDOS (velocidad de arranque)
# El agente y el router arrastran openpyxl, python-pptx, qdrant_client y
# fastembed: ~2 segundos medidos. Los comandos que NO ejecutan una tarea
# (--help, --keys, --config, --check-providers, --list-skills) no necesitan
# nada de eso, y pagarlo hacia que abrir una ayuda de texto tardase dos
# segundos. Se importan dentro de las funciones que de verdad los usan.


def _importar_agente():
    """Trae de golpe lo que hace falta para ejecutar tareas."""
    from app.router.heuristic_router import match_domain_or_none
    from groq_agent.agent_loop import run_agent
    from groq_agent.auto_router import CHARLA, auto_route, auto_route_llm, model_for_specialist
    from groq_agent.skills_loader import build_system_prompt
    from groq_agent.tools import ToolExecutor, schemas_for

    return {
        "CHARLA": CHARLA,
        "match_domain_or_none": match_domain_or_none,
        "run_agent": run_agent,
        "auto_route": auto_route,
        "auto_route_llm": auto_route_llm,
        "model_for_specialist": model_for_specialist,
        "build_system_prompt": build_system_prompt,
        "ToolExecutor": ToolExecutor,
        "schemas_for": schemas_for,
    }



# La charla lleva SOLO busqueda web (no las 27 herramientas): asi una
# pregunta de tiempo real (un precio, una noticia, la ultima version de algo)
# se busca en vez de inventarse, pero sin cargar el skill completo ni el menu
# de tools que hacia que un 'hola' abriera un ask_user. Son 2 esquemas
# pequeños, no los ~27K tokens del catalogo entero.
_TOOLS_CHARLA = {"search_web", "deep_research"}

# Clave bajo la que se guarda el historial de la charla en `conversations`
# (que por lo demas va por especialista). Empieza por "__" para que no choque
# con ningun nombre de especialista real.
_CLAVE_CHARLA = "__charla__"


def _responder_charla(
    client, executor, run_agent, task: str,
    existing_messages: list[dict] | None = None,
) -> list[dict]:
    """Contesta un mensaje que no pide trabajo sobre archivos con el prompt
    minimo (~400 tokens) y SOLO busqueda web: sabe la fecha real y puede
    buscar lo que sea en tiempo real, sin cargar los ~19.000 tokens del
    skill+catalogo de un especialista.

    Mantiene memoria entre mensajes: recibe el historial de la charla y
    devuelve el actualizado, igual que un especialista. Devuelve el historial
    SIN tocar si la llamada falla, para no perder lo que ya se venia hablando."""
    from groq_agent.tools import TOOL_SCHEMAS

    ui.print_note("pregunta - busqueda web, prompt minimo")
    schemas = [t for t in TOOL_SCHEMAS if t["function"]["name"] in _TOOLS_CHARLA]
    sistema = prompt_classifier.prompt_conversacional()
    # Al continuar una charla, refresca el system (mensaje 0) con la fecha de
    # HOY: run_agent reutiliza existing_messages[0] tal cual, y si no se
    # refrescara quedaria congelada la fecha del primer mensaje de la sesion.
    if existing_messages:
        existing_messages = [{"role": "system", "content": sistema}, *existing_messages[1:]]
    # Igual que un especialista: la charla ya corre el loop con herramientas
    # (varios turnos + animaciones), asi que necesita la MISMA gestion del
    # vigilante de ESC. Sin el `dejar_de_vigilar` del finally, el vigilante
    # (o el estado de consola tras las animaciones) deja la siguiente entrada
    # vacia y la sesion parece cerrarse sola (ver el finally del bloque de
    # especialista mas abajo). Con esto, ademas, ESC corta una busqueda lenta.
    try:
        interrupt.empezar_a_vigilar()
        respuesta, mensajes = run_agent(
            client=client,
            executor=executor,
            system_prompt=sistema,
            user_task=task,
            model=DEFAULT_TIER,
            # Varios turnos: uno para buscar, otro para responder con lo hallado.
            # Un saludo que no necesita buscar sigue costando un solo turno.
            max_turns=4,
            tool_schemas=schemas,
            existing_messages=existing_messages,
        )
    except RuntimeError as exc:
        ui.print_error_corto(exc)
        return existing_messages or []
    finally:
        interrupt.dejar_de_vigilar()
        interrupt.limpiar()
    ui.render_result(respuesta)
    return mensajes


def _mostrar_gasto(workspace: Path) -> int:
    """`--usage`: gasto por sesion de este workspace.

    Lee de disco, no del cliente: asi informa tambien de sesiones pasadas,
    que es el caso normal (uno mira el gasto DESPUES, no durante)."""
    from groq_agent import usage

    ui.print_header("gasto de tokens", [("workspace", str(workspace))])
    todo = usage.cargar_todo(workspace)
    if not todo:
        ui.console.print(
            f"  [{ui.C_DIM}]Todavia no hay gasto registrado en este workspace.[/{ui.C_DIM}]"
        )
        return 0

    sesiones = {s["session_id"]: s for s in session_store.list_sessions(workspace)}
    total = usage.Gasto()
    # De mas reciente a mas antigua: lo que interesa suele ser lo ultimo.
    for sid in sorted(todo, key=lambda s: sesiones.get(s, {}).get("updated_at", ""), reverse=True):
        gasto = usage.Gasto.desde_dict(todo[sid])
        meta = sesiones.get(sid, {})
        titulo = meta.get("name") or session_naming.nombre_para(meta.get("preview", "")) or sid
        ui.print_usage_panel(
            f"{titulo}  ({sid})",
            gasto,
            extra=[("actualizada", meta.get("updated_at", "?"))] if meta else None,
        )
        total.entrada += gasto.entrada
        total.salida += gasto.salida
        total.cache += gasto.cache
        total.llamadas += gasto.llamadas
        total.sin_datos += gasto.sin_datos
        for modelo, (ent, cac) in gasto.cache_por_proveedor.items():
            e, c = total.cache_por_proveedor.get(modelo, (0, 0))
            total.cache_por_proveedor[modelo] = (e + ent, c + cac)

    ui.print_usage_panel(f"TOTAL de {len(todo)} sesion(es)", total)

    # Quien cachea y quien no. Es la cifra que decide la factura: el mismo
    # trabajo cuesta ~4 veces mas en un proveedor que no cachea, y con una
    # escalera de failover el total de la sesion mezcla a los dos.
    filas = total.quien_cachea()
    if filas:
        ui.console.print(f"\n  [{ui.C_DIM}]cache servida por proveedor[/{ui.C_DIM}]")
        for modelo, tasa, entrada in filas:
            marca = "sirve cache" if tasa > 0.05 else "NO cachea: se paga entero"
            color = ui.C_OK if tasa > 0.05 else ui.C_FAIL
            ui.console.print(
                f"    {modelo:38} [{color}]{tasa:5.0%}[/{color}]  "
                f"[{ui.C_DIM}]{entrada:,} tok de entrada · {marca}[/{ui.C_DIM}]".replace(",", ".")
            )
    return 0



def _mostrar_api(workspace: Path) -> int:
    """`-api`: cuantas llamadas se han hecho a cada API (proveedor) en total
    HISTORICO (todos los workspaces y sesiones), con el desglose por modelo.
    Lee del historico global (~/.orquestador/api_historico.json), que se
    acumula por delta cada vez que se guarda una sesion."""
    from groq_agent import usage

    ui.print_header("llamadas por API (historico)", None)
    total = usage.Gasto(llamadas_por_modelo=usage.cargar_historico())

    filas = total.llamadas_por_proveedor()
    if not filas:
        ui.console.print(
            f"  [{ui.C_DIM}]Todavia no hay llamadas registradas por API.[/{ui.C_DIM}]"
        )
        return 0

    suma = sum(n for _, n, _ in filas)
    ancho = max(len(p) for p, _, _ in filas)
    for proveedor, n, detalle in filas:
        pct = (n / suma * 100) if suma else 0.0
        ui.console.print(
            f"\n  [{ui.C_ACCENT}]{proveedor:<{ancho}}[/{ui.C_ACCENT}]  "
            f"[{ui.C_OK}]{n:>5}[/{ui.C_OK}] [{ui.C_DIM}]llamada(s) · {pct:.0f}%[/{ui.C_DIM}]"
        )
        for modelo, m in detalle:
            ui.console.print(f"    [{ui.C_DIM}]{m:>5} ×[/{ui.C_DIM}]  {modelo}")
    ui.console.print(
        f"\n  [{ui.C_DIM}]TOTAL: {suma} llamada(s) repartidas entre {len(filas)} "
        f"API(s).[/{ui.C_DIM}]"
    )
    return 0


def _mostrar_diff(workspace: Path) -> int:
    from groq_agent import checkpoint

    puntos = checkpoint.listar(workspace)
    if not puntos:
        ui.print_header("cambios", [("workspace", str(workspace))])
        ui.console.print(
            f"  [{ui.C_DIM}]No hay checkpoints todavia: se crean solos antes de "
            f"cada tarea.[/{ui.C_DIM}]"
        )
        return 0

    ui.print_header(
        "cambios desde el ultimo checkpoint",
        [("workspace", str(workspace)), ("checkpoint", puntos[0].resumen)],
    )
    cambios = checkpoint.diff(workspace)
    if not cambios:
        ui.console.print(f"  [{ui.C_DIM}]Sin cambios.[/{ui.C_DIM}]")
        return 0

    color = {"nuevo": ui.C_OK, "modificado": ui.C_SYSTEM, "borrado": ui.C_FAIL}
    marca = {"nuevo": "+", "modificado": "~", "borrado": "-"}
    for estado, ruta in cambios:
        ui.console.print(
            f"  [{color[estado]}]{marca[estado]}[/{color[estado]}] "
            f"{ui.escape(ruta)}  [{ui.C_DIM}]{estado}[/{ui.C_DIM}]",
            highlight=False,
        )
    ui.console.print(
        f"\n  [{ui.C_DIM}]{len(cambios)} cambio(s) · deshacer con: "
        f"orquestador --undo[/{ui.C_DIM}]"
    )
    return 0


def _deshacer(workspace: Path) -> int:
    from groq_agent import checkpoint

    puntos = checkpoint.listar(workspace)
    if not puntos:
        ui.print_error_corto(
            "No hay checkpoints en este workspace",
            "Se crean solos antes de cada tarea.",
        )
        return 1

    cambios = checkpoint.diff(workspace)
    ui.print_header(
        "deshacer",
        [
            ("workspace", str(workspace)),
            ("volver a", puntos[0].resumen),
            ("cambios a revertir", f"{len(cambios)}"),
        ],
    )
    if not cambios:
        ui.console.print(f"  [{ui.C_DIM}]No hay nada que deshacer.[/{ui.C_DIM}]")
        return 0

    # Restaurar sobreescribe archivos del usuario: se confirma siempre, y
    # con la lista delante. Ningun --yes lo salta.
    elegido = ui.select(
        "¿Restaurar el workspace a ese checkpoint?",
        ["No, cancelar", "Si, restaurar"],
        default=0,
        hints=[
            "no se toca nada",
            f"sobreescribe {len(cambios)} archivo(s); antes se guarda otro checkpoint",
        ],
    )
    if elegido != 1:
        ui.console.print(f"  [{ui.C_DIM}]Cancelado.[/{ui.C_DIM}]")
        return 0

    _, nota = checkpoint.restaurar(workspace)
    ui.print_note(nota)
    return 0




# --- goldentokens ------------------------------------------------------
#
# goldentoken.py vive fuera de este repo (es una herramienta del usuario).
# Se le llama por su interfaz de linea de comandos, no importandolo: asi
# este proyecto no queda atado a su estructura interna, que evoluciona por
# su cuenta.

_GOLDENTOKEN = Path.home() / "Desktop" / "goldentokens" / "goldentoken.py"

_NIVELES = [
    ("safe", "-s", "Solo lo de riesgo bajo: correccion pura, cero degradacion"),
    ("medium", "-m", "Incluye safe + bloquear relecturas y lecturas de archivos enormes"),
    ("dangerous", "-d", "Todo, incluido el reescalado automatico de imagenes "
                        "(el mayor ahorro, con riesgo de perder detalle fino)"),
]


def _goldentokens(argumentos: list[str]) -> int:
    """Ejecuta goldentoken.py heredando la consola.

    Sin capturar la salida: goldentoken imprime tablas y puede preguntar, y
    tragarse eso para reimprimirlo seria peor que dejarlo escribir directo.
    """
    import subprocess

    if not _GOLDENTOKEN.is_file():
        ui.print_error_corto(
            f"No se encontro goldentoken.py en {_GOLDENTOKEN}",
            "Si lo moviste, ajusta _GOLDENTOKEN en groq_agent/cli.py",
        )
        return 1
    try:
        return subprocess.call([sys.executable, str(_GOLDENTOKEN), *argumentos])
    except OSError as exc:
        ui.print_error_corto(exc)
        return 1


def _goldentokens_selector() -> int:
    ui.print_header("goldentokens")
    elegido = ui.select(
        "Nivel de goldentokens",
        [f"{nombre}" for nombre, _, _ in _NIVELES],
        default=1,   # medium: el punto de equilibrio de su propia documentacion
        hints=[desc for _, _, desc in _NIVELES],
    )
    if elegido is None:
        ui.console.print(f"  [{ui.C_DIM}]Cancelado.[/{ui.C_DIM}]")
        return 0
    nombre, flag, _ = _NIVELES[elegido]
    ui.print_note(f"aplicando nivel {nombre}")
    return _goldentokens([flag])



def _listar_comandos() -> int:
    from groq_agent import commands

    guardados = commands.listar()
    ui.print_header("comandos guardados")
    if not guardados:
        ui.console.print(
            f"  [{ui.C_DIM}]Ninguno todavia. Guarda uno con:[/{ui.C_DIM}]\n"
            f'  [{ui.C_DIM}]orquestador --save-command web "hazme una web para $1 en $2"'
            f"[/{ui.C_DIM}]"
        )
        return 0
    for nombre, plantilla in sorted(guardados.items()):
        huecos = commands.placeholders(plantilla)
        sufijo = f"  [{ui.C_DIM}]({', '.join(huecos)})[/{ui.C_DIM}]" if huecos else ""
        ui.console.print(f"  [{ui.C_ACCENT}]{nombre}[/{ui.C_ACCENT}]{sufijo}")
        ui.console.print(f"    [{ui.C_DIM}]{ui.escape(plantilla[:100])}[/{ui.C_DIM}]",
                         highlight=False)
    ui.console.print(
        f"\n  [{ui.C_DIM}]Ejecutar: orquestador --do <nombre> <argumentos>[/{ui.C_DIM}]"
    )
    return 0


def _diagnostico() -> int:
    from groq_agent import doctor

    ui.print_header("chequeo de salud")
    resultados = doctor.diagnosticar(Path(__file__).resolve().parent.parent)

    color = {
        doctor.ESTADO_OK: ui.C_OK,
        doctor.ESTADO_AVISO: ui.C_DIM,
        doctor.ESTADO_FALLO: ui.C_FAIL,
    }
    marca = {doctor.ESTADO_OK: "+", doctor.ESTADO_AVISO: "~", doctor.ESTADO_FALLO: "×"}

    fallos = avisos = 0
    for r in resultados:
        if r["estado"] == doctor.ESTADO_FALLO:
            fallos += 1
        elif r["estado"] == doctor.ESTADO_AVISO:
            avisos += 1
        ui.console.print(
            f"  [{color[r['estado']]}]{marca[r['estado']]}[/{color[r['estado']]}] "
            f"{r['nombre']:<26} [{ui.C_DIM}]{ui.escape(r['detalle'])}[/{ui.C_DIM}]",
            highlight=False,
        )
        if r["arreglo"] and r["estado"] != doctor.ESTADO_OK:
            ui.console.print(
                f"    {'':<26} [{ui.C_DIM}]-> {ui.escape(r['arreglo'])}[/{ui.C_DIM}]",
                highlight=False,
            )

    ui.console.print()
    if fallos:
        ui.console.print(f"[{ui.C_FAIL}]{fallos} fallo(s), {avisos} aviso(s).[/{ui.C_FAIL}]")
        return 1
    if avisos:
        ui.console.print(f"[{ui.C_DIM}]Sin fallos. {avisos} aviso(s).[/{ui.C_DIM}]")
        return 0
    ui.console.print(f"[{ui.C_OK}]Todo en orden.[/{ui.C_OK}]")
    return 0


def _buscar_sesiones(workspace: Path, termino: str) -> int:
    """Busca en el texto de las tareas, no solo en el titulo: uno recuerda
    lo que PIDIO, no como se llamo la sesion."""
    ui.print_header("buscar en sesiones", [("termino", termino)])
    aguja = termino.lower()
    encontradas = []

    for meta in session_store.list_sessions(workspace):
        try:
            datos = session_store.load_session(workspace, meta["session_id"])
        except (FileNotFoundError, ValueError):
            continue
        golpes = [
            turno for turno in datos.get("turns", [])
            if aguja in (turno.get("task", "") + " " + turno.get("result", "")).lower()
        ]
        if golpes:
            encontradas.append((meta, golpes))

    if not encontradas:
        ui.console.print(f"  [{ui.C_DIM}]Sin resultados para '{ui.escape(termino)}'.[/{ui.C_DIM}]")
        return 0

    for meta, golpes in encontradas:
        titulo = session_naming.nombre_para(meta.get("preview", ""))
        ui.console.print(
            f"  [{ui.C_ACCENT}]{ui.escape(titulo)}[/{ui.C_ACCENT}]  "
            f"[{ui.C_DIM}]{meta['session_id']} · {len(golpes)} coincidencia(s)[/{ui.C_DIM}]",
            highlight=False,
        )
        for turno in golpes[:2]:
            ui.console.print(
                f"    [{ui.C_DIM}]{ui.escape(turno.get('task', '')[:80])}[/{ui.C_DIM}]",
                highlight=False,
            )
    ui.console.print(
        f"\n  [{ui.C_DIM}]{len(encontradas)} sesion(es) · retomar con: "
        f"orquestador -r <id>[/{ui.C_DIM}]"
    )
    return 0


def _cargar_env() -> None:
    """Carga el .env del repo en el entorno del proceso.

    Hace falta aca y no solo en orquestador.ps1: lanzado como
    `python -m groq_agent.cli` no hay wrapper que exporte nada, y entonces
    --keys/--config/--check-providers no veian ninguna key y reportaban
    "sin key" para keys que SI estaban puestas - un falso negativo
    especialmente confuso justo en los comandos que existen para
    diagnosticar la configuracion.

    override=False: si una variable ya viene del entorno (porque el wrapper
    la exporto, o porque el usuario la puso a mano para esta corrida), esa
    manda. El .env es el default, no la autoridad.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        load_dotenv(env, override=False)


def main() -> int:
    _cargar_env()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("task", nargs="?", help="Tarea a resolver. Si se omite, entra en modo interactivo.")
    parser.add_argument(
        "--skill", default=None,
        help="Forzar un skill (ver --list-skills). Sin esto: se auto-detecta por tarea.",
    )
    parser.add_argument(
        "--workspace", default=str(Path.home() / "Desktop" / "workspace"),
        help=(
            "Carpeta a la que el modelo tiene acceso de lectura/escritura - el limite de "
            "seguridad lo fija quien lanza el comando, no el modelo (ver groq_agent/safety.py). "
            "Por defecto: una carpeta 'workspace' en el Escritorio (se crea sola si no existe)."
        ),
    )
    parser.add_argument(
        "--model", default=None,
        help="Forzar un modelo del proveedor. Sin esto: se elige automaticamente segun el tamaño que le corresponde al skill.",
    )
    parser.add_argument("--yes", action="store_true", help="No pedir confirmacion antes de escribir/ejecutar.")
    parser.add_argument(
        "--router", choices=["heuristic", "llm"], default="llm",
        help=(
            "Motor de clasificacion en modo auto (ignorado si usas --skill). "
            "'llm' (default): un modelo chico real (nivel router-tiny) clasifica y "
            "entiende si un mensaje es continuacion de la conversacion en curso o una "
            "tarea nueva - cuesta una llamada extra al proveedor por tarea (fraccion de "
            "centavo). 'heuristic': regex, gratis e instantaneo, pero no distingue "
            "continuacion de tarea nueva salvo por si el texto matchea algun patron."
        ),
    )
    parser.add_argument(
        "--max-turns", type=int, default=None,
        help=(
            "Presupuesto de turnos (ida y vuelta modelo<->herramientas) por tarea. Por "
            "defecto SIN LIMITE - la tarea sigue hasta que el modelo termine, por mas "
            "compleja que sea (sitio web con varias secciones + busqueda de imagenes + "
            "verificacion). Pasa un numero solo si quieres un techo de costo/tiempo duro "
            "(ej. uso automatizado)."
        ),
    )
    parser.add_argument("--list-skills", action="store_true")
    parser.add_argument("--list-models", action="store_true")
    parser.add_argument(
        "-cfg", "--config", action="store_true",
        help=(
            "Pedir las API keys que falten, una a una, con el enlace de registro de "
            "cada proveedor. No vuelve a preguntar por las que ya estan."
        ),
    )
    parser.add_argument(
        "--keys", "--list-keys", dest="list_keys", action="store_true",
        help=(
            "Ver que API keys hay configuradas (enmascaradas) y cuales son "
            "sospechosas, sin abrir el .env a mano."
        ),
    )
    parser.add_argument(
        "-acc", "--account", action="store_true",
        help=(
            "Iniciar sesion en tu cuenta de NovaChat e importar sus API keys al .env "
            "(suma las nuevas, sin duplicar). Guarda la sesion para que se sincronice "
            "sola en cada arranque."
        ),
    )
    parser.add_argument(
        "-gldn", "--goldentokens", action="store_true", dest="goldentokens",
        help=(
            "Configurar goldentokens: abre un selector para elegir el nivel "
            "(safe / medium / dangerous) y lo aplica."
        ),
    )
    parser.add_argument(
        "-gldn-sts", "--goldentokens-stats", action="store_true",
        dest="goldentokens_stats",
        help="Estadisticas de goldentokens (equivale a: goldentoken -sts).",
    )
    parser.add_argument(
        "--save-command", metavar="NOMBRE", default=None, dest="save_command",
        help=(
            "Guardar la tarea como plantilla reutilizable. Usa $ARGUMENTS o "
            "$1 $2 como huecos. Ej: orquestador --save-command web "
            "\"hazme una web para $1 en $2\""
        ),
    )
    parser.add_argument(
        "--do", metavar="NOMBRE", default=None,
        help="Ejecutar un comando guardado. Los argumentos van detras.",
    )
    parser.add_argument(
        "--commands", action="store_true",
        help="Listar los comandos guardados.",
    )
    parser.add_argument(
        "--forget-command", metavar="NOMBRE", default=None, dest="forget_command",
        help="Borrar un comando guardado.",
    )
    parser.add_argument(
        "--doctor", action="store_true",
        help=(
            "Chequeo de salud: .env, API keys, cobertura de proveedores, "
            "playwright, base de casos dificiles y skills. Sin llamadas a modelos."
        ),
    )
    parser.add_argument(
        "--search", metavar="TERMINO", default=None,
        help="Buscar entre las sesiones de este workspace por texto.",
    )
    parser.add_argument(
        "--diff", action="store_true",
        help="Que cambio en el workspace desde el ultimo checkpoint.",
    )
    parser.add_argument(
        "--undo", action="store_true",
        help=(
            "Deshacer: devolver el workspace al ultimo checkpoint. Crea otro "
            "checkpoint antes, asi que tambien se puede deshacer el undo."
        ),
    )
    parser.add_argument(
        "--usage", action="store_true",
        help=(
            "Gasto de tokens por sesion de este workspace: entrada, salida, "
            "cuanto vino de cache y que modelo lo consumio."
        ),
    )
    parser.add_argument(
        "-api", "--api", action="store_true", dest="api",
        help=(
            "Cuantas llamadas se han hecho a cada API (proveedor) en este "
            "workspace, con el desglose por modelo."
        ),
    )
    parser.add_argument(
        "--check-providers", nargs="?", const="__all__", default=None,
        metavar="TIER",
        help=(
            "Probar de verdad cada candidato con una tool call minima y decir cual "
            "esta vivo. Sin valor: todos los tiers. Con un tier: solo ese."
        ),
    )
    parser.add_argument(
        "-c", "--continue", dest="continue_session", action="store_true",
        help="Continuar la sesion mas reciente de este workspace.",
    )
    parser.add_argument(
        "-r", "--resume", nargs="?", const="__pick__", default=None,
        help="Reanudar una sesion. Sin valor: elegir de una lista. Con un ID: la reanuda directo.",
    )
    args = parser.parse_args()

    if args.list_skills:
        from groq_agent.skills_loader import list_available_skills

        for name in list_available_skills():
            print(name)
        return 0

    # --config y --check-providers van ANTES de construir el cliente: son
    # justo los comandos que hay que poder correr cuando todavia no hay
    # ninguna key (construir el cliente ahi falla, y con razon).
    if args.config:
        from groq_agent.config_wizard import run_config
        return run_config()

    if args.list_keys:
        from groq_agent.config_wizard import run_list_keys
        return run_list_keys()

    if args.account:
        import subprocess
        from pathlib import Path as _Path
        script = _Path(__file__).resolve().parent.parent / "scripts" / "sync_novachat_keys.py"
        return subprocess.call([sys.executable, str(script), "--login"])

    if args.commands:
        return _listar_comandos()

    if args.save_command:
        from groq_agent import commands

        if not args.task:
            ui.print_error_corto(
                "Falta la plantilla",
                'Ej: orquestador --save-command web "hazme una web para $1"',
            )
            return 1
        ui.print_note(commands.guardar(args.save_command, args.task))
        return 0

    if args.forget_command:
        from groq_agent import commands

        ui.print_note(commands.borrar(args.forget_command))
        return 0

    if args.goldentokens_stats:
        return _goldentokens(["-sts"])

    if args.goldentokens:
        return _goldentokens_selector()

    if args.doctor:
        return _diagnostico()

    if args.search:
        return _buscar_sesiones(Path(args.workspace).resolve(), args.search)

    if args.usage:
        return _mostrar_gasto(Path(args.workspace).resolve())

    if args.api:
        return _mostrar_api(Path(args.workspace).resolve())

    if args.diff:
        return _mostrar_diff(Path(args.workspace).resolve())

    if args.undo:
        return _deshacer(Path(args.workspace).resolve())

    if args.check_providers is not None:
        from groq_agent.config_wizard import run_check
        tier = None if args.check_providers == "__all__" else args.check_providers
        return run_check(tier)

    # A partir de aca si se ejecuta una tarea: ahora si hace falta el
    # agente completo.
    _ag = _importar_agente()
    match_domain_or_none = _ag["match_domain_or_none"]
    run_agent = _ag["run_agent"]
    auto_route = _ag["auto_route"]
    auto_route_llm = _ag["auto_route_llm"]
    CHARLA = _ag["CHARLA"]
    model_for_specialist = _ag["model_for_specialist"]
    build_system_prompt = _ag["build_system_prompt"]
    ToolExecutor = _ag["ToolExecutor"]

    # httpx cuesta ~180ms y solo hace falta para hablar con un proveedor:
    # --help, --commands o --keys no lo necesitan.
    from groq_agent.groq_client import GroqClient

    try:
        if args.list_models:
            client = GroqClient()
            for model_id in client.list_models():
                print(model_id)
            return 0

        workspace = Path(args.workspace).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        client = GroqClient()
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    executor = ToolExecutor(workspace, auto_yes=args.yes, client=client)
    # --yes arranca en modo AUTO (la barra lo refleja); sin el, en CONFIRMA. El
    # usuario lo alterna despues con shift+tab en el prompt.
    ui.set_modo_auto(bool(args.yes))
    # Sembrar la barra de estado apuntando al objeto Gasto VIVO del cliente:
    # como se muta en su sitio, las cifras suben solas en la barra sin que
    # nadie tenga que volver a llamar a set_estado. Asi la barra existe ya
    # en el primer prompt, en vez de aparecer tras la primera tarea.
    ui.set_estado(0, client.gasto)

    session_id, conversations, turns, created_at = _load_or_start_session(workspace, args)
    if session_id is None:
        return 1

    auto_mode = args.skill is None
    # Todo el estado de arranque va DENTRO del splash, en una sola tabla de
    # etiqueta+valor. Antes salian tres lineas de prosa debajo ("Modo:
    # auto...", "Las acciones que escriben...", "Modo interactivo...") que
    # decian lo mismo ocupando el triple y ensuciaban la pantalla que
    # acabamos de limpiar.
    modo = (
        "auto (skill y modelo por tarea)" if auto_mode
        else f"{args.skill} · {args.model or model_for_specialist(args.skill)}"
    )
    ui.print_splash(
        "terminal agentica",
        [
            ("workspace", str(workspace)),
            ("sesion", session_id),
            ("proveedores", f"{len(prov.configured_providers())} activos"),
            ("modo", modo),
            ("escrituras", "sin confirmar (--yes)" if args.yes else "piden confirmacion"),
        ],
        pie=(
            None if args.task
            else "escribe tu tarea · ESC para parar al modelo · 'salir' para terminar"
        ),
    )

    # Al retomar una sesion, reimprime lo anterior: continuar "donde lo
    # dejaste", no una pantalla en blanco. Solo con TTY (en una tuberia
    # ensuciaria la salida) y solo si hay historial (una sesion nueva no).
    if turns and sys.stdin.isatty():
        _replay_sesion(conversations, turns)

    if args.do:
        from groq_agent import commands

        # Los argumentos del comando son lo que venga en `task`, partido por
        # espacios. Es lo mas parecido a como se invoca un alias de shell.
        expandido = commands.expandir(args.do, (args.task or "").split())
        if expandido is None:
            ui.print_error_corto(
                f"No existe el comando '{args.do}'",
                "Ver los guardados con: orquestador --commands",
            )
            return 1
        ui.print_note(f"comando '{args.do}' -> {expandido[:70]}")
        tasks = [expandido]
    elif args.task:
        tasks = [args.task]
    else:
        tasks = _interactive_tasks()

    last_specialist: str | None = None
    last_domain: str | None = None
    last_model: str | None = None
    last_task: str | None = None
    # ¿El hilo en curso es una charla (preguntas que se contestan buscando)?
    # Si lo es y NO hay especialista real activo, la charla es "pegajosa": el
    # siguiente mensaje se clasifica con ese contexto para que un seguimiento
    # ('y del nuevo?', 'cual es la fuente') siga en la charla -que busca- en
    # vez de saltar a un especialista que responde de memoria.
    en_charla = False
    charla_task: str | None = None
    hubo_parada = False
    # Lo comparte el generador de tareas para saber si, al acabarse las de
    # arranque, hay que devolverle el turno al usuario en vez de salir.
    estado_parada: dict[str, bool] = {"parada": False}

    def atender_charla(task: str) -> None:
        """Contesta en modo charla CON memoria: pasa el historial guardado,
        recoge el actualizado y persiste la sesion igual que un especialista.
        Asi 'que dia es hoy' y el mensaje siguiente comparten conversacion."""
        nonlocal en_charla, charla_task
        previo = conversations.get(_CLAVE_CHARLA)
        conversations[_CLAVE_CHARLA] = _responder_charla(
            client, executor, run_agent, task, existing_messages=previo
        )
        turns.append({
            "task": task, "specialist": _CLAVE_CHARLA, "model": DEFAULT_TIER,
            "domain": None, "result": "",
        })
        session_store.save_session(workspace, session_id, conversations, turns, created_at)
        # Deja el hilo marcado como charla y guarda de que iba, para dar
        # contexto al clasificador del proximo mensaje.
        en_charla = True
        charla_task = task
        ui.parar_cronometro()  # llego la respuesta de charla: congela el tiempo

    for task in _tareas_de_la_sesion(tasks, estado_parada, interactivo=not args.task):
        ui.print_task_header(task)
        # Cronometro prompt -> resultado: arranca al recibir el prompt y se
        # congela al llegar el resultado (ver los parar_cronometro de abajo).
        ui.iniciar_cronometro()
        # Modo de escritura elegido con shift+tab en el prompt: se aplica a esta
        # tarea. AUTO -> las escrituras dentro del workspace no piden confirmar.
        executor.session_accept_all = ui.modo_auto()

        if hubo_parada:
            task = interrupt.envolver_tras_parada(task)
            hubo_parada = False

        # Instantanea ANTES de tocar nada: el agente escribe archivos
        # reales y hasta ahora no habia forma de volver atras. Falla en
        # silencio a proposito (checkpoint.crear devuelve None): una red de
        # seguridad que falla no puede impedir hacer el trabajo.
        _checkpoint.crear(workspace, task)

        # @archivo: inyecta los ficheros que el usuario referencio, ya
        # leidos. Cada read_file evitado es un turno completo del
        # presupuesto, y este sistema reenvia la conversacion entera en
        # cada turno.
        task, notas_refs = prompt_refs.expandir(task, workspace)
        for nota in notas_refs:
            ui.print_note(nota)

        # Si el mensaje es una pregunta que no toca archivos, no hace falta
        # el skill completo (hasta 44K chars) ni las herramientas (27K):
        # ~19.000 tokens para responder tres frases. Se atiende con un
        # prompt minimo y cero tools.
        #
        # QUIEN DECIDE ESO: con el router LLM, el MODELO - es una etiqueta
        # mas de la misma llamada que ya elige el dominio, asi que no
        # cuesta ni una llamada ni un token extra, y entiende la intencion
        # en vez de reconocer palabras. La regex de prompt_classifier queda
        # solo como red para `--router heuristic` y para cuando la llamada
        # al router falla: una lista de palabras no entiende una errata
        # ("qie dices?" acababa en el especialista de javascript) ni un
        # saludo que no este escrito en la lista.
        usa_router_llm = auto_mode and args.router == "llm"
        if not usa_router_llm and not prompt_classifier.necesita_herramientas(task):
            atender_charla(task)
            continue

        if auto_mode:
            if args.router == "llm":
                # Si venimos de una charla y no hay especialista real activo,
                # se clasifica CON el contexto de la charla (CHARLA + de que
                # iba) para que un seguimiento se quede en la charla en vez de
                # reclasificarse en frio y caer en un especialista al azar.
                charla_pegajosa = en_charla and last_specialist is None
                ctx_specialist = CHARLA if charla_pegajosa else last_specialist
                ctx_task = charla_task if charla_pegajosa else last_task
                try:
                    routed = auto_route_llm(
                        client, task, current_specialist=ctx_specialist, last_task=ctx_task
                    )
                except RuntimeError as exc:
                    ui.print_note(f"router LLM fallo ({exc}) - usando el heuristico para esta tarea")
                    # Sin router no hay quien juzgue la intencion: aca si
                    # vale la regex, que imperfecta es mejor que cargar
                    # 19.000 tokens de herramientas para un "hola".
                    if not prompt_classifier.necesita_herramientas(task):
                        atender_charla(task)
                        continue
                    specialist, auto_model, domain = auto_route(task)
                    model = args.model or auto_model
                    ui.print_routing("auto-heuristic-fallback", specialist, model, domain)
                    last_task = task
                    en_charla = False
                else:
                    if routed == CHARLA:
                        # El modelo entendio que no se le pide trabajo sobre
                        # archivos. No se toca last_specialist/last_task: una
                        # charla en medio de una tarea no borra el tema.
                        atender_charla(task)
                        continue
                    if routed is None and charla_pegajosa:
                        # Seguimiento de la charla ('y del nuevo?', 'cual es
                        # la fuente'): sigue en la charla, que BUSCA, en vez
                        # de responder de memoria desde un especialista.
                        ui.print_note("continuacion de la charla - sigue buscando")
                        atender_charla(task)
                        continue
                    if routed is None:
                        # El router entendio que esto es continuacion de
                        # la conversacion en curso (saludo, "si"/"ok",
                        # aclaracion), no una tarea nueva - seguir con el
                        # mismo especialista en vez de reclasificar.
                        specialist, domain, model = last_specialist, last_domain, last_model
                        ui.print_note(f"continuacion de la conversacion - sigue {specialist}")
                    else:
                        specialist, auto_model, domain = routed
                        model = args.model or auto_model
                        ui.print_routing("auto-llm", specialist, model, domain)
                        # Tarea NUEVA -> se convierte en el "tema" que el
                        # clasificador usara de contexto para juzgar
                        # continuacion en los proximos mensajes (ver
                        # auto_router._classifier_system_prompt). Si esto
                        # fue una continuacion (routed is None, rama de
                        # arriba), last_task NO se toca - el tema sigue
                        # siendo la tarea original, no el mensaje corto de
                        # seguimiento.
                        last_task = task
                        en_charla = False  # se abrio una tarea real: fin del hilo de charla
            else:
                # Heuristico puro (regex): no entiende continuacion vs
                # tarea nueva por si solo, asi que se aplica la misma
                # idea de forma mas cruda - si el texto no matchea NINGUN
                # patron de dominio (saludo, "si"/"ok", etc.) se asume
                # continuacion en vez de reiniciar con python-specialist
                # por default.
                if match_domain_or_none(task) is None and last_specialist is not None:
                    specialist, domain, model = last_specialist, last_domain, last_model
                    ui.print_note(f"sin señal clara de dominio - continuando con {specialist}")
                else:
                    specialist, auto_model, domain = auto_route(task)
                    model = args.model or auto_model
                    ui.print_routing("auto", specialist, model, domain)
                    last_task = task
                    en_charla = False  # tarea real: fin del hilo de charla
            last_specialist, last_domain, last_model = specialist, domain, model
        else:
            specialist = args.skill
            domain = None
            model = args.model or model_for_specialist(specialist)
            ui.print_fixed_route(specialist, model)

        try:
            system_prompt = build_system_prompt(specialist)
        except FileNotFoundError as exc:
            print(f"Error: {exc}")
            ui.parar_cronometro()
            continue

        # El executor se crea una vez por sesion, pero en modo auto el
        # especialista cambia por tarea: hay que decirle cual esta activo o
        # load_skill_section serviria secciones del skill anterior.
        executor.skill_name = specialist

        existing_messages = conversations.get(specialist)
        # Si este especialista arranca de cero en esta sesion (nunca hablo
        # con el) pero YA hubo una conversacion previa con otro, ese
        # especialista nuevo quedaria totalmente a ciegas de que se venia
        # haciendo - no hay ningun "resumen" automatico entre
        # conversaciones aisladas por especialista. Se compensa
        # inyectando una nota breve con la ultima tarea+resultado real
        # (de 'turns', el log de toda la sesion) en el mensaje que ve el
        # modelo, sin ensuciar el 'task' tal cual lo escribio el usuario
        # (eso se guarda sin el prefijo en turns/session_store).
        user_task = task
        # Se ignora un turno de charla como "previo": su result queda vacio y
        # referenciar '__charla__' solo ensuciaria el mensaje del especialista.
        turnos_reales = [t for t in turns if t["specialist"] != _CLAVE_CHARLA]
        if existing_messages is None and turnos_reales and turnos_reales[-1]["specialist"] != specialist:
            previo = turnos_reales[-1]
            user_task = (
                f"[Contexto: en esta misma sesion, justo antes, se hablo con "
                f"'{previo['specialist']}' sobre: \"{previo['task'][:200]}\" - "
                f"resultado de esa conversacion: \"{previo['result'][:300]}\"]\n\n{task}"
            )
        try:
            interrupt.empezar_a_vigilar()
            result, updated_messages = run_agent(
                client=client,
                executor=executor,
                system_prompt=system_prompt,
                user_task=user_task,
                model=model,
                max_turns=args.max_turns,
                existing_messages=existing_messages,
                # Solo las herramientas de ESTE especialista: el catalogo
                # entero son ~7.700 tokens por llamada, y un especialista
                # de codigo no puede hacer nada con `search_images` ni con
                # `fetch_menu_and_reviews_from_maps`. Ver
                # config/tool_sets.yaml.
                tool_schemas=_ag["schemas_for"](specialist),
            )
        except RuntimeError as exc:
            # Errores de la API del proveedor (rate limit, modelo caido,
            # etc.) no deberian tumbar toda la sesion interactiva - se
            # reportan y se sigue con la proxima tarea (sin guardar este turno).
            ui.console.print(f"\n[{ui.C_FAIL}]Error llamando al proveedor: {ui.escape(str(exc))}[/{ui.C_FAIL}]\n")
            ui.parar_cronometro()
            continue
        finally:
            # SIEMPRE, tambien por el `continue` de arriba. El vigilante de
            # ESC consume teclas del terminal: si se queda vivo mientras el
            # usuario escribe, se come sus pulsaciones y el prompt recibe
            # una entrada vacia o rota - que es como se veia esto desde
            # fuera, como si el programa se hubiera cerrado solo.
            interrupt.dejar_de_vigilar()

        conversations[specialist] = updated_messages
        turns.append({
            "task": task, "specialist": specialist, "model": model,
            "domain": domain, "result": result[:500],
        })
        session_store.save_session(workspace, session_id, conversations, turns, created_at)
        # Si el usuario paro, el SIGUIENTE mensaje suyo lleva una nota
        # explicandole al modelo que lo pararon, para que valore si su plan
        # anterior sigue valiendo en vez de retomarlo sin mas.
        hubo_parada = interrupt.pedido()
        estado_parada["parada"] = hubo_parada
        interrupt.limpiar()

        ui.parar_cronometro()  # llego el resultado del especialista: congela el tiempo
        ui.render_result(result)
        # Persistir despues de CADA tarea, no al salir: una sesion que se
        # corta por un error igual deja registrado lo que gasto.
        if getattr(client, "gasto", None) is not None:
            from groq_agent import usage as _usage

            _usage.guardar(workspace, session_id, client.gasto)
            # Deja el estado listo para que el prompt lo muestre encima:
            # asi tokens y contexto estan siempre a la vista.
            from groq_agent import context_manager as _cm

            ui.set_estado(_cm.tamano(updated_messages), client.gasto)

    # Suelta la barra fija por si quedo abierta (camino --task, que no pasa por
    # boxed_input): sin esto su Live seguiria activo al imprimir la foto final.
    ui.cerrar_barra_fija()
    # Al cerrar la sesion si vale una foto fija del gasto: la barra viva se
    # va con el prompt, y esto es lo unico que queda en el historial.
    if getattr(client, "gasto", None) is not None and client.gasto.llamadas:
        from groq_agent import context_manager as _cm

        # De `conversations`, no de una variable del bucle: si la sesion se
        # cierra sin haber ejecutado ninguna tarea, esa variable no existe.
        ultimo = max((v for v in conversations.values()), key=len, default=[])
        ui._print_status_bar(_cm.tamano(ultimo), client.gasto)

    return 0


def _texto_msg(m: dict) -> str:
    """Texto de un mensaje del historial. `content` puede venir como lista de
    partes (algunos proveedores), no solo como string."""
    c = m.get("content")
    if isinstance(c, list):
        return " ".join(
            p.get("text", "") for p in c if isinstance(p, dict)
        ).strip()
    return (c or "").strip()


def _replay_sesion(conversations: dict, turns: list) -> None:
    """Reimprime la conversacion previa al retomar una sesion, para seguir
    'donde lo dejaste' en vez de arrancar en una pantalla vacia.

    El orden cronologico real es el de `turns` (los hilos por especialista de
    `conversations` no tienen un orden global entre si). El texto COMPLETO de
    cada respuesta se saca del hilo de SU especialista -contando las respuestas
    ya mostradas de ese especialista- y se cae al `result` guardado del turno
    si ahi no se encuentra (respuesta vacia, hilo podado, etc.)."""
    reales = [t for t in turns if t.get("task")]
    if not reales:
        return
    ui.print_note(f"retomando la sesion · {len(reales)} mensaje(s) anteriores")
    consumidos: dict[str, int] = {}
    for turn in reales:
        spec = turn.get("specialist")
        ui.print_task_header(turn["task"])
        asistentes = [
            _texto_msg(m) for m in conversations.get(spec, [])
            if m.get("role") == "assistant" and _texto_msg(m)
        ]
        i = consumidos.get(spec, 0)
        consumidos[spec] = i + 1
        respuesta = asistentes[i] if i < len(asistentes) else (turn.get("result") or "")
        if respuesta:
            ui.render_result(respuesta)
    ui.print_note("— fin del historial · sigue donde lo dejaste —")


def _etiqueta_sesion(s: dict) -> str:
    """El nombre que se ve en el selector: el puesto a mano si lo hay, si no
    el auto-generado del primer prompt."""
    return s.get("name") or session_naming.nombre_para(s["preview"]) or "(sin tareas)"


def _elegir_sesion_interactiva(workspace: Path) -> str | None:
    """Selector de `orquestador -r`. ↑↓ moverse, enter retomar, → abrir el
    submenu de esa sesion (ver uso / renombrar / borrar). Devuelve el
    session_id elegido, o None si se cancela o ya no quedan sesiones."""
    while True:
        sessions = session_store.list_sessions(workspace)
        if not sessions:
            ui.print_note("No hay sesiones guardadas en este workspace.")
            return None
        # Selector de flechas en vez de "escribe un numero": no hay numero
        # que teclear mal, y al elegir el menu se borra solo.
        elegido = ui.select(
            "Sesiones de este workspace",
            [_etiqueta_sesion(s) for s in sessions],
            hints=[f"{s['updated_at']} · {s['turn_count']} tarea(s)" for s in sessions],
            submenu_on_right=True,
        )
        if elegido is None:
            return None
        if isinstance(elegido, tuple):    # ("__right__", i): submenu de esa sesion
            _submenu_sesion(workspace, sessions[elegido[1]])
            continue                      # re-listar: pudo renombrarse o borrarse
        return sessions[elegido]["session_id"]


def _submenu_sesion(workspace: Path, sesion: dict) -> None:
    """El submenu de una sesion: ver su uso de tokens, cambiarle el nombre, o
    borrarla. Se abre con la flecha derecha desde el selector."""
    from groq_agent import usage

    sid = sesion["session_id"]
    nombre = _etiqueta_sesion(sesion)
    # Borrar primero: es la accion mas pedida sobre una sesion vieja, y asi el
    # flujo rapido con la flecha (→ abre este menu, enter sobre Borrar, → confirma)
    # cae sobre ella.
    accion = ui.select(
        f"Sesion: {nombre}",
        ["Borrar", "Ver uso (tokens)", "Cambiar nombre", "Volver"],
        hints=["borra la sesion (no se deshace)", "gasto de esta sesion",
               "el nombre que se ve en la lista", ""],
        right_is_enter=True,   # → elige aqui tambien (cadena de flechas)
    )
    if accion == 0:
        # Si por defecto y confirmable con → (flujo rapido); Esc/No cancelan.
        if ui.confirmar(f"Borrar '{nombre}'? No se puede deshacer.",
                        si="Si, borrar", no="No", default_si=True, right_is_enter=True):
            session_store.delete_session(workspace, sid)
            usage.borrar(workspace, sid)
            ui.print_note(f"Sesion borrada: {nombre}")
    elif accion == 1:
        ui.print_usage_panel(f"{nombre}  ({sid})", usage.cargar(workspace, sid))
        ui.pausa_enter()
    elif accion == 2:
        nuevo = ui.leer_linea("nuevo nombre", sesion.get("name") or "")
        if nuevo and nuevo.strip():
            session_store.rename_session(workspace, sid, nuevo)
            ui.print_note(f"Renombrada a: {nuevo.strip()}")


def _load_or_start_session(
    workspace: Path, args: argparse.Namespace
) -> tuple[str | None, dict[str, list[dict]], list[dict], str]:
    """Decide si arrancar una sesion nueva o retomar una existente segun
    -c/-r. Devuelve (session_id, conversations, turns, created_at); el
    id es None si -r no encontro nada valido (main() debe salir)."""
    now = datetime.now().isoformat(timespec="seconds")

    if args.continue_session:
        latest = session_store.find_latest_session_id(workspace)
        if latest is None:
            print("No hay ninguna sesion previa en este workspace - arrancando una nueva.")
            return session_store.new_session_id(), {}, [], now
        data = session_store.load_session(workspace, latest)
        print(f"Continuando la sesion mas reciente ({len(data.get('turns', []))} tareas previas).")
        return data["session_id"], data.get("conversations", {}), data.get("turns", []), data.get("created_at", now)

    if args.resume is not None:
        session_id = args.resume
        if session_id == "__pick__":
            session_id = _elegir_sesion_interactiva(workspace)
            if session_id is None:
                return None, {}, [], now
        try:
            data = session_store.load_session(workspace, session_id)
        except FileNotFoundError as exc:
            ui.print_error_corto(exc, "Comprueba el id con: orquestador -r")
            return None, {}, [], now
        ui.print_note(f"Sesion {data['session_id']} · {len(data.get('turns', []))} tareas previas")
        return data["session_id"], data.get("conversations", {}), data.get("turns", []), data.get("created_at", now)

    return session_store.new_session_id(), {}, [], now


def _tareas_de_la_sesion(iniciales, estado: dict, interactivo: bool):
    """Las tareas de arranque y, si al usuario le cortaron una con ESC, el
    turno de vuelta.

    ESC promete "paro y sigues tú". Con una tarea lanzada desde la linea de
    comandos (`orquestador "hazme una web"`) esa lista tiene UN elemento:
    al pararla se acababa, se acababa el bucle y el programa se cerraba
    entero - justo lo contrario de lo prometido. Si hubo parada, se entra
    en modo interactivo en vez de salir."""
    yield from iniciales
    if interactivo:
        # Ya venia del prompt: si ese generador termino es porque el
        # usuario escribio "salir" o una linea vacia, y eso si es irse.
        return
    while estado.get("parada"):
        estado["parada"] = False
        yield from _interactive_tasks()


def _interactive_tasks():
    while True:
        try:
            task = ui.boxed_input("tarea").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if not task or task.lower() in ("salir", "exit", "quit"):
            return
        yield task


if __name__ == "__main__":
    sys.exit(main())
