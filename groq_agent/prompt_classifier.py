"""¿Este mensaje necesita herramientas, o es solo una pregunta?

EL AHORRO
Una pregunta como "¿que diferencia hay entre flex y grid?" no necesita
escribir ni leer nada, pero hoy paga el paquete completo: el skill entero
(hasta 44.000 caracteres en web-builder), los esquemas de las 28
herramientas de ese especialista (27.000) y el preambulo de tools (5.500).
Casi 19.000 tokens para responder tres frases.

Si el mensaje es conversacional, se manda un prompt minimo y CERO
herramientas. Medido: ~19.000 tokens -> ~400.

QUIEN DECIDE ESTO EN REALIDAD: EL MODELO
Esta heuristica ya NO es el camino principal. Con el router LLM (el modo
por defecto) la decision charla/tarea la toma el propio clasificador de
router-tiny, en la MISMA llamada que ya elige el dominio: cero llamadas y
cero tokens extra, y entiende la intencion en vez de reconocer palabras.
Ver groq_agent/auto_router.py (_CHARLA_SENTINEL).

El motivo del cambio: una lista de palabras reconoce, no entiende. Fallos
vistos en uso real - "qie dices?" acababa clasificado como javascript
porque una errata matcheo un patron, y "hola" solo se salvaba por estar
escrito literalmente en la lista de saludos (no asi "buenas tardes que tal
todo").

PARA QUE SIGUE ESTANDO
Como red, en los dos casos donde no hay un router al que preguntar:
`--router heuristic` y cuando la llamada al router falla. Ahi una
heuristica imperfecta es mejor que nada, porque el coste de equivocarse es
asimetrico y bajo: si se marca como pregunta algo que si necesitaba
herramientas, el modelo responde sin poder actuar y el usuario lo repite.
Por eso ante la duda SIEMPRE devuelve "necesita herramientas": el error
barato es cargar de mas.
"""
from __future__ import annotations

import re

# Verbos de accion: si aparece uno, hay trabajo real que hacer. Ante la
# duda esta lista manda sobre cualquier señal de pregunta - "¿me puedes
# crear un excel?" es una peticion disfrazada de pregunta.
_ACCION = re.compile(
    r"\b("
    r"haz|hazme|haceme|hacer|crea|creame|crear|genera|generame|generar|"
    r"escribe|escribeme|escribir|construye|construir|monta|montar|"
    r"arregla|arreglar|corrige|corregir|repara|reparar|soluciona|"
    r"edita|editar|modifica|modificar|cambia|cambiar|actualiza|actualizar|"
    r"borra|borrar|elimina|eliminar|renombra|mueve|mover|"
    r"refactoriza|refactorizar|implementa|implementar|añade|añadir|agrega|"
    # Verbos de CREAR que no son de codigo. Faltaban, y con ellos se
    # escapaba "diseñame una carcasa": no es charla, es un encargo.
    r"diseña|disena|diseñame|disename|diseñar|disenar|"
    r"modela|modelame|modelar|dibuja|dibujame|dibujar|traza|trazar|"
    r"design|model|draw|sketch|"
    r"instala|instalar|ejecuta|ejecutar|corre|correr|lanza|"
    r"revisa|revisar|audita|auditar|analiza|analizar|comprueba|comprobar|"
    r"busca|buscar|descarga|descargar|guarda|guardar|exporta|"
    r"lee|leer|abre|abrir|mira|mirar|"
    r"traduce|traducir|resume|resumir|convierte|convertir|"
    # Verbos de TRANSFORMAR un texto que se daban por charla cuando el modelo
    # dudaba (bug en vivo: "reescribe este parrafo", "acorta este texto" ->
    # charla). Son trabajo inequivoco sobre un texto, no conversacion.
    r"reescribe|reescribir|reformula|reformular|acorta|acortar|"
    # "documenta" es SIEMPRE trabajo (documentar codigo/un proyecto), no
    # charla - faltaba y hacia que "documenta esta funcion" se tratara como
    # conversacion cuando el modelo dudaba (bug visto en vivo: -> charla).
    # "explica" NO se añade a proposito: "explicame la diferencia entre X e
    # Y" es charla legitima, y el veto solo debe llevar señales inequivocas.
    r"documenta|documentame|documentar|document|"
    r"make|create|build|write|fix|edit|update|delete|add|run|refactor|"
    r"implement|review|analyze|check|read|search"
    r")\b",
    re.IGNORECASE,
)

# Señales de que se habla de artefactos concretos: si hay una ruta, una
# extension o un @archivo, hay trabajo sobre ficheros.
_ARTEFACTO = re.compile(
    r"(@[\w./\\-]+|[\w-]+\.(py|js|ts|tsx|jsx|html|css|json|yaml|yml|md|txt|"
    r"csv|xlsx|docx|pptx|sql|java|go|rs|rb|php|cs|cpp|h|sh|ps1)\b|[/\\][\w.-]+)",
    re.IGNORECASE,
)

# Aperturas tipicas de pregunta conceptual.
_PREGUNTA = re.compile(
    r"^\s*(¿|que |qué |cual |cuál |como |cómo |por que |por qué |porque |"
    r"cuando |cuándo |donde |dónde |quien |quién |cuanto |cuánto |"
    r"para que |para qué |sirve |existe |hay |se puede |puedo |es mejor |"
    r"diferencia |what |which |how |why |when |where |who |is |are |can |does"
    r")",
    re.IGNORECASE,
)

# Frases que piden opinion o explicacion, no accion.
_CONVERSACIONAL = re.compile(
    r"\b(explicame|explica|explicar|dime|contame|cuentame|opinas|opinion|"
    r"recomiendas|recomendacion|aconsejas|mejor practica|buenas practicas|"
    r"diferencia entre|pros y contras|ventajas|desventajas|significa|"
    r"explain|tell me|what do you think|recommend|difference between)\b",
    re.IGNORECASE,
)


# Saludos y cortesias. Van aparte del resto porque son CORTOS, y la regla
# de "menos de N palabras -> ante la duda carga todo" los mandaba al
# paquete completo: un "hola" cargaba el skill entero y las 28
# herramientas, y el modelo, viendose con herramientas y sin tarea,
# llamaba a ask_user para preguntar que se queria. Visto en uso real.
# OJO con lo que se mete aqui: "ok", "vale", "si", "dale", "perfecto" y
# "genial" NO son saludos - son CONTINUACIONES. Contestan a algo que el
# especialista pregunto ("¿lo hago asi?" -> "ok") y tienen que seguir
# yendo a ese especialista con su conversacion, no al prompt minimo, que
# no sabe nada de lo que se estaba haciendo. Solo entran aqui las formulas
# que abren o cierran, nunca las que responden.
_SALUDO = re.compile(
    r"^\s*(hola|buenas|buenos d[ií]as|buenas tardes|buenas noches|hey|"
    r"qu[eé] tal|c[oó]mo est[aá]s|hi|hello|saludos|"
    r"gracias|muchas gracias|adi[oó]s|chao|hasta luego)"
    r"[\s!¡.,?¿]*$",
    re.IGNORECASE,
)

# Por debajo de esto no hay contexto suficiente para decidir nada, y un
# saludo o un "si" son continuacion, no una tarea.
_MIN_PALABRAS = 3


def necesita_herramientas(mensaje: str) -> bool:
    """True si hay que cargar el skill completo y las herramientas.

    Devuelve True ante cualquier duda: cargar de mas cuesta tokens, cargar
    de menos deja al agente sin poder hacer su trabajo."""
    if not mensaje or not mensaje.strip():
        return True

    texto = mensaje.strip()

    # Un saludo o un "gracias" se contestan, no se convierten en una
    # tarea. Va lo PRIMERO, antes incluso que la comprobacion de
    # artefactos, porque es la unica señal que no admite duda.
    if _SALUDO.match(texto):
        return False

    # Un artefacto concreto (ruta, extension, @archivo) implica trabajo
    # sobre ficheros, aunque la frase tenga forma de pregunta:
    # "¿que hace app/config.py?" necesita leerlo.
    if _ARTEFACTO.search(texto):
        return True

    # Un verbo de accion manda sobre la forma interrogativa: "¿me creas un
    # excel?" es una peticion.
    if _ACCION.search(texto):
        return True

    palabras = texto.split()
    if len(palabras) < _MIN_PALABRAS:
        return True

    # Sin artefactos ni verbos de accion: si ademas tiene forma de pregunta
    # o de peticion de explicacion, es conversacional.
    if _PREGUNTA.search(texto) or _CONVERSACIONAL.search(texto):
        return False

    return True


_PROMPT_CONVERSACIONAL = """Eres un asistente tecnico dentro de una terminal \
de desarrollo. El usuario te esta haciendo una pregunta, no pidiendote que \
hagas nada sobre sus archivos.

Responde directo y al grano, con criterio tecnico y sin relleno. No puedes \
leer ni escribir archivos del usuario en este turno, asi que no digas que vas \
a hacerlo; si la respuesta REQUIERE mirar o tocar sus archivos, dilo en una \
linea y pídele que lo pida explicitamente (ej. "revisa @app/config.py").

TIENES busqueda web: `search_web` (una consulta -> lista de resultados) y \
`deep_research` (investiga y lee varias fuentes, para dudas que hay que \
contrastar). Úsala SOLA, sin avisar ni pedir permiso.

REGLA CLAVE: para cualquier DATO CONCRETO Y VERIFICABLE del mundo real -una \
medida, una fecha, una cifra, una estadistica, un dato biografico de una \
persona, quien/cuando/donde/cuanto de un hecho o cosa, o algo que dependa del \
MOMENTO (fecha de hoy, un precio, una noticia, la ultima version)- LLAMA a \
search_web YA y responde con lo que encuentres. En los detalles concretos tu \
memoria FALLA aunque te parezca que los sabes: si contestas "de cabeza" te los \
INVENTAS con total seguridad (ej. "cuanto medía Rasputin" -> BUSCALO, no te lo \
sabes; NO respondas un numero de memoria). NUNCA digas "no tengo datos", "no \
estoy seguro" ni "puedo buscarlo si quieres": eso es la señal de buscar - busca \
y da la respuesta, sin anunciarlo. Solo si TRAS buscar no lo encuentras, dilo \
(y jamas te lo inventes). Contesta directo, sin buscar, SOLO explicaciones y \
conceptos generales que se saben con certeza (que es la recursividad, como \
funciona un bucle for, sintaxis de un lenguaje).

CITA LA FUENTE de lo que saques de search_web/deep_research, de UNA forma: \
(a) un marcador enlazado tras el dato, `1,68 m [▪](url-real)`; o (b) al final, \
`Fuentes: [dominio](url), ...`. Siempre enlace markdown `[texto](url)` con la \
URL REAL del resultado (campo `url`), nunca inventada. Lo que respondas sin \
buscar no lleva fuente.

Si el usuario solo saluda o da las gracias, contéstale en una linea y dile \
en otra que puede pedirte - no le abras un menu de opciones ni le preguntes que \
quiere: acaba de escribirte, si quisiera algo concreto lo habria dicho.

PROHIBIDO usar emojis, en el chat y en cualquier texto que generes. Para \
marcar exito/fallo o dar enfasis usa simbolos tipograficos: ✓ ✗ → - •"""


def prompt_conversacional() -> str:
    """System prompt minimo para una pregunta: busqueda web para todo lo de
    tiempo real (incluida la fecha), en vez de los ~19.000 tokens del paquete
    completo de un especialista."""
    return _PROMPT_CONVERSACIONAL
