"""Asistente interactivo de API keys (`orquestador --config` / `-cfg`) y
verificador en vivo de candidatos (`--check-providers`).

POR QUE EXISTE
--------------
Con 11 proveedores y varias keys por proveedor, pedirle al usuario que
edite un .env a mano es pedirle que se equivoque: nombres de variable
exactos, saber en que web se saca cada key, y no tener forma de comprobar
si lo que pego funciona. Este modulo pide UNA a UNA solo las que faltan,
con el enlace de registro y la cuota de cada una, y las guarda sin pisar
nada de lo que ya hubiera.

DOS GARANTIAS SOBRE EL .env
---------------------------
1. Nunca se pisa un valor ya configurado (por eso no se vuelve a preguntar
   por el).
2. Se reescribe preservando comentarios, orden y variables ajenas: el .env
   tiene tambien API_KEYS, QDRANT_*, etc. y perderlas romperia el
   orquestador de produccion, no solo la terminal.
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from groq_agent import providers as prov
from groq_agent import ui
from groq_agent.groq_client import GroqClient

# Orden de preguntas: primero lo que mas cobertura da por key pegada.
# Un usuario que se cansa a la tercera pregunta deberia quedarse igual con
# un sistema que funciona, asi que lo esencial va delante.
#
# `esencial` = sin esto, o no hay red de seguridad (nvidia) o falta el tier
# que mas se usa. El resto es cobertura extra para no tocar nunca NVIDIA.
# Orden de preguntas: primero lo que mas cobertura da por key pegada. Un
# usuario que se cansa a la tercera pregunta deberia quedarse igual con un
# sistema que funciona, asi que lo esencial va delante.
#
# `esencial` = sin esto, o no hay red de seguridad (nvidia) o falta un tier
# clave. El resto es cobertura extra para no tocar nunca NVIDIA.
#
# Las descripciones dicen los modelos REALES de cada catalogo, consultados
# en vivo (GET /models, 2026-07-28) - no los que uno supondria. Cerebras,
# por ejemplo, sirve solo 3 modelos, y ninguno es un Qwen.
_ORDEN = [
    ("nvidia", True, "https://build.nvidia.com/  (Login > API keys)",
     "~40 RPM, SIN tope diario. La red de seguridad de todo el sistema."),
    ("mistral", True, "https://console.mistral.ai/api-keys/  (plan Experiment, gratis)",
     "60 modelos: Devstral Medium, Mistral Large/Medium, Ministral 8B. "
     "El proveedor que mas tiers cubre por si solo."),
    ("huggingface", True, "https://huggingface.co/settings/tokens",
     "127 modelos, incluidos Qwen2.5-Coder-32B y Qwen3-Coder-30B: el unico "
     "free tier que sigue sirviendo los Coder que este catalogo queria."),
    ("groq", True, "https://console.groq.com/keys",
     "30 RPM / 14.400 RPD en modelos 8B. Cuota alta para el router."),
    ("gemini", True, "https://aistudio.google.com/apikey",
     "Google AI Studio. Free tier sin tarjeta y tool calling. gemini-3.x "
     "flash-lite/flash/pro cubren desde router hasta web-builder."),
    ("codestral", True, "https://console.mistral.ai/codestral  (key aparte de la de Mistral)",
     "30 RPM / 2000 RPD SOLO para Codestral, con cuota independiente."),
    ("sambanova", True, "https://cloud.sambanova.ai/apis",
     "DeepSeek V3.1/V3.2: verificados sosteniendo el prompt largo de "
     "web-builder y aun asi llamando a tools."),
    ("cloudflare", False, "https://dash.cloudflare.com/profile/api-tokens",
     "10.000 neurons/dia. Pide tambien el Account ID."),
    ("chutes", False, "https://chutes.ai/  (API keys)",
     "15 modelos grandes (DeepSeek V3.2, GLM-5.2, Qwen3.6-27B). OJO: sus IDs "
     "llevan el sufijo -TEE."),
    ("novita", False, "https://novita.ai/settings/key-management",
     "143 modelos. Credito inicial + algunos gratis."),
    ("zai", False, "https://z.ai/manage-apikey/apikey-list",
     "Zhipu GLM. glm-4.x-flash gratis (~1000 RPD) con tool calling nativo."),
    ("cohere", False, "https://dashboard.cohere.com/api-keys",
     "20 RPM / 1.000 llamadas al mes. OJO: trial key = uso NO comercial."),
    ("modelscope", False, "https://modelscope.cn/my/myaccesstoken",
     "Hub de Alibaba. 2000 llamadas/dia SIN tarjeta, tool calling OpenAI-compatible. "
     "900+ modelos: Qwen(-Coder), DeepSeek, GLM."),
]

# Variables que no son API keys de proveedor pero hacen falta para que un
# proveedor funcione. Cloudflare mete el account id DENTRO de la URL, asi
# que sin el la key no sirve para nada.
_EXTRAS: dict[str, list[tuple[str, str]]] = {
    "cloudflare": [(
        "CLOUDFLARE_ACCOUNT_ID",
        "Account ID de Cloudflare (esta en la URL del dashboard, /accounts/<esto>)",
    )],
}


# Palabras con las que el usuario corta el asistente. Se comparan SIEMPRE
# sobre el texto ya saneado: PowerShell 5.1 añade un BOM al mandar texto por
# una tuberia a un proceso nativo, asi que `input()` puede devolver
# "\ufefffin" y no "fin". Sin sanear, ese "fin" se guardaba como si fuera
# una API key - paso de verdad durante las pruebas y dejo dos keys basura en
# el .env que luego reventaban httpx con UnicodeEncodeError.
_CORTAR = {"fin", "salir", "exit", "q", "quit"}

# Palabras que SALTAN este proveedor y siguen con el siguiente. Antes "no"
# estaba en _CORTAR, y eso era una trampa: en un prompt que pide una key,
# "no" significa "esta no la tengo", no "cierra el asistente". Paso de
# verdad - un "no" a mitad cerro el asistente y, como entonces solo se
# guardaba al final, se perdio todo lo tecleado antes.
_SALTAR = {"no", "n", "nada", "skip", "-", "ninguna"}

# Caracteres invisibles que se cuelan al pegar desde una web o al pasar por
# una tuberia de PowerShell: BOM, zero-width space/joiner, non-breaking
# space. Ninguna API key real los lleva.
_INVISIBLES = "\ufeff\u200b\u200c\u200d\u2060\u00a0"

# Longitud minima creible. Las keys mas cortas de los proveedores de la
# lista rondan los 30 caracteres; 8 es un piso deliberadamente laxo que solo
# caza pulsaciones accidentales y palabras sueltas.
_MIN_LARGO_KEY = 8


def sanear_entrada(raw: str) -> str:
    """Quita invisibles y espacios de una linea leida del usuario."""
    limpio = raw.strip()
    for ch in _INVISIBLES:
        limpio = limpio.replace(ch, "")
    return limpio.strip()


def validar_key(valor: str) -> str | None:
    """None si la key es plausible; si no, el motivo para mostrarselo.

    No valida el formato de cada proveedor (cambian y no vale la pena
    perseguirlos): solo descarta lo que con certeza NO es una key y ademas
    romperia la cabecera HTTP mas adelante."""
    if len(valor) < _MIN_LARGO_KEY:
        return f"son solo {len(valor)} caracteres - eso no es una API key"
    try:
        valor.encode("ascii")
    except UnicodeEncodeError:
        return (
            "tiene caracteres no-ASCII (suele pasar al copiar desde una web). "
            "Vuelve a copiarla en texto plano"
        )
    if any(c.isspace() for c in valor):
        return "tiene espacios en medio"
    return None


def _env_path() -> Path:
    return Path(__file__).resolve().parent.parent / ".env"


def _leer_env(path: Path) -> tuple[list[str], dict[str, str]]:
    """Devuelve (lineas_originales, valores). Las lineas se conservan para
    poder reescribir el archivo sin perder comentarios ni variables
    ajenas."""
    if not path.exists():
        return [], {}
    # utf-8-sig, no utf-8: un .env guardado por PowerShell o por un editor
    # de Windows puede llevar BOM, y leyendolo como utf-8 el BOM queda
    # pegado al nombre de la PRIMERA variable ("﻿NVIDIA_API_KEY"). Esa
    # variable pasaria por inexistente, el asistente la volveria a pedir y
    # el .env acabaria con la key duplicada.
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    values: dict[str, str] = {}
    for line in lines:
        stripped = sanear_entrada(line)
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        values[sanear_entrada(name)] = sanear_entrada(value)
    return lines, values


_CABECERA_KEYS = "# --- API keys de proveedores (orquestador --config) ---"


def _escribir_env(path: Path, lines: list[str], nuevos: dict[str, str]) -> None:
    """Actualiza en su sitio las variables que ya existian y añade las
    nuevas al final, bajo una cabecera. No reordena ni borra nada.

    Se llama una vez POR KEY (guardado incremental), asi que tiene que ser
    idempotente: sin la guarda de abajo, cada llamada repetia la cabecera y
    el .env acababa con una linea de comentario por cada key pegada."""
    pendientes = dict(nuevos)
    salida: list[str] = []
    ya_hay_cabecera = any(line.strip() == _CABECERA_KEYS for line in lines)
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            name = stripped.partition("=")[0].strip()
            if name in pendientes:
                salida.append(f"{name}={pendientes.pop(name)}")
                continue
        salida.append(line)
    if pendientes:
        if salida and salida[-1].strip():
            salida.append("")
        if not ya_hay_cabecera:
            salida.append(_CABECERA_KEYS)
        salida.extend(f"{name}={value}" for name, value in pendientes.items())
    path.write_text("\n".join(salida) + "\n", encoding="utf-8")


def _ya_configurada(name: str, env_values: dict[str, str]) -> bool:
    """Configurada = tiene valor en el .env O en el entorno del proceso.
    Se mira el entorno tambien porque el usuario puede exportarlas fuera
    del .env, y volver a preguntar por algo que ya funciona es justo lo que
    este asistente evita."""
    return bool(env_values.get(name, "").strip() or os.environ.get(name, "").strip())


def _siguiente_slot_libre(prefix: str, env_values: dict[str, str]) -> str:
    """Nombre de la primera variable libre del banquillo de ese proveedor:
    NVIDIA_API_KEY, luego NVIDIA_API_KEY_2, _3... Asi 'añadir otra key'
    nunca pisa la que ya hay."""
    base = f"{prefix}_API_KEY"
    if not _ya_configurada(base, env_values):
        return base
    for i in range(2, 10):
        name = f"{base}_{i}"
        if not _ya_configurada(name, env_values):
            return name
    return f"{base}_9"



def _enmascarar(valor: str) -> str:
    """Muestra lo justo para reconocer una key sin exponerla: los primeros
    caracteres (que identifican al proveedor: 'nvapi-', 'csk-', 'sk-') y los
    ultimos, que es por donde se distinguen dos keys del mismo sitio."""
    if len(valor) <= 10:
        return "*" * len(valor)
    return f"{valor[:6]}…{valor[-4:]}"


def run_list_keys() -> int:
    """Muestra el estado de las API keys. Nunca imprime una key entera."""
    path = _env_path()
    _, env_values = _leer_env(path)

    ui.print_header("API keys configuradas")
    ui.console.print(f"[{ui.C_DIM}]Archivo: {path}[/{ui.C_DIM}]\n")

    sospechosas = 0
    sin_key = []
    for name, provider in prov.PROVIDERS.items():
        if not provider.activo:
            continue
        claves = prov.keys_for(provider)
        # keys_for devuelve [""] para los que no necesitan key (llm7, ovh):
        # eso no es "tener key", es "no hacer falta".
        if not provider.needs_key:
            ui.console.print(
                f"  [{ui.C_OK}]+[/{ui.C_OK}] {name:14} "
                f"[{ui.C_DIM}]no necesita key[/{ui.C_DIM}]"
            )
            continue
        if not claves:
            sin_key.append(name)
            continue

        etiquetas = []
        for i, key in enumerate(claves):
            aviso = ""
            motivo = validar_key(key)
            if motivo:
                aviso = f" [{ui.C_FAIL}]<- {motivo}[/{ui.C_FAIL}]"
                sospechosas += 1
            etiquetas.append(f"{_enmascarar(key)}{aviso}")
        banquillo = f"  [{ui.C_DIM}](+{len(claves) - 1} en banquillo)[/{ui.C_DIM}]" if len(claves) > 1 else ""
        ui.console.print(f"  [{ui.C_OK}]+[/{ui.C_OK}] {name:14} {etiquetas[0]}{banquillo}")
        for extra in etiquetas[1:]:
            ui.console.print(f"    {'':14} {extra}")

        for extra_var, _ in _EXTRAS.get(name, []):
            valor = env_values.get(extra_var, "")
            marca = _enmascarar(valor) if valor else f"[{ui.C_FAIL}]FALTA[/{ui.C_FAIL}]"
            ui.console.print(f"    {'':14} {extra_var}: {marca}")

    if sin_key:
        ui.console.print(
            f"\n  [{ui.C_DIM}]sin key: {', '.join(sorted(sin_key))}[/{ui.C_DIM}]"
        )
    for nombre, motivo in prov.desactivados():
        ui.console.print(
            f"  [{ui.C_DIM}]-[/{ui.C_DIM}] [{ui.C_DIM}]{nombre:14} DESACTIVADO: "
            f"{motivo.split('(')[0].strip()}[/{ui.C_DIM}]",
            highlight=False,
        )

    ui.console.print("")
    if sospechosas:
        ui.console.print(
            f"[{ui.C_FAIL}]{sospechosas} key(s) sospechosa(s).[/{ui.C_FAIL}] "
            f"Corregilas con 'orquestador --config' (te pedira las que falten)."
        )
        return 1
    ui.console.print(
        f"[{ui.C_DIM}]Ninguna key sospechosa. Para probar que responden de verdad: "
        f"'orquestador --check-providers'[/{ui.C_DIM}]"
    )
    return 0

def run_config(solo_faltantes: bool = True) -> int:
    """Pide las keys que faltan, una a una. Enter salta; 'fin' corta."""
    path = _env_path()
    lines, env_values = _leer_env(path)
    nuevos: dict[str, str] = {}

    ui.print_header("configuracion de proveedores")
    configurados = [
        name for name, _, _, _ in _ORDEN
        if prov.PROVIDERS.get(name) and _ya_configurada(
            f"{prov.PROVIDERS[name].key_prefix}_API_KEY", env_values
        )
    ]
    if configurados:
        ui.console.print(
            f"[{ui.C_DIM}]Ya configurados (no se vuelven a pedir): "
            f"{', '.join(configurados)}[/{ui.C_DIM}]"
        )
    ui.console.print(
        f"[{ui.C_DIM}]Enter (o 'no') salta esa · 'fin' termina · cada key se "
        f"guarda al momento, no al final · todas son gratis y sin tarjeta"
        f"[/{ui.C_DIM}]\n"
    )

    for name, esencial, url, nota in _ORDEN:
        provider = prov.PROVIDERS.get(name)
        if provider is None or not provider.activo:
            continue
        var = f"{provider.key_prefix}_API_KEY"
        if solo_faltantes and _ya_configurada(var, env_values):
            continue

        etiqueta = "ESENCIAL" if esencial else "opcional"
        ui.console.print(f"[{ui.C_ACCENT}]{name.upper()}[/{ui.C_ACCENT}]  ({etiqueta})")
        ui.console.print(f"  {nota}")
        ui.console.print(f"  [{ui.C_DIM}]Sacar la key en:[/{ui.C_DIM}] {url}")
        if not provider.verified_base_url:
            ui.console.print(
                f"  [{ui.C_DIM}]Nota: su URL base no la pude confirmar contra doc "
                f"oficial - comprobala luego con --check-providers.[/{ui.C_DIM}]"
            )
        try:
            valor = sanear_entrada(input("  key> "))
        except (EOFError, KeyboardInterrupt):
            ui.console.print("\n  (cortado)")
            break
        if valor.lower() in _CORTAR:
            break
        if not valor or valor.lower() in _SALTAR:
            ui.console.print(f"  [{ui.C_DIM}](saltado)[/{ui.C_DIM}]\n")
            continue
        motivo = validar_key(valor)
        if motivo:
            ui.console.print(
                f"  [{ui.C_FAIL}]No la guardo: {motivo}.[/{ui.C_FAIL}] "
                f"[{ui.C_DIM}]Se salta {name}; vuelve a ejecutar --config cuando la "
                f"tengas.[/{ui.C_DIM}]\n"
            )
            continue

        destino = _siguiente_slot_libre(provider.key_prefix, {**env_values, **nuevos})
        nuevos[destino] = valor
        # Guardado INCREMENTAL, no al final: pegar 8 keys es varios minutos
        # de trabajo, y cualquier salida a mitad (un Ctrl-C, una palabra de
        # corte sin querer) no puede tirarlo todo. Se reescribe el .env
        # entero en cada paso, que es barato y deja el archivo siempre en un
        # estado valido.
        _escribir_env(path, lines, nuevos)
        lines, env_values = _leer_env(path)
        ui.console.print(f"  [{ui.C_OK}]guardada en {destino}[/{ui.C_OK}]")

        for extra_var, extra_desc in _EXTRAS.get(name, []):
            if _ya_configurada(extra_var, {**env_values, **nuevos}):
                continue
            ui.console.print(f"  {extra_desc}")
            try:
                extra_val = sanear_entrada(input(f"  {extra_var}> "))
            except (EOFError, KeyboardInterrupt):
                break
            if extra_val:
                nuevos[extra_var] = extra_val
                _escribir_env(path, lines, nuevos)
                lines, env_values = _leer_env(path)
                ui.console.print(f"  [{ui.C_OK}]guardado[/{ui.C_OK}]")

        # Un mismo proveedor admite varias keys (dos cuentas gratis de
        # NVIDIA son 80 RPM, no 40): ofrecerlo aca es lo que hace real el
        # "banquillo", pero solo se pregunta si acaba de pegar una.
        while True:
            try:
                otra = sanear_entrada(input(
                    f"  ¿otra key de {name} para el banquillo? (Enter=no) > "
                ))
            except (EOFError, KeyboardInterrupt):
                otra = ""
            if not otra or otra.lower() in _CORTAR or otra.lower() in _SALTAR:
                break
            motivo = validar_key(otra)
            if motivo:
                ui.console.print(f"  [{ui.C_FAIL}]No la guardo: {motivo}.[/{ui.C_FAIL}]")
                continue
            destino = _siguiente_slot_libre(provider.key_prefix, {**env_values, **nuevos})
            nuevos[destino] = otra
            _escribir_env(path, lines, nuevos)
            lines, env_values = _leer_env(path)
            ui.console.print(f"  [{ui.C_OK}]guardada en {destino}[/{ui.C_OK}]")
        ui.console.print("")

    if not nuevos:
        ui.console.print(f"[{ui.C_DIM}]No se añadio nada. El .env queda igual.[/{ui.C_DIM}]")
        return 0

    ui.console.print(
        f"[{ui.C_OK}]{len(nuevos)} valor(es) guardados en {path.name}[/{ui.C_OK}]"
    )
    ui.console.print(
        f"[{ui.C_DIM}]Siguiente paso recomendado: 'orquestador --check-providers' "
        f"para ver que candidatos responden de verdad.[/{ui.C_DIM}]"
    )
    return 0



def _probar(client, candidate) -> tuple[bool, str]:
    """probe() aislado: este comando existe para encontrar fallos, asi que
    no puede morirse por uno."""
    try:
        return client.probe(candidate)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def _probar_proveedor(client, candidatos) -> dict[str, tuple[bool, str]]:
    """Prueba EN FILA todos los candidatos de un mismo proveedor.

    Entre prueba y prueba se espera su `min_interval_s` (2s para uno de 30
    RPM, 5s para OVH que va a 2 RPM). Sin esa pausa, probar los 3 modelos
    que un proveedor tiene en las escaleras se come su cuota por minuto y
    los ultimos salen como muertos sin estarlo."""
    salida: dict[str, tuple[bool, str]] = {}
    for i, (clave, candidate) in enumerate(candidatos):
        if i:
            provider = prov.PROVIDERS.get(candidate.provider)
            espera = min(provider.min_interval_s, prov.MAX_PACING_WAIT_S) if provider else 2.0
            time.sleep(espera)
        salida[clave] = _probar(client, candidate)
    return salida


def run_check(tier: str | None = None) -> int:
    """Prueba de verdad cada candidato con una tool call minima.

    Listar /models NO alcanza: un modelo puede aparecer ahi y fallar al
    primer uso real con 'no endpoints support tool use' (pasaba con
    OpenRouter). Y como el ciclo agentico depende de tool-calling, un
    candidato que responde texto pero no llama a la tool sirve para el
    router y NO para un especialista - por eso se distinguen."""
    ui.print_header("verificacion de proveedores")

    apagados = prov.desactivados()
    if apagados:
        ui.console.print(
            f"[{ui.C_DIM}]Desactivados (no son gratis): "
            f"{', '.join(n for n, _ in apagados)}[/{ui.C_DIM}]"
        )
    faltan = [
        name for name, provider in prov.PROVIDERS.items()
        if provider.activo and not prov.keys_for(provider)
    ]
    if faltan:
        ui.console.print(
            f"[{ui.C_DIM}]Sin key (se saltan): {', '.join(sorted(faltan))} · "
            f"añadilas con 'orquestador --config'[/{ui.C_DIM}]\n"
        )

    try:
        client = GroqClient()
    except RuntimeError as exc:
        ui.console.print(f"[{ui.C_FAIL}]{exc}[/{ui.C_FAIL}]")
        return 1

    tiers = [tier] if tier else list(prov.CHAIN_BY_TIER)
    for nombre_tier in tiers:
        if nombre_tier not in prov.CHAIN_BY_TIER:
            ui.print_error_corto(
                f"Tier '{nombre_tier}' desconocido",
                f"Tiers: {', '.join(prov.CHAIN_BY_TIER)}",
            )
            client.close()
            return 1

    # Un mismo candidato aparece en varios tiers (codestral-latest esta en
    # 4). Probarlo una vez por tier era repetir el 45% del trabajo, y cada
    # prueba es una llamada de red.
    unicos: dict[str, prov.Candidate] = {}
    for nombre_tier in tiers:
        for candidate in prov.CHAIN_BY_TIER[nombre_tier]:
            if prov.PROVIDERS[candidate.provider].activo:
                unicos.setdefault(str(candidate), candidate)

    ui.console.print(
        f"[{ui.C_DIM}]Probando {len(unicos)} candidatos unicos en paralelo…[/{ui.C_DIM}]"
    )

    # Agrupar por proveedor: dentro de uno las pruebas van EN FILA y
    # respetando su ritmo; entre proveedores distintos, en paralelo.
    por_proveedor: dict[str, list[tuple[str, prov.Candidate]]] = {}
    for clave, cand in unicos.items():
        por_proveedor.setdefault(cand.provider, []).append((clave, cand))

    resultados: dict[str, tuple[bool, str]] = {}
    try:
        # Un hilo por PROVEEDOR, no por candidato. Lanzar varias pruebas a
        # la vez contra el mismo proveedor provocaba los 429 y los timeouts
        # que luego aparecian en el informe como si el proveedor estuviera
        # caido - el diagnostico se estaba autolesionando.
        with ThreadPoolExecutor(max_workers=min(8, len(por_proveedor) or 1)) as pool:
            futuros = {
                pool.submit(_probar_proveedor, client, lista): nombre
                for nombre, lista in por_proveedor.items()
            }
            for futuro in as_completed(futuros):
                resultados.update(futuro.result())

        problemas = 0
        for nombre_tier in tiers:
            ui.console.print(f"[{ui.C_ACCENT}]{nombre_tier}[/{ui.C_ACCENT}]")
            vivos_antes_de_nvidia = 0
            visto_nvidia = False
            for candidate in prov.CHAIN_BY_TIER[nombre_tier]:
                if not prov.PROVIDERS[candidate.provider].activo:
                    continue
                if candidate.provider == "nvidia":
                    visto_nvidia = True
                ok, detalle = resultados.get(str(candidate), (False, "sin probar"))
                if ok and "NO uso la tool" in detalle:
                    color, marca = ui.C_DIM, "~"
                elif ok:
                    color, marca = ui.C_OK, "+"
                else:
                    color, marca = ui.C_DIM, "-"
                resumen = ui._mensaje_util(detalle)
                if len(resumen) > 72:
                    resumen = resumen[:69] + "…"
                ui.console.print(
                    f"  [{color}]{marca}[/{color}] {candidate}  "
                    f"[{ui.C_DIM}]{ui.escape(resumen)}[/{ui.C_DIM}]",
                    highlight=False,
                )
                if ok and not visto_nvidia:
                    vivos_antes_de_nvidia += 1
            # Lo que de verdad importa del informe: cuanta cobertura real
            # hay ANTES de NVIDIA. Si es 0, cada tarea de este tier va a
            # gastar la red de seguridad.
            if vivos_antes_de_nvidia == 0:
                problemas += 1
                ui.console.print(
                    f"  [{ui.C_FAIL}]Sin cobertura antes de NVIDIA: este tier gastara "
                    f"la red de seguridad en cada tarea.[/{ui.C_FAIL}]"
                )
            else:
                ui.console.print(
                    f"  [{ui.C_DIM}]{vivos_antes_de_nvidia} vivo(s) antes de NVIDIA"
                    f"[/{ui.C_DIM}]"
                )
            ui.console.print("")
    finally:
        client.close()

    if problemas:
        ui.console.print(
            f"[{ui.C_FAIL}]{problemas} tier(s) sin cobertura previa a NVIDIA.[/{ui.C_FAIL}] "
            f"Añadí keys con 'orquestador --config'."
        )
        return 1
    ui.console.print(f"[{ui.C_OK}]Todos los tiers tienen cobertura antes de NVIDIA.[/{ui.C_OK}]")
    return 0
