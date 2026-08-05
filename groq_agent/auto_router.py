"""Enrutamiento automatico de skill + modelo para la terminal: dado el
texto de la tarea, reusa la MISMA clasificacion de dominio que el
orquestador de produccion (app/router/heuristic_router.classify_domain)
pero sin el gating de "especialista habilitado en produccion" - aca
cualquiera de los 20 skills del catalogo es una opcion valida, ese
gating solo aplica al servicio que expone /v1/generate.

IMPORTANTE sobre el tamaño de los modelos: ningun proveedor gratis hostea
los modelos EXACTOS del catalogo real (Qwen2.5-Coder-14B,
Qwen2.5-1.5B-Instruct), pero el objetivo de diseño sigue siendo el
mismo - modelos que quepan en una GPU de 24GB, no modelos frontera.

DONDE VIVE AHORA LA ELECCION DE MODELO: este modulo ya NO elige un modelo
concreto. Devuelve el TIER (`base_model_ref` de config/specialists.yaml:
router-tiny / coder-main / devstral-agentic / escalation-30b /
web-builder-large) y es groq_agent/providers.py el que mapea cada tier a
una escalera ORDENADA de candidatos (proveedor + modelo) con failover.
El motivo del cambio: con un solo modelo por tier, un 429 cortaba la tarea
a mitad del ciclo agentico. Los criterios de por que cada modelo esta en
cada escalera - y los intentos fallidos ya descartados - estan
documentados en providers.py, junto a las escaleras.

Para ver que modelos hay disponibles hoy con tus keys:
`orquestador --list-models`; para comprobar que cada candidato responde
DE VERDAD a una tool call: `orquestador --check-providers`.
"""
from __future__ import annotations

import re
from typing import get_args

from app.config import get_specialist
from app.router.heuristic_router import (
    DOMAIN_TO_SPECIALIST_HINT,
    classify_domain,
    match_domain_or_none,
)
from app.schemas import Domain
from groq_agent.groq_client import GroqClient
from groq_agent.providers import CHAIN_BY_TIER, DEFAULT_TIER

# El tier ES la unidad de eleccion de modelo. La escalera concreta de
# (proveedor, modelo) por tier - y el razonamiento de cada eleccion, con
# los intentos fallidos ya descartados - vive en groq_agent/providers.py.
# Aca solo se traduce especialista -> tier, leyendo el `base_model_ref`
# que ya estaba en config/specialists.yaml.
DEFAULT_MODEL = DEFAULT_TIER

FALLBACK_SPECIALIST = "python-specialist"


def model_for_specialist(specialist_name: str) -> str:
    """Devuelve el TIER del especialista, no un modelo concreto - la
    escalera de proveedores la resuelve groq_client via providers.py. Un
    `base_model_ref` que no tenga escalera cae al tier por defecto en vez
    de propagarse como un tier inexistente (que reventaria en chat())."""
    spec_cfg = get_specialist(specialist_name)
    tier = spec_cfg.get("base_model_ref", DEFAULT_TIER)
    return tier if tier in CHAIN_BY_TIER else DEFAULT_TIER


def auto_route(task: str, language_hint: str | None = None) -> tuple[str, str, Domain]:
    """Router heuristico (regex, gratis e instantaneo). Devuelve
    (nombre_del_skill, tier_de_modelo, dominio_detectado)."""
    domain, _risk_signals = classify_domain(task, language_hint)
    specialist = DOMAIN_TO_SPECIALIST_HINT.get(domain, FALLBACK_SPECIALIST)
    model = model_for_specialist(specialist)
    return specialist, model, domain


# --- Router real basado en un modelo chico (no solo regex) ---
#
# El diseño original del catalogo siempre penso el router como un LLM
# chico (Qwen2.5-1.5B-Instruct, nivel "router-tiny"), con la heuristica
# de regex como fallback mientras no hubiera GPU. Esta es la version de
# prueba: usa el tier router-tiny para clasificar, en vez del regex.
# Sirve para comparar en la practica cual clasifica mejor - ver
# eval/scripts/eval_router.py, que ya soporta correr con ROUTER_IMPL=llm
# para el router de produccion; esto es el equivalente para la terminal.
#
# OJO con bajar de ~8B en este tier: probado en vivo, un modelo de 3B
# fallaba 6/6 distinguiendo CONTINUACION de tarea nueva ante mensajes
# cortos y ambiguos, y mandaba la conversacion a un especialista
# completamente distinto. El detalle esta en providers.py, en
# CHAIN_ROUTER_TINY.
_VALID_DOMAINS: tuple[str, ...] = get_args(Domain)
_ROUTER_MODEL = "router-tiny"

# Emparejadores de etiqueta por FRONTERA DE PALABRA, no por subcadena. El 8B
# a veces desobedece "una sola palabra" y contesta con una frase; con un
# `if domain in content` crudo, la etiqueta corta 'go' matcheaba dentro de
# 'algo'/'tengo'/'luego' y clasificaba mal (fallo real en la bateria natural:
# 'ordename esta lista...' -> go). Se toleran las multipalabra escritas con
# espacio o guion bajo ('code_review' o 'code review').
_DOMAIN_MATCHERS: tuple[tuple[str, "re.Pattern[str]"], ...] = tuple(
    (d, re.compile(r"\b" + d.replace("_", "[ _]") + r"\b")) for d in _VALID_DOMAINS
)


def _extraer_dominio(content: str) -> str | None:
    """Devuelve la etiqueta de dominio que aparece ANTES en la respuesta del
    modelo (la primera que dice), o None si no dice ninguna."""
    mejor_pos, mejor_dom = len(content) + 1, None
    for dominio, patron in _DOMAIN_MATCHERS:
        m = patron.search(content)
        if m and m.start() < mejor_pos:
            mejor_pos, mejor_dom = m.start(), dominio
    return mejor_dom

_CONTINUE_SENTINEL = "continue"

# El modelo responde esto cuando el mensaje no pide trabajo sobre
# archivos: un saludo, una pregunta conceptual, una charla, o algo que
# no se entiende. Ahi no se carga ni el skill del especialista ni las
# herramientas (~19.000 tokens de diferencia): se contesta con un
# prompt minimo.
_CHARLA_SENTINEL = "charla"


_MAX_LAST_TASK_CHARS = 300

# El clasificador es un modelo chico eligiendo entre ~24 etiquetas SIN
# ninguna definicion, solo el nombre - encontrado como fuente real de
# errores (ej. "generame un .txt con la palabra hola" clasificado como
# 'office_spreadsheet': ningun dato tabular ni excel de por medio, el
# modelo aparentemente asocio "archivo" con la primera etiqueta de
# oficina que le sono plausible). El grupo office_*/trivial_text/docs es
# el mas propenso a esto porque las 5 etiquetas son semanticamente
# cercanas entre si - una glosa de una linea por dominio alcanza para
# que la eleccion sea por CONTENIDO real del pedido, no por asociacion
# vaga de palabras.
_DOMAIN_GLOSS: dict[str, str] = {
    "python": "codigo Python",
    "typescript": "codigo TypeScript",
    "javascript": "codigo JavaScript",
    "java": "codigo Java",
    "csharp": "codigo C#",
    "cpp": "codigo C/C++",
    "sql": (
        "consultas, esquemas, INDICES o planes de ejecucion SQL: escribir/optimizar una "
        "query, decidir que indice crear, un JOIN, un GROUP BY, acelerar un SELECT. Aunque "
        "no se pegue la palabra SQL, si el pedido es sobre una consulta/tabla/indice de una "
        "base de datos es esto"
    ),
    "rust": "codigo Rust",
    "go": "codigo Go",
    "php": "codigo PHP",
    "ruby": "codigo Ruby (incluye Rails / ActiveRecord: modelos, scopes, migraciones)",
    "kotlin": "codigo Kotlin",
    "iac": (
        "infraestructura y DESPLIEGUE como codigo: Terraform (.tf), un manifiesto de "
        "Kubernetes (Deployment, Service, Ingress, un yaml de k8s), Helm, un Dockerfile "
        "o docker-compose, un playbook de Ansible, un pipeline de CI/CD (GitHub Actions). "
        "OJO: aunque la app sea de Node/Python/otro lenguaje, si lo que se pide es el "
        "Dockerfile / el manifiesto / la infra para desplegarla, es iac, NO el lenguaje "
        "de la app ni 'web'"
    ),
    "web": "un sitio web completo (HTML/CSS, landing page, pagina con contenido real)",
    "testing": "escribir tests/pruebas automatizadas sobre codigo existente",
    "refactor": (
        "reestructurar codigo YA EXISTENTE sin cambiar lo que hace: dividir algo que hace "
        "demasiado, quitar codigo/logica repetida, desenredar algo confuso, mejorar nombres"
    ),
    "docs": "documentacion TECNICA de codigo (README, docstrings, como instalar/arrancar)",
    "security": (
        "un riesgo de seguridad, descrito con terminos tecnicos o en lenguaje llano: "
        "inyeccion (colar comandos/SQL por un campo), datos sensibles sin proteger "
        "(contraseñas en claro/sin cifrar), acceso no autorizado, un input que no se valida"
    ),
    "code_review": (
        "revisar un CONJUNTO DE CAMBIOS ya escrito (un PR, un commit, un diff) para dar "
        "feedback de calidad/riesgo antes de mergear. NO es esto: revisar o arreglar un "
        "fragmento suelto, una funcion o una consulta (eso va al dominio de SU lenguaje), "
        "ni diagnosticar un fallo (eso es debugging)"
    ),
    "debugging": (
        "diagnosticar un fallo cuando NO hay un lenguaje concreto de por medio o el pedido "
        "es PURO diagnostico: un stacktrace/traceback suelto que analizar, 'no entiendo por "
        "que pasa esto' sin mas contexto. OJO - si el fallo es en un lenguaje identificable "
        "(un import de Python que falla, un Go que no compila, un componente de React que se "
        "re-renderiza), NO uses debugging: usa el dominio de ESE lenguaje, que conoce sus "
        "librerias y herramientas. El fallo a secas describe la tarea, no la cambia de dominio"
    ),
    "accessibility": (
        "accesibilidad web (WCAG, a11y, ARIA, contraste, lectores de pantalla, navegacion "
        "por teclado y gestion del foco). Hacer algo usable SIN raton o para tecnologia de "
        "apoyo -que un menu/modal se maneje con el teclado, atrapar el foco- es "
        "accesibilidad AUNQUE se implemente con HTML/CSS/JS: no lo mandes a javascript"
    ),
    "research": (
        "una INVESTIGACION sobre un sujeto del mundo real -una persona, empresa, marca o "
        "tema- que hay que ARMAR reuniendo y CONTRASTANDO varias fuentes: 'investiga la "
        "huella digital de Fulano', 'averigua todo sobre la empresa X', 'que se sabe de Y', "
        "un dossier/OSINT/due diligence, la reputacion online de alguien. Es MAS que una "
        "pregunta suelta (eso es charla): pide recopilar, cruzar fuentes y a menudo separar "
        "homonimos (personas distintas con el mismo nombre). NO es esto: si el pedido es "
        "generar un DOCUMENTO con esa info (un word/una presentacion 'sobre X') va a "
        "office_*; si es MONTAR un sitio web va a 'web'; una duda puntual de un dato es charla"
    ),
    "office_email": "redactar un correo, carta o mensaje formal (no un archivo adjunto)",
    "office_word": (
        "un documento Word/.docx con texto narrativo (informe, propuesta, contrato, o un "
        "documento 'sobre X' con explicaciones) - si el usuario dice 'un word'/'en word'/"
        "'documento word' es ESTO, aunque pida 'guárdalo': lo genera bien (generate_docx), "
        "no python"
    ),
    "office_spreadsheet": (
        "una hoja de calculo Excel/.xlsx con datos TABULARES, formulas o calculos - si dice "
        "'un excel'/'una hoja de calculo' es ESTO, aunque pida 'guárdalo', no python"
    ),
    "office_presentation": (
        "una presentacion PowerPoint/.pptx con diapositivas SOBRE cualquier tema (un producto, "
        "un coche, una empresa, un viaje...) - si el usuario dice 'una presentacion'/'un "
        "powerpoint'/'unas diapositivas' es ESTO, aunque sea sobre un tema del mundo y aunque "
        "pida ponerle fotos o 'guárdalo': lo genera bien (generate_pptx, con diseño y fotos), "
        "NUNCA python"
    ),
    "trivial_text": (
        "redactar o TRANSFORMAR un texto corto para MOSTRAR/copiar: una nota, un resumen "
        "breve, o reescribir / traducir / corregir / acortar / cambiar el tono de un texto "
        "que te dan (eso es una TAREA, no charla; devuelve el texto ya trabajado). El "
        "especialista de este dominio NO guarda archivos, solo devuelve texto. Si en cambio "
        "el pedido pide explicitamente GUARDAR/CREAR un archivo de TEXTO PLANO trivial en el "
        "disco (ej. 'guárdalo como .txt', 'creame un archivo .txt con...'), NO uses "
        "trivial_text - usa 'python' (escribe archivos de texto reales). OJO: esto es SOLO "
        "para texto plano; si pide un formato de OFICINA (Word/.docx, Excel/.xlsx, "
        "PowerPoint/.pptx) usa su dominio office_* -no python-, aunque diga 'guárdalo'"
    ),
    "multi": "la tarea mezcla varios dominios distintos a la vez, ninguno claramente principal",
}


def _domain_list_con_glosas() -> str:
    return "\n".join(f"- {d}: {_DOMAIN_GLOSS[d]}" for d in _VALID_DOMAINS if d in _DOMAIN_GLOSS)


# Regla de PRIORIDAD global, en el cuerpo del prompt y no dentro de una glosa
# suelta: un modelo de 8B honra mucho mejor una regla de alto nivel que un
# "OJO" enterrado en la linea de un dominio. Nace de un hallazgo en vivo -
# midiendo los 117 prompts contra el router-tiny real, cualquier pedido que
# describiera un fallo/test/revision EN UN LENGUAJE concreto ('mi import de
# pandas falla', 'este go no compila') se iba a debugging/testing/code_review
# en vez de al especialista del lenguaje, que es quien conoce sus librerias.
# Es la MISMA decision de diseño que ya tomaba el router heuristico (el
# lenguaje gana al concern transversal); aqui se hace explicita para el LLM.
_REGLA_PRIORIDAD = (
    "REGLA DE PRIORIDAD (aplicala SIEMPRE): si el mensaje menciona o implica un "
    "LENGUAJE o tecnologia concreta (python, typescript, javascript, java, c#, c++, "
    "go, rust, php, ruby, kotlin, sql, un componente de React, un archivo .py/.go/.rb...), "
    "clasifica por ESE lenguaje AUNQUE el mensaje tambien hable de un fallo, un error, "
    "escribir tests, refactorizar, revisar o documentar. Las etiquetas transversales "
    "-debugging, testing, refactor, code_review, docs- son SOLO para cuando NINGUN "
    "lenguaje concreto domina el pedido (un stacktrace suelto sin lenguaje claro, "
    "'revisame este PR' sin decir de que es, 'escribe la documentacion' a secas). "
    "El nombre del FRAMEWORK DE TEST delata el lenguaje: cargo test -> rust, go test -> "
    "go, junit -> java, pytest -> python, rspec -> ruby, phpunit -> php. "
    "Ejemplos: 'mi import de pandas falla' -> python (no debugging); 'este go no "
    "compila' -> go (no debugging); 'escribe tests para esta clase de Java' -> java "
    "(no testing); 'un test con cargo test para esta funcion de Rust' -> rust (no testing); "
    "'un test de tabla en Go con go test' -> go (no testing); 'revisa esta consulta SQL' "
    "-> sql (no code_review)."
)


# La MISMA idea que _REGLA_PRIORIDAD pero para OFICINA, como regla de alto nivel
# (no un matiz enterrado en una glosa, que un 8B ignora). Nace de un fallo real
# y repetido: 'hazme una presentacion del bmw m5' se iba a python-specialist,
# que ni siquiera puede generar un .pptx. python es para CODIGO.
_REGLA_OFICINA = (
    "REGLA DE OFICINA (aplicala SIEMPRE, tiene PRIORIDAD sobre 'python'): un pedido de "
    "un DOCUMENTO Word, una PRESENTACION/PowerPoint, una HOJA de calculo Excel o un "
    "CORREO va a su dominio office_* (office_word / office_presentation / "
    "office_spreadsheet / office_email) AUNQUE sea 'sobre' un tema del mundo (un coche, "
    "una empresa, un viaje), aunque pida buscar informacion, poner fotos o guardarlo. "
    "python es para escribir CODIGO, NUNCA para generar un documento de oficina - de "
    "hecho python no tiene la herramienta para crearlo. Ejemplos: 'hazme una "
    "presentacion chula del bmw m5' -> office_presentation; 'un powerpoint sobre la "
    "energia solar' -> office_presentation; 'me haces un word contando la historia de "
    "Roma' -> office_word; 'un excel con mis gastos' -> office_spreadsheet; 'escribeme "
    "un correo al casero' -> office_email."
)


def _pista_heuristica(hint: str | None) -> str:
    """Bloque que inyecta la propuesta del router HEURISTICO (regex, 0 tokens)
    como prior fuerte. El heuristico acierta el dominio ~99% en la bateria;
    el 8B, ~74%. Asi que en vez de pedirle al modelo que clasifique de cero,
    se le da la respuesta del regex y solo se le pide CONFIRMARLA o corregirla
    si esta claramente mal (arquitectura hibrida elegida por el usuario tras
    medir ambos en vivo). No se toca la decision charla/continuacion, que
    sigue siendo puro juicio del modelo (lo que el regex no sabe hacer)."""
    if not hint:
        return ""
    return (
        "\n\nPISTA: un clasificador por reglas (rapido y muy fiable, aunque literal) "
        f"sugiere que el dominio es '{hint}'. Es una propuesta FUERTE: si encaja con lo "
        "que el mensaje pide, CONFIRMA ese dominio devolviendo esa misma etiqueta. "
        "Devuelve otra SOLO si el mensaje pertenece claramente a un dominio distinto. "
        "Esta pista es solo para el dominio; NO la uses para decidir charla o continuacion."
    )


def _classifier_system_prompt(
    current_specialist: str | None,
    last_task: str | None = None,
    heuristic_hint: str | None = None,
) -> str:
    """Si hay un especialista activo, el clasificador tiene que poder
    decir "esto no es una tarea nueva" en vez de forzar una etiqueta de
    dominio para cualquier cosa - un saludo, un "si"/"ok" contestando
    una pregunta pendiente, una aclaracion, etc. son CONTINUACION, no
    tareas nuevas, y clasificarlas por dominio (aunque sea "python" por
    default) reinicia la conversacion sin venir a cuento. Esto reemplaza
    la heuristica cruda de "el regex no matcheo nada" por una decision
    real del modelo, que entiende contexto y no solo palabras clave.

    IMPORTANTE: pasar last_task (la tarea original de la conversacion en
    curso) no es opcional en la practica - sin ella, el clasificador solo
    sabe el NOMBRE del especialista activo (ej. 'web-builder-specialist')
    pero no QUE se estaba construyendo, y un mensaje corto y generico
    como '¿donde esta guardado exactamente?' (claramente una continuacion
    de "hazme una web...") puede sonar a cualquier otra cosa (ej. un
    archivo de oficina) sin ese contexto - bug real observado en
    produccion: se clasifico como 'office_spreadsheet' con un especialista
    de office-spreadsheet-specialist tomando la posta sin saber de que
    hablaba el usuario."""
    if current_specialist == CHARLA:
        # Hay un hilo de CHARLA en curso (preguntas que se contestan buscando
        # en la web). Un seguimiento -"y del nuevo?", "cual es la fuente"- debe
        # QUEDARSE en la charla (que busca), no saltar a un especialista que
        # responde de memoria: ese era el bug ("que motor lleva el 118d" ->
        # python-specialist, que invento "1.8 TDI"). Solo un pedido claro de
        # trabajo sobre archivos rompe el hilo.
        contexto = (
            f' La ultima pregunta fue sobre: "{last_task[:_MAX_LAST_TASK_CHARS]}".'
            if last_task else ""
        )
        return (
            "Eres un clasificador de un sistema de orquestacion. El usuario esta en una "
            "CHARLA de preguntas y respuestas que se contestan BUSCANDO en la web (datos "
            f"del mundo real: fechas, precios, hechos, personas, productos, coches...).{contexto} "
            "Dado el proximo mensaje, respondes EXCLUSIVAMENTE con UNA palabra, sin "
            "explicacion, sin puntuacion y sin comillas:\n\n"
            "- Si es otra PREGUNTA o charla -un dato del mundo real, una pregunta "
            "conceptual, un seguimiento de lo anterior ('y del nuevo?', 'y de ahora?', "
            "'cual es la fuente', 'pero cual es el codigo'), un saludo, un "
            f"agradecimiento-, respondes exactamente: {_CONTINUE_SENTINEL}\n"
            "- SOLO si el mensaje pide CREAR, MODIFICAR, REVISAR o LEER un ARCHIVO o "
            "codigo concreto del usuario (un pedido de trabajo real, no una pregunta), "
            "respondes con la etiqueta (sola, no su glosa) de su dominio:\n\n"
            + _domain_list_con_glosas() + "\n\n" + _REGLA_PRIORIDAD + "\n\n" + _REGLA_OFICINA + "\n\n"
            "Ante la duda -si PODRIA ser una pregunta mas- respondes "
            f"{_CONTINUE_SENTINEL}: pasar a un especialista solo se justifica si el "
            "usuario claramente pide trabajo sobre sus archivos o codigo."
            + _pista_heuristica(heuristic_hint)
        )
    if not current_specialist:
        return (
            "Eres un clasificador de tareas de programacion/oficina de un sistema de "
            "orquestacion. Dado un mensaje del usuario, respondes EXCLUSIVAMENTE con "
            "UNA palabra, sin explicacion, sin puntuacion y sin comillas.\n\n"
            "- Si el mensaje NO pide trabajo sobre archivos - un saludo, un "
            "agradecimiento, una pregunta conceptual o teorica, una charla, algo que "
            "no se entiende, o cualquier cosa que se conteste hablando y no haciendo - "
            f"respondes exactamente: {_CHARLA_SENTINEL}\n"
            "- Si en cambio pide CREAR, MODIFICAR, REVISAR o LEER algo concreto, "
            "respondes con la etiqueta (la etiqueta sola, no su glosa) que mejor "
            "describa su dominio segun lo que el pedido REALMENTE dice:\n\n"
            + _domain_list_con_glosas()
            + "\n\n" + _REGLA_PRIORIDAD
            + "\n\n" + _REGLA_OFICINA
            + "\n\nFijate en la INTENCION, no en que aparezca una palabra suelta: "
            "'que dices?', o un mensaje con erratas que no se entiende, es charla "
            "aunque contenga alguna palabra que suene tecnica.\n\n"
            "IMPORTANTE: 'charla' significa que NO se pide trabajo, no que no "
            "sepas en que cajon meterlo. Si el mensaje pide crear o modificar "
            "algo y NINGUNA etiqueta encaja del todo (un modelo 3D, un plano, un "
            "esquema), elige la mas cercana o 'multi' - nunca 'charla'."
            + _pista_heuristica(heuristic_hint)
        )
    contexto_tarea = (
        f" sobre esta tarea: \"{last_task[:_MAX_LAST_TASK_CHARS]}\"" if last_task else ""
    )
    return (
        "Eres un clasificador de tareas de programacion/oficina de un sistema de "
        f"orquestacion. Ahora mismo hay una conversacion en curso con el especialista "
        f"'{current_specialist}'{contexto_tarea}. Dado el proximo mensaje del usuario, "
        "respondes EXCLUSIVAMENTE con UNA palabra, sin explicacion, sin puntuacion, sin "
        "comillas:\n\n"
        f"- Si el mensaje es una CONTINUACION de ESA conversacion (una pregunta sobre lo "
        f"que se acaba de hacer - donde quedo guardado, que contiene, pedir un cambio o "
        f"correccion sobre lo mismo -, una respuesta corta, un saludo, un 'si'/'ok'/'dale' "
        f"contestando algo que el especialista pregunto, o cualquier cosa que no sea un "
        f"pedido de tarea nueva y distinta), respondes exactamente: {_CONTINUE_SENTINEL}\n"
        f"- Si NO pide trabajo sobre archivos y ademas NO es continuacion de lo de "
        f"arriba - un saludo suelto, una pregunta conceptual, algo que no se entiende - "
        f"respondes exactamente: {_CHARLA_SENTINEL}\n"
        "- Si en cambio es claramente una tarea nueva y no relacionada con la de arriba, "
        "respondes con la palabra (la etiqueta sola, no su glosa) de esta lista que mejor "
        "describa su dominio segun lo que el pedido REALMENTE dice:\n\n"
        + _domain_list_con_glosas() + "\n\n" + _REGLA_PRIORIDAD + "\n\n" + _REGLA_OFICINA + "\n\n"
        "Cambiar de especialista tiene un costo real (el nuevo arranca sin memoria de esta "
        "conversacion), asi que es la EXCEPCION, no la opcion por defecto: ante la duda, o "
        "si el mensaje PODRIA razonablemente ser sobre lo mismo que se viene hablando, "
        f"respondes {_CONTINUE_SENTINEL}. Reserva una etiqueta de dominio nueva solo para "
        "cuando el mensaje sea inequivocamente un pedido distinto y no relacionado (ej. "
        "'ahora hazme un excel con gastos' en medio de una conversacion sobre una web).\n\n"
        "OJO: un pedido explicito de crear/hacer algo nuevo (verbos como 'hazme', 'podrias "
        "hacerme', 'quiero un/una...', 'necesito que generes...') es SIEMPRE señal de "
        "dominio, incluso si en la MISMA frase el usuario pide hablar/planificar antes de "
        "generar nada (ej. 'hablemos antes', 'no generes nada aun', 'quiero discutir el "
        "diseño primero'). Esa parte solo describe COMO quiere que trabaje el especialista "
        "(charlando antes de escribir archivos), no cambia QUE esta pidiendo - clasifícala "
        "igual por su dominio, nunca como continuacion. Ejemplo: 'me podrias hacer una web, "
        "hablemos antes, no generes nada aun' -> 'web' (no continue), aunque la conversacion "
        "activa fuera sobre otra cosa."
        + _pista_heuristica(heuristic_hint)
    )


# Lo que devuelve llm_classify_domain cuando el mensaje es charla. Es un
# centinela propio y no None porque None YA significa otra cosa
# ("continuacion de la conversacion en curso"); confundirlos haria que un
# saludo reanudase la tarea anterior.
CHARLA = "__charla__"


def _pide_trabajo(task: str) -> bool:
    """Veto sobre un veredicto de `charla`: ¿el mensaje pide algo, sea del
    dominio que sea?

    El fallo real que esto evita: "hazme un modelo 3D de un bloque motor de
    3 cilindros" acababa contestado con un tutorial de Blender. La lista de
    dominios no tiene ninguno de 3D/CAD, asi que el modelo chico buscaba
    una etiqueta que encajase, no encontraba ninguna, y se acogia a
    `charla`. Pero `charla` significa "no se pide trabajo", NO "no se en
    que cajon meterlo": ante un pedido sin dominio claro hay que elegir el
    mas cercano y trabajar, no dar conversacion.

    Aqui la heuristica de palabras si es de fiar, porque solo se usa en UNA
    direccion: reconocer un verbo de accion explicito ("hazme", "crea",
    "genera") no requiere entender el mensaje, y equivocarse solo cuesta
    cargar herramientas de mas. El juicio de que ES charla lo sigue
    haciendo el modelo."""
    from groq_agent import prompt_classifier

    # Solo la señal POSITIVA (un verbo de accion, o un archivo nombrado), no
    # `necesita_herramientas`: esa devuelve True ante la duda - por diseño,
    # porque cargar de mas es su error barato - y con eso el veto se
    # disparaba hasta con "qie dices?", que tiene dos palabras y ninguna
    # peticion. Un veto que salta ante la duda no es un veto.
    return bool(
        prompt_classifier._ACCION.search(task) or prompt_classifier._ARTEFACTO.search(task)
    )


def llm_classify_domain(
    client: GroqClient,
    task: str,
    language_hint: str | None = None,
    current_specialist: str | None = None,
    last_task: str | None = None,
) -> Domain | None | str:
    """Devuelve el dominio, o None si es continuacion de la conversacion
    en curso (solo posible si se paso current_specialist), o CHARLA si el
    mensaje no pide trabajo sobre archivos.

    La decision charla/tarea la toma el MODELO, no una lista de palabras:
    una lista reconoce, no entiende, y fallaba con las erratas ('qie
    dices?' acababa clasificado como javascript)."""
    text = f"[{language_hint}] {task}" if language_hint else task
    # Propuesta del router HEURISTICO (regex, 0 tokens): se le pasa al modelo
    # como prior fuerte para que la CONFIRME o corrija (hibrido). None si el
    # regex no matcheo nada -> el modelo decide sin prior.
    hint = match_domain_or_none(task, language_hint)
    response = client.chat(
        messages=[
            {"role": "system",
             "content": _classifier_system_prompt(current_specialist, last_task, hint)},
            {"role": "user", "content": text},
        ],
        model=_ROUTER_MODEL,
        temperature=0.0,
    )
    content = response["choices"][0]["message"]["content"].strip().lower()
    if _CHARLA_SENTINEL in content and not _pide_trabajo(task):
        return CHARLA
    if current_specialist and _CONTINUE_SENTINEL in content:
        return None
    # El modelo a veces agrega puntuacion o una palabra de mas alrededor -
    # se busca la primera etiqueta valida (por frontera de palabra, para que
    # 'go' no matchee dentro de 'algo') en vez de exigir un match exacto.
    dominio = _extraer_dominio(content)
    if dominio is not None:
        return dominio  # type: ignore[return-value]
    # Salida ilegible: caer al dominio del HEURISTICO (99% de acierto) antes
    # que al relleno 'python'. Solo si el regex tampoco matcheo, 'python'.
    return hint or "python"


def auto_route_llm(
    client: GroqClient,
    task: str,
    language_hint: str | None = None,
    current_specialist: str | None = None,
    last_task: str | None = None,
) -> tuple[str, str, Domain] | None | str:
    """Igual que auto_route() pero clasificando con un modelo real
    (router-tiny) en vez de regex - entiende contexto, no solo palabras
    clave. Devuelve (nombre_del_skill, modelo, dominio_detectado), o
    None si el mensaje es continuacion de la conversacion con
    current_specialist (el caller debe reusar el especialista/modelo
    anterior en ese caso), o CHARLA si el mensaje no pide trabajo sobre
    archivos y hay que contestarlo con el prompt minimo. Cuesta una
    llamada extra al proveedor (tokens + latencia, centavos de fraccion
    de centavo) por tarea - la MISMA que decide el dominio, asi que
    detectar la charla aqui no cuesta nada extra."""
    domain = llm_classify_domain(client, task, language_hint, current_specialist, last_task)
    if domain is None or domain == CHARLA:
        return domain
    specialist = DOMAIN_TO_SPECIALIST_HINT.get(domain, FALLBACK_SPECIALIST)
    model = model_for_specialist(specialist)
    return specialist, model, domain
