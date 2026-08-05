"""Cliente OpenAI-compatible MULTI-PROVEEDOR con tool-calling y failover.

No usamos ningun SDK de proveedor especifico para no agregar dependencias:
httpx ya esta en el proyecto y la API es un simple POST JSON, identica
entre NVIDIA/Cerebras/Groq/Mistral/vLLM/etc.

El nombre del modulo/clase quedo de cuando esto hablaba solo con Groq. Se
mantiene para no romper todos los imports por un cambio cosmetico - lo que
importa es el comportamiento, no el nombre.

QUE CAMBIO Y POR QUE
--------------------
Antes: un proveedor (OpenRouter) y un modelo por tier. Un 429 cortaba la
tarea A MITAD del ciclo agentico, despues de haber escrito archivos
reales. Ahora `model=` no es un modelo concreto sino un TIER (o un
"proveedor:modelo" explicito), y el cliente recorre una escalera de
candidatos en proveedores distintos, con varias keys por proveedor. La
tarea solo falla si se agota TODA la escalera.

Como se reparten las culpas:
  - 429 / 402 (cuota de ESA key)      -> siguiente candidato, al instante
  - 5xx / 404 / timeout               -> siguiente candidato, al instante
  - 401 / 403 (key invalida)          -> siguiente candidato, y esa key se
                                         aparta: no se arregla sola, solo
                                         editando el .env
  - 400 tool_use_failed               -> NO es del proveedor: lo maneja
                                         agent_loop pidiendo al modelo que
                                         reintente. No quema la escalera.
  - 400 otro                          -> se sigue probando, pero si TODOS
                                         dan 400 el error final lo dice:
                                         el problema es la peticion, no
                                         los proveedores.

TRES REGLAS DE COMPORTAMIENTO (pedidas explicitamente)
------------------------------------------------------
1. SIN DESTIERROS. Cada llamada vuelve a empezar por el primero de la
   escalera. Un 429 puntual no condena al proveedor preferido al resto de
   la sesion: en cuanto se recupera, la siguiente llamada ya lo usa.
2. NO SE ESPERA A QUIEN FALLA. Si un candidato falla se pasa al siguiente
   sin pausa. Las esperas son PREVENTIVAS (para no provocar el 429) y solo
   sobre un candidato sano, con un tope duro de 5 segundos.
3. SILENCIO HASTA AGOTAR. Un fallo de failover no se le muestra al
   usuario; el error solo sale si se agota la escalera completa. Por eso
   este modulo no imprime nada: acumula los intentos y, si no queda
   ninguno, los mete todos en el mensaje final.
"""
from __future__ import annotations

import os
import time

import httpx

from groq_agent import providers as prov
from groq_agent.providers import Candidate

# Reintentos ante errores de TRANSPORTE con el MISMO candidato (conexion
# cortada, TLS corrupto, timeout): una conexion pooled corrupta suele
# arreglarse con una nueva. Si igual falla, se pasa al siguiente
# candidato.
_TRANSIENT_RETRIES = 1

# Codigos que significan "esta KEY no puede servir esto ahora", pero el
# proveedor y el modelo estan bien -> probar la siguiente key.
_KEY_LEVEL_STATUS = {401, 402, 403, 429}
# Codigos que significan "este PROVEEDOR/MODELO no sirve" -> ninguna key
# del proveedor lo va a arreglar, saltar entero.
_PROVIDER_LEVEL_STATUS = {404, 405, 408, 500, 502, 503, 504, 529}

# Campos ESTANDAR de un mensaje de chat (compatibles con todos los proveedores
# OpenAI-like). Cualquier otro se quita antes de enviar.
_CLAVES_MENSAJE = {"role", "content", "name", "tool_calls", "tool_call_id"}


def _sanear_mensajes(messages: list[dict]) -> list[dict]:
    """Deja en cada mensaje SOLO los campos estandar. Los modelos de
    razonamiento de algunos proveedores (DeepSeek-R1, etc.) devuelven un
    `reasoning_content` en el mensaje de asistente; ese campo se guarda en el
    historial y, al reenviarlo, otros proveedores (mistral, codestral) lo
    rechazan con `HTTP 422 extra_forbidden` y tumban la escalera ENTERA - la
    cabeza de la cadena (mistral-large, 128K) incluida, aunque tenga contexto
    de sobra. Se limpia SIEMPRE, en el unico punto por el que pasa todo envio,
    asi da igual que un provider raro haya metido lo que sea en el historial."""
    saneados = []
    for m in messages:
        if _CLAVES_MENSAJE.issuperset(m):
            saneados.append(m)                    # ya esta limpio, sin copiar
        else:
            saneados.append({k: v for k, v in m.items() if k in _CLAVES_MENSAJE})
    return saneados


class ToolCallFailedError(RuntimeError):
    """El modelo genero una tool call mal formada (el proveedor responde
    tool_use_failed) - pasa con algunos modelos cuando el argumento de una
    funcion es un string grande (ej. un HTML completo). Se separa de
    RuntimeError generico para que agent_loop pueda reintentar con una
    correccion en vez de crashear toda la sesion.

    Deliberadamente NO dispara failover: es un limite del modelo ante ESTA
    peticion, no una caida del proveedor, y agent_loop ya sabe pedirle al
    modelo que reintente en pasos mas chicos."""


class InvalidKeyError(RuntimeError):
    """La API key tiene caracteres que no pueden ir en una cabecera HTTP
    (BOM, invisibles, no-ASCII). Se trata como key muerta: reintentarla no
    puede funcionar, hace falta corregirla con --config.

    Existe porque httpx levanta UnicodeEncodeError al construir la cabecera
    y, sin capturarlo, tumbaba la sesion entera en vez de pasar al
    proveedor siguiente."""


class NoProviderAvailableError(RuntimeError):
    """Se agoto la escalera completa: todos los candidatos de este tier
    fallaron. Hereda de RuntimeError para que agent_loop lo trate como
    cualquier error de API y guarde el progreso ya hecho."""


class _Attempt:
    """Un intento fallido, para poder explicar al final que paso con cada
    candidato en vez de mostrar solo el ultimo error."""

    __slots__ = ("candidate", "key_index", "reason")

    def __init__(self, candidate: Candidate, key_index: int, reason: str):
        self.candidate = candidate
        self.key_index = key_index
        self.reason = reason

    def __str__(self) -> str:
        key_note = f" (key #{self.key_index + 1})" if self.key_index >= 0 else ""
        return f"  - {self.candidate}{key_note}: {self.reason}"


class GroqClient:
    """Un cliente para TODOS los proveedores. Mantiene un httpx.Client por
    (proveedor, key) para reusar conexiones sin mezclar credenciales."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        provider: str | None = None,
        timeout_s: float = 120.0,
    ):
        self._timeout_s = timeout_s
        self._pool: dict[tuple[str, int], httpx.Client] = {}
        # Ultimo candidato que respondio bien: la UI lo muestra para que se
        # vea QUIEN sirvio el turno (con failover, ya no es obvio).
        self.last_served_by: Candidate | None = None
        # Contabilidad de tokens de esta sesion. Se acumula aca porque es
        # el unico punto por el que pasan TODAS las llamadas, incluidas las
        # del router y las de compactacion - que tambien gastan y antes no
        # se contaban en ningun sitio.
        from groq_agent.usage import Gasto

        self.gasto = Gasto()
        # Override manual: si se pasa provider/base_url/api_key a mano
        # (util para apuntar a un vLLM propio cuando haya GPU), ese
        # candidato manda y no se toca el registro.
        self._forced_base_url = base_url
        self._forced_api_key = api_key
        self._forced_provider = provider or os.environ.get("LLM_PROVIDER") or None
        if self._forced_provider and self._forced_provider not in prov.PROVIDERS:
            raise RuntimeError(
                f"LLM_PROVIDER '{self._forced_provider}' desconocido. Opciones: "
                f"{', '.join(sorted(prov.PROVIDERS))}."
            )
        if not self._forced_provider and not prov.configured_providers():
            raise RuntimeError(
                "No hay ninguna API key configurada. Corré 'orquestador --config' "
                "(o -cfg) y te las pide una a una, solo las que falten."
            )

    # --- construccion de clientes HTTP --------------------------------

    def _client_for(self, provider_name: str, key_index: int, key: str) -> httpx.Client:
        cached = self._pool.get((provider_name, key_index))
        if cached is not None:
            return cached
        provider = prov.PROVIDERS[provider_name]
        base_url = self._forced_base_url or prov.resolve_base_url(provider)
        if base_url is None:
            missing = ", ".join(provider.base_url_vars)
            raise RuntimeError(f"Falta {missing} para el proveedor '{provider_name}'.")
        headers = {**dict(provider.extra_headers)}
        if key:
            # Las cabeceras HTTP son ASCII. Una key con un BOM o un invisible
            # (tipico al pegar desde una web, o al pasar texto por una
            # tuberia de PowerShell 5.1, que le añade BOM) hace que httpx
            # levante UnicodeEncodeError. Se detecta aca para poder tratarlo
            # como key muerta en vez de dejar escapar una excepcion que no
            # se parece en nada a la causa real.
            try:
                key.encode("ascii")
            except UnicodeEncodeError as exc:
                raise InvalidKeyError(
                    f"la key #{key_index + 1} de '{provider_name}' tiene caracteres "
                    f"no-ASCII y no puede ir en una cabecera HTTP. Corregila con "
                    f"'orquestador --config'."
                ) from exc
            headers["Authorization"] = f"Bearer {key}"
        client = httpx.Client(base_url=base_url, headers=headers, timeout=self._timeout_s)
        self._pool[(provider_name, key_index)] = client
        return client

    # --- resolucion de la escalera ------------------------------------

    def _slots(self, model_or_tier: str) -> list[tuple[Candidate, int, str]]:
        """Aplana la escalera a (candidato, indice_de_key, key) en ORDEN DE
        PREFERENCIA, siempre el mismo.

        Se reconstruye en cada llamada a proposito: asi la preferencia
        vuelve a empezar por arriba cada vez (regla 1 del docstring del
        modulo) en vez de arrastrar el resultado de un fallo anterior. Lo
        unico que se filtra son las keys invalidas, que no pueden funcionar
        por mucho que se reintenten."""
        if self._forced_provider:
            chain = [Candidate(self._forced_provider, self._model_hint(model_or_tier))]
        else:
            chain = prov.chain_for(model_or_tier)

        slots: list[tuple[Candidate, int, str]] = []
        for candidate in chain:
            provider = prov.PROVIDERS.get(candidate.provider)
            if provider is None:
                continue
            # Desactivado por no ser gratis: no gastar el round-trip.
            if not provider.activo:
                continue
            if prov.resolve_base_url(provider) is None and not self._forced_base_url:
                continue
            keys = [self._forced_api_key] if self._forced_api_key else prov.keys_for(provider)
            for key_index, key in enumerate(keys):
                if key is None:
                    continue
                if prov.is_dead_key(candidate.provider, key_index):
                    continue
                slots.append((candidate, key_index, key))
        return slots

    def _model_hint(self, model_or_tier: str) -> str:
        """Con LLM_PROVIDER forzado, `model=` puede seguir siendo un tier:
        se usa el modelo del primer candidato de ese tier para ese
        proveedor, o el del primer candidato a secas."""
        if ":" in model_or_tier:
            return model_or_tier.partition(":")[2]
        chain = prov.CHAIN_BY_TIER.get(model_or_tier)
        if not chain:
            return model_or_tier
        for candidate in chain:
            if candidate.provider == self._forced_provider:
                return candidate.model
        return chain[0].model

    # --- la llamada ---------------------------------------------------

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str = prov.DEFAULT_TIER,
        temperature: float = 0.2,
    ) -> dict:
        """`model` es un TIER del catalogo ('coder-main') o un
        'proveedor:modelo' explicito. Recorre la escalera hasta que uno
        responda."""
        slots = self._slots(model)
        if not slots:
            raise NoProviderAvailableError(
                f"Ningun proveedor configurado puede servir '{model}'. Corré "
                "'orquestador --config' para añadir keys, o "
                "'orquestador --check-providers' para ver que esta vivo."
            )

        attempts: list[_Attempt] = []
        only_bad_requests = True

        mensajes_limpios = _sanear_mensajes(messages)
        for position, (candidate, key_index, key) in enumerate(slots):
            payload: dict = {
                "model": candidate.model,
                "messages": mensajes_limpios,
                "temperature": temperature,
            }
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = "auto"

            # Espera PREVENTIVA, solo sobre el candidato preferido y solo si
            # su propio ritmo la pide (tope 5s). En los suplentes no se
            # espera: si ya estamos bajando por la escalera es porque algo
            # fallo, y ahi la prioridad es responder, no ser cuidado con el
            # rate limit de un proveedor que ni era el elegido.
            if position == 0:
                pause = prov.wait_needed(candidate.provider, key_index)
                if pause > 0:
                    time.sleep(pause)
            prov.note_used(candidate.provider, key_index)

            try:
                response = self._post(candidate, key_index, key, payload)
            except InvalidKeyError as exc:
                # No se arregla reintentando: se aparta como key muerta y se
                # pasa al siguiente candidato, igual que un 401.
                prov.mark_dead_key(candidate.provider, key_index)
                attempts.append(_Attempt(candidate, key_index, str(exc)))
                only_bad_requests = False
                continue
            except httpx.TransportError as exc:
                attempts.append(_Attempt(candidate, key_index, f"red/TLS: {exc}"))
                only_bad_requests = False
                continue

            status = response.status_code
            if status < 400:
                self.last_served_by = candidate
                datos = response.json()
                self.gasto.sumar(datos, str(candidate))
                return datos

            body = response.text[:800]

            # Limite del MODELO ante esta peticion, no del proveedor: se
            # propaga tal cual para que agent_loop pida un reintento mas
            # chico. Cambiar de proveedor no arreglaria nada.
            if status == 400 and "tool_use_failed" in body:
                self.last_served_by = candidate
                raise ToolCallFailedError(body)

            if status in _KEY_LEVEL_STATUS:
                # 401/403 = key invalida o revocada. Es lo unico que se
                # aparta, porque ninguna cantidad de reintentos la va a
                # arreglar: hace falta editar el .env.
                if status in (401, 403):
                    prov.mark_dead_key(candidate.provider, key_index)
                    reason = f"HTTP {status} (key invalida - revísala con --config)"
                else:
                    reason = f"HTTP {status} (cuota agotada por ahora)"
                attempts.append(_Attempt(candidate, key_index, reason))
                only_bad_requests = False
                continue

            if status in _PROVIDER_LEVEL_STATUS:
                attempts.append(_Attempt(candidate, key_index, f"HTTP {status}"))
                only_bad_requests = False
                continue

            # 400 u otro: puede ser una rareza de schema de ESTE proveedor
            # (pasa en los free tiers), asi que se sigue probando - pero si
            # todos responden lo mismo, el mensaje final lo dice.
            attempts.append(_Attempt(candidate, key_index, f"HTTP {status}: {body[:200]}"))

        detail = "\n".join(str(a) for a in attempts)
        if only_bad_requests and attempts:
            raise RuntimeError(
                "Todos los proveedores rechazaron la peticion con un error de formato "
                "(400). Eso apunta a la peticion en si, no a los proveedores:\n" + detail
            )
        raise NoProviderAvailableError(
            f"Se agoto la escalera de '{model}' sin respuesta. Intentos:\n{detail}\n"
            "Añadí mas keys con 'orquestador --config' o revisá "
            "'orquestador --check-providers'."
        )

    def _post(
        self, candidate: Candidate, key_index: int, key: str, payload: dict
    ) -> httpx.Response:
        client = self._client_for(candidate.provider, key_index, key)
        last_exc: httpx.TransportError | None = None
        for attempt in range(_TRANSIENT_RETRIES + 1):
            try:
                return client.post("/chat/completions", json=payload)
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt >= _TRANSIENT_RETRIES:
                    break
                # Una conexion pooled corrupta no se arregla reusandola.
                self._pool.pop((candidate.provider, key_index), None)
                client = self._client_for(candidate.provider, key_index, key)
        assert last_exc is not None
        raise last_exc

    # --- utilidades ---------------------------------------------------

    def list_models(self, provider_name: str | None = None) -> list[str]:
        """Modelos que expone un proveedor. Sin argumento, recorre todos
        los configurados y prefija cada id con su proveedor (los ids se
        repiten entre proveedores, asi que sin prefijo no se distinguen)."""
        targets = [provider_name] if provider_name else prov.configured_providers()
        out: list[str] = []
        for name in targets:
            provider = prov.PROVIDERS.get(name)
            if provider is None:
                continue
            keys = prov.keys_for(provider)
            if not keys:
                continue
            try:
                client = self._client_for(name, 0, keys[0])
                response = client.get("/models")
                response.raise_for_status()
                data = response.json().get("data", [])
                out.extend(f"{name}:{m['id']}" for m in data if "id" in m)
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                out.append(f"{name}: <no se pudo listar: {exc}>")
        return out

    # Un probe es un diagnostico, no una generacion: si un endpoint no
    # contesta en este tiempo, la respuesta util ya es "no sirve". El
    # timeout normal del cliente (120s) esta pensado para que un modelo
    # grande termine de escribir, y aplicado a 27 candidatos en serie
    # convertia --check-providers en varios minutos de espera.
    PROBE_TIMEOUT_S = 20.0

    def probe(self, candidate: Candidate) -> tuple[bool, str]:
        """Verifica UN candidato de verdad: una tool call minima, que es lo
        que el ciclo agentico necesita. Un modelo puede aparecer en
        /models y fallar con 404 'no endpoints support tool use' al primer
        uso real (pasaba con OpenRouter), asi que listar no alcanza."""
        provider = prov.PROVIDERS.get(candidate.provider)
        if provider is None:
            return False, "proveedor desconocido"
        if prov.resolve_base_url(provider) is None:
            return False, f"falta {', '.join(provider.base_url_vars)}"
        keys = prov.keys_for(provider)
        if not keys:
            return False, "sin API key"
        payload = {
            "model": candidate.model,
            "messages": [{"role": "user", "content": "Di la hora de Madrid usando la tool."}],
            "temperature": 0.0,
            "tools": [{
                "type": "function",
                "function": {
                    "name": "get_time",
                    "description": "Devuelve la hora de una ciudad.",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                },
            }],
            "tool_choice": "auto",
        }
        try:
            client = self._client_for(candidate.provider, 0, keys[0])
            response = client.post(
                "/chat/completions", json=payload, timeout=self.PROBE_TIMEOUT_S
            )
        except InvalidKeyError as exc:
            return False, str(exc)
        except httpx.TransportError as exc:
            return False, f"red/TLS: {exc}"
        except Exception as exc:  # noqa: BLE001
            # probe() es diagnostico: su trabajo es INFORMAR de por que un
            # candidato no sirve. Si deja escapar una excepcion, tumba el
            # comando que existe precisamente para encontrar problemas.
            return False, f"{type(exc).__name__}: {exc}"
        if response.status_code >= 400:
            return False, f"HTTP {response.status_code}: {response.text[:160]}"
        try:
            message = response.json()["choices"][0]["message"]
        except (KeyError, IndexError, ValueError) as exc:
            return False, f"respuesta inesperada: {exc}"
        if message.get("tool_calls"):
            return True, "ok (tool call)"
        # Responde, pero no llamo a la tool. Sirve para el router (que solo
        # necesita texto) y no para un especialista agentico - por eso se
        # distingue en vez de darlo por bueno.
        return True, "responde pero NO uso la tool (vale para el router, no para agentes)"

    def close(self) -> None:
        for client in self._pool.values():
            client.close()
        self._pool.clear()
