"""Registro de proveedores gratuitos OpenAI-compatibles y la escalera de
fallback por categoria (tier).

POR QUE ESTE MODULO EXISTE
--------------------------
Antes habia un solo proveedor (OpenRouter) y un solo modelo por tier, asi
que un 429 o un modelo caido cortaba la tarea a mitad de camino (pasaba de
verdad: se corto despues de escribir 2 archivos, ver README). El objetivo
de diseño ahora es que una tarea NUNCA se caiga por el proveedor: cada
tier tiene una lista ORDENADA de candidatos en proveedores distintos, y
cada proveedor tiene varias API keys "en el banquillo". Si el primero
devuelve 429/401/5xx, se prueba la siguiente key; si el proveedor entero
falla, el siguiente proveedor. NVIDIA NIM cierra todas las escaleras
(~40 RPM y sin tope diario - el unico free tier que aguanta un ciclo
agentico completo).

EL FILTRO QUE IMPORTA: TOOL-CALLING
-----------------------------------
groq_client.chat() manda 'tools' + tool_choice=auto, y agent_loop no
arranca sin eso. Un proveedor gratis sin function calling no sirve para
nada aca, por muy buena que sea su cuota. Todos los de abajo lo soportan.

EL SEGUNDO FILTRO: TAMAÑO (7B-32B)
----------------------------------
Sigue vigente el objetivo original del catalogo: modelos que quepan en una
GPU de 24GB, no modelos frontera (ver config/specialists.yaml,
`base_model_ref`). Por eso las escaleras de abajo eligen, para cada tier,
el modelo comparable en tamaño de cada proveedor - NO el mas grande que
tenga. La unica excepcion deliberada sigue siendo web-builder-large.

OJO CON LOS IDs DE MODELO Y LAS URLs
------------------------------------
Los catalogos de los free tiers cambian casi cada mes. Un ID de modelo
equivocado o una base_url equivocada dejan a ese candidato MUERTO EN
SILENCIO (solo se nota como "un fallback mas que no responde"). Por eso
existe `orquestador --check-providers`: prueba de verdad cada candidato
con una tool call minima y dice cual esta vivo. Correrlo al configurar las
keys y cada vez que algo se sienta raro; `verified_base_url=False` marca
las URLs que NO pude confirmar contra la doc oficial.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

# --- Proveedores ------------------------------------------------------
#
# key_prefix: de ahi salen las variables de entorno del banquillo. Con
# prefijo "NVIDIA" se leen NVIDIA_API_KEY, NVIDIA_API_KEY_2 ... _9, y
# ademas cada una puede llevar varias keys separadas por coma. Ver
# keys_for().


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    key_prefix: str
    # Placeholders a rellenar en base_url desde el entorno (Cloudflare
    # mete el account id EN la URL, no en un header).
    base_url_vars: tuple[str, ...] = ()
    # LLM7 responde sin key; el resto la exige.
    needs_key: bool = True
    extra_headers: tuple[tuple[str, str], ...] = ()
    # False = no pude confirmar esta URL contra doc oficial. Verificar con
    # --check-providers antes de confiar en este proveedor.
    verified_base_url: bool = True
    # Segundos minimos entre dos llamadas a la MISMA key, derivados del RPM
    # publicado del free tier (30 RPM -> 2.0s). Espera preventiva para no
    # provocar el 429 en vez de reaccionar a el. Ver wait_needed().
    min_interval_s: float = 2.0
    # Si esta puesto, el proveedor esta DESACTIVADO y el texto dice por que
    # (tipicamente: resulto no ser gratis). Se desactiva en vez de borrarse
    # para no repetir el error de volver a añadirlo dentro de tres meses.
    # Un proveedor desactivado no se pregunta, no se prueba y no gasta un
    # round-trip en el failover.
    de_pago: str = ""
    notes: str = ""

    @property
    def activo(self) -> bool:
        return not self.de_pago


PROVIDERS: dict[str, Provider] = {
    # ~40 RPM y SIN tope diario, 100+ modelos en el rango 7B-32B, tool
    # calling documentado oficialmente. Es el unico free tier que aguanta
    # un ciclo agentico entero, asi que cierra todas las escaleras.
    "nvidia": Provider(
        name="nvidia",
        base_url="https://integrate.api.nvidia.com/v1",
        key_prefix="NVIDIA",
        min_interval_s=1.5,  # ~40 RPM
        notes="~40 RPM, sin tope diario. Respaldo final de todos los tiers.",
    ),
    # Endpoint DEDICADO a Codestral (modelo de codigo), 30 RPM / 2000 RPD
    # aparte de la cuota de La Plateforme. OJO: host distinto a
    # api.mistral.ai, es su propia base_url.
    "codestral": Provider(
        name="codestral",
        base_url="https://codestral.mistral.ai/v1",
        key_prefix="CODESTRAL",
        min_interval_s=2.0,  # 30 RPM
        notes="30 RPM / 2000 RPD solo para Codestral.",
    ),
    # Devstral y Mistral Small 24B nativos: exactamente los modelos que el
    # catalogo real queria para el tier devstral-agentic.
    "mistral": Provider(
        name="mistral",
        base_url="https://api.mistral.ai/v1",
        key_prefix="MISTRAL",
        min_interval_s=1.0,  # ~1 RPS
        notes="Plan Experiment gratis. Devstral/Mistral Small nativos.",
    ),
    # Qwen3-32B a mucha velocidad, 1M tokens/dia.
    "cerebras": Provider(
        name="cerebras",
        base_url="https://api.cerebras.ai/v1",
        key_prefix="CEREBRAS",
        min_interval_s=2.0,  # 30 RPM
        de_pago="402 'payment required' en sus 3 modelos (comprobado "
                "2026-07-28). El free tier que anuncian exige activar "
                "facturacion.",
        notes="Muy rapido, pero sus 3 modelos piden pago.",
    ),
    # Su catalogo bajo 24B mejoro desde que se dejo de usar: ya tiene
    # qwen3-32b. Y 14.400 RPD en los 8B lo hace ideal para el router.
    "groq": Provider(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        key_prefix="GROQ",
        min_interval_s=2.0,  # 30 RPM
        notes="14.400 RPD en modelos 8B - el mejor para el router.",
    ),
    "siliconflow": Provider(
        name="siliconflow",
        base_url="https://api.siliconflow.com/v1",
        key_prefix="SILICONFLOW",
        min_interval_s=2.0,  # 30 RPM
        de_pago="403 'account balance is insufficient' en TODOS sus modelos "
                "(comprobado 2026-07-28). Su catalogo de 73 modelos solo se "
                "sirve con saldo.",
        notes="73 modelos, pero ninguno gratis.",
    ),
    "sambanova": Provider(
        name="sambanova",
        base_url="https://api.sambanova.ai/v1",
        key_prefix="SAMBANOVA",
        verified_base_url=False,
        min_interval_s=3.0,  # 20 RPM
        notes="20 RPM / 200K tokens-dia. Modelos grandes (DeepSeek V3.x).",
    ),
    "cloudflare": Provider(
        name="cloudflare",
        base_url="https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1",
        key_prefix="CLOUDFLARE",
        base_url_vars=("CLOUDFLARE_ACCOUNT_ID",),
        notes="10.000 neurons/dia. Modelos chicos + FLUX para imagenes.",
    ),
    # Hosteado en la UE, con nivel ANONIMO: no hace falta cuenta ni key.
    # Va lento (2 RPM por IP) pero es cobertura que no cuesta nada montar,
    # y en una escalera lo que importa es que exista cuando los de arriba
    # ya fallaron.
    "ovh": Provider(
        name="ovh",
        base_url="https://oai.endpoints.kepler.ai.cloud.ovh.net/v1",
        key_prefix="OVH",
        needs_key=False,
        verified_base_url=False,
        min_interval_s=5.0,  # 2 RPM por IP
        notes="Sin registro, hosteado en la UE. 2 RPM: solo ultimo recurso.",
    ),
    # 100 RPM / 200K TPM, hosteado en la UE. De los free tiers nuevos, el
    # de limites mas altos por peticion.
    "scaleway": Provider(
        name="scaleway",
        base_url="https://api.scaleway.ai/v1",
        key_prefix="SCALEWAY",
        verified_base_url=False,
        min_interval_s=1.0,  # 100 RPM
        de_pago=(
            '429 "INSUFFICIENT QUOTA" en todos sus modelos (comprobado 2026-07-28). Los 100 RPM que anuncia son del plan de pago.'
        ),
        notes="100 RPM / 200K TPM. UE. Registro sin tarjeta.",
    ),
    # Router OpenAI-compatible de HuggingFace: da acceso a modelos servidos
    # por varios proveedores detras con una sola key.
    "huggingface": Provider(
        name="huggingface",
        base_url="https://router.huggingface.co/v1",
        key_prefix="HUGGINGFACE",
        verified_base_url=False,
        min_interval_s=2.0,
        notes="Creditos mensuales de Inference Providers. Miles de modelos.",
    ),
    "chutes": Provider(
        name="chutes",
        base_url="https://llm.chutes.ai/v1",
        key_prefix="CHUTES",
        verified_base_url=False,
        min_interval_s=2.0,
        notes="Tier gratis con colas compartidas: lento en horas punta.",
    ),
    # OpenRouter VUELVE, pero en otro papel. Se saco de aqui cuando era el
    # UNICO proveedor y un 429 suyo cortaba la tarea entera. Como uno mas
    # entre diez, con ~20 modelos ':free' y una key que ya esta en el .env,
    # es cobertura gratis que no cuesta nada aprovechar. Va en posiciones
    # tardias: nunca vuelve a ser el primero de una escalera.
    "openrouter": Provider(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        key_prefix="OPENROUTER",
        min_interval_s=3.0,  # 20 RPM
        extra_headers=(
            ("HTTP-Referer", "https://github.com/local/orquestador-modelos"),
            ("X-Title", "orquestador-modelos"),
        ),
        de_pago=(
            "404 'This model is unavailable for free' en sus modelos :free "
            "(comprobado 2026-07-28). Ahora exigen 10 USD de saldo para acceder "
            "al catalogo gratuito. Se saco de aqui en su dia por ser un punto "
            "unico de fallo, se reintrodujo como suplente, y vuelve a salir: su "
            "tramo gratis ya no existe sin pagar."
        ),
        notes="~20 modelos ':free', pero exigen saldo. Desactivado.",
    ),
    "deepinfra": Provider(
        name="deepinfra",
        base_url="https://api.deepinfra.com/v1/openai",
        key_prefix="DEEPINFRA",
        verified_base_url=False,
        min_interval_s=2.0,
        de_pago=(
            '402 "You need positive balance to do inference" (comprobado 2026-07-28). No hay tramo gratis, solo credito comprado.'
        ),
        notes="Catalogo open-weight amplio; tramo gratis limitado.",
    ),
    "together": Provider(
        name="together",
        base_url="https://api.together.xyz/v1",
        key_prefix="TOGETHER",
        verified_base_url=False,
        min_interval_s=2.0,
        de_pago=(
            "401 en todos sus modelos con una key valida solo entonces creada (comprobado 2026-07-28): su tramo gratis ya no cubre inferencia."
        ),
        notes="Tiene modelos etiquetados 'free' ademas del credito inicial.",
    ),
    "novita": Provider(
        name="novita",
        base_url="https://api.novita.ai/v3/openai",
        key_prefix="NOVITA",
        verified_base_url=False,
        min_interval_s=2.0,
        notes="Credito inicial + algunos modelos gratis.",
    ),
    "fireworks": Provider(
        name="fireworks",
        base_url="https://api.fireworks.ai/inference/v1",
        key_prefix="FIREWORKS",
        verified_base_url=False,
        min_interval_s=2.0,
        de_pago=(
            "412 Precondition Failed incluso para listar modelos (comprobado 2026-07-28): la cuenta exige configuracion de pago antes de servir nada."
        ),
        notes="Credito inicial. Rapido.",
    ),
    "vercel": Provider(
        name="vercel",
        base_url="https://ai-gateway.vercel.sh/v1",
        key_prefix="VERCEL",
        verified_base_url=False,
        min_interval_s=2.0,
        de_pago=(
            '403 "AI Gateway requires a valid credit card" (comprobado 2026-07-28).'
        ),
        notes="5 USD/mes de credito renovable sobre un catalogo amplio.",
    ),
    "cohere": Provider(
        name="cohere",
        base_url="https://api.cohere.ai/compatibility/v1",
        key_prefix="COHERE",
        verified_base_url=False,
        min_interval_s=3.0,  # 20 RPM
        notes="20 RPM / 1.000 llamadas al mes. OJO: la trial key es SOLO uso "
              "no comercial.",
    ),
    # Google AI Studio, endpoint OpenAI-compatible oficial (confirmado en
    # ai.google.dev/gemini-api/docs/openai). Free tier real sin tarjeta y tool
    # calling documentado. Los flash/flash-lite son chicos y rapidos (router);
    # el pro es grande y con contexto enorme (escalation / web-builder).
    "gemini": Provider(
        name="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        key_prefix="GEMINI",
        min_interval_s=4.0,  # free tier ~15 RPM en flash; conservador
        notes="Google AI Studio. Free tier sin tarjeta, tool calling. Modelos gemini-3.x.",
    ),
    # Zhipu GLM (plataforma global z.ai). glm-4.x-flash son gratis (~1000 RPD)
    # y con tool calling nativo (schema OpenAI). GLM ya se usaba via chutes/
    # nvidia; esto lo da directo. base_url no confirmada contra doc oficial.
    "zai": Provider(
        name="zai",
        base_url="https://api.z.ai/api/paas/v4",
        key_prefix="ZAI",
        verified_base_url=False,
        min_interval_s=2.0,
        notes="Zhipu GLM. glm-4.x-flash gratis (~1000 RPD), tool calling nativo.",
    ),
    # Net-new (2026-08): hub de modelos de Alibaba. Tramo gratis REAL (2000
    # llamadas/dia, 500/modelo, SIN tarjeta) y function calling OpenAI-compatible
    # confirmado en vivo con Qwen (varias fuentes + un agent loop de terceros).
    # 900+ modelos: Qwen(-Coder), DeepSeek, GLM. verified_base_url=False para
    # re-sondear endpoint e ids en la primera corrida.
    "modelscope": Provider(
        name="modelscope",
        base_url="https://api-inference.modelscope.cn/v1",
        key_prefix="MODELSCOPE",
        verified_base_url=False,
        min_interval_s=1.5,
        notes="Alibaba ModelScope. 2000 RPD sin tarjeta, tool calling OpenAI-compatible (Qwen/DeepSeek/GLM).",
    ),
    # Sin registro para el tier basico: ultimo recurso absoluto cuando no
    # hay ni una key configurada.
    "llm7": Provider(
        name="llm7",
        base_url="https://api.llm7.io/v1",
        key_prefix="LLM7",
        needs_key=False,
        verified_base_url=False,
        min_interval_s=2.0,  # 30 RPM
        notes="30 RPM sin key. Fiabilidad desconocida.",
    ),
    # Net-new (2026-08): proxy comunitario SIN key con tool calling
    # OpenAI-compatible real (parametro `tools` -> `tool_calls`, confirmado en
    # su APIDOCS). Mismo rol que llm7: ultimo recurso cuando no hay NI UNA key.
    # Su endpoint es .../openai (no .../v1): verified_base_url=False para que se
    # re-sondee. Modelo `openai` = su clase GPT, la mejor para tool use.
    "pollinations": Provider(
        name="pollinations",
        base_url="https://text.pollinations.ai/openai",
        key_prefix="POLLINATIONS",
        needs_key=False,
        verified_base_url=False,
        min_interval_s=3.0,  # tramo anonimo, limitado
        notes="Sin key, tool calling OpenAI-compatible (openai/mistral). Fiabilidad desconocida; ultimo recurso.",
    ),
}


# --- Banquillo de API keys -------------------------------------------


def keys_for(provider: Provider) -> list[str]:
    """Todas las keys configuradas para un proveedor, en orden de uso.

    Dos formas de poner suplentes, y se pueden combinar:
      NVIDIA_API_KEY=k1                 -> [k1]
      NVIDIA_API_KEY_2=k2               -> [k1, k2]
      NVIDIA_API_KEY=k1,k2,k3           -> [k1, k2, k3]

    Varias keys del MISMO proveedor son cuota extra real: los free tiers
    limitan por cuenta, asi que dos cuentas gratis de NVIDIA son 80 RPM en
    vez de 40. Devuelve [""] para proveedores sin key (LLM7) para que
    igual tengan un "slot" que intentar.
    """
    names = [f"{provider.key_prefix}_API_KEY"] + [
        f"{provider.key_prefix}_API_KEY_{i}" for i in range(2, 10)
    ]
    keys: list[str] = []
    for name in names:
        raw = os.environ.get(name, "")
        for piece in raw.split(","):
            key = piece.strip()
            # Sin dedupe silencioso: una key repetida en dos variables es
            # un error de configuracion, y contarla dos veces solo gasta
            # dos intentos identicos en la escalera.
            if key and key not in keys:
                keys.append(key)
    if not keys and not provider.needs_key:
        return [""]
    return keys


def resolve_base_url(provider: Provider) -> str | None:
    """None si falta una variable que va DENTRO de la URL (el account id
    de Cloudflare): sin ella la URL queda con un placeholder literal y
    todas las peticiones darian 404 sin explicar por que."""
    url = provider.base_url
    for var in provider.base_url_vars:
        value = os.environ.get(var, "").strip()
        if not value:
            return None
        url = url.replace("{" + var + "}", value)
    return url


# --- Ritmo y keys muertas --------------------------------------------
#
# DECISION DE DISEÑO (pedida explicitamente): un fallo NO destierra a un
# candidato del resto de la sesion. Cada llamada vuelve a empezar por el
# PRIMERO de la escalera. Si el preferido se recupera a los 10 segundos,
# la siguiente tarea ya lo usa - no se queda arrastrando un suplente peor
# durante toda la sesion solo porque hubo un 429 puntual.
#
# La unica excepcion es una key INVALIDA (401/403): no se arregla sola,
# solo editando el .env, asi que reintentarla en cada llamada durante toda
# la sesion es gasto puro sin ninguna posibilidad de exito. Esas si se
# apartan, y si al final se agota la escalera el error lo dice para que se
# sepa que hay que corregirlas.

_DEAD_KEYS: set[tuple[str, int]] = set()
_LAST_USED: dict[tuple[str, int], float] = {}


def mark_dead_key(provider_name: str, key_index: int) -> None:
    _DEAD_KEYS.add((provider_name, key_index))


def is_dead_key(provider_name: str, key_index: int) -> bool:
    return (provider_name, key_index) in _DEAD_KEYS


def dead_keys() -> list[tuple[str, int]]:
    return sorted(_DEAD_KEYS)


def note_used(provider_name: str, key_index: int) -> None:
    _LAST_USED[(provider_name, key_index)] = time.monotonic()


def wait_needed(provider_name: str, key_index: int) -> float:
    """Segundos que conviene esperar antes de volver a usar esta key para
    no chocar contra su propio rate limit.

    El ritmo sale del RPM de cada proveedor (30 RPM = una llamada cada
    2s). Es una espera PREVENTIVA sobre un candidato sano - nunca se espera
    a uno que acaba de fallar: ahi se pasa al siguiente al instante."""
    provider = PROVIDERS.get(provider_name)
    if provider is None:
        return 0.0
    last = _LAST_USED.get((provider_name, key_index))
    if last is None:
        return 0.0
    elapsed = time.monotonic() - last
    return max(0.0, min(provider.min_interval_s, MAX_PACING_WAIT_S) - elapsed)


def reset_runtime_state() -> None:
    _DEAD_KEYS.clear()
    _LAST_USED.clear()


# Tope duro de espera preventiva. Pedido explicitamente: nunca mas de 5
# segundos colgado esperando a un proveedor. Si su ritmo pide mas, se
# intenta igual y, si contesta 429, se pasa al siguiente (mas rapido que
# esperar).
MAX_PACING_WAIT_S = 5.0


# --- Escaleras por categoria -----------------------------------------
#
# Un candidato es (proveedor, id_de_modelo). El orden es el orden de
# intento. Criterios, en este orden:
#   1. Que el modelo encaje en el TIER (tamaño y especialidad), no que sea
#      el mas potente del proveedor.
#   2. Cuota que aguante el uso real de ese tier (el router se llama una
#      vez por tarea; coder-main, decenas de veces por tarea).
#   3. Diversidad de proveedor: dos candidatos seguidos del mismo
#      proveedor comparten el 429, asi que no son un fallback de verdad.
#   4. NVIDIA al final SIEMPRE (sin tope diario).


@dataclass(frozen=True)
class Candidate:
    provider: str
    model: str

    def __str__(self) -> str:  # lo que se ve en la UI y en las sesiones
        return f"{self.provider}:{self.model}"


def _c(provider: str, model: str) -> Candidate:
    return Candidate(provider, model)


# OBJETIVO DE ESTAS ESCALERAS: no llegar nunca a NVIDIA.
#
# NVIDIA es el unico free tier sin tope diario, asi que es la red de
# seguridad - y una red de seguridad que se usa a diario deja de serlo:
# sus ~40 RPM se agotan y entonces si no queda nada detras. Por eso cada
# escalera mete DELANTE todos los proveedores que puedan hacer ese trabajo
# concreto, y NVIDIA queda al final como lo que debe ser: el recurso que
# casi nunca se toca.
#
# "Que puedan hacer ese trabajo" es la parte no negociable: no vale rellenar
# con cualquier proveedor. Cada candidato tiene que tener un modelo del
# tamaño/especialidad del tier. Un relleno que no sostiene el trabajo no es
# cobertura, es un fallo mas lento.
#
# Criterios de orden, en este orden:
#   1. Que el modelo encaje en el TIER (tamaño y especialidad).
#   2. Cuota que aguante el uso real de ese tier (el router se llama una
#      vez por tarea; coder-main, decenas de veces por tarea).
#   3. Diversidad de proveedor: dos candidatos seguidos del mismo
#      proveedor comparten el 429, asi que no son un fallback de verdad.
#   4. NVIDIA al final SIEMPRE, y con todo lo posible por delante.
#
# OJO: varios IDs de modelo de cloudflare/zai/siliconflow NO estan
# verificados contra la doc oficial (sus catalogos cambian y no todos
# publican una lista estable). Estan aca como cobertura extra, no como
# apuesta: `orquestador --check-providers` los prueba de verdad uno a uno y
# dice cuales estan vivos. Un candidato muerto no rompe nada - se salta en
# silencio - pero conviene podarlo cuando se detecte.


# TAMAÑO DE MODELO: por que estas escaleras ya no son de 7B-32B
# ---------------------------------------------------------------
# El diseño original pedia modelos que cupieran en una GPU de 24GB. Al
# consultar los catalogos REALES (no adivinarlos) resulta que ese rango casi
# ha desaparecido de los free tiers, y ademas un 8B no da la talla
# programando de verdad. Donde SI se mantiene lo chico es en `router-tiny`:
# ahi el trabajo es elegir una etiqueta entre 24, no programar; un 8B lo
# hace bien (probado), gasta la minima cuota, y es el tier que mas veces se
# llama - una vez por tarea.
#
# QUE ES GRATIS DE VERDAD (comprobado con tool calls reales, 2026-07-28)
# ----------------------------------------------------------------------
# Estos responden gratis:   mistral, codestral, groq, cloudflare, ovh,
#                           sambanova (solo DeepSeek V3.1/V3.2), nvidia.
# Estos NO son gratis:      siliconflow (403 "account balance is
#                           insufficient" en TODOS sus modelos) y cerebras
#                           (402 "payment required" en sus 3 modelos).
# Por eso siliconflow y cerebras quedan al final de las escaleras: un
# candidato que siempre falla no es cobertura, es un round-trip tirado. Se
# dejan por si la cuenta se activa mas adelante - el failover los salta sin
# ruido.
#
# NVIDIA NO TIENE NI UN MODELO QWEN. Su catalogo (102 modelos) va de
# Mistral, DeepSeek, Llama, Nemotron y gpt-oss. La version anterior de este
# archivo asumia Qwen ahi y daba 404 en 7 de sus 8 entradas - justo en la
# red de seguridad, que es donde menos se puede fallar.
#
# Los marcados [ok] respondieron a una tool call real en --check-providers.


# router-tiny: clasificar dominio y decidir CONTINUACION vs tarea nueva.
# Groq primero por cuota (14.400 peticiones/dia en sus 8B) y porque este
# tier se llama una vez por tarea.
CHAIN_ROUTER_TINY = [
    _c("groq", "llama-3.1-8b-instant"),                                # [ok]
    _c("mistral", "ministral-8b-latest"),                              # [ok]
    _c("huggingface", "Qwen/Qwen3-8B"),                                # [ok]
    _c("cloudflare", "@cf/mistralai/mistral-small-3.1-24b-instruct"),  # [ok]
    _c("novita", "meta-llama/llama-3.1-8b-instruct"),
    _c("chutes", "Qwen/Qwen3.6-27B-TEE"),
    _c("ovh", "Mistral-Small-3.2-24B-Instruct-2506"),                  # [ok] 2 RPM
    _c("gemini", "gemini-3.1-flash-lite"),                             # nuevo: probar con --check-providers
    _c("zai", "glm-4.5-flash"),                                        # nuevo
    _c("modelscope", "Qwen/Qwen3-8B"),                                 # nuevo: probar id+toolcall en vivo
    _c("nvidia", "nvidia/llama-3.1-nemotron-nano-8b-v1"),              # [ok]
    _c("nvidia", "meta/llama-3.1-8b-instruct"),                        # [ok]
]

# coder-main: el caballo de batalla, DEDICADO a codigo, y el tier que mas
# llamadas hace por tarea. HuggingFace es clave aca: es el unico free tier
# que sigue sirviendo los Qwen-Coder que el catalogo real queria.
CHAIN_CODER_MAIN = [
    _c("codestral", "codestral-latest"),                               # [ok]
    _c("mistral", "devstral-medium-latest"),                           # [ok]
    _c("huggingface", "Qwen/Qwen3-Coder-30B-A3B-Instruct"),
    _c("sambanova", "DeepSeek-V3.2"),                                  # [ok]
    _c("huggingface", "Qwen/Qwen2.5-Coder-32B-Instruct"),
    _c("chutes", "Qwen/Qwen3-32B-TEE"),
    _c("cloudflare", "@cf/qwen/qwen2.5-coder-32b-instruct"),
    _c("ovh", "Mistral-Small-3.2-24B-Instruct-2506"),                  # [ok]
    _c("zai", "glm-4.7-flash"),                                        # nuevo: GLM es fuerte en codigo
    _c("gemini", "gemini-3.6-flash"),                                  # nuevo
    _c("modelscope", "Qwen/Qwen3-Coder-30B-A3B-Instruct"),            # nuevo: Qwen-Coder gratis
    _c("nvidia", "openai/gpt-oss-120b"),                               # [ok]
]

# devstral-agentic: agentico / tool-use intensivo. Devstral es literalmente
# el modelo agentico de Mistral, el que le da nombre al tier.
CHAIN_DEVSTRAL_AGENTIC = [
    _c("mistral", "devstral-medium-latest"),                           # [ok]
    _c("codestral", "codestral-latest"),                               # [ok]
    _c("cloudflare", "@cf/mistralai/mistral-small-3.1-24b-instruct"),  # [ok]
    _c("mistral", "mistral-medium-latest"),                            # [ok]
    _c("sambanova", "DeepSeek-V3.2"),                                  # [ok]
    _c("huggingface", "Qwen/Qwen3-Coder-30B-A3B-Instruct"),
    _c("chutes", "zai-org/GLM-5.2-TEE"),
    _c("ovh", "Mistral-Small-3.2-24B-Instruct-2506"),                  # [ok]
    _c("zai", "glm-4.7-flash"),                                        # nuevo: agentico + tool use
    _c("gemini", "gemini-3.6-flash"),                                  # nuevo
    _c("modelscope", "Qwen/Qwen3-Coder-30B-A3B-Instruct"),            # nuevo: Qwen-Coder agentico
    _c("nvidia", "openai/gpt-oss-120b"),                               # [ok]
]

# escalation-30b: el respaldo final cuando el modelo de un especialista ya
# fallo. Reintentar con el mismo modelo no es un fallback, asi que aca va lo
# mas capaz disponible y deliberadamente distinto de los otros tiers.
CHAIN_ESCALATION = [
    _c("sambanova", "DeepSeek-V3.2"),                                  # [ok]
    _c("mistral", "mistral-large-latest"),                             # [ok]
    _c("codestral", "codestral-latest"),                               # [ok]
    _c("huggingface", "deepseek-ai/DeepSeek-V3"),                      # [ok]
    _c("cloudflare", "@cf/mistralai/mistral-small-3.1-24b-instruct"),  # [ok]
    _c("chutes", "deepseek-ai/DeepSeek-V3.2-TEE"),
    _c("novita", "deepseek/deepseek-r1"),
    _c("ovh", "Mistral-Small-3.2-24B-Instruct-2506"),                  # [ok]
    _c("gemini", "gemini-3.1-pro"),                                    # nuevo: grande, contexto enorme
    _c("zai", "glm-4.7-flash"),                                        # nuevo
    _c("modelscope", "deepseek-ai/DeepSeek-V3"),                       # nuevo: DeepSeek grande gratis
    _c("nvidia", "deepseek-ai/deepseek-v4-pro"),                       # [ok]
    _c("nvidia", "z-ai/glm-5.2"),                                      # [ok]
    _c("nvidia", "meta/llama-3.3-70b-instruct"),                       # [ok]
]

# web-builder-large: EXCEPCION deliberada, ya justificada en auto_router. El
# criterio verificado en vivo NO es el tamaño ni el contexto, es "¿emite una
# tool call como primera accion contra el system prompt REAL del especialista?".
# llama-4-scout (10M de contexto) lo falla siempre: narra en texto plano. Aca un
# modelo chico no es cobertura, por eso esta escalera es toda de modelos grandes.
#
# RE-VERIFICADO 2026-08-03 con eval/scripts/probe_toolcall.py contra el system
# prompt real: `sambanova:DeepSeek-V3.1` -que era la cabeza- esta CAIDO (ese id ya
# no responde en sambanova, mientras V3.2 del MISMO proveedor si), y web-builder
# no producia NADA porque la cadena caia a un modelo que NARRA las tool calls como
# texto (`load_skill_section(migrations: {...})`). Reordenada para encabezar con los
# candidatos que la sonda confirmo TOOLCALL_OK. V3.1 baja a suplente por si revive.
# Red de seguridad adicional en agent_loop (_PARECE_TOOLCALL_NARRADA) por si un
# suplente narra igual. Re-correr la sonda si vuelve a fallar.
CHAIN_WEB_BUILDER_LARGE = [
    _c("mistral", "mistral-large-latest"),                             # [ok] probe TOOLCALL_OK
    _c("sambanova", "DeepSeek-V3.2"),                                  # [ok] probe TOOLCALL_OK
    _c("huggingface", "deepseek-ai/DeepSeek-V3"),                      # [ok] probe TOOLCALL_OK
    _c("codestral", "codestral-latest"),                               # [ok] probe TOOLCALL_OK
    _c("nvidia", "meta/llama-3.3-70b-instruct"),                       # [ok] probe TOOLCALL_OK
    _c("sambanova", "DeepSeek-V3.1"),                                  # suplente: id caido 2026-08-03
    _c("chutes", "deepseek-ai/DeepSeek-V3.2-TEE"),
    _c("novita", "deepseek/deepseek-r1"),
    _c("ovh", "Mistral-Small-3.2-24B-Instruct-2506"),
    _c("gemini", "gemini-3.1-pro"),                                    # nuevo: probar con probe_toolcall antes de subirlo
    _c("modelscope", "deepseek-ai/DeepSeek-V3"),                       # nuevo: probar toolcall en vivo antes de subirlo
    _c("nvidia", "deepseek-ai/deepseek-v4-pro"),
    _c("nvidia", "z-ai/glm-5.2"),
]

# Backstop SIN key: se apendea al FINAL de cada escalera (despues del respaldo
# de nvidia) para que, aun sin NINGUNA key configurada, quede algo que intentar.
# Solo dispara si todo lo de arriba fallo -> latencia extra unicamente en el caso
# catastrofico, que es justo cuando cualquier ultimo recurso vale. Va al final,
# nunca de cabeza (encabezar con un proxy de fiabilidad desconocida seria peor
# que no tenerlo). Pollinations hace tool calling real; llm7 sigue sin id de
# modelo verificado, por eso no se cablea aun.
_SIN_KEY_BACKSTOP = [_c("pollinations", "openai")]

CHAIN_BY_TIER: dict[str, list[Candidate]] = {
    "router-tiny": CHAIN_ROUTER_TINY + _SIN_KEY_BACKSTOP,
    "coder-main": CHAIN_CODER_MAIN + _SIN_KEY_BACKSTOP,
    "devstral-agentic": CHAIN_DEVSTRAL_AGENTIC + _SIN_KEY_BACKSTOP,
    "escalation-30b": CHAIN_ESCALATION + _SIN_KEY_BACKSTOP,
    "web-builder-large": CHAIN_WEB_BUILDER_LARGE + _SIN_KEY_BACKSTOP,
}

DEFAULT_TIER = "coder-main"

# Respaldo universal: se APENDEA a cualquier escalera explicita (--model
# provider:modelo) para que ni siquiera una eleccion manual pueda dejar la
# tarea sin terminar.
UNIVERSAL_BACKSTOP = [
    _c("nvidia", "openai/gpt-oss-120b"),
    _c("mistral", "mistral-medium-latest"),
    _c("pollinations", "openai"),  # sin key: ultimo recurso tambien para --model manual
]


def chain_for(model_or_tier: str) -> list[Candidate]:
    """Resuelve lo que llega por `model=` a una escalera de candidatos.

    Acepta tres formas:
      - un tier del catalogo ("coder-main")      -> su escalera completa
      - "proveedor:id_de_modelo"                 -> ese candidato + respaldo
      - "" o None                                -> el tier por defecto
    """
    if not model_or_tier:
        return list(CHAIN_BY_TIER[DEFAULT_TIER])
    if model_or_tier in CHAIN_BY_TIER:
        return list(CHAIN_BY_TIER[model_or_tier])
    if ":" in model_or_tier:
        provider, _, model = model_or_tier.partition(":")
        provider = provider.strip()
        if provider not in PROVIDERS:
            raise RuntimeError(
                f"Proveedor '{provider}' desconocido. Opciones: "
                f"{', '.join(sorted(PROVIDERS))}."
            )
        explicit = _c(provider, model.strip())
        # El respaldo va detras, no delante: una eleccion manual se
        # respeta como PRIMERA opcion, pero no deja la tarea a medias.
        return [explicit] + [c for c in UNIVERSAL_BACKSTOP if c != explicit]
    raise RuntimeError(
        f"'{model_or_tier}' no es un tier ni un 'proveedor:modelo'. Tiers: "
        f"{', '.join(CHAIN_BY_TIER)}. Ejemplo explicito: "
        f"nvidia:qwen/qwen2.5-coder-32b-instruct"
    )


def configured_providers() -> list[str]:
    """Proveedores ACTIVOS que hoy tienen key (y account id, si hace falta).

    Los desactivados por `de_pago` no cuentan: tener su key puesta no los
    hace utilizables, y contarlos daria una sensacion falsa de cobertura."""
    live = []
    for name, provider in PROVIDERS.items():
        if not provider.activo:
            continue
        if keys_for(provider) and resolve_base_url(provider) is not None:
            live.append(name)
    return live


def desactivados() -> list[tuple[str, str]]:
    """(nombre, motivo) de los proveedores apagados."""
    return sorted(
        (n, p.de_pago) for n, p in PROVIDERS.items() if not p.activo
    )
