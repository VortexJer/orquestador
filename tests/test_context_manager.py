"""La gestion de contexto toca el historial que se manda a la API, asi que
un fallo aca no es "gasta mas tokens": es un 400 en todos los proveedores
o un modelo que pierde datos reales del negocio y se los inventa.

El invariante critico: la API exige que cada mensaje `assistant` con
`tool_calls` vaya seguido de un mensaje `tool` por cada llamada, con su
`tool_call_id`. Todo lo que hace este modulo tiene que preservarlo."""
import json

import pytest

from groq_agent import context_manager as cm


def _turno(call_id: str, tool: str, resultado: str) -> list[dict]:
    """Un par assistant(tool_calls) + tool(result), como lo produce el loop."""
    return [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {"name": tool, "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": call_id, "content": resultado},
    ]


def _conversacion(n_lecturas: int = 6) -> list[dict]:
    msgs: list[dict] = [
        {"role": "system", "content": "prompt del especialista"},
        {"role": "user", "content": "hazme una web para el bar la prosperidad"},
    ]
    for i in range(n_lecturas):
        msgs += _turno(f"c{i}", "read_file", "X" * 5000)
    return msgs


def _pares_intactos(messages: list[dict]) -> bool:
    """Cada tool_call declarado tiene su resultado, en orden."""
    esperados: list[str] = []
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            esperados.extend(c["id"] for c in msg["tool_calls"])
        elif msg.get("role") == "tool":
            if not esperados or msg.get("tool_call_id") not in esperados:
                return False
            esperados.remove(msg["tool_call_id"])
    return not esperados


@pytest.mark.parametrize(
    ("tool", "intactos_esperados"),
    [
        # read_file es EFIMERO_PESADO: con la ultima alcanza. Guardar dos
        # copias de una salida de decenas de KB era casi todo el peso
        # muerto que quedaba tras la primera version de la poda.
        ("read_file", cm.KEEP_RECENT_PESADAS),
        ("read_reference_component", cm.KEEP_RECENT_PESADAS),
        # Las efimeras ligeras conservan dos: son baratas y el modelo suele
        # necesitar comparar la anterior con la actual.
        ("critique_screenshot", cm.KEEP_RECENT_POR_TOOL),
        ("run_check", cm.KEEP_RECENT_POR_TOOL),
    ],
)
def test_poda_recorta_lo_viejo_y_deja_lo_reciente(tool, intactos_esperados):
    msgs = [{"role": "system", "content": "p"}, {"role": "user", "content": "t"}]
    for i in range(6):
        msgs += _turno(f"k{i}", tool, "X" * 5000)

    antes = cm.tamano(msgs)
    ahorrado = cm.podar_resultados_de_tools(msgs)

    assert ahorrado > 0
    assert cm.tamano(msgs) < antes
    intactos = [
        m for m in msgs
        if m.get("role") == "tool" and len(m["content"]) == 5000
    ]
    assert len(intactos) == intactos_esperados


def test_no_se_poda_dos_veces_la_misma_salida():
    """La poda corre en CADA vuelta. Sin guarda, un resultado ya recortado
    se volveria a recortar y el informe de la UI inflaria el ahorro."""
    msgs = _conversacion(6)
    primera = cm.podar_resultados_de_tools(msgs)
    segunda = cm.podar_resultados_de_tools(msgs)
    assert primera > 0
    assert segunda == 0


def test_la_poda_nunca_rompe_el_emparejamiento():
    msgs = _conversacion(8)
    cm.podar_resultados_de_tools(msgs)
    assert _pares_intactos(msgs)
    # Y no se pierde ningun mensaje: la poda encoge, no borra.
    assert len([m for m in msgs if m.get("role") == "tool"]) == 8


def test_los_datos_reales_del_negocio_no_se_podan_nunca():
    """Recortar esto es peor que reenviarlo: el modelo empieza a inventar
    horarios y telefonos, que es el fallo mas caro que puede tener una web
    de un negocio real."""
    msgs = [
        {"role": "system", "content": "p"},
        {"role": "user", "content": "t"},
    ]
    for i in range(5):
        msgs += _turno(f"n{i}", "fetch_business_from_maps", "HORARIO REAL " * 500)
    cm.podar_resultados_de_tools(msgs)
    for m in msgs:
        if m.get("role") == "tool":
            assert "recortado" not in m["content"]


def test_una_tool_desconocida_se_trata_como_efimera():
    """Una tool nueva que nadie clasifico no debe quedar sin podar en
    silencio: ese es el fallo caro (crece el contexto y no se nota)."""
    msgs = [{"role": "system", "content": "p"}, {"role": "user", "content": "t"}]
    for i in range(5):
        msgs += _turno(f"z{i}", "tool_inventada_manana", "Y" * 5000)
    ahorrado = cm.podar_resultados_de_tools(msgs)
    assert ahorrado > 0


def test_frontera_segura_no_corta_entre_tool_calls_y_su_resultado():
    msgs = _conversacion(3)
    # El indice de un mensaje 'tool' nunca es frontera segura.
    for i, msg in enumerate(msgs):
        if msg.get("role") == "tool":
            assert not cm._es_frontera_segura(msgs, i)
    # Tampoco justo despues de un assistant con tool_calls.
    for i in range(1, len(msgs)):
        anterior = msgs[i - 1]
        if anterior.get("role") == "assistant" and anterior.get("tool_calls"):
            assert not cm._es_frontera_segura(msgs, i)


class _ClienteFalso:
    """Devuelve un resumen fijo. Sin red: la bateria local tiene que correr
    a coste cero (ver feedback del usuario sobre reverificar solo lo que
    fallo)."""

    def __init__(self, resumen: str = "- se leyeron 40 archivos\n- se escribio index.html"):
        self.resumen = resumen
        self.llamadas = 0
        self.modelos_usados: list[str] = []

    def chat(self, messages, model="", **kwargs):
        self.llamadas += 1
        self.modelos_usados.append(model)
        return {"choices": [{"message": {"content": self.resumen}}]}


def test_compactar_no_toca_nada_si_cabe_en_el_presupuesto():
    msgs = _conversacion(2)
    cliente = _ClienteFalso()
    assert cm.compactar(msgs, cliente) == 0
    assert cliente.llamadas == 0, "no debe gastar una llamada si no hace falta"


def test_compactar_reduce_y_preserva_la_tarea_original_y_el_emparejamiento():
    # Suficientemente grande para pasarse del presupuesto.
    msgs = _conversacion(60)
    cliente = _ClienteFalso()
    antes = cm.tamano(msgs)
    ahorrado = cm.compactar(msgs, cliente)

    assert ahorrado > 0
    assert cm.tamano(msgs) < antes
    # El system prompt y la tarea original siguen en su sitio: sin la tarea
    # el modelo pierde el objetivo, el peor fallo de una compactacion.
    assert msgs[0]["role"] == "system"
    assert "la prosperidad" in msgs[1]["content"]
    assert _pares_intactos(msgs)


def test_compactar_usa_el_tier_barato():
    """Resumir no necesita el modelo bueno; gastar coder-main en esto seria
    tirar la cuota del tier que mas se usa."""
    msgs = _conversacion(60)
    cliente = _ClienteFalso()
    cm.compactar(msgs, cliente)
    assert cliente.modelos_usados == ["router-tiny"]


def test_si_el_resumen_falla_la_tarea_sigue():
    """Un contexto gordo es un problema; perder la tarea por no haber
    podido resumirlo seria mucho peor."""
    class ClienteRoto:
        def chat(self, *a, **k):
            raise RuntimeError("se agoto la escalera")

    msgs = _conversacion(60)
    antes = list(msgs)
    assert cm.compactar(msgs, ClienteRoto()) == 0
    assert msgs == antes


def test_un_resumen_vacio_no_destruye_el_historial():
    msgs = _conversacion(60)
    n_antes = len(msgs)
    assert cm.compactar(msgs, _ClienteFalso(resumen="   ")) == 0
    assert len(msgs) == n_antes


def test_gestionar_devuelve_un_informe_coherente():
    msgs = _conversacion(10)
    informe = cm.gestionar(msgs, _ClienteFalso())
    assert informe["antes"] >= informe["despues"]
    assert informe["antes"] - informe["despues"] == (
        informe["podado"] + informe["compactado"]
    )


def test_gestionar_sin_cliente_solo_poda():
    """agent_loop puede llamar sin cliente: la poda no debe depender de
    poder compactar.

    Son 12 lecturas y no 10 porque la poda ya no ocurre en todas las
    vueltas: cuesta el cache de prefijo del proveedor, asi que se hace por
    cadencia (ver cadencia_de_poda)."""
    msgs = _conversacion(12)
    informe = cm.gestionar(msgs, client=None)
    assert informe["podado"] > 0
    assert informe["compactado"] == 0


def test_funciona_sobre_una_sesion_reanudada():
    """El hueco de la version anterior: al reanudar con -c, el registro de
    llamadas arrancaba vacio y el historial heredado - el mas viejo y mas
    gordo - nunca se podaba. Este modulo lee los nombres de tool del propio
    historial, asi que no le afecta."""
    heredado = _conversacion(6)  # como si viniera de session_store
    ahorrado = cm.podar_resultados_de_tools(heredado)
    assert ahorrado > 0, "el historial heredado tiene que poder podarse"


@pytest.mark.parametrize("tool", sorted(cm.DURADERO))
def test_ninguna_tool_esta_en_las_dos_listas(tool):
    assert tool not in cm.EFIMERO, (
        f"'{tool}' esta en DURADERO y en EFIMERO: la clasificacion seria ambigua "
        "y dependeria del orden de evaluacion."
    )


# --- el contenido escrito viaja en los ARGUMENTOS, no en el resultado ---
#
# La poda de resultados solo mira mensajes `role == "tool"`, asi que el
# HTML y el CSS que el modelo escribe - que van dentro de los argumentos de
# write_file, en el mensaje del assistant - se reenviaban intactos hasta el
# final de la tarea. En una web son ~70KB por vuelta.


def _escritura(call_id: str, ruta: str, contenido: str) -> list[dict]:
    return [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {
                    "name": "write_file",
                    "arguments": json.dumps({"path": ruta, "content": contenido}),
                },
            }],
        },
        {"role": "tool", "tool_call_id": call_id, "content": f"Escrito {ruta}"},
    ]


def test_el_contenido_escrito_se_recorta_del_historial():
    css = "body { margin: 0; }\n" * 2000
    messages = [
        {"role": "system", "content": "skill"},
        {"role": "user", "content": "hazme una web"},
        *_escritura("1", "index.html", "<html>" + "x" * 20_000 + "</html>"),
        *_escritura("2", "styles.css", css),
        *_escritura("3", "app.js", "console.log(1);" * 500),
    ]
    antes = cm.tamano(messages)
    ahorrado = cm.podar_argumentos_de_escritura(messages)

    assert ahorrado > 30_000
    assert cm.tamano(messages) < antes / 2


def test_la_ultima_escritura_se_conserva_entera():
    """Es la que el modelo puede necesitar para encadenar una correccion
    sin gastar un turno en volver a leer el archivo."""
    ultimo = "cuerpo del archivo " * 500
    messages = [
        {"role": "user", "content": "t"},
        *_escritura("1", "a.css", "viejo " * 500),
        *_escritura("2", "b.css", ultimo),
    ]
    cm.podar_argumentos_de_escritura(messages)

    args_ultima = json.loads(messages[-2]["tool_calls"][0]["function"]["arguments"])
    assert args_ultima["content"] == ultimo


def test_la_ruta_sobrevive_al_recorte():
    """Sin la ruta el modelo no sabe QUE archivo escribio, y el aviso de
    'lee el archivo' deja de tener sentido."""
    messages = [
        {"role": "user", "content": "t"},
        *_escritura("1", "sitio/styles.css", "z" * 5000),
        *_escritura("2", "otro.html", "y" * 5000),
    ]
    cm.podar_argumentos_de_escritura(messages)

    args = json.loads(messages[1]["tool_calls"][0]["function"]["arguments"])
    assert args["path"] == "sitio/styles.css"
    assert "read_file" in args["content"], "el aviso debe decir como recuperarlo"


def test_el_recorte_deja_json_valido():
    """Los argumentos son una cadena JSON: si se recorta a lo bruto, el
    proveedor devuelve un 400 antes de que nadie note el ahorro."""
    messages = [
        {"role": "user", "content": "t"},
        *_escritura("1", "a.css", 'contenido con "comillas" y \\ barras ' * 200),
        *_escritura("2", "b.css", "x" * 300),
    ]
    cm.podar_argumentos_de_escritura(messages)

    for msg in messages:
        for call in msg.get("tool_calls") or []:
            json.loads(call["function"]["arguments"])  # no debe lanzar


def test_no_recorta_dos_veces_ni_infla_el_ahorro():
    messages = [
        {"role": "user", "content": "t"},
        *_escritura("1", "a.css", "x" * 9000),
        *_escritura("2", "b.css", "y" * 9000),
    ]
    primero = cm.podar_argumentos_de_escritura(messages)
    segundo = cm.podar_argumentos_de_escritura(messages)
    assert primero > 8000
    assert segundo == 0


def test_una_escritura_pequena_se_deja_en_paz():
    messages = [
        {"role": "user", "content": "t"},
        *_escritura("1", "a.txt", "hola"),
        *_escritura("2", "b.txt", "adios"),
    ]
    assert cm.podar_argumentos_de_escritura(messages) == 0


def test_gestionar_incluye_el_ahorro_de_los_argumentos():
    # Cuatro escrituras, no dos: la poda va por cadencia desde que se
    # midio que hacerla en cada vuelta rompia el cache de prefijo.
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "t"},
        *_escritura("1", "a.css", "x" * 20_000),
        *_escritura("2", "b.css", "y" * 20_000),
        *_escritura("3", "c.css", "z" * 20_000),
        *_escritura("4", "d.css", "w" * 20_000),
    ]
    informe = cm.gestionar(messages)
    assert informe["podado"] > 15_000
    assert informe["despues"] < informe["antes"]


# --- lo que NO se puede podar ------------------------------------------


def test_una_seccion_de_skill_cargada_no_se_poda_nunca():
    """`load_skill_section` no devuelve datos, devuelve INSTRUCCIONES. El
    modelo carga el sistema de diseño en la Fase 0 y escribe el CSS en la
    Fase 3, varias vueltas despues: podarlo por el camino le quita la
    paleta justo antes de usarla."""
    def carga(i, cuerpo):
        return [
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": f"s{i}", "type": "function",
                "function": {"name": "load_skill_section",
                             "arguments": json.dumps({"section": "2."})}}]},
            {"role": "tool", "tool_call_id": f"s{i}", "content": cuerpo},
        ]

    diseno = "La paleta va en :root con --color-acento. " * 400
    messages = [{"role": "user", "content": "hazme una web"}]
    for i in range(4):
        messages += carga(i, diseno)

    cm.podar_resultados_de_tools(messages)

    cuerpos = [m["content"] for m in messages if m.get("role") == "tool"]
    assert all(c == diseno for c in cuerpos), "las instrucciones no se recortan"


# --- la poda cuesta cache: no se hace por costumbre ---------------------
#
# Los proveedores cobran barato el prefijo que ya vieron. Mutar un mensaje
# viejo lo invalida desde ahi. Medido en local sobre una web de 12 vueltas:
# podando en cada vuelta se rompia el cache 6 veces y la parte cacheable
# caia del 86% al 71% - con un cache al 10%, el cambio salia perdiendo.


def _conversacion_de(chars: int, vueltas: int = 8) -> list[dict]:
    msgs = [{"role": "system", "content": "S" * 34_000},
            {"role": "user", "content": "hazme una web"}]
    for i in range(vueltas):
        msgs += [
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": str(i), "type": "function",
                "function": {"name": "read_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": str(i), "content": "x" * (chars // vueltas)},
        ]
    return msgs


def test_una_conversacion_pequena_no_se_toca():
    """Si no hay peso muerto, podar solo tira el cache de prefijo."""
    assert cm.toca_podar(_conversacion_de(5_000)) is False


def test_el_system_prompt_no_cuenta_para_el_umbral():
    """Son ~34.000 chars en web y ~11.000 en python: contarlos haria que el
    listón dependiese del especialista y no de la conversacion."""
    corta = _conversacion_de(5_000)
    assert cm.tamano(corta) > cm.UMBRAL_DE_PODA_CHARS, "el system prompt ya pasa del umbral"
    assert cm.toca_podar(corta) is False


def test_por_encima_del_presupuesto_se_poda_aunque_no_toque():
    """Quedarse sin ventana de contexto es peor que pagar una rotura."""
    enorme = _conversacion_de(cm.PRESUPUESTO_CHARS + 50_000, vueltas=3)
    assert cm.toca_podar(enorme) is True


class _Gasto:
    def __init__(self, llamadas, cache):
        self.llamadas, self.cache = llamadas, cache


def test_si_el_proveedor_no_cachea_se_poda_en_cada_vuelta():
    """Ahi la poda no cuesta nada y el ahorro es directo."""
    assert cm.cadencia_de_poda(_Gasto(llamadas=5, cache=0)) == 1


def test_si_el_proveedor_cachea_no_se_poda_nunca():
    """Una rotura de cache solo ocurre al MODIFICAR un mensaje que ya
    estaba. Si el proveedor cachea, la respuesta a "cuando podar" es nunca:
    el peso se controla con los topes de salida, que no cuestan roturas."""
    assert cm.cadencia_de_poda(_Gasto(llamadas=5, cache=12_000)) == cm.NUNCA_PODAR


def test_con_cache_no_se_toca_el_historial_por_grande_que_sea():
    msgs = _conversacion_de(cm.UMBRAL_DE_PODA_CHARS * 2, vueltas=8)
    assert cm.toca_podar(msgs, _Gasto(llamadas=5, cache=9_000)) is False


def test_pero_si_no_cabe_se_poda_igual_aunque_cachee():
    """Quedarse sin ventana de contexto no es "mas caro": es que la tarea no
    puede seguir. Ahi una rotura es un precio aceptable."""
    msgs = _conversacion_de(cm.PRESUPUESTO_CHARS + 50_000, vueltas=4)
    assert cm.toca_podar(msgs, _Gasto(llamadas=5, cache=9_000)) is True


def test_sin_datos_suficientes_no_se_asume_que_no_cachea():
    """Con una sola respuesta no se distingue 'no cachea' de 'aun no ha
    tenido ocasion' - y asumir lo primero pondria la poda a maxima
    agresividad desde el turno uno."""
    assert cm.cadencia_de_poda(_Gasto(llamadas=1, cache=0)) == cm.PODAR_CADA_N_VUELTAS
    assert cm.cadencia_de_poda(None) == cm.PODAR_CADA_N_VUELTAS


# --- quien cachea, por proveedor ---------------------------------------


def test_el_gasto_dice_QUE_proveedor_cachea():
    """La escalera de failover mezcla proveedores: un unico total de cache
    junta al que cachea con el que no y no dice cual es cual. Y esa es la
    cifra que decide la factura - el mismo trabajo cuesta unas 4 veces mas
    en uno que no cachea."""
    from groq_agent.usage import Gasto

    g = Gasto()
    g.sumar({"usage": {"prompt_tokens": 10_000, "completion_tokens": 500,
                       "prompt_tokens_details": {"cached_tokens": 8_500}}}, "groq/llama")
    g.sumar({"usage": {"prompt_tokens": 10_000, "completion_tokens": 500}}, "ovh/mixtral")

    filas = dict((m, t) for m, t, _ in g.quien_cachea())
    assert filas["groq/llama"] > 0.8
    assert filas["ovh/mixtral"] == 0.0


def test_el_desglose_sobrevive_a_guardar_y_cargar():
    """`--usage` lee de disco, no del cliente: si no persiste, el dato solo
    existe durante la sesion que ya termino."""
    from groq_agent.usage import Gasto

    g = Gasto()
    g.sumar({"usage": {"prompt_tokens": 900, "completion_tokens": 10,
                       "cached_tokens": 600}}, "groq/llama")
    assert Gasto.desde_dict(g.como_dict()).quien_cachea() == g.quien_cachea()


def test_cuenta_llamadas_servidas_por_api_incluso_sin_usage():
    """orquestador -api: cuenta TODA llamada servida por cada API (proveedor),
    tenga o no datos de tokens - una respuesta sin `usage` sigue siendo una
    llamada. Las claves son 'proveedor:modelo' (str(Candidate))."""
    from groq_agent.usage import Gasto

    g = Gasto()
    g.sumar({"usage": {"prompt_tokens": 100, "completion_tokens": 5}}, "groq:llama-3.3-70b")
    g.sumar({"usage": {"prompt_tokens": 100, "completion_tokens": 5}}, "groq:llama-3.3-70b")
    g.sumar({}, "sambanova:DeepSeek-V3")          # sin `usage`: cuenta igual
    g.sumar({"usage": {"prompt_tokens": 50, "completion_tokens": 2}}, "nvidia:qwen")

    filas = g.llamadas_por_proveedor()
    assert {p: n for p, n, _ in filas} == {"groq": 2, "sambanova": 1, "nvidia": 1}
    assert filas[0][0] == "groq"                  # ordenado de mas a menos llamado
    groq_detalle = dict(next(d for p, _, d in filas if p == "groq"))
    assert groq_detalle == {"llama-3.3-70b": 2}   # desglose por modelo dentro de la API
    assert sum(n for _, n, _ in filas) == g.llamadas == 4   # cuadra con el total


def test_llamadas_por_api_sobrevive_guardar_y_cargar():
    from groq_agent.usage import Gasto

    g = Gasto()
    g.sumar({}, "groq:llama")
    g.sumar({"usage": {"prompt_tokens": 10, "completion_tokens": 1}}, "ovh:mixtral")
    reconstruido = Gasto.desde_dict(g.como_dict())
    assert reconstruido.llamadas_por_proveedor() == g.llamadas_por_proveedor()
