"""El auto-router tiene que elegir modelos CHICOS (el objetivo real es una
GPU de 24GB), nunca modelos frontera (70B/120B) por default - eso es lo
que se verifica aca.

Con el paso a multi-proveedor, el router ya no devuelve un modelo concreto
sino un TIER, y los modelos viven en las escaleras de providers.py. Asi
que la misma garantia se comprueba un nivel mas abajo: sobre TODOS los
candidatos de TODAS las escaleras, no solo sobre el primero de cada tier
(un fallback que escala a un modelo frontera invalidaria la prueba igual
que un default que lo hiciera)."""
import pytest

from groq_agent.auto_router import auto_route
from groq_agent.providers import (
    CHAIN_BY_TIER,
    DEFAULT_TIER,
    PROVIDERS,
    chain_for,
)

# POLITICA DE TAMAÑO (revisada 2026-07-28, contra los catalogos reales)
# ---------------------------------------------------------------------
# El diseño original pedia modelos de 7B-32B, para que cupieran en la GPU
# de 24GB que se piensa alquilar. Al consultar los catalogos EN VIVO de los
# free tiers, ese rango practicamente ha desaparecido: Cerebras sirve 3
# modelos y el mas chico es de 31B; el mas chico de SambaNova tambien es
# 31B. La eleccion real hoy no es "24B o 70B", es "31B o nada".
#
# Sumado a que un 8B no da la talla programando de verdad, la regla pasa a
# aplicarse SOLO donde lo chico sigue siendo lo correcto: `router-tiny`.
# Ahi el trabajo es elegir una etiqueta entre 24, no programar; un 8B lo
# hace bien (probado), gasta la minima cuota, y es el tier que mas veces se
# llama - una vez por tarea. Subirlo seria pagar el doble por acertar lo
# mismo.
#
# En los tiers de programacion ya no hay techo de tamaño: un fallback que
# escribe codigo malo no es un fallback.
_MODELOS_GRANDES = ("70b", "120b", "235b", "397b", "405b", "deepseek-v3", "deepseek-v4")

_TIERS_CON_PRESUPUESTO = {"router-tiny"}


def _es_frontera(model: str) -> bool:
    lowered = model.lower()
    return any(hint in lowered for hint in _MODELOS_GRANDES)


def test_auto_route_python_task():
    specialist, tier, domain = auto_route("Escribe una funcion es_primo(n) con tests")
    assert domain == "python"
    assert specialist == "python-specialist"
    assert tier in CHAIN_BY_TIER


def test_auto_route_web_task():
    specialist, tier, domain = auto_route("Necesito una landing page para mi restaurante")
    assert domain == "web"
    assert specialist == "web-builder-specialist"
    assert tier in CHAIN_BY_TIER


def test_auto_route_office_email_usa_tier_capaz():
    """office-email ya NO va en router-tiny(8B): daba textos flojos. Ahora
    coder-main, como el resto de oficina (word/ppt usan el tier grande de
    prosa). Un modelo diminuto para redactar no es lo que se quiere."""
    specialist, tier, domain = auto_route("Redacta un correo avisando que llego tarde")
    assert domain == "office_email"
    assert specialist == "office-email-specialist"
    assert tier == "coder-main"
    assert tier != "router-tiny"


@pytest.mark.parametrize("tier", sorted(_TIERS_CON_PRESUPUESTO))
def test_ningun_candidato_de_la_escalera_es_un_modelo_frontera(tier):
    culpables = [str(c) for c in CHAIN_BY_TIER[tier] if _es_frontera(c.model)]
    assert not culpables, (
        f"El tier '{tier}' tiene candidatos fuera del presupuesto de 24GB: {culpables}. "
        "Si es a proposito, documentalo como excepcion en providers.py y añadilo a "
        "_TIERS_CON_PRESUPUESTO."
    )


@pytest.mark.parametrize("tier", sorted(CHAIN_BY_TIER))
def test_toda_escalera_termina_en_nvidia(tier):
    """NVIDIA NIM (~40 RPM, sin tope diario) es el respaldo universal: si
    una escalera no termina ahi, esa categoria puede quedarse sin servir
    la tarea cuando los demas free tiers agoten su cuota diaria."""
    assert CHAIN_BY_TIER[tier][-1].provider == "nvidia"


# Cuantos candidatos NO-nvidia tiene que haber antes del primer nvidia.
# El objetivo declarado es no llegar nunca a NVIDIA: es la red de
# seguridad, y una red que se usa a diario deja de serlo (sus ~40 RPM se
# agotan y detras no queda nada). web-builder-large lleva un minimo mas
# bajo porque su requisito - sostener un system prompt de ~62K caracteres
# y aun asi emitir una tool call - descarta a los modelos chicos: ahi no
# se puede rellenar con cualquiera.
_MIN_COBERTURA_ANTES_DE_NVIDIA = {
    "router-tiny": 5,
    "coder-main": 5,
    "devstral-agentic": 5,
    "escalation-30b": 5,
    "web-builder-large": 3,
}


@pytest.mark.parametrize("tier", sorted(CHAIN_BY_TIER))
def test_nvidia_esta_bien_cubierto_por_delante(tier):
    chain = CHAIN_BY_TIER[tier]
    antes = 0
    for candidate in chain:
        if candidate.provider == "nvidia":
            break
        antes += 1
    minimo = _MIN_COBERTURA_ANTES_DE_NVIDIA[tier]
    assert antes >= minimo, (
        f"El tier '{tier}' solo tiene {antes} candidatos antes de NVIDIA (minimo "
        f"{minimo}). NVIDIA es la red de seguridad: si se usa a diario, se agota y "
        "deja de serlo."
    )


@pytest.mark.parametrize("tier", sorted(CHAIN_BY_TIER))
def test_la_cobertura_previa_a_nvidia_es_de_proveedores_distintos(tier):
    """Diez candidatos de dos proveedores no son cobertura: un 429 se lleva
    la mitad de la escalera de golpe."""
    previos = []
    for candidate in CHAIN_BY_TIER[tier]:
        if candidate.provider == "nvidia":
            break
        previos.append(candidate.provider)
    assert len(set(previos)) >= 3, (
        f"El tier '{tier}' se apoya en solo {len(set(previos))} proveedores distintos "
        f"antes de NVIDIA: {sorted(set(previos))}."
    )


@pytest.mark.parametrize("tier", sorted(CHAIN_BY_TIER))
def test_la_escalera_no_repite_proveedor_en_los_dos_primeros(tier):
    """Dos candidatos seguidos del mismo proveedor comparten el rate limit,
    asi que el segundo no es un fallback de verdad para un 429 - que es
    justo el error que mas se ve en los free tiers."""
    chain = CHAIN_BY_TIER[tier]
    assert chain[0].provider != chain[1].provider, (
        f"El tier '{tier}' arranca con dos candidatos de '{chain[0].provider}': un 429 "
        "de ese proveedor se come los dos primeros intentos."
    )


@pytest.mark.parametrize("tier", sorted(CHAIN_BY_TIER))
def test_todo_candidato_apunta_a_un_proveedor_registrado(tier):
    for candidate in CHAIN_BY_TIER[tier]:
        assert candidate.provider in PROVIDERS, (
            f"'{candidate}' apunta a un proveedor que no esta en PROVIDERS - "
            "se saltaria en silencio en cada llamada."
        )


def test_las_escaleras_tienen_fallback_real():
    """Una escalera de un solo candidato no es una escalera."""
    for tier, chain in CHAIN_BY_TIER.items():
        assert len(chain) >= 2, f"El tier '{tier}' no tiene fallback."


def test_chain_for_acepta_tier_modelo_explicito_y_vacio():
    assert chain_for("coder-main") == CHAIN_BY_TIER["coder-main"]
    assert chain_for("") == CHAIN_BY_TIER[DEFAULT_TIER]

    explicito = chain_for("nvidia:un-modelo-cualquiera")
    assert explicito[0].provider == "nvidia"
    assert explicito[0].model == "un-modelo-cualquiera"
    # Incluso una eleccion manual lleva respaldo detras: el objetivo es
    # que la tarea no se caiga, no honrar la eleccion a toda costa.
    assert len(explicito) > 1


def test_chain_for_rechaza_basura_con_un_mensaje_util():
    with pytest.raises(RuntimeError, match="no es un tier"):
        chain_for("qwen/qwen3-32b")  # id de modelo sin proveedor
    with pytest.raises(RuntimeError, match="desconocido"):
        chain_for("proveedor-inventado:modelo")


# --- charla vs tarea: lo decide el MODELO, no una lista de palabras ----
#
# El fallo que motivo esto: la preclasificacion era regex, corria ANTES del
# router, y mando "qie dices?" (una errata de "que dices?") al
# javascript-specialist. Ninguna lista de palabras entiende una errata.


class _ClienteFalso:
    """Devuelve lo que se le diga y guarda el system prompt que recibio."""

    def __init__(self, respuesta: str):
        self.respuesta = respuesta
        self.system_prompt = ""

    def chat(self, messages, model=None, temperature=None, **kwargs):
        self.system_prompt = messages[0]["content"]
        return {"choices": [{"message": {"content": self.respuesta}}]}


def test_el_clasificador_puede_contestar_charla():
    from groq_agent.auto_router import CHARLA, llm_classify_domain

    assert llm_classify_domain(_ClienteFalso("charla"), "qie dices?") == CHARLA


def test_la_charla_es_pegajosa_un_seguimiento_sigue_en_charla():
    """Un hilo de preguntas de conocimiento (que se contestan buscando) no
    debe saltar a un especialista que responde de memoria: con el contexto de
    charla (current_specialist=CHARLA), un seguimiento devuelve None -sigue en
    la charla-. Bug real: 'que motor lleva el 118d' -> python-specialist, que
    invento '1.8 TDI'."""
    from groq_agent.auto_router import CHARLA, llm_classify_domain

    r = llm_classify_domain(
        _ClienteFalso("continue"), "y del 118d nuevo de ahora?",
        current_specialist=CHARLA, last_task="que motor lleva el 118d F40",
    )
    assert r is None


def test_la_charla_pegajosa_si_rompe_ante_un_pedido_de_archivo():
    """Pero un pedido de trabajo REAL sobre archivos si sale de la charla al
    especialista de su dominio."""
    from groq_agent.auto_router import CHARLA, llm_classify_domain

    r = llm_classify_domain(
        _ClienteFalso("python"), "hazme un script .py con esos codigos motor",
        current_specialist=CHARLA, last_task="que motor lleva el 118d F40",
    )
    assert r == "python"


def test_el_prompt_de_charla_manda_buscar_y_seguir_en_charla():
    from groq_agent.auto_router import CHARLA, _CONTINUE_SENTINEL, _classifier_system_prompt

    p = _classifier_system_prompt(CHARLA, last_task="a que precio esta el bitcoin")
    assert "BUSCANDO" in p                 # el contexto es buscar en la web
    assert _CONTINUE_SENTINEL in p         # un seguimiento se queda en la charla
    assert "bitcoin" in p                  # cita de que iba la ultima pregunta


@pytest.mark.parametrize("ctx", [None, "__charla__", "python-specialist"])
def test_el_prompt_lleva_la_regla_de_oficina(ctx):
    """Un pedido de documento/presentacion/excel SOBRE un tema se iba a python
    (que ni puede generarlo). La REGLA DE OFICINA -de alto nivel, con ejemplos-
    va en las tres ramas del clasificador para que el 8B no lo mande a python."""
    from groq_agent.auto_router import _classifier_system_prompt

    p = _classifier_system_prompt(ctx, last_task="una web de tapas")
    assert "REGLA DE OFICINA" in p
    assert "office_presentation" in p and "office_word" in p
    assert "NUNCA" in p or "NO python" in p or "nunca" in p.lower()


def test_charla_no_se_confunde_con_continuacion():
    """None y CHARLA significan cosas distintas: confundirlos haria que un
    saludo reanudase la tarea anterior (o que una continuacion perdiese su
    especialista)."""
    from groq_agent.auto_router import CHARLA, llm_classify_domain

    assert llm_classify_domain(_ClienteFalso("continue"), "si", current_specialist="web-builder") is None
    assert CHARLA is not None


def test_auto_route_llm_propaga_la_charla_sin_elegir_especialista():
    from groq_agent.auto_router import CHARLA, auto_route_llm

    assert auto_route_llm(_ClienteFalso("charla"), "hola") == CHARLA


def test_una_tarea_de_verdad_sigue_clasificandose_por_dominio():
    """La etiqueta nueva no puede tragarse el caso normal."""
    from groq_agent.auto_router import auto_route_llm

    routed = auto_route_llm(_ClienteFalso("web"), "hazme una web para el bar")
    assert routed is not None
    especialista, _modelo, dominio = routed
    assert dominio == "web"


@pytest.mark.parametrize("especialista_activo", [None, "web-builder-specialist"])
def test_el_prompt_le_ofrece_la_opcion_charla_siempre(especialista_activo):
    """Con y sin conversacion en curso: un saludo en medio de una tarea
    tampoco tiene que cargar las herramientas."""
    from groq_agent.auto_router import _CHARLA_SENTINEL, _classifier_system_prompt

    assert _CHARLA_SENTINEL in _classifier_system_prompt(especialista_activo)


def test_el_prompt_pide_juzgar_la_intencion_no_las_palabras():
    """Es LA instruccion que arregla el caso 'qie dices?'."""
    from groq_agent.auto_router import _classifier_system_prompt

    assert "INTENCION" in _classifier_system_prompt(None)


# --- 'charla' es "no pide trabajo", no "no se en que cajon meterlo" -----
#
# Fallo real: "hazme un modelo 3D de un bloque motor 3 cilindros" se
# contesto con un tutorial de Blender. No hay dominio de 3D/CAD en la
# lista, asi que el modelo chico busco una etiqueta, no encontro ninguna, y
# se acogio a `charla`. Antes del centinela caia en el fallback y al menos
# trabajaba: fue una regresion, no un fallo de siempre.


@pytest.mark.parametrize(
    "pedido",
    [
        "hazme un modelo 3D de un bloque motor 3 cilindros",
        "crea un esquema del circuito",
        "generame un plano de la pieza",
        "diseñame una carcasa para la placa",
    ],
)
def test_un_pedido_sin_dominio_claro_no_es_charla(pedido):
    """Aunque el modelo diga 'charla', si hay un verbo de accion explicito
    se trabaja: equivocarse hacia el trabajo cuesta herramientas de mas;
    equivocarse hacia la charla deja al usuario con un tutorial."""
    from groq_agent.auto_router import CHARLA, llm_classify_domain

    assert llm_classify_domain(_ClienteFalso("charla"), pedido) != CHARLA


def test_documenta_es_trabajo_no_charla():
    """Bug visto en vivo: 'Documenta esta funcion' salia como charla (el
    modelo dudaba y 'documenta' no estaba en el veto de accion) -> no se
    hacia nada. Documentar codigo SIEMPRE es una tarea."""
    from groq_agent.auto_router import CHARLA, llm_classify_domain

    assert llm_classify_domain(
        _ClienteFalso("charla"), "documenta esta funcion explicando cada parametro"
    ) != CHARLA


def test_explicar_un_concepto_sigue_siendo_charla():
    """El contrapeso: 'explica' NO se metio en el veto, para no romper la
    charla legitima de una pregunta conceptual."""
    from groq_agent.auto_router import CHARLA, llm_classify_domain

    assert llm_classify_domain(
        _ClienteFalso("charla"), "explicame la diferencia entre una lista y una tupla"
    ) == CHARLA


def test_el_veto_no_se_come_la_charla_de_verdad():
    """El contrapeso: si el veto disparase siempre, volveriamos a cargar
    19.000 tokens para responder a un 'hola'."""
    from groq_agent.auto_router import CHARLA, llm_classify_domain

    for suelto in ("hola", "que tal", "gracias", "¿que diferencia hay entre flex y grid?"):
        assert llm_classify_domain(_ClienteFalso("charla"), suelto) == CHARLA


def test_el_prompt_avisa_de_que_charla_no_es_un_cajon_de_sastre():
    from groq_agent.auto_router import _classifier_system_prompt

    p = _classifier_system_prompt(None)
    assert "no que no" in p and "nunca 'charla'" in p


# --- Router HIBRIDO: el heuristico propone el dominio, el LLM confirma -----
#
# Medido en vivo (117 prompts, router-tiny sin nvidia): el heuristico acierta
# ~99% el dominio y el 8B ~74%. Asi que el dominio lo propone el regex (gratis)
# y el modelo solo confirma o corrige. La decision charla/continuacion sigue
# siendo del modelo (lo que el regex no sabe). Sin llamada extra: la pista va
# en el MISMO prompt del router que ya se llamaba.


def test_el_prompt_incluye_la_pista_del_heuristico_cuando_hay_una():
    """Un dominio claro para el regex ('en Rust') tiene que llegar al modelo
    como propuesta explicita a confirmar."""
    from groq_agent.auto_router import _classifier_system_prompt

    p = _classifier_system_prompt(None, heuristic_hint="rust")
    assert "'rust'" in p and "CONFIRMA ese dominio" in p


def test_sin_pista_el_prompt_no_inventa_una():
    """Si el regex no matcheo nada, no se le mete al modelo un prior falso."""
    from groq_agent.auto_router import _classifier_system_prompt

    p = _classifier_system_prompt(None, heuristic_hint=None)
    assert "PISTA:" not in p


def test_salida_ilegible_del_modelo_cae_al_dominio_del_heuristico():
    """El fallback deja de ser el relleno 'python': si el modelo devuelve
    basura pero el regex sabia que era rust, gana rust (99% vs default)."""
    from groq_agent.auto_router import llm_classify_domain

    # "en Rust" lo matchea el heuristico; el modelo responde algo ilegible.
    got = llm_classify_domain(_ClienteFalso("???"), "tengo un problema en Rust con ownership")
    assert got == "rust"


def test_salida_ilegible_sin_pista_sigue_cayendo_a_python():
    """Si ni el modelo ni el regex clasifican, 'python' sigue de ultimo recurso."""
    from groq_agent.auto_router import llm_classify_domain

    got = llm_classify_domain(_ClienteFalso("???"), "zzz qwerty asdf")
    assert got == "python"


def test_el_modelo_puede_corregir_la_pista_del_heuristico():
    """La pista es fuerte pero no una jaula: si el modelo devuelve un dominio
    valido distinto, ese gana (es una confirmacion/correccion, no un candado)."""
    from groq_agent.auto_router import llm_classify_domain

    # El regex vería 'python' por 'def', pero el modelo corrige a 'security'.
    got = llm_classify_domain(_ClienteFalso("security"), "def login(): revisa la vulnerabilidad")
    assert got == "security"


# --- Extraccion de la etiqueta por FRONTERA DE PALABRA ---------------------
#
# El 8B a veces contesta con una frase en vez de una palabra. Con match por
# subcadena, 'go' se colaba dentro de 'algo'/'tengo'/'luego' -> clasificaba
# como go tareas que no lo eran (fallo real: 'ordename esta lista...' -> go).


def test_go_no_se_cuela_dentro_de_palabras_espanolas():
    from groq_agent.auto_router import _extraer_dominio

    assert _extraer_dominio("te lo hago algo, luego tengo que pensarlo") is None


def test_extraer_dominio_reconoce_la_etiqueta_suelta():
    from groq_agent.auto_router import _extraer_dominio

    assert _extraer_dominio("go") == "go"
    assert _extraer_dominio("creo que es python") == "python"


def test_extraer_dominio_tolera_multipalabra_con_espacio_o_guion():
    from groq_agent.auto_router import _extraer_dominio

    assert _extraer_dominio("code review") == "code_review"
    assert _extraer_dominio("code_review") == "code_review"
    assert _extraer_dominio("office spreadsheet") == "office_spreadsheet"


def test_extraer_dominio_devuelve_la_primera_etiqueta_mencionada():
    """Si el modelo se corrige a media frase, gana la que dice primero."""
    from groq_agent.auto_router import _extraer_dominio

    assert _extraer_dominio("security, aunque tambien podria ser python") == "security"


def test_frase_con_go_incrustado_no_clasifica_como_go():
    """Integracion: salida en prosa sin etiqueta valida -> cae al heuristico
    (aqui None) o python, nunca 'go' por 'tengo'/'algo'."""
    from groq_agent.auto_router import llm_classify_domain

    got = llm_classify_domain(
        _ClienteFalso("claro, te lo ordeno con un algoritmo, tengo que verlo"),
        "ordename esta lista de diccionarios por la fecha",
    )
    assert got != "go"
