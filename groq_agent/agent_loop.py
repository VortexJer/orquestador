"""Loop agentico tipo Claude Code: le da al modelo (servido por
OpenRouter u otro endpoint OpenAI-compatible) un turno, ejecuta las
tool calls que pida (con confirmacion previa para las que escriben/
ejecutan algo, salvo --yes), le devuelve el resultado, y repite hasta
que responda con texto final o (opcionalmente) se llegue a max_turns.
"""
from __future__ import annotations

import itertools
import json
import os
import re
from pathlib import Path

from groq_agent import ui
from groq_agent import interrupt
from groq_agent.context_manager import gestionar as gestionar_contexto
from groq_agent.groq_client import GroqClient, ToolCallFailedError
from groq_agent.tools import (
    TOOL_SCHEMAS,
    WRITE_TOOLS,
    ToolExecutor,
    escribe_fuera_del_workspace,
)

_MAX_TOOL_CALL_RETRIES = 2

# EXPERIMENTO (a peticion del usuario): la poda de contexto se sospecha de
# provocar el thrashing de edit_file - al recortar el historial se lleva el
# CONTENIDO del archivo que se esta editando, y el modelo pasa a editar a
# ciegas (old_text que ya no matchea -> 16 fallos seguidos). Aqui se puede
# apagar sin tocar el modulo de contexto. Por defecto DESACTIVADA para la
# prueba; para volver a activarla, pon ORQUESTADOR_PODA=1 (o cambia el default
# a "1" cuando el diagnostico termine).
_PODA_ACTIVA = os.environ.get("ORQUESTADOR_PODA", "0") != "0"

# Cuantos fallos seguidos de la MISMA herramienta hacen falta para mandar la
# correccion. Dos es demasiado pronto (el segundo intento suele acertar);
# cuatro ya son cuatro turnos tirados.
_FALLOS_PARA_CORREGIR = 3

# Veces que la MISMA llamada puede dar el MISMO resultado antes de
# darlo por bucle. Tres es suficiente para distinguir un reintento
# legitimo (dos) de estar atascado.
_REPETICIONES_PARA_CORTAR = 3


def _es_error(resultado: str) -> bool:
    return isinstance(resultado, str) and resultado.lstrip().startswith("ERROR")


# Marcadores de que el modelo escribio un ARTEFACTO de oficina (deck, doc,
# hoja) como TEXTO en vez de llamar a la herramienta que lo genera. Cuando
# pasa, el usuario se queda con un volcado de JSON y sin archivo (bug visto
# en vivo con presentaciones: la salida era el JSON del deck, no el .pptx,
# "y pasa todo el rato"). Se le da UN empujon para que haga la tool call.
_MARCADOR_OFFICE = re.compile(
    r'"(?:slides|sheets|sections)"\s*:\s*\[|'
    r'"type"\s*:\s*"(?:heading|paragraph|list)"|'
    r'\bgenerate_(?:pptx|docx|xlsx)\s*\(|\bcreate_(?:presentation|docx)\s*\(',
    re.I,
)

# Nombres de las tools que ENTREGAN un archivo de oficina. El empujon solo
# tiene sentido si alguna esta disponible en esta sesion: asi una tarea de
# codigo que devuelva un JSON con "sections"/"sheets" no dispara un falso
# empujon (no hay ninguna tool que llamar).
_TOOLS_OFFICE = {"generate_pptx", "generate_docx", "generate_xlsx"}

_NUDGE_OFFICE = (
    "No has generado el archivo: terminaste tu turno sin llamar a la herramienta que "
    "lo crea (lo escribiste como texto, o preguntaste donde guardarlo, o prometiste "
    "hacerlo). El usuario se queda SIN archivo. NO preguntes la ruta ni lo pegues como "
    "texto: llama AHORA a la herramienta que corresponde -generate_pptx para una "
    "presentacion, generate_docx para un Word, generate_xlsx para un Excel- como una "
    "tool call DE VERDAD (function-calling), con el contenido en su argumento (slides / "
    "sections / sheets) y un `path` por defecto en el workspace con un nombre "
    "descriptivo y la extension correcta."
)

# Los especialistas cuya ENTREGA es un archivo generado: si terminan sin
# haberlo creado (lo volcaron como texto, preguntaron la ruta, o prometieron y
# pararon), hay que empujarles a hacer la tool call. office-email NO esta: su
# entrega es texto, aunque tenga tools de oficina disponibles.
_SKILLS_GENERAN_ARCHIVO = {
    "office-word-specialist",
    "office-spreadsheet-specialist",
    "office-presentation-specialist",
}
_EXT_OFFICE = {".docx", ".xlsx", ".pptx"}


def _genero_archivo_office(executor: object) -> bool:
    """True si en esta sesion ya se creo algun .docx/.xlsx/.pptx."""
    tocados = getattr(executor, "archivos_tocados", None) or {}
    return any(
        estado == "creado" and Path(ruta).suffix.lower() in _EXT_OFFICE
        for ruta, estado in tocados.items()
    )


# El usuario pidio GUARDAR/CREAR un archivo. Se ancla en el VERBO de guardar/
# crear-archivo, no en un nombre suelto: "explicame utils.py" menciona un
# archivo pero no pide escribirlo, y no debe disparar el empujon.
_PIDE_GUARDAR_ARCHIVO = re.compile(
    r"\bgu[aá]rda(?:lo|la|los|las|me|r)?\b"
    r"|\b(?:cr(?:e[ae]|ear)|gener[ae]|escrib(?:e|ir|elo|ela|eme))\s+"
    r"(?:un |una |el |la )?(?:archivo|fichero|script|dockerfile|makefile)\b"
    r"|\ben\s+(?:el\s+|un\s+)?(?:archivo|fichero)\b"
    r"|\b(?:dockerfile|makefile)\b",
    re.I,
)
_NUDGE_ARCHIVO = (
    "Pediste GUARDAR el contenido en un archivo, pero terminaste tu turno sin escribirlo "
    "(lo dejaste como texto en el chat, o preguntaste donde guardarlo, o prometiste "
    "hacerlo). Un archivo descrito en el chat pero no escrito NO existe en el disco. Llama "
    "AHORA a write_file con el `path` pedido (por defecto en el workspace, con un nombre "
    "descriptivo) y el contenido completo. No preguntes la ruta ni vuelvas a pegar el "
    "contenido como texto."
)


def _tiene_write_file(schemas: list[dict] | None) -> bool:
    return any(
        (t.get("function") or {}).get("name") == "write_file" for t in (schemas or [])
    )


# Tools de ORQUESTACION cuyos nombres casi nunca aparecen en codigo/prosa normal
# (a diferencia de write_file/read_file/edit_file/run_check, que si). Un modelo
# que las NARRA como texto -`load_skill_section(migrations: {...})`- en vez de
# emitir function-calling deja la sesion sin hacer nada. Visto en vivo con
# web-builder-large cuando la cabeza de su cadena de proveedores se cayo.
_TOOLS_NARRABLES = {
    "load_skill_section", "list_reference_components", "read_reference_component",
    "verificar_web", "fetch_business_from_maps", "fetch_menu_and_reviews_from_maps",
    "fetch_menu_photos_from_maps", "extract_menu_text", "search_images",
    "search_web_image", "search_web", "deep_research", "wikipedia", "web_fetch",
    "extract_business_info", "check_links", "find_api", "sample_data", "search_icons",
    "search_fonts", "color_palette", "design_assets", "uiverse", "delegate_to_specialist",
    "generate_pptx", "generate_docx", "generate_xlsx", "lint_email", "analyze_image",
    "classify_image_content", "search_hard_cases",
}
_NUDGE_TOOLCALL = (
    "Escribiste las llamadas a herramientas como TEXTO (formato tipo "
    "`herramienta(args)`), pero NO las emitiste como function-calling, asi que "
    "NO se ejecuto ninguna y la tarea no avanzo. Vuelve a emitir esas mismas "
    "llamadas AHORA como tool calls DE VERDAD (el mecanismo de function-calling), "
    "no como texto en tu respuesta."
)


def _parece_toolcall_narrada(content: str, schemas: list[dict] | None) -> bool:
    """El modelo describio tools de orquestacion como TEXTO en vez de llamarlas.
    Formatos vistos en vivo: `load_skill_section(migrations: {...})` y
    `load_skill_section nuestrame {"section": "2."}`. El discriminador frente a
    la prosa ("voy a usar load_skill_section(seccion 2)") es que la narracion
    lleva los ARGS como JSON: un `{` pegado al nombre de la tool. Dispara con
    >=2 tools DISTINTAS asi (una tanda) o con 1 sola si el texto es basicamente
    SOLO esa llamada (corto)."""
    if not content:
        return False
    disponibles = {
        (t.get("function") or {}).get("name") for t in (schemas or [])
    } & _TOOLS_NARRABLES
    if not disponibles:
        return False
    # nombre de tool + (hasta 20 chars sin llave) + '{' de los args JSON
    patron = re.compile(
        r"\b(" + "|".join(re.escape(n) for n in disponibles) + r")\b[^\n{}]{0,20}\{",
        re.I,
    )
    distintas = {m.group(1).lower() for m in patron.finditer(content)}
    if len(distintas) >= 2:
        return True
    return len(distintas) == 1 and len(content.strip()) < 300


def _tool_por_claves(claves: set[str], schemas: list[dict] | None) -> str | None:
    """Infiere que tool corresponde a un dict de argumentos por sus CLAVES.
    Elige la tool cuyas propiedades contienen TODAS las claves dadas y que
    mejor se 'llena' con ellas (mayor solape). Empata a favor de la que
    exige esas claves como `required` (asi {path, content} cae en write_file
    y no en una tool con path opcional)."""
    if not claves:
        return None
    mejor, mejor_punt = None, -1.0
    for t in schemas or []:
        fn = t.get("function") or {}
        params = fn.get("parameters") or {}
        props = set((params.get("properties") or {}))
        if not props or not claves <= props:
            continue
        requeridas = set(params.get("required") or [])
        # solape: cuanto llenan las claves esta tool; + bonus si son las requeridas
        punt = len(claves & props) / len(props) + (1.0 if claves == requeridas else 0.0)
        if punt > mejor_punt:
            mejor, mejor_punt = fn.get("name"), punt
    return mejor


def _reparar_tool_call(
    name: str, arguments: dict, executor: object, schemas: list[dict] | None
) -> tuple[str, dict]:
    """Repara un tool_call malformado en el que el modelo mete el JSON de
    ARGUMENTOS dentro del campo `name` (y deja `arguments` vacio). Visto en
    vivo con web-builder-large: name='{"path":"malta-norte/index.html",
    "content":"<!DOCTYPE html>..."}' -> el dispatch lo trataba como
    'herramienta desconocida' y NO escribia el archivo. Si el nombre no es
    una tool real pero parsea como dict de args, infiere la tool por sus
    claves. Si no se puede reparar, devuelve lo original tal cual."""
    if getattr(executor, name, None) is not None:
        return name, arguments  # nombre valido: nada que reparar
    if not (isinstance(name, str) and name.lstrip()[:1] == "{"):
        return name, arguments
    try:
        posibles = json.loads(name)
    except (json.JSONDecodeError, ValueError):
        return name, arguments
    if not isinstance(posibles, dict) or not posibles:
        return name, arguments
    # los args reales van en el `name`; se respeta cualquier arg suelto valido.
    # Se infiere por las claves COMBINADAS: si el name solo trae {path} pero en
    # arguments viene {content}, el conjunto {path, content} cae en write_file
    # y no en una tool de solo-path.
    combinados = {**posibles, **(arguments or {})}
    inferida = _tool_por_claves(set(combinados), schemas)
    if not inferida:
        return name, arguments
    return inferida, combinados


def _escribio_algun_archivo(executor: object) -> bool:
    """True si en esta sesion ya se escribio/edito CUALQUIER archivo."""
    return bool(getattr(executor, "archivos_tocados", None))


def _texto_de_content(content: object) -> str:
    """Normaliza el `content` del mensaje a str. Algunos proveedores (p.ej.
    mistral-large) lo devuelven como LISTA de partes (`[{"type":"text",
    "text":"..."}]`) en vez de un string; sin esto, cualquier regex sobre el
    (`_MARCADOR_OFFICE.search`, etc.) revienta con 'expected string, got list'
    y tumba la sesion entera en el primer turno."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        partes = []
        for p in content:
            if isinstance(p, dict):
                partes.append(p.get("text") or p.get("content") or "")
            else:
                partes.append(str(p))
        return " ".join(x for x in partes if x)
    return str(content)


def _parece_artefacto_office_sin_llamar(content: str) -> bool:
    """El modelo devolvio TEXTO (sin tool_calls) con pinta de ser el contenido
    de un documento de oficina: deberia haber llamado a la herramienta que lo
    genera. Detecta el deck JSON, los bloques de docx, o una pseudo-llamada."""
    return bool(content) and bool(_MARCADOR_OFFICE.search(content))


def _mensaje_de_correccion(nombre: str, intentos: list[tuple[dict, str]]) -> str:
    """Un solo mensaje con los intentos fallidos juntos.

    Se incluyen los ARGUMENTOS de cada intento, no solo el error: el patron
    que hay que ver casi siempre esta ahi (la misma ruta mal escrita, el
    mismo old_text que no existe), y sin ellos el modelo solo ve tres veces
    el mismo mensaje de error sin saber que cambio entre uno y otro."""
    lineas = [
        f"Llamaste a `{nombre}` {len(intentos)} veces seguidas y las "
        f"{len(intentos)} fallaron. Estos fueron los intentos, con sus "
        f"argumentos y su error:",
        "",
    ]
    for i, (args, error) in enumerate(intentos, 1):
        argumentos = ", ".join(f"{k}={v!r}"[:120] for k, v in (args or {}).items())
        lineas.append(f"{i}. {nombre}({argumentos})")
        lineas.append(f"   -> {error.strip()[:300]}")
    lineas += [
        "",
        "PARA antes de volver a intentarlo. Mira los tres juntos: lo que "
        "falla no es el intento, es la suposicion que comparten (una ruta que "
        "no existe, un texto que no esta en el archivo, un parametro con el "
        "nombre equivocado).",
        "",
        "Cambia de enfoque en vez de repetir con una variacion: si es una "
        "ruta, listala con list_dir o glob_search antes; si es un old_text "
        "que no aparece, el propio error trae el fragmento REAL del archivo - "
        "usa ese texto literal. Y si despues de mirarlo no ves como seguir, "
        "dilo y pregúntale al usuario con ask_user en vez de gastar mas "
        "turnos a ciegas.",
    ]
    return "\n".join(lineas)


# La gestion de contexto (que se reenvia y que no en cada vuelta) vive en
# groq_agent/context_manager.py. Antes estaba aca, con una lista blanca de
# 6 tools y un contador construido durante la corrida; tenia tres huecos:
# las tools mas pesadas (render_check, critique_screenshot, search_images)
# no estaban en la lista, no habia presupuesto TOTAL de conversacion, y al
# reanudar con -c el contador arrancaba vacio, asi que el historial
# heredado - el mas viejo y mas gordo - nunca se podaba. El modulo nuevo
# trabaja solo sobre la lista de mensajes, asi que no tiene ese sesgo.


_MARCA_MANIFIESTO = "[Archivos de esta tarea"


def _sembrar_manifiesto_desde_historial(messages: list[dict], executor: ToolExecutor) -> None:
    """Reconstruye la lista de archivos tocados leyendo los resultados de
    escritura del historial. Necesario al REANUDAR (-c/-r): el executor
    nace vacio, pero los 'Escrito/Editado/Generado <ruta>' siguen en los
    mensajes (son DURADERO, no se podan). Asi el manifiesto no se pierde
    entre sesiones - igual que el context_manager, se deriva del historial
    en vez de depender de un contador que se resetea."""
    if executor is None or not hasattr(executor, "archivos_tocados"):
        return
    for m in messages:
        if m.get("role") != "tool":
            continue
        c = m.get("content")
        if not isinstance(c, str):
            continue
        if c.startswith("Escrito "):
            ruta = c[len("Escrito "):].rsplit(" (", 1)[0].strip()
            executor.archivos_tocados.setdefault(ruta, "creado")
        elif c.startswith("Editado "):
            executor.archivos_tocados[c[len("Editado "):].rstrip(". ").strip()] = "editado"
        elif c.startswith("Generado "):
            ruta = c[len("Generado "):].split(". ", 1)[0].strip()
            executor.archivos_tocados.setdefault(ruta, "creado")


def _refrescar_manifiesto(messages: list[dict], executor: ToolExecutor) -> None:
    """Mantiene al FINAL del contexto una lista viva de los archivos que el
    agente ha creado/editado, con su ruta absoluta REAL.

    La poda dispersa los resultados de escritura y el modelo se perdia: se
    inventaba otra carpeta ('workspace/casa-lucio' en vez de la real en
    Descargas). Esta nota consolidada, siempre en la ultima posicion (donde
    mas atencion recibe), lo evita. Se quita la anterior y se re-añade la
    actual cada turno para que no se acumule y refleje el estado real.

    Es un mensaje `user` normal (sin claves extra que puedan dar un 400) y
    autonomo (sin tool_calls), asi que quitarlo nunca rompe el
    emparejamiento assistant/tool que exige la API."""
    messages[:] = [
        m for m in messages
        if not (
            m.get("role") == "user"
            and isinstance(m.get("content"), str)
            and m["content"].startswith(_MARCA_MANIFIESTO)
        )
    ]
    archivos = getattr(executor, "archivos_tocados", None)
    if not archivos:
        return
    lineas = "\n".join(f"- {accion}: {ruta}" for ruta, accion in archivos.items())
    messages.append({
        "role": "user",
        "content": (
            f"{_MARCA_MANIFIESTO} - estas son las rutas EXACTAS en disco. Para volver a "
            "leer o editar cualquiera, usa su ruta TAL CUAL; NO inventes otra carpeta ni "
            "cambies el nombre. No es un pedido nuevo del usuario.]\n" + lineas
        ),
    })


_MARCA_PLAN = "[PLAN ACTUAL"
_PLAN_ESTADO_HECHO = ("hecho", "done", "completado", "ok")
_PLAN_ESTADO_CURSO = ("en_curso", "en curso", "in_progress", "actual", "haciendo")


def _refrescar_plan(messages: list[dict], executor: ToolExecutor) -> None:
    """Mantiene al FINAL del contexto el checklist VIVO del especialista, para
    que no lo pierda de vista y lo vaya marcando segun avanza - el mismo patron
    que _refrescar_manifiesto usa con los archivos. Sin esto, en una tarea larga
    el modelo se olvida de su plan y deja de actualizarlo (se veia: el checklist
    'perdia la memoria'). Se quita el anterior y se re-añade el actual cada
    turno para que refleje el estado real y no se acumule."""
    messages[:] = [
        m for m in messages
        if not (
            m.get("role") == "user"
            and isinstance(m.get("content"), str)
            and m["content"].startswith(_MARCA_PLAN)
        )
    ]
    pasos = getattr(executor, "plan_actual", None)
    if not pasos:
        return
    lineas = []
    hechos = 0
    for p in pasos:
        if isinstance(p, dict):
            texto = (p.get("paso") or p.get("texto") or p.get("descripcion")
                     or p.get("step") or p.get("tarea") or "")
            estado = str(p.get("estado") or p.get("status") or "pendiente").strip().lower()
        else:
            texto, estado = str(p), "pendiente"
        if estado in _PLAN_ESTADO_HECHO:
            marca = "[x]"
            hechos += 1
        elif estado in _PLAN_ESTADO_CURSO:
            marca = "[>]"
        else:
            marca = "[ ]"
        lineas.append(f"{marca} {texto}")
    messages.append({
        "role": "user",
        "content": (
            f"{_MARCA_PLAN} - {hechos}/{len(pasos)} hechos] Este es TU checklist, no lo "
            "pierdas de vista. Segun avances vuelve a llamar a `plan` con la lista COMPLETA "
            "y los estados al dia: marca 'hecho' lo terminado y 'en_curso' el paso que estas "
            "haciendo AHORA. No es un pedido nuevo del usuario.\n" + "\n".join(lineas)
        ),
    })


def _confirmar_y_despachar_externo(name: str, arguments: dict, executor: ToolExecutor) -> str:
    """Confirma (en el HILO PRINCIPAL) una escritura FUERA del workspace y
    la despacha si se aprueba. La UI de flechas vive aqui, no en la tool:
    la tool corre en el hilo de fondo de animate_dispatch, donde
    prompt_toolkit parpadea y no llega a preguntar.

    - Ruta de SISTEMA: no se pregunta; se despacha para que la tool
      devuelva su ERROR de bloqueo duro.
    - Carpeta ya aprobada antes ('y todo en esta carpeta'): se despacha sin
      volver a preguntar.
    - Si no: menu de flechas (no / solo este archivo / toda la carpeta)."""
    from groq_agent.safety import is_system_path, resolve_write_destination

    try:
        destino, _ = resolve_write_destination(executor.root, arguments.get("path", ""))
    except Exception:  # noqa: BLE001 - ruta invalida: que la tool lo explique
        return executor.dispatch(name, arguments)

    if is_system_path(destino):
        r = executor.dispatch(name, arguments)  # ERROR de sistema, sin animar un write que no ocurre
        ui.print_tool_result(name, arguments, r[:300], root=executor.root)
        return r

    if not executor.carpeta_externa_aprobada(destino):
        decision = ui.confirmar_ruta_externa(destino)
        if decision == "no":
            r = (
                "El usuario RECHAZO guardar en esa ubicacion. No se escribio nada. "
                "IMPORTANTE: NO guardes el resultado en otro sitio por tu cuenta - ni en el "
                "workspace con una ruta relativa, ni en otra carpeta. Detente y pregúntale al "
                "usuario DONDE quiere que lo guardes (o si prefiere no guardarlo)."
            )
            ui.print_tool_result(name, arguments, r[:300], root=executor.root)
            return r
        if decision == "folder":
            executor.aprobar_carpeta_externa(destino.parent)

    # Aprobada (por carpeta previa o por esta confirmacion): se marca el
    # destino puntual y se despacha; la tool lo verifica antes de escribir.
    executor._escritura_externa_aprobada = destino
    try:
        return ui.animate_dispatch(name, arguments, executor, root=executor.root)
    finally:
        executor._escritura_externa_aprobada = None


# Marca que separa el skill/prompt del bloque de fecha, para poder re-datar el
# mensaje de sistema en cada turno SIN acumular bloques viejos (idempotente).
_MARCA_FECHA = "\n\n=== CONTEXTO TEMPORAL ==="
_DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _bloque_fecha() -> str:
    """Fecha y hora REAL del turno. El orquestador corre LOCAL en la maquina del
    usuario (a diferencia de NovaChat, que corre en UTC en Render), asi que la
    hora local del sistema YA es la del usuario: basta datetime.now(), sin zona
    horaria. Nombres de dia/mes a mano para no depender del locale de Windows."""
    from datetime import datetime
    ahora = datetime.now()
    stamp = (f"{_DIAS[ahora.weekday()]}, {ahora.day} de {_MESES[ahora.month - 1]} "
             f"de {ahora.year}, {ahora.hour:02d}:{ahora.minute:02d}")
    return (
        f"FECHA Y HORA ACTUAL: {stamp} (hora local). Es el momento REAL de esta "
        "peticion; usala para todo razonamiento temporal y para construir las "
        "busquedas (p. ej. 'mundial 2026', 'precio hoy').\n\n"
        "RAZONAMIENTO TEMPORAL OBLIGATORIO antes de responder sobre un evento con fecha "
        "(un mundial, unas elecciones, un estreno, una temporada, una version):\n"
        "1) Determina CUANDO es el evento; si no lo sabes con certeza, BUSCALO.\n"
        "2) Compara esa fecha con la FECHA ACTUAL de arriba. OJO: el ano EN CURSO ya "
        "tiene meses PASADOS; 'de este ano' NO significa 'en el futuro' - agosto es "
        "DESPUES de julio del mismo ano.\n"
        "3) Si el evento YA termino (su fecha es ANTERIOR a hoy) -> YA HAY RESULTADO: "
        "BUSCALO y dalo con su fuente. NO digas 'aun no se ha celebrado' de algo que ya paso.\n"
        "4) Si el evento es POSTERIOR a hoy -> todavia no se ha celebrado: dilo con "
        "claridad y ofrece la edicion ANTERIOR o pregunta si quiere la PROXIMA. Nunca "
        "inventes ni des por ganado un resultado que aun no existe.\n"
        "5) Si esta EN CURSO -> dilo y busca el estado actual.\n"
        "Ejemplo: si HOY es agosto de 2026 y te preguntan por el Mundial 2026 (se jugo en "
        "junio-julio de 2026), ese torneo YA TERMINO (agosto > julio) -> busca QUIEN LO "
        "GANO y respondelo; seria un error decir que no se ha celebrado."
    )


def _con_fecha(system_prompt: str) -> str:
    """Pega (o refresca) el bloque de fecha al final del prompt de sistema. Quita
    primero cualquier bloque anterior por la marca, para que al continuar una
    sesion la fecha no quede congelada ni se apilen varios bloques."""
    base = system_prompt.split(_MARCA_FECHA)[0].rstrip()
    return f"{base}{_MARCA_FECHA}\n{_bloque_fecha()}"


def run_agent(
    client: GroqClient,
    executor: ToolExecutor,
    system_prompt: str,
    user_task: str,
    model: str,
    max_turns: int | None = None,
    existing_messages: list[dict] | None = None,
    tool_schemas: list[dict] | None = None,
) -> tuple[str, list[dict]]:
    """Devuelve (texto_final, mensajes_actualizados). Si existing_messages
    viene de una sesion guardada (ver session_store.py), se continua esa
    conversacion en vez de arrancar una nueva - el system_prompt pasado
    se ignora en ese caso (ya esta en existing_messages[0]).

    max_turns=None (default) significa SIN limite: la tarea sigue hasta
    que el modelo de una respuesta final, sin importar cuantas idas y
    vueltas con herramientas haga falta. Antes habia un tope fijo (15,
    luego 30) que cortaba tareas complejas (sitios web con planificacion
    + varias herramientas) a mitad de camino con 'se alcanzo el maximo
    de turnos'. Pasar un numero explicito (via --max-turns) sigue
    sirviendo para uso automatizado/CI donde se quiere un techo de costo
    duro.

    tool_schemas=None (default) usa el catalogo completo (TOOL_SCHEMAS),
    igual que siempre. Pasar una lista propia restringe que funciones ve
    el modelo en esta llamada - util para un especialista de SOLO
    DECISION (ej. web-designer-specialist) al que no le hace falta ni
    tiene sentido ofrecerle write_file/edit_file: al no estar en el
    schema, el modelo no puede pedirlas (restriccion real a nivel de
    API, no una convencion de prompt)."""
    schemas = tool_schemas if tool_schemas is not None else TOOL_SCHEMAS
    if existing_messages:
        # Al continuar, el system_prompt pasado se ignora (ya esta en [0]); pero SI
        # refrescamos la fecha de ese mensaje 0, o quedaria congelada en la del
        # primer turno de la sesion.
        prim = existing_messages[0]
        if isinstance(prim, dict) and prim.get("role") == "system":
            existing_messages = [
                {"role": "system", "content": _con_fecha(str(prim.get("content", "")))},
                *existing_messages[1:],
            ]
        messages: list[dict] = [*existing_messages, {"role": "user", "content": user_task}]
    else:
        messages = [
            {"role": "system", "content": _con_fecha(system_prompt)},
            {"role": "user", "content": user_task},
        ]
    # Cada tarea arranca sin plan (ni en la barra ni en el executor), para no
    # arrastrar el de la tarea anterior a la barra ni al recordatorio de contexto.
    ui.set_plan([])
    if hasattr(executor, "plan_actual"):
        executor.plan_actual = []
    # Al reanudar, reconstruir la lista de archivos tocados desde el
    # historial para que el manifiesto no arranque vacio.
    _sembrar_manifiesto_desde_historial(messages, executor)
    tool_call_retries_left = _MAX_TOOL_CALL_RETRIES
    # Fallos consecutivos de la misma herramienta, para la correccion
    # de un tiro (ver _mensaje_de_correccion).
    fallos_seguidos: list[tuple[str, dict, str]] = []
    # firma de una llamada -> cuantas veces dio EXACTAMENTE lo mismo
    repeticiones: dict[tuple, int] = {}
    # Un solo empujon cuando el modelo escribe un documento de oficina como
    # texto en vez de llamar a la herramienta (ver _NUDGE_OFFICE). Solo aplica
    # si esta sesion tiene alguna tool de oficina disponible: sin eso, no hay
    # nada que llamar y el "empujon" seria un falso positivo.
    sesion_ofimatica = any(
        (t.get("function") or {}).get("name") in _TOOLS_OFFICE for t in (schemas or [])
    )
    # Un solo empujon por sesion, sea por artefacto de oficina o por un archivo
    # (codigo/config) que se pidio guardar y no se escribio.
    nudge_archivo_dado = False

    turn_counter = itertools.count() if max_turns is None else range(max_turns)
    for _turn in turn_counter:
        # Frontera limpia 1: antes de pagar otra llamada al proveedor.
        if interrupt.pedido():
            ui.print_note("parado - escribe que quieres que haga")
            return (
                "[Parado por el usuario. El progreso hecho hasta aca quedo "
                "guardado en la sesion.]",
                messages,
            )
        # Lista viva de archivos tocados, siempre al final del contexto: que
        # el modelo nunca pierda de vista donde estan sus propios archivos.
        _refrescar_manifiesto(messages, executor)
        # Y su checklist vivo, por la misma razon: que lo mantenga al dia.
        _refrescar_plan(messages, executor)
        try:
            with ui.spinner("pensando…"):
                response = client.chat(messages=messages, tools=schemas, model=model)
        except ToolCallFailedError:
            # Pasa con algunos modelos cuando el argumento de una funcion
            # es un string grande (ej. un HTML completo): generan una
            # tool call mal formada y el proveedor la rechaza. En vez de
            # crashear la sesion entera, se le pide al modelo que
            # reintente con una llamada valida (y mas chica si hace
            # falta) hasta agotar el presupuesto de reintentos.
            if tool_call_retries_left <= 0:
                return (
                    "El modelo genero llamadas a herramientas invalidas repetidamente "
                    "(esto es un limite del modelo, no de la terminal). Probá con otro "
                    "modelo (--model) o pídele la tarea en pasos mas chicos.",
                    messages,
                )
            tool_call_retries_left -= 1
            ui.print_note("tool call malformada del modelo - pidiendo que reintente")
            messages.append({
                "role": "user",
                "content": (
                    "Tu llamada anterior a una herramienta no se pudo interpretar (formato "
                    "invalido). Reintenta con UNA sola llamada de funcion valida en JSON, sin "
                    "envolverla en texto ni pseudo-XML. Si el contenido es muy largo, dividilo "
                    "en pasos mas chicos (ej. escribir el HTML y el CSS en llamadas separadas)."
                ),
            })
            continue
        except RuntimeError as exc:
            # Cualquier otro error de la API (rate limit, modelo caido,
            # etc.) puede pasar A MITAD del ciclo, despues de que ya se
            # ejecutaron tool calls reales (archivos ya escritos). Antes
            # esto se perdia por completo porque la excepcion se
            # propagaba sin devolver nada; ahora se devuelve el progreso
            # acumulado para que la sesion se guarde igual y se pueda
            # continuar despues con -c, en vez de repetir desde cero.
            return (
                f"Se corto la conversacion por un error de la API del proveedor: {exc}\n"
                "El progreso hecho hasta este punto (herramientas ya ejecutadas) quedo "
                "guardado en la sesion - continua con -c/--continue cuando quieras.",
                messages,
            )

        choice = response["choices"][0]
        message = choice["message"]
        messages.append(message)

        tool_calls = message.get("tool_calls")
        if not tool_calls:
            content = _texto_de_content(message.get("content"))
            # Rescate: el modelo escribio el deck/documento como TEXTO en vez
            # de llamar a generate_pptx/docx/xlsx -> sin este empujon el
            # usuario se queda con el JSON crudo y sin archivo. Una sola vez,
            # para no entrar en bucle si el modelo insiste.
            if not nudge_archivo_dado:
                # Tres formas de terminar "sin entregar el archivo":
                # (a) volcar el artefacto de oficina como texto; (b) ser un
                # generador (word/xlsx/pptx) y no haber creado el archivo
                # (incluye preguntar la ruta o prometer y parar); (c) el
                # usuario pidio GUARDAR un archivo (codigo, config...) y no se
                # escribio ninguno. En todos, un empujon para hacer la tool call.
                debe_generar_office = (
                    getattr(executor, "skill_name", None) in _SKILLS_GENERAN_ARCHIVO
                )
                disparar_office = (
                    (sesion_ofimatica and _parece_artefacto_office_sin_llamar(content))
                    or (debe_generar_office and not _genero_archivo_office(executor))
                )
                disparar_archivo = (
                    _tiene_write_file(schemas)
                    and _PIDE_GUARDAR_ARCHIVO.search(user_task or "")
                    and not _escribio_algun_archivo(executor)
                )
                disparar_narrada = _parece_toolcall_narrada(content, schemas)
                nudge = (
                    _NUDGE_OFFICE if disparar_office
                    else _NUDGE_TOOLCALL if disparar_narrada
                    else _NUDGE_ARCHIVO if disparar_archivo
                    else None
                )
                if nudge:
                    nudge_archivo_dado = True
                    ui.print_note(
                        "el especialista termino sin escribir el archivo - pidiendo la tool call"
                    )
                    messages.append({"role": "user", "content": nudge})
                    continue
            ui.set_plan([])  # tarea terminada: el plan desaparece de la barra
            return content, messages

        for call in tool_calls:
            # Frontera limpia 2: entre herramienta y herramienta. NO se
            # corta a mitad de una: dejaria un archivo escrito a medias.
            if interrupt.pedido():
                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": "El usuario paro la ejecucion antes de correr esta herramienta.",
                })
                continue
            name = call["function"]["name"]
            try:
                arguments = json.loads(call["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                arguments = {}
            # Algunos proveedores devuelven el tool_call malformado: el JSON de
            # argumentos metido en el campo `name` y `arguments` vacio. Se
            # repara ANTES de despachar para no perder la escritura.
            name, arguments = _reparar_tool_call(name, arguments, executor, schemas)

            # Escribir FUERA del workspace se confirma AQUI, en el hilo
            # principal: prompt_toolkit (el menu de flechas) no se dibuja
            # bien desde el hilo de fondo de animate_dispatch - hacerlo ahi
            # parpadeaba y no llegaba a preguntar. La tool solo enforcea.
            fuera_workspace = escribe_fuera_del_workspace(name, arguments, executor.root)
            if fuera_workspace and not executor.auto_yes:
                result = _confirmar_y_despachar_externo(name, arguments, executor)
            elif (
                name in WRITE_TOOLS
                and not fuera_workspace
                and not executor.auto_yes
                and not executor.session_accept_all
            ):
                decision = ui.confirm_action(name, arguments)
                if decision == "yes_session":
                    # "Aceptar todas en esta sesion" - se guarda en el
                    # executor (no en una variable local) porque persiste
                    # entre tareas de toda la sesion interactiva (ver
                    # groq_agent/cli.py), asi que tambien evita
                    # confirmaciones en tareas futuras, no solo el resto
                    # de esta. Deliberadamente NO toca auto_yes: el
                    # usuario sigue presente para ask_user, solo dejo de
                    # confirmar escrituras rutinarias.
                    executor.session_accept_all = True
                    # Que la barra lo refleje y siga en AUTO en las tareas
                    # siguientes (el CLI la sincroniza desde ui.modo_auto()).
                    ui.set_modo_auto(True)
                    result = ui.animate_dispatch(name, arguments, executor, root=executor.root)
                elif decision == "yes":
                    result = ui.animate_dispatch(name, arguments, executor, root=executor.root)
                else:
                    result = "El usuario NO aprobo esta accion. No se ejecuto."
                    ui.print_tool_result(name, arguments, result[:300], root=executor.root)
            else:
                result = ui.animate_dispatch(name, arguments, executor, root=executor.root)

            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": result,
            })

            # Racha de fallos de la MISMA herramienta: se registra aca y se
            # actua al terminar la vuelta, para no cortar a mitad de un
            # grupo de tool calls.
            if _es_error(result):
                if fallos_seguidos and fallos_seguidos[0][0] != name:
                    fallos_seguidos.clear()
                fallos_seguidos.append((name, arguments, result))
            else:
                fallos_seguidos.clear()

            # Detector de bucle. La racha de arriba NO lo cubre: se reinicia
            # con cada exito, y un bucle real alterna exito y fallo. Visto
            # en uso: write_file(requirements.txt) OK -> run_check FALLA ->
            # write_file OK -> run_check FALLA... indefinidamente, hasta que
            # el usuario pulso ESC. La racha valia 1 en cada vuelta y nunca
            # llegaba al umbral.
            #
            # Lo que se cuenta es la REPETICION exacta: misma herramienta,
            # mismos argumentos, mismo resultado. Repetir eso no es
            # insistir, es estar atascado - el entorno no ha cambiado, asi
            # que la proxima vuelta dara lo mismo.
            firma = (name, json.dumps(arguments, sort_keys=True, default=str)[:400],
                     (result or "")[:200])
            repeticiones[firma] = repeticiones.get(firma, 0) + 1
            if repeticiones[firma] >= _REPETICIONES_PARA_CORTAR:
                ui.print_note(
                    f"{name} repetido {repeticiones[firma]} veces con el mismo "
                    f"resultado - cortando el bucle"
                )
                return (
                    f"Me he quedado atascado repitiendo `{name}` con el mismo "
                    f"resultado {repeticiones[firma]} veces, asi que paro en vez "
                    f"de seguir gastando turnos.\n\nLo ultimo que devolvio:\n"
                    f"{(result or '')[:600]}\n\nSi es una dependencia que falta, "
                    f"puedo instalarla con `install_dependency`. Si no, dime como "
                    f"quieres que siga.",
                    messages,
                )

        # Poda los resultados de tool viejos y, si aun asi la conversacion
        # se pasa del presupuesto, compacta lo mas antiguo con un modelo
        # barato. Se hace al final de cada vuelta para que la proxima
        # llamada ya salga con el historial recortado.
        if len(fallos_seguidos) >= _FALLOS_PARA_CORREGIR:
            nombre = fallos_seguidos[0][0]
            ui.print_note(
                f"{len(fallos_seguidos)} fallos seguidos de {nombre} - "
                f"mandando una correccion en vez de dejar que siga tanteando"
            )
            messages.append({
                "role": "user",
                "content": _mensaje_de_correccion(
                    nombre, [(a, e) for _, a, e in fallos_seguidos]
                ),
            })
            fallos_seguidos.clear()

        if _PODA_ACTIVA:
            informe = gestionar_contexto(messages, client)
            if informe["antes"] != informe["despues"]:
                ui.print_context_saving(informe)
            ui.print_status_bar(informe["despues"], getattr(client, "gasto", None))
        else:
            # Poda desactivada (diagnostico): solo se actualiza la barra con el
            # tamaño real, sin recortar nada del historial.
            from groq_agent.context_manager import tamano as _tamano_contexto
            ui.print_status_bar(_tamano_contexto(messages), getattr(client, "gasto", None))

    return "(se alcanzo el maximo de turnos sin una respuesta final - revisa el historial arriba)", messages
