"""El rescate del documento de oficina escrito como TEXTO.

Bug real (visto en vivo, "y pasa todo el rato"): al pedir una presentacion,
el especialista devolvia el JSON del deck como TEXTO en el chat y NUNCA
llamaba a `generate_pptx`, asi que no se generaba ningun .pptx: el usuario
se quedaba con un volcado de JSON. El loop, al no ver tool_calls, devolvia
ese texto tal cual.

El arreglo: si el modelo termina sin tool_calls pero su texto tiene pinta de
ser el contenido de un documento de oficina (deck con `slides`, bloques de
docx, o una pseudo-llamada), se le da UN empujon para que haga la tool call
de verdad. Una sola vez, para no entrar en bucle si el modelo insiste.
"""
import json

from groq_agent import agent_loop

# El empujon solo aplica si la sesion tiene tools de oficina disponibles.
_ESQUEMAS_OFFICE = [
    {"type": "function", "function": {"name": "generate_pptx"}},
    {"type": "function", "function": {"name": "generate_docx"}},
    {"type": "function", "function": {"name": "generate_xlsx"}},
]


class _EjecutorOffice:
    auto_yes = True
    session_accept_all = True

    def __init__(self, root, skill_name=None):
        self.root = root
        self.skill_name = skill_name
        self.generado = []
        # el harness mira archivos_tocados para saber si ya se creo el archivo
        self.archivos_tocados: dict[str, str] = {}

    def dispatch(self, name, arguments):
        if name in ("generate_pptx", "generate_docx", "generate_xlsx"):
            self.generado.append(arguments)
            ext = {"generate_pptx": ".pptx", "generate_docx": ".docx",
                   "generate_xlsx": ".xlsx"}[name]
            ruta = arguments.get("path") or f"salida{ext}"
            self.archivos_tocados[ruta] = "creado"
            return f"Generado en {ruta}"
        if name in ("write_file", "edit_file"):
            self.generado.append(arguments)
            ruta = arguments.get("path") or "salida.txt"
            self.archivos_tocados[ruta] = "creado"
            return f"Escrito {ruta}"
        return "ok"


_ESQUEMAS_CODIGO = [
    {"type": "function", "function": {"name": "write_file"}},
    {"type": "function", "function": {"name": "run_check"}},
]
_ESQUEMAS_WEB = [
    {"type": "function", "function": {"name": "load_skill_section"}},
    {"type": "function", "function": {"name": "list_reference_components"}},
    {"type": "function", "function": {"name": "fetch_business_from_maps"}},
    {"type": "function", "function": {"name": "write_file"}},
]


def _sin_ruido(monkeypatch):
    monkeypatch.setattr(agent_loop.interrupt, "pedido", lambda: False)
    monkeypatch.setattr(agent_loop, "gestionar_contexto",
                        lambda messages, client: {"antes": 0, "despues": 0})
    monkeypatch.setattr(agent_loop.ui, "animate_dispatch",
                        lambda name, args, ex, root=None: ex.dispatch(name, args))


class _ClienteDeckComoTexto:
    """1) escribe el deck como TEXTO (el bug); 2) tras el empujon hace la
    tool call de verdad; 3) cierra con texto."""

    def __init__(self):
        self.llamadas = 0
        self.vio_el_empujon = False

    def chat(self, messages, tools=None, model="", **kwargs):
        self.llamadas += 1
        if self.llamadas == 1:
            return {"choices": [{"message": {"role": "assistant", "content":
                '{"theme":"profundo","slides":[{"kind":"cover","title":"BMW 118d"}]}'}}]}
        if self.llamadas == 2:
            self.vio_el_empujon = any(
                agent_loop._NUDGE_OFFICE in (m.get("content") or "") for m in messages)
            return {"choices": [{"message": {"role": "assistant", "content": None,
                "tool_calls": [{"id": "c1", "type": "function", "function": {
                    "name": "generate_pptx",
                    "arguments": json.dumps({"path": "bmw-118d.pptx", "theme": "profundo",
                                             "slides": [{"kind": "cover", "title": "BMW 118d"}]})}}]}}]}
        return {"choices": [{"message": {"role": "assistant", "content": "Listo, presentacion generada."}}]}


def test_deck_como_texto_dispara_la_tool_call(monkeypatch, tmp_path):
    _sin_ruido(monkeypatch)
    cliente = _ClienteDeckComoTexto()
    ejecutor = _EjecutorOffice(tmp_path)
    agent_loop.run_agent(
        client=cliente, executor=ejecutor, system_prompt="s",
        user_task="hazme una presentacion del bmw 118d", model="m",
        max_turns=6, tool_schemas=_ESQUEMAS_OFFICE,
    )
    assert cliente.vio_el_empujon, "el empujon tiene que llegar al modelo"
    assert ejecutor.generado, "tras el empujon el .pptx tiene que generarse via tool call"
    assert ejecutor.generado[0]["path"].endswith(".pptx")


def test_texto_normal_no_dispara_empujon(monkeypatch, tmp_path):
    _sin_ruido(monkeypatch)

    class _C:
        llamadas = 0

        def chat(self, messages, tools=None, model="", **k):
            self.llamadas += 1
            return {"choices": [{"message": {"role": "assistant",
                                             "content": "Hola, aqui tienes la respuesta."}}]}

    c = _C()
    texto, _ = agent_loop.run_agent(
        client=c, executor=_EjecutorOffice(tmp_path), system_prompt="s",
        user_task="hola", model="m", max_turns=3, tool_schemas=_ESQUEMAS_OFFICE)
    assert texto == "Hola, aqui tienes la respuesta."
    assert c.llamadas == 1, "una respuesta de texto normal NO debe re-preguntar"


def test_el_empujon_es_una_sola_vez(monkeypatch, tmp_path):
    """Si el modelo INSISTE en texto, se empuja UNA vez y se devuelve, sin bucle."""
    _sin_ruido(monkeypatch)

    class _CTerco:
        llamadas = 0

        def chat(self, messages, tools=None, model="", **k):
            self.llamadas += 1
            return {"choices": [{"message": {"role": "assistant",
                                             "content": '{"theme":"x","slides":[{"kind":"cover"}]}'}}]}

    c = _CTerco()
    texto, _ = agent_loop.run_agent(
        client=c, executor=_EjecutorOffice(tmp_path), system_prompt="s",
        user_task="deck", model="m", max_turns=6, tool_schemas=_ESQUEMAS_OFFICE)
    assert c.llamadas == 2, "un empujon (2 llamadas), luego se rinde y devuelve el texto"
    assert '"slides"' in texto


def test_sin_tools_de_oficina_no_hay_empujon(monkeypatch, tmp_path):
    """Una tarea de CODIGO (sin tools de oficina) que devuelve un JSON con
    `sections`/`sheets` NO debe disparar el empujon: no hay ninguna tool de
    oficina que llamar, seria un falso positivo."""
    _sin_ruido(monkeypatch)

    class _CJsonDeCodigo:
        llamadas = 0

        def chat(self, messages, tools=None, model="", **k):
            self.llamadas += 1
            return {"choices": [{"message": {"role": "assistant",
                "content": '{"sections":[{"name":"auth","rows":3}]}'}}]}

    c = _CJsonDeCodigo()
    texto, _ = agent_loop.run_agent(
        client=c, executor=_EjecutorOffice(tmp_path), system_prompt="s",
        user_task="analiza el modulo", model="m", max_turns=3,
        tool_schemas=[{"type": "function", "function": {"name": "read_file"}}])
    assert c.llamadas == 1, "sin tools de oficina no se re-pregunta"
    assert '"sections"' in texto


class _ClientePreguntaLaRuta:
    """El bug real de office-spreadsheet: en vez de generar, pregunta donde
    guardar y para (sin JSON, sin tool call). 2) tras el empujon, genera."""

    def __init__(self):
        self.llamadas = 0

    def chat(self, messages, tools=None, model="", **k):
        self.llamadas += 1
        if self.llamadas == 1:
            return {"choices": [{"message": {"role": "assistant", "content":
                "Vale, voy a generar el archivo. ¿Lo guardo en el workspace?"}}]}
        if self.llamadas == 2:
            return {"choices": [{"message": {"role": "assistant", "content": None,
                "tool_calls": [{"id": "c1", "type": "function", "function": {
                    "name": "generate_xlsx",
                    "arguments": json.dumps({"path": "presupuesto.xlsx", "sheets": []})}}]}}]}
        return {"choices": [{"message": {"role": "assistant", "content": "Listo."}}]}


def test_generador_que_pregunta_la_ruta_y_para_recibe_empujon(monkeypatch, tmp_path):
    """No hay JSON volcado, pero un generador de archivo termino sin crearlo:
    el empujon tiene que dispararse igual (via skill_name + sin archivo)."""
    _sin_ruido(monkeypatch)
    cliente = _ClientePreguntaLaRuta()
    ejecutor = _EjecutorOffice(tmp_path, skill_name="office-spreadsheet-specialist")
    agent_loop.run_agent(
        client=cliente, executor=ejecutor, system_prompt="s",
        user_task="hazme un excel de presupuesto", model="m", max_turns=6,
        tool_schemas=_ESQUEMAS_OFFICE)
    assert ejecutor.generado, "tras el empujon el .xlsx tiene que generarse"
    assert ejecutor.generado[0]["path"].endswith(".xlsx")


def test_generador_que_ya_creo_el_archivo_no_recibe_empujon(monkeypatch, tmp_path):
    """Si el archivo ya se genero en un turno previo, la respuesta final de
    texto (un 'listo, aqui tienes') NO debe re-disparar el empujon."""
    _sin_ruido(monkeypatch)

    class _CGeneraYCierra:
        llamadas = 0

        def chat(self, messages, tools=None, model="", **k):
            self.llamadas += 1
            if self.llamadas == 1:
                return {"choices": [{"message": {"role": "assistant", "content": None,
                    "tool_calls": [{"id": "c1", "type": "function", "function": {
                        "name": "generate_docx",
                        "arguments": json.dumps({"path": "informe.docx", "sections": []})}}]}}]}
            return {"choices": [{"message": {"role": "assistant",
                                             "content": "Listo, informe.docx generado."}}]}

    c = _CGeneraYCierra()
    ejecutor = _EjecutorOffice(tmp_path, skill_name="office-word-specialist")
    texto, _ = agent_loop.run_agent(
        client=c, executor=ejecutor, system_prompt="s",
        user_task="hazme un word", model="m", max_turns=6,
        tool_schemas=_ESQUEMAS_OFFICE)
    assert c.llamadas == 2, "genero (1) y cerro con texto (2), sin empujon extra"
    assert "generado" in texto.lower()


class _ClienteCodigoComoTexto:
    """El bug intermitente de los especialistas de codigo: piden 'guardala en
    iban.py' y devuelven el codigo como TEXTO sin llamar write_file. Tras el
    empujon, escriben el archivo."""

    def __init__(self):
        self.llamadas = 0

    def chat(self, messages, tools=None, model="", **k):
        self.llamadas += 1
        if self.llamadas == 1:
            return {"choices": [{"message": {"role": "assistant", "content":
                "```python\ndef validar_iban(iban):\n    return True\n```"}}]}
        if self.llamadas == 2:
            return {"choices": [{"message": {"role": "assistant", "content": None,
                "tool_calls": [{"id": "c1", "type": "function", "function": {
                    "name": "write_file",
                    "arguments": json.dumps({"path": "iban.py",
                                             "content": "def validar_iban(iban): return True"})}}]}}]}
        return {"choices": [{"message": {"role": "assistant", "content": "Listo, iban.py escrito."}}]}


def test_pedir_guardar_codigo_y_devolver_texto_dispara_empujon(monkeypatch, tmp_path):
    _sin_ruido(monkeypatch)
    cliente = _ClienteCodigoComoTexto()
    ejecutor = _EjecutorOffice(tmp_path, skill_name="python-specialist")
    agent_loop.run_agent(
        client=cliente, executor=ejecutor, system_prompt="s",
        user_task="Escribe una funcion que valide un IBAN y guardala en iban.py.",
        model="m", max_turns=6, tool_schemas=_ESQUEMAS_CODIGO)
    assert ejecutor.generado, "tras el empujon el archivo tiene que escribirse"
    assert ejecutor.generado[0]["path"] == "iban.py"


def test_pregunta_de_codigo_sin_guardar_no_dispara_empujon(monkeypatch, tmp_path):
    """'escribe una funcion que valide un IBAN' SIN pedir guardarla: devolver
    el codigo como texto es correcto, no debe empujar."""
    _sin_ruido(monkeypatch)

    class _C:
        llamadas = 0

        def chat(self, messages, tools=None, model="", **k):
            self.llamadas += 1
            return {"choices": [{"message": {"role": "assistant",
                                             "content": "```python\ndef f(): ...\n```"}}]}

    c = _C()
    texto, _ = agent_loop.run_agent(
        client=c, executor=_EjecutorOffice(tmp_path, skill_name="python-specialist"),
        system_prompt="s", user_task="escribe una funcion que valide un IBAN",
        model="m", max_turns=3, tool_schemas=_ESQUEMAS_CODIGO)
    assert c.llamadas == 1, "una respuesta de codigo sin 'guardar' no se re-pregunta"
    assert "def f" in texto


def test_mencionar_un_archivo_para_explicarlo_no_dispara(monkeypatch, tmp_path):
    """'explicame utils.py' menciona un archivo pero no pide escribirlo."""
    _sin_ruido(monkeypatch)

    class _C:
        llamadas = 0

        def chat(self, messages, tools=None, model="", **k):
            self.llamadas += 1
            return {"choices": [{"message": {"role": "assistant",
                                             "content": "utils.py define helpers de fechas."}}]}

    c = _C()
    _texto, _ = agent_loop.run_agent(
        client=c, executor=_EjecutorOffice(tmp_path, skill_name="python-specialist"),
        system_prompt="s", user_task="explicame que hace utils.py",
        model="m", max_turns=3, tool_schemas=_ESQUEMAS_CODIGO)
    assert c.llamadas == 1, "explicar un archivo no es pedir escribirlo"


class _ClienteNarraTools:
    """El fallo real de web-builder-large con la cabeza de su cadena caida:
    NARRA la tanda de tool calls del Turno 0 como texto en vez de emitirlas.
    Tras el empujon, hace una tool call de verdad."""

    def __init__(self):
        self.llamadas = 0
        self.vio_nudge = False

    def chat(self, messages, tools=None, model="", **k):
        self.llamadas += 1
        if self.llamadas == 1:
            return {"choices": [{"message": {"role": "assistant", "content":
                "load_skill_section(migrations: {\"section\": \"2.\"}); "
                "list_reference_components(migrations: {}); "
                "fetch_business_from_maps(migrations: {\"consulta\": \"Studio Zero\"});"}}]}
        if self.llamadas == 2:
            self.vio_nudge = any(
                agent_loop._NUDGE_TOOLCALL in (m.get("content") or "") for m in messages)
            return {"choices": [{"message": {"role": "assistant", "content": None,
                "tool_calls": [{"id": "c1", "type": "function", "function": {
                    "name": "write_file",
                    "arguments": json.dumps({"path": "index.html", "content": "<h1>ok</h1>"})}}]}}]}
        return {"choices": [{"message": {"role": "assistant", "content": "Listo."}}]}


def test_content_como_lista_no_revienta(monkeypatch, tmp_path):
    """Algunos proveedores (mistral-large) devuelven content como LISTA de
    partes. Los detectores regex reventaban con 'expected string, got list' y
    tumbaban la sesion en el primer turno. Ahora se normaliza a str."""
    _sin_ruido(monkeypatch)

    class _C:
        llamadas = 0

        def chat(self, messages, tools=None, model="", **k):
            self.llamadas += 1
            return {"choices": [{"message": {"role": "assistant",
                "content": [{"type": "text", "text": "Aqui tienes la respuesta."}]}}]}

    c = _C()
    texto, _ = agent_loop.run_agent(
        client=c, executor=_EjecutorOffice(tmp_path, skill_name="web-builder-specialist"),
        system_prompt="s", user_task="hazme una landing", model="m", max_turns=3,
        tool_schemas=_ESQUEMAS_WEB)
    assert texto == "Aqui tienes la respuesta.", "el content-lista se normaliza a str"


def test_toolcall_narrada_dispara_empujon(monkeypatch, tmp_path):
    _sin_ruido(monkeypatch)
    cliente = _ClienteNarraTools()
    ejecutor = _EjecutorOffice(tmp_path, skill_name="web-builder-specialist")
    agent_loop.run_agent(
        client=cliente, executor=ejecutor, system_prompt="s",
        user_task="hazme una landing", model="m", max_turns=6,
        tool_schemas=_ESQUEMAS_WEB)
    assert cliente.vio_nudge, "narrar >=2 tools como texto tiene que disparar el empujon"
    assert ejecutor.generado, "tras el empujon se emite una tool call de verdad"


def test_una_sola_narrada_sin_parentesis_dispara(monkeypatch, tmp_path):
    """Caso real: `load_skill_section nuestrame {"section": "2."}` - una sola
    call, sin parentesis, args JSON, texto corto -> es narracion, empuja."""
    _sin_ruido(monkeypatch)

    class _C:
        llamadas = 0
        vio_nudge = False

        def chat(self, messages, tools=None, model="", **k):
            self.llamadas += 1
            if self.llamadas == 1:
                return {"choices": [{"message": {"role": "assistant",
                    "content": 'load_skill_section nuestrame {"section": "2."}'}}]}
            self.vio_nudge = any(
                agent_loop._NUDGE_TOOLCALL in (m.get("content") or "") for m in messages)
            return {"choices": [{"message": {"role": "assistant", "content": "Listo."}}]}

    c = _C()
    agent_loop.run_agent(
        client=c, executor=_EjecutorOffice(tmp_path, skill_name="web-builder-specialist"),
        system_prompt="s", user_task="hazme una landing", model="m", max_turns=4,
        tool_schemas=_ESQUEMAS_WEB)
    assert c.vio_nudge, "una call narrada corta con args JSON tiene que empujar"


def test_una_sola_tool_mencionada_no_dispara(monkeypatch, tmp_path):
    """Una sola tool 'nombrada' (o en prosa) no es narrar una tanda: no empuja."""
    _sin_ruido(monkeypatch)

    class _C:
        llamadas = 0

        def chat(self, messages, tools=None, model="", **k):
            self.llamadas += 1
            return {"choices": [{"message": {"role": "assistant",
                "content": "Voy a usar load_skill_section(seccion 2) para el diseno."}}]}

    c = _C()
    agent_loop.run_agent(
        client=c, executor=_EjecutorOffice(tmp_path, skill_name="web-builder-specialist"),
        system_prompt="s", user_task="hazme una landing", model="m", max_turns=3,
        tool_schemas=_ESQUEMAS_WEB)
    assert c.llamadas == 1, "una sola tool mencionada no dispara el empujon"


def test_codigo_con_write_file_no_se_confunde_con_narrada(monkeypatch, tmp_path):
    """Codigo que usa write_file(...)/read_file(...) como funciones NO es narrar
    tools de orquestacion: esos nombres no estan en _TOOLS_NARRABLES."""
    _sin_ruido(monkeypatch)

    class _C:
        llamadas = 0

        def chat(self, messages, tools=None, model="", **k):
            self.llamadas += 1
            return {"choices": [{"message": {"role": "assistant", "content":
                "```python\ndef main():\n    write_file(p, x)\n    read_file(p)\n```"}}]}

    c = _C()
    agent_loop.run_agent(
        client=c, executor=_EjecutorOffice(tmp_path, skill_name="python-specialist"),
        system_prompt="s", user_task="muestrame un ejemplo de io", model="m",
        max_turns=3, tool_schemas=_ESQUEMAS_CODIGO)
    assert c.llamadas == 1, "write_file/read_file en codigo no es una tanda narrada"
