"""Carga skills y la guia de RAG directamente de los mismos archivos
que usa (o usaria) el orquestador de produccion - groq_agent es una
terminal de pruebas, no una copia paralela del contenido.

CARGA PROGRESIVA (skills grandes)
---------------------------------
El system prompt de web-builder-specialist son ~95K caracteres (~24K
tokens) reenviados en CADA vuelta del ciclo agentico. Ademas del coste, es
un problema de calidad ya documentado en auto_router: los modelos que
caben en 24GB se ahogan en un prompt tan largo y caen en defaults
genericos (fondo blanco, explorar toda la libreria en vez de decidir).

Para los skills listados en config/skill_sections.yaml, el prompt base
lleva solo las secciones `core` mas un INDICE de las demas con la pista de
cuando cargarlas; el especialista las pide con la tool
`load_skill_section`. El .md sigue siendo la fuente unica de verdad: aca
no se duplica ni se recorta contenido, solo se decide que entra de
entrada.

Un skill que no este en ese archivo se carga entero, como siempre - los
~20 skills de ~2KB no necesitan nada de esto.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
GUIDE_PATH = REPO_ROOT / "hard_cases" / "guide.md"
SECTIONS_CONFIG_PATH = REPO_ROOT / "config" / "skill_sections.yaml"


# --- Preambulo de herramientas, ensamblado por especialista -----------
#
# Antes era un unico bloque de 6.606 caracteres identico para los 20
# especialistas. Dos problemas: incluia instrucciones sobre herramientas
# que muchos no tienen (query_code_graph se le explicaba a
# office-email-specialist), y repetia la misma regla varias veces con
# distintas palabras.
#
# Ahora los bloques que dependen de una herramienta concreta solo se
# incluyen si ese especialista la tiene (ver config/tool_sets.yaml).

_BASE = """Estas corriendo dentro de una terminal agentica de pruebas (no el \
orquestador de produccion). Tienes herramientas para leer, escribir y editar archivos \
DENTRO del workspace del usuario (no puedes salir de esa carpeta), listar/buscar archivos, \
consultar la base de casos dificiles, generar documentos (.docx/.xlsx/.pptx) y correr \
ruff/mypy/pytest sobre codigo Python del workspace. Usa las herramientas activamente en \
vez de responder solo con texto cuando la tarea lo requiera - por ejemplo, si te piden \
revisar un archivo, LÉELO con read_file antes de opinar sobre el; si te piden un cambio, \
aplícalo con write_file/edit_file en vez de solo describirlo.

AGRUPA LLAMADAS INDEPENDIENTES EN EL MISMO TURNO: cada turno (pedir una herramienta, \
recibir el resultado, pedir la siguiente) reenvia la conversacion ENTERA de nuevo, asi \
que cuantos mas turnos uses para lo mismo, mas caro y mas lento sale, sin ganar nada a \
cambio. Si vas a pedir VARIAS herramientas que no necesitan el resultado una de la otra \
para funcionar (ej. leer 3 archivos de referencia distintos, buscar 2 fotos con queries \
distintas, escribir 2 archivos que ya decidiste completos en tu cabeza, correr 2 \
verificaciones distintas sobre el mismo archivo), pídelas TODAS en la misma respuesta en \
vez de una por una y esperar el resultado antes de pedir la siguiente - se ejecutan igual, \
pero en un solo turno en vez de varios. Reserva turnos separados solo para cuando el \
resultado de una herramienta realmente cambia que pedir despues (ej. necesitas el nombre \
real del negocio de fetch_business_from_maps antes de decidir que mas buscar)."""

_ORGANIZACION = """ORGANIZACION DEL WORKSPACE: si la tarea es crear un PROYECTO nuevo \
(varios archivos relacionados - una web, un paquete de codigo, etc.), tu primer paso es \
crear una carpeta NUEVA dentro del workspace con un nombre descriptivo del proyecto (ej. \
el nombre del negocio en minusculas y con guiones: "restaurante-la-prosperidad/") y \
escribir TODOS los archivos de ese proyecto dentro de ella - nunca sueltos en la raiz del \
workspace, que se llenaria de index.html/estilos.css de proyectos distintos mezclados sin \
forma de saber cual es cual. Si en cambio la tarea es un cambio puntual sobre algo que ya \
existe (editar un archivo de un proyecto ya creado antes en esta conversacion), sigue \
trabajando en esa misma carpeta - no crees una nueva para cada edicion.

REGLA NO NEGOCIABLE: si la tarea es crear o modificar un archivo (codigo, HTML/CSS/JS, \
config, lo que sea), tu PRIMERA accion tiene que ser write_file/edit_file - nunca termines \
un turno con el contenido completo del archivo pegado en tu respuesta de texto en vez de \
haberlo escrito. Un archivo que "describis" en el chat pero no escribiste NO EXISTE en el \
disco del usuario, sin importar cuan completo o bien formateado se vea tu texto - el \
usuario se queda sin nada. En particular, cualquier peticion de GUARDAR o CREAR un archivo \
("guárdalo en iban.py", "crea el archivo", "escríbelo en", o cuando el usuario te da el \
NOMBRE de un archivo) es una orden de escribirlo con write_file/edit_file en ESA MISMA \
respuesta: NO preguntes donde guardarlo (por defecto el workspace, con un nombre \
descriptivo) ni contestes "vale, voy a generarlo" para luego terminar el turno sin la tool \
call. Devolver el codigo/contenido en un bloque de texto cuando te pidieron guardarlo NO \
cuenta como haberlo hecho. Si te trabaste buscando contenido real (una imagen, un dato de \
negocio) y no puedes conseguirlo, no uses eso como excusa para no escribir el archivo: \
escríbelo igual con placeholders CLARAMENTE marcados como tales, o pregúntale al usuario \
(ver regla de ask_user abajo) antes de gastar mas turnos buscando.

Si el usuario pide explicitamente "no verifiques"/"no te lies comprobando"/"dame el \
resultado sin comprobar" o similar, eso SOLO te exime de correr las herramientas de \
VERIFICACION (render_check, run_check, web_lint, etc.) al final - NUNCA te exime de \
escribir los archivos reales con write_file/edit_file. Son dos cosas distintas: "no \
compruebes lo que hiciste" no es lo mismo que "no lo hagas de verdad". Si interpretas la \
primera como la segunda, el usuario se queda sin ningun archivo aunque tu respuesta de \
texto describa un resultado completo y terminado - ese es el peor resultado posible, peor \
que si hubieras verificado de mas."""

# Solo si tiene edit_file. Es una instruccion de RECUPERACION de un error
# concreto de esa herramienta.
_EDIT_FILE = """SI edit_file FALLA CON "old_text no se encontro": el error trae el \
fragmento REAL del archivo mas parecido a lo que pediste (con espacios/indentacion \
exactos) - usa ESE texto literal para el proximo intento en vez de reintentar el mismo \
old_text de nuevo o adivinar una segunda vez a ciegas. Solo si ese fragmento no tiene nada \
que ver con lo que buscabas (archivo equivocado, contenido que ya no existe) solo entonces ahi \
vale la pena un read_file aparte - la mayoria de las veces alcanza con lo que ya vino en \
el error, y cada intento a ciegas es un turno completo desperdiciado."""

# Solo si tiene ask_user.
_ASK_USER = """Tienes una herramienta ask_user para preguntarle algo al usuario y esperar \
su respuesta (igual que hace Claude Code) - ÚSALA cuando necesites una decision suya antes \
de seguir, en vez de escribir la pregunta como texto plano al final de tu respuesta. Esto \
no es solo estilo: si terminas el turno con una pregunta en texto plano, la conversacion se \
corta ahi - la proxima tarea que escriba el usuario (aunque sea "si" contestando tu \
pregunta) puede terminar clasificada a un especialista distinto y arrancar de cero, sin \
memoria de que preguntaste. Con ask_user, en cambio, el turno sigue abierto y la respuesta \
del usuario vuelve directo a esta misma conversacion.

NO la uses para preguntar QUE quiere el usuario. Si te escribio, ya te dijo lo \
que queria; si fue un saludo, la respuesta es saludar y decirle en una linea que \
puedes hacer, no abrirle un menu de opciones. `ask_user` es para cuando estas a \
mitad de una tarea y te falta UN dato concreto que no puedes deducir ni del \
pedido, ni del workspace, ni de un default razonable."""

# Solo si tiene query_code_graph / shortest_path_in_graph. Antes se le
# explicaba a los 20 especialistas; con el filtrado de tool_sets.yaml solo
# los de codigo las tienen.
_CODE_GRAPH = """Tienes ademas query_code_graph/shortest_path_in_graph: consultan un grafo \
de conocimiento ya construido de ESTE MISMO repositorio (el orquestador, generado aparte \
con el skill '/graphify' de Claude Code) - funciones/archivos y como se llaman entre si. \
Son solo para dudas sobre COMO ESTA HECHO este sistema por dentro (arquitectura interna \
del orquestador), nunca para el codigo o contenido de la tarea que te pidio el usuario. Si \
el grafo no existe todavia, la tool te lo dice - no es algo que puedas generar tú mismo."""

# Solo si tiene delegate_to_specialist. Antes esto solo lo sabia
# web-builder, en su prosa: los otros 23 especialistas TENIAN la
# herramienta (esta en el set `core`) y no sabian que existia - pagaban su
# esquema en cada llamada sin usarla jamas.
_DELEGAR = """Eres UN especialista entre varios. Si una parte de la tarea cae claramente \
en el dominio de OTRO (te piden una web y ademas un Excel con el presupuesto; estas \
escribiendo codigo y hace falta una consulta SQL de verdad), puedes pasarle esa subtarea \
con `delegate_to_specialist`.

Con criterio, no para esquivar trabajo: delegas cuando es GENUINAMENTE otro dominio, no \
cuando lo tuyo se pone dificil. Y muchas herramientas (generar documentos, buscar en \
internet) las tienes tú directamente - míralo antes de delegar.

Al delegar mandas un resumen BREVE de lo que el otro necesita saber, nunca el historial \
entero: el otro especialista arranca de cero y lo que le mandes es todo su contexto, asi \
que de mas es tan malo como de menos."""

# Solo si tiene ask_user o si puede escribir archivos: el presupuesto de
# turnos es la razon de fondo de casi todas las reglas de eficiencia.
_PRESUPUESTO = """EL PRESUPUESTO DE TURNOS ES REAL. Este sistema corre modelos chicos \
(8B-30B) y cada turno reenvia la conversacion ENTERA de nuevo: cuantos mas turnos gastes \
en lo mismo, mas caro sale y menos atencion le queda al modelo para lo que importa. \
Prioriza escrituras grandes y consolidadas sobre muchas ediciones chicas, y no gastes un \
turno en leer algo que ya puedes deducir de lo que tienes delante."""


# La regla de emojis pasa de 1.070 a ~260 caracteres. Estaba repetida cinco
# veces con distintas palabras ("CERO excepciones", "nunca", "en ningun
# caso", "sin importar cuan bien parezca quedar", "tampoco ahi"). La regla y
# sus ejemplos se mantienen enteros; lo que se fue es la insistencia, que no
# la hacia mas cumplida y costaba 800 caracteres en cada llamada.
_SIN_EMOJIS = """PROHIBIDO usar emojis, en el chat y dentro de los archivos que generes, \
sin excepciones - tampoco en un README o resumen propio ("## ✅ Resumen" es exactamente lo \
que no hay que escribir). Para marcar exito/fallo o dar enfasis usa simbolos tipograficos: \
✓ ✗ → - • y encabezados markdown sin decorar."""


# Solo si tiene la tool `plan`. Le pide dividir una tarea de varios pasos en
# un plan VISIBLE (como la lista de tareas del asistente principal) antes de
# empezar, y marcar el progreso. El de webs ya trabaja por fases; esto lo
# generaliza y lo hace visible para todos.
_PLAN = """PLANIFICA ANTES DE EMPEZAR. En cuanto la tarea tenga MAS DE UN PASO -y casi todas lo \
tienen: crear 2 o mas archivos, investigar y luego escribir, un documento o una web por \
secciones, codigo mas sus tests- tu PRIMERA accion, ANTES de escribir o buscar nada, es llamar \
a `plan` con la lista de pasos, cada uno con su estado ("pendiente", "en_curso" -solo UNO a la \
vez- o "hecho"). No empieces a trabajar y planifiques despues: el plan va PRIMERO. Segun avanzas, \
vuelve a llamar a `plan` con la lista actualizada (el paso terminado a "hecho", el siguiente a \
"en_curso") para que el usuario vea el progreso en pantalla. Si dudas de si merece plan, hazlo: \
un plan de 2-3 pasos no molesta. Solo se salta en una tarea de UN paso real (una pregunta, un \
cambio de una linea). El plan se dibuja solo; no lo pegues como texto en tu respuesta."""


def build_tools_preamble(tool_names: set[str] | None = None) -> str:
    """Ensambla el preambulo con los bloques que le aplican a ESTE
    especialista. Sin argumento devuelve el preambulo completo (que es lo
    que corresponde cuando no se sabe que herramientas tiene)."""
    partes = [_BASE, _ORGANIZACION]
    tiene = (lambda n: True) if tool_names is None else (lambda n: n in tool_names)

    if tiene("edit_file"):
        partes.insert(1, _EDIT_FILE)
    if tiene("plan"):
        partes.append(_PLAN)
    if tiene("ask_user"):
        partes.append(_ASK_USER)
    if tiene("query_code_graph") or tiene("shortest_path_in_graph"):
        partes.append(_CODE_GRAPH)
    if tiene("delegate_to_specialist"):
        partes.append(_DELEGAR)
    # El presupuesto de turnos importa cuando hay un ciclo de herramientas
    # que lo pueda gastar. Un especialista sin herramientas de escritura no
    # tiene ciclo que optimizar.
    if tiene("write_file"):
        partes.append(_PRESUPUESTO)
    partes.append(_SIN_EMOJIS)
    return "\n\n".join(partes)


# Compatibilidad: algun sitio puede seguir importando la constante.
_TOOLS_PREAMBLE = build_tools_preamble()


def list_available_skills() -> list[str]:
    return sorted(p.stem for p in SKILLS_DIR.glob("*.md"))


def load_skill_text(skill_name: str) -> str:
    path = SKILLS_DIR / f"{skill_name}.md"
    if not path.exists():
        disponibles = ", ".join(list_available_skills())
        raise FileNotFoundError(f"No existe el skill '{skill_name}'. Disponibles: {disponibles}")
    return path.read_text(encoding="utf-8")


def load_rag_guide() -> str:
    return GUIDE_PATH.read_text(encoding="utf-8")


# --- Carga progresiva -------------------------------------------------


def _sections_config() -> dict:
    if not SECTIONS_CONFIG_PATH.exists():
        return {}
    return yaml.safe_load(SECTIONS_CONFIG_PATH.read_text(encoding="utf-8")) or {}


def split_sections(skill_text: str) -> list[tuple[str, str]]:
    """Parte el skill por encabezados `## `. Devuelve [(titulo, texto)].

    El texto anterior al primer `## ` (titulo del documento, intro) se
    devuelve con titulo "" y SIEMPRE va en el prompt base: ahi suele estar
    la identidad del especialista."""
    matches = list(re.finditer(r"(?m)^## (.+)$", skill_text))
    if not matches:
        return [("", skill_text)]
    out: list[tuple[str, str]] = []
    preamble = skill_text[: matches[0].start()].strip()
    if preamble:
        out.append(("", preamble))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(skill_text)
        out.append((m.group(1).strip(), skill_text[m.start(): end].rstrip()))
    return out


def _matches_prefix(title: str, prefix: str) -> bool:
    return title.startswith(prefix)


def section_titles(skill_name: str) -> list[str]:
    return [t for t, _ in split_sections(load_skill_text(skill_name)) if t]


def get_skill_section(skill_name: str, wanted: str) -> str:
    """Texto completo de una seccion, por su prefijo o por parte del titulo.

    Es lo que sirve la tool `load_skill_section`. Acepta las dos formas
    porque un modelo chico va a escribir cualquiera de ellas por mucho que
    el indice diga una: exigir la exacta seria regalar turnos a errores de
    formato."""
    sections = split_sections(load_skill_text(skill_name))
    wanted_norm = wanted.strip().lstrip("#").strip()
    if not wanted_norm:
        disponibles = ", ".join(t for t, _ in sections if t)
        return f"ERROR: falta el parametro 'section'. Disponibles: {disponibles}"
    for title, body in sections:
        if not title:
            continue
        if _matches_prefix(title, wanted_norm) or wanted_norm.lower() in title.lower():
            return body
    disponibles = ", ".join(t for t, _ in sections if t)
    return (
        f"ERROR: la seccion '{wanted}' no existe en {skill_name}. "
        f"Secciones disponibles: {disponibles}"
    )


def _build_section_index(on_demand: dict, present: list[tuple[str, str]]) -> str:
    """Indice de las secciones a demanda, con su tamaño y la pista de
    cuando cargarla.

    Se genera del .md REAL, asi que no puede desincronizarse: si una
    seccion se renombra en el .md y ya no matchea, desaparece del indice en
    vez de quedar como una entrada muerta que el modelo pediria en vano."""
    filas = []
    for prefix, hint in on_demand.items():
        for title, body in present:
            if title and _matches_prefix(title, str(prefix)):
                pista = " ".join(str(hint).split())
                filas.append(
                    f"- `{prefix}` - {title}  (~{len(body) // 4} tokens)\n  {pista}"
                )
                break
    if not filas:
        return ""
    cabecera = (
        "## Secciones de este skill que NO estan cargadas todavia\n\n"
        "Este documento es mas largo de lo que conviene tener siempre delante, asi "
        "que sus secciones de REFERENCIA no vienen cargadas. Pídelas con la tool "
        "`load_skill_section` (parametro `section`, por ejemplo \"2.\") en el momento "
        "que indica cada una.\n\n"
        "NO son opcionales por ser a demanda: arrancar una fase sin haber cargado su "
        "seccion es exactamente como se producen los resultados genericos que este "
        "skill existe para evitar. Cárgala cuando toque, y agrupa esa llamada con "
        "las demas del mismo turno si puedes.\n"
    )
    return cabecera + "\n" + "\n".join(filas)


def build_system_prompt(skill_name: str) -> str:
    skill_text = load_skill_text(skill_name)
    cfg = _sections_config().get(skill_name)
    if cfg:
        skill_text = _assemble_progressive(skill_text, cfg)
    guide_text = load_rag_guide()
    # Import local: tools importa cosas caras y skills_loader se usa desde
    # sitios que no necesitan el catalogo de herramientas.
    from groq_agent.tools import tool_names_for

    preambulo = build_tools_preamble(tool_names_for(skill_name))
    return (
        f"{skill_text}\n\n---\n\n{guide_text}\n\n---\n\n{preambulo}"
    )


def _assemble_progressive(skill_text: str, cfg: dict) -> str:
    """Prompt base = preambulo + secciones `core` + indice de las demas.

    Si una seccion listada en `core` no existe en el .md (renombrada,
    borrada, reordenada), NO se sigue en silencio: se cae al documento
    completo. Un prompt de mas cuesta tokens y se nota; un especialista al
    que le falta media pagina de su manual de operaciones sin que nadie
    avise produce basura y parece que el modelo es malo."""
    core_prefixes = [str(p) for p in cfg.get("core", [])]
    on_demand = cfg.get("on_demand", {}) or {}
    sections = split_sections(skill_text)
    titles = [t for t, _ in sections if t]

    faltantes = [p for p in core_prefixes if not any(_matches_prefix(t, p) for t in titles)]
    if faltantes:
        return skill_text

    partes = []
    for title, body in sections:
        if not title or any(_matches_prefix(title, p) for p in core_prefixes):
            partes.append(body)

    indice = _build_section_index(on_demand, sections)
    if indice:
        partes.append(indice)
    return "\n\n".join(partes)
