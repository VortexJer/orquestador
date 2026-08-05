"""Gestion de contexto: que se reenvia y que no en cada vuelta.

EL PROBLEMA
-----------
La API es sin estado: cada vuelta del ciclo agentico reenvia la
conversacion ENTERA. Con `--max-turns` ilimitado, una tarea larga acaba
mandando decenas de KB de HTML de referencia, screenshots ya vistos y
listados de directorios en cada llamada. Eso cuesta tokens, pero el
sintoma que se nota primero es de CALIDAD: el modelo "se pierde" -
contradice su propio plan, repite pasos - no porque haya olvidado nada,
sino porque el ruido de contexto viejo pesa mas que la señal reciente.

QUE HACE, EN DOS NIVELES
------------------------
1. PODA (barata, sin llamadas al modelo). Recorta el contenido de los
   resultados de tool viejos segun su clase de retencion. Es donde esta
   casi todo el ahorro: un `read_file` de 3.000 lineas o la salida de
   `render_check` pesan mas que toda la conversacion junta.
2. COMPACTACION (con una llamada a un modelo barato). Solo si tras podar
   se sigue pasando del presupuesto: resume los turnos mas antiguos en una
   nota y los sustituye.

POR QUE NO ES LA LISTA BLANCA QUE HABIA ANTES
---------------------------------------------
La version anterior vivia en agent_loop con una lista de 6 tools
"compactables" y un contador por tool. Tres huecos reales:
  - Las tools mas pesadas no estaban en la lista (`render_check`,
    `critique_screenshot`, `search_images`, `web_fetch`).
  - No habia presupuesto TOTAL: podaba por tool y nunca miraba el tamaño
    de la conversacion.
  - Dependia de un `tool_call_log` que se construye durante la corrida:
    al reanudar una sesion con `-c` empezaba vacio, asi que el historial
    heredado - justo el mas viejo y mas gordo - nunca se podaba.
Este modulo trabaja SOLO sobre la lista de mensajes, asi que funciona
igual en una sesion nueva y en una reanudada.

INVARIANTE QUE NO SE PUEDE ROMPER
---------------------------------
La API exige que cada mensaje `assistant` con `tool_calls` vaya seguido de
un mensaje `tool` por cada llamada, con su `tool_call_id`. Romper ese
emparejamiento da un 400 en TODOS los proveedores. Por eso la poda solo
ENCOGE el `content` de un mensaje y nunca lo borra, y la compactacion solo
corta en fronteras seguras.
"""
from __future__ import annotations

import json

# Clases de retencion. La pregunta para clasificar una tool no es "¿su
# salida es grande?" sino "¿el modelo necesita el CONTENIDO literal mas
# tarde, o le basta recordar que ya lo hizo?".
#
# EFIMERO: el modelo mira la salida, actua sobre ella (adapta un
# componente, corrige un overflow) y el contenido literal ya no aporta -
# solo hace falta saber que la llamada ocurrio. Aca esta el peso muerto.
EFIMERO = {
    # Exploracion del workspace
    "read_file", "list_dir", "glob_search", "grep_search",
    # Libreria de referencia web-kit: HTML entero, y de los mas gordos
    "read_reference_component", "list_reference_components",
    # Verificacion visual: el veredicto se actua en el momento; el volcado
    # de consola/regiones no sirve dos turnos despues
    "verificar_web", "render_check", "critique_screenshot", "lint_web_page",
    "web_lint",
    "check_links", "run_check",
    # Busquedas: se elige una opcion y el resto del listado sobra
    "search_images", "search_web_image", "search_web", "web_fetch",
    "analyze_image", "classify_image_content",
    # Consultas sobre el propio repo
    "query_code_graph", "shortest_path_in_graph", "search_hard_cases",
}

# DURADERO: datos de negocio reales y respuestas del usuario. Son la fuente
# de verdad de la tarea y reinventarlos es peor que reenviarlos: si se
# recortan, el modelo empieza a inventar horarios, telefonos o precios.
# Nunca se podan, cuesten lo que cuesten.
DURADERO = {
    "fetch_business_from_maps", "fetch_menu_and_reviews_from_maps",
    "fetch_menu_photos_from_maps", "extract_business_info",
    "extract_menu_text", "ask_user",
    # Secciones del propio skill. Esto NO son datos, son INSTRUCCIONES: el
    # modelo carga el sistema de diseño en la Fase 0 y escribe el CSS en la
    # Fase 3, varias vueltas despues. Estaba clasificado como efimero con el
    # argumento de que "la instruccion queda aplicada", y no es cierto: lo
    # que queda aplicado es lo que ya hizo, no lo que le falta por hacer.
    # Podarlo le quita la paleta y las reglas justo antes de usarlas, y por
    # fuera se ve como que el especialista ha dejado de seguir su guia.
    "load_skill_section",
    # Escrituras: el modelo necesita saber la ruta exacta que quedo
    "write_file", "edit_file", "generate_docx", "generate_xlsx",
    "generate_pptx", "delete_files",
}

# EFIMERO_PESADO: salidas de decenas de KB cada una. Guardar dos copias
# intactas de estas es casi todo el peso muerto que quedaba - un componente
# de web-kit son ~25KB, dos son 50KB reenviados en cada vuelta hasta el
# final de la tarea. Con la ULTIMA alcanza: el modelo lee el componente, lo
# adapta, y lo que necesita despues es su propio codigo ya escrito, no el
# original otra vez.
EFIMERO_PESADO = {
    "read_reference_component", "list_reference_components",
    "verificar_web", "render_check", "search_images", "search_web_image",
    "read_file", "web_fetch", "search_web",
}

# Herramientas cuyo peso NO esta en su resultado sino en los ARGUMENTOS de
# la llamada: el contenido entero del archivo viaja dentro del mensaje del
# assistant que la invoco.
#
# Aqui estaba el mayor peso muerto que quedaba, y la poda de resultados no
# lo veia porque solo mira mensajes `role == "tool"`. En una web tipica el
# modelo escribe index.html (~25KB) y styles.css (~45KB): 70KB que se
# reenviaban INTACTOS en cada vuelta hasta el final de la tarea. A ocho
# vueltas restantes son ~140.000 tokens, mas de un tercio del gasto de
# construir una web entera.
#
# Es seguro encogerlos: agent_loop parsea los argumentos y ejecuta la
# herramienta en el MISMO turno (ver agent_loop.py, donde se hace
# json.loads sobre call["function"]["arguments"]), y nadie vuelve a leerlos
# del historial despues. Y el contenido no se pierde: esta en el disco, que
# es justo lo que la herramienta acababa de hacer.
ESCRITURA_PESADA = {
    "write_file", "edit_file", "generate_docx", "generate_xlsx", "generate_pptx",
}

# Argumentos que identifican la llamada y caben en nada: se conservan
# siempre para que el modelo siga sabiendo QUE escribio y DONDE.
ARGS_QUE_SE_CONSERVAN = {"path", "ruta", "output_path", "encoding"}

# Un argumento mas corto que esto no compensa tocarlo.
ARG_MINIMO_PARA_PODAR = 200

# Cuantas llamadas recientes de cada tool efimera se dejan intactas, por si
# el modelo necesita volver a mirarlas de inmediato.
KEEP_RECENT_POR_TOOL = 2
KEEP_RECENT_PESADAS = 1
# De las escrituras se conserva solo la ULTIMA con su contenido literal: es
# la que el modelo puede necesitar para encadenar una correccion sin volver
# a leer el archivo.
KEEP_RECENT_ESCRITURAS = 1
# A cuanto se recorta una salida efimera vieja. Suficiente para que el
# modelo reconozca QUE era sin arrastrar el cuerpo.
RECORTE_CHARS = 400

# A partir de que tamaño se poda. Por debajo NO SE TOCA NADA, y esa es
# toda la gracia.
#
# POR QUE NO SE PODA EN CADA VUELTA
# Los proveedores cobran barato el PREFIJO que ya vieron: si los primeros
# mensajes de esta llamada son identicos a los de la anterior, reutilizan su
# trabajo. Cualquier cambio en un mensaje viejo invalida ese cache desde ahi
# hasta el final.
#
# Podar en cada vuelta hacia justo eso: segun entran llamadas nuevas, las
# anteriores se caen de la ventana de "recientes" y se recortan, asi que el
# prefijo cambiaba constantemente. Medido en local sobre una web de 12
# vueltas: 6 de 12 rompian el cache y la parte cacheable bajaba del 86% al
# 71%. Se ahorraba un 35% de caracteres para pagar el resto a precio
# completo - con un cache al 10%, el cambio salia PERDIENDO.
#
# Con umbral, la poda es un evento raro: se deja crecer, se corta de golpe
# (una sola rotura), y despues el prefijo vuelve a ser estable muchas
# vueltas seguidas. Se queda lo mejor de las dos.
#
# El umbral se mide SIN el system prompt: no cambia nunca y no se puede
# podar, asi que incluirlo solo desplazaria el listón segun el especialista
# (el de web son ~34.000 chars y el de python ~11.000 - con un umbral sobre
# el total, uno podaria siempre y el otro nunca, por el prompt y no por la
# conversacion).
UMBRAL_DE_PODA_CHARS = 30_000

# Un umbral solo no basta: despues de podar, el tamaño se queda POR ENCIMA
# del umbral (lo que no se puede tocar - datos de negocio, la ventana
# reciente - ya pesa mas que eso), asi que volvia a dispararse en la vuelta
# siguiente y seguian rompiendose 6 de 12. Medido.
#
# Con una cadencia, la poda ocurre como mucho una vez cada N vueltas: se
# acumula peso muerto durante N-1 vueltas de prefijo estable y se corta todo
# junto en una. Es el mismo ahorro de caracteres concentrado en menos
# roturas de cache.
PODAR_CADA_N_VUELTAS = 4

# Lo que devuelve la cadencia cuando NO hay que podar en ninguna vuelta.
NUNCA_PODAR = 0

# Presupuesto total de la conversacion (sin el system prompt) antes de
# compactar. ~4 chars por token: 120.000 chars son ~30.000 tokens, que con
# un system prompt de ~11.000 deja margen de sobra en los 32K-128K de
# contexto de los modelos de estos tiers.
PRESUPUESTO_CHARS = 120_000
# Turnos recientes que la compactacion no toca nunca: es donde esta el
# trabajo en curso.
COMPACTAR_PRESERVA_RECIENTES = 8


def _nombre_por_tool_call_id(messages: list[dict]) -> dict[str, str]:
    """Mapea tool_call_id -> nombre de la tool, leyendo los `tool_calls` de
    los mensajes de assistant. Asi la clasificacion no depende de ningun
    registro externo y funciona igual en una sesion reanudada."""
    mapa: dict[str, str] = {}
    for msg in messages:
        for call in msg.get("tool_calls") or []:
            call_id = call.get("id")
            nombre = (call.get("function") or {}).get("name")
            if call_id and nombre:
                mapa[call_id] = nombre
    return mapa


def tamano(messages: list[dict]) -> int:
    """Chars totales de la conversacion. Aproximacion deliberada: no hay
    tokenizador comun a 11 proveedores distintos, y para decidir si hay que
    compactar sobra con el orden de magnitud."""
    total = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            total += len(content)
        for call in msg.get("tool_calls") or []:
            total += len(str((call.get("function") or {}).get("arguments") or ""))
    return total


def podar_resultados_de_tools(messages: list[dict]) -> int:
    """Recorta in-place las salidas EFIMERAS viejas. Devuelve chars
    ahorrados.

    Recorre de atras hacia adelante para que "las 2 mas recientes de cada
    tool" se cuente por tool y no por posicion absoluta: el modelo suele
    necesitar la ultima lectura de un archivo, no la primera."""
    nombres = _nombre_por_tool_call_id(messages)
    vistas: dict[str, int] = {}
    ahorrado = 0

    for msg in reversed(messages):
        if msg.get("role") != "tool":
            continue
        nombre = nombres.get(msg.get("tool_call_id", ""), "")
        if nombre in DURADERO:
            continue
        # Nota sobre las tools que no estan en ninguna de las dos listas
        # (solo entonces añadidas, o de una sesion vieja): caen aca y se tratan
        # como EFIMERAS a proposito. No podar algo gordo falla en silencio
        # y es el fallo caro; recortar de mas se nota enseguida y el modelo
        # puede volver a llamar a la tool. Las que NO se pueden recortar
        # estan enumeradas en DURADERO.
        vistas[nombre] = vistas.get(nombre, 0) + 1
        limite = KEEP_RECENT_PESADAS if nombre in EFIMERO_PESADO else KEEP_RECENT_POR_TOOL
        if vistas[nombre] <= limite:
            continue

        content = msg.get("content")
        if not isinstance(content, str) or len(content) <= RECORTE_CHARS:
            continue
        if "[recortado" in content[-200:]:
            # Ya podado en una vuelta anterior: no recortar dos veces ni
            # contar el ahorro de nuevo (inflaria el informe de la UI).
            continue
        original = len(content)
        msg["content"] = (
            content[:RECORTE_CHARS]
            + f"\n… [recortado: ya lo usaste en su momento. Eran {original} caracteres. "
            f"Si necesitas el contenido otra vez, vuelve a llamar a la herramienta.]"
        )
        ahorrado += original - len(msg["content"])
    return ahorrado


def podar_argumentos_de_escritura(messages: list[dict]) -> int:
    """Encoge in-place el contenido que viaja en los argumentos de las
    escrituras ya hechas. Devuelve chars ahorrados.

    Solo toca los VALORES largos y deja el resto del objeto intacto, asi que
    la llamada sigue siendo un JSON valido con su `path` a la vista. No
    borra ningun mensaje: el emparejamiento tool_calls/tool queda igual."""
    vistas = 0
    ahorrado = 0

    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        for call in msg.get("tool_calls") or []:
            funcion = call.get("function") or {}
            if funcion.get("name") not in ESCRITURA_PESADA:
                continue
            vistas += 1
            if vistas <= KEEP_RECENT_ESCRITURAS:
                continue

            crudo = funcion.get("arguments") or ""
            if len(crudo) <= ARG_MINIMO_PARA_PODAR:
                continue
            try:
                args = json.loads(crudo)
            except (json.JSONDecodeError, TypeError):
                # Sin JSON valido no se puede recortar por campos sin
                # arriesgarse a partir una cadena por la mitad. Se deja tal
                # cual: es peso muerto, pero corromper la llamada es peor.
                continue
            if not isinstance(args, dict):
                continue

            reducidos, toco = {}, False
            for clave, valor in args.items():
                if clave in ARGS_QUE_SE_CONSERVAN or not _es_pesado(valor):
                    reducidos[clave] = valor
                    continue
                reducidos[clave] = _marca_de_podado(valor)
                toco = True
            if not toco:
                continue

            nuevo = json.dumps(reducidos, ensure_ascii=False)
            ahorrado += len(crudo) - len(nuevo)
            funcion["arguments"] = nuevo
    return ahorrado


def _es_pesado(valor) -> bool:
    if isinstance(valor, str):
        return len(valor) > ARG_MINIMO_PARA_PODAR
    if isinstance(valor, (list, dict)):
        return len(json.dumps(valor, ensure_ascii=False)) > ARG_MINIMO_PARA_PODAR
    return False


def _marca_de_podado(valor) -> str:
    """El aviso dice DONDE esta ahora el contenido. Sin eso, el modelo ve un
    hueco y su reflejo es volver a escribir el archivo entero desde cero."""
    largo = len(valor if isinstance(valor, str) else json.dumps(valor, ensure_ascii=False))
    return (
        f"[recortado del historial: {largo} caracteres que ya se escribieron en "
        "el disco. Si necesitas ver como quedo, lee el archivo con read_file.]"
    )


def _es_frontera_segura(messages: list[dict], i: int) -> bool:
    """¿Se puede cortar el historial JUSTO ANTES del indice i sin dejar
    huerfano un tool_calls sin sus resultados?

    Es seguro cortar antes de un mensaje de `user` o de un `assistant` que
    no pide tools. Cortar antes de un `tool`, o entre un assistant con
    tool_calls y sus resultados, produce un 400 en todos los proveedores."""
    if i <= 0 or i >= len(messages):
        return False
    msg = messages[i]
    if msg.get("role") == "tool":
        return False
    anterior = messages[i - 1]
    if anterior.get("role") == "assistant" and anterior.get("tool_calls"):
        return False
    return True


def compactar(messages: list[dict], client, resumen_model: str = "router-tiny") -> int:
    """Sustituye los turnos mas antiguos por un resumen. Devuelve chars
    ahorrados (0 si no hizo falta o no se pudo).

    Se llama SOLO si podar no alcanzo: cuesta una llamada al modelo, y el
    resumen pierde detalle por definicion. Se usa el tier mas barato
    (router-tiny) porque resumir no necesita el modelo bueno.
    """
    if tamano(messages) <= PRESUPUESTO_CHARS:
        return 0

    # messages[0] es el system prompt y messages[1] la tarea original: los
    # dos se preservan siempre. Sin la tarea original el modelo pierde el
    # objetivo, que es el peor fallo posible de una compactacion.
    inicio = 2
    fin = len(messages) - COMPACTAR_PRESERVA_RECIENTES
    while fin > inicio and not _es_frontera_segura(messages, fin):
        fin -= 1
    if fin <= inicio:
        return 0

    bloque = messages[inicio:fin]
    antes = tamano(messages)

    transcripcion = []
    for msg in bloque:
        role = msg.get("role", "?")
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            transcripcion.append(f"[{role}] {content[:1500]}")
        for call in msg.get("tool_calls") or []:
            nombre = (call.get("function") or {}).get("name", "?")
            transcripcion.append(f"[{role}] llamo a {nombre}")
    if not transcripcion:
        return 0

    prompt = (
        "Resume este fragmento de una conversacion entre un usuario y un agente "
        "que construye archivos. El resumen lo va a leer el propio agente para "
        "seguir trabajando, asi que conserva SOLO lo que necesita para continuar: "
        "decisiones tomadas y por que, rutas de archivos creados o editados, datos "
        "reales obtenidos (nombres, horarios, telefonos, precios), y que quedaba "
        "pendiente. Omite el contenido de los archivos y los pasos ya cerrados. "
        "Escribe en viñetas, sin preambulo.\n\n" + "\n".join(transcripcion)
    )

    try:
        response = client.chat(
            messages=[{"role": "user", "content": prompt}],
            model=resumen_model,
            temperature=0.0,
        )
        resumen = response["choices"][0]["message"].get("content", "") or ""
    except Exception:
        # Si la compactacion falla, la tarea sigue con el historial largo.
        # Un contexto gordo es un problema; perder la tarea por no haber
        # podido resumirlo seria mucho peor.
        return 0
    if not resumen.strip():
        return 0

    messages[inicio:fin] = [{
        "role": "user",
        "content": (
            "[Resumen automatico de la parte mas antigua de esta conversacion, "
            "generado para no reenviarla entera en cada turno. Trata esto como "
            "hechos ya establecidos, no como un pedido nuevo del usuario.]\n\n"
            + resumen.strip()
        ),
    }]
    return antes - tamano(messages)


# Llamadas antes de fiarse de lo que dice el gasto sobre el cache: con una
# sola respuesta no hay forma de distinguir "este proveedor no cachea" de
# "todavia no ha tenido ocasion".
_MINIMO_PARA_JUZGAR_EL_CACHE = 3


def cadencia_de_poda(gasto=None) -> int:
    """Cada cuantas vueltas se poda. NUNCA (0) si el proveedor cachea.

    Una rotura de cache solo ocurre al MODIFICAR un mensaje que ya estaba:
    mientras solo se añada al final, el prefijo sigue intacto y el proveedor
    lo sirve barato indefinidamente. Asi que con un proveedor que cachea, la
    respuesta correcta a "cuando podar" es "nunca", y el peso se controla
    por el otro lado: con los topes de salida de `dispatch`, que impiden que
    el texto entre (que ademas es gratis, no cuesta ni una rotura).

    Sin cache no hay nada que romper, y ahi podar es todo ventaja: medido,
    un 35% menos de caracteres enviados.

    Se mira si el proveedor ha servido cache ALGUNA vez, no su tasa actual:
    la tasa depende de lo que hayamos podado nosotros, asi que usarla de
    entrada crearia un bucle (podamos -> baja la tasa -> podamos mas)."""
    if gasto is None or getattr(gasto, "llamadas", 0) < _MINIMO_PARA_JUZGAR_EL_CACHE:
        # Sin datos aun: cadencia prudente, ni agresiva ni nula.
        return PODAR_CADA_N_VUELTAS
    if not getattr(gasto, "cache", 0):
        return 1
    return NUNCA_PODAR


def toca_podar(messages: list[dict], gasto=None) -> bool:
    """¿Toca podar en esta vuelta?

    Cada mutacion de un mensaje viejo le cuesta al proveedor el cache de
    prefijo, asi que la poda no es gratis y no se hace por costumbre: hace
    falta que HAYA peso muerto (umbral) y que toque por cadencia. Por encima
    del presupuesto se poda igual, cadencia aparte: quedarse sin ventana de
    contexto es peor que pagar una rotura de cache."""
    # El system prompt no se puede podar; contarlo solo movería el listón
    # segun el especialista que toque.
    podable = tamano(messages[1:] if messages and messages[0].get("role") == "system" else messages)
    if podable <= UMBRAL_DE_PODA_CHARS:
        return False
    if podable > PRESUPUESTO_CHARS:
        # Ultimo recurso: quedarse sin ventana de contexto no es "mas caro",
        # es que la tarea no puede seguir. Una rotura de cache es un precio
        # aceptable por eso, y solo por eso.
        return True
    cadencia = cadencia_de_poda(gasto)
    if cadencia == NUNCA_PODAR:
        return False
    vueltas = sum(1 for m in messages if m.get("role") == "assistant")
    return vueltas % cadencia == 0


def gestionar(messages: list[dict], client=None) -> dict[str, int]:
    """Punto de entrada: poda y, si hace falta, compacta.

    Devuelve un informe para que la UI pueda mostrar el ahorro sin que este
    modulo imprima nada por su cuenta."""
    antes = tamano(messages)
    podado = 0
    if toca_podar(messages, getattr(client, "gasto", None)):
        podado = podar_resultados_de_tools(messages)
        podado += podar_argumentos_de_escritura(messages)
    compactado = 0
    if client is not None and tamano(messages) > PRESUPUESTO_CHARS:
        compactado = compactar(messages, client)
    return {
        "antes": antes,
        "despues": tamano(messages),
        "podado": podado,
        "compactado": compactado,
    }
