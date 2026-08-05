"""Un proveedor marcado `de_pago` NO se usa. En ningun camino.

Se descubrieron probandolos en vivo: 8 de los 20 proveedores que se
anuncian como gratis devuelven 402/403/429 pidiendo saldo o tarjeta. Se
marcan con `de_pago` en vez de borrarse - si se borran, la proxima vez que
alguien mire una lista de "APIs gratis" los vuelve a añadir y se vuelve a
comer el error. Pero marcados tienen que quedar completamente fuera de
juego, y eso es lo que se verifica aca.
"""
import httpx
import pytest

from groq_agent import providers as prov
from groq_agent.groq_client import GroqClient


def test_hay_proveedores_marcados_de_pago():
    """Si esto falla, o se limpiaron los marcadores o se perdio el
    aprendizaje de que estos no son gratis."""
    assert prov.desactivados(), "no hay ninguno marcado - ¿se borro el aprendizaje?"


@pytest.mark.parametrize("nombre", [n for n, _ in prov.desactivados()])
def test_cada_desactivado_explica_por_que(nombre):
    """El motivo tiene que citar la respuesta real del proveedor, no una
    suposicion: es lo que evita que alguien lo reactive 'por probar'."""
    motivo = prov.PROVIDERS[nombre].de_pago
    assert len(motivo) > 30, f"'{nombre}' no explica nada util"
    assert any(c in motivo for c in ("402", "403", "429", "401", "404", "412")), (
        f"'{nombre}' no cita el codigo HTTP que devolvio"
    )


def test_ninguna_escalera_incluye_un_proveedor_de_pago():
    """Funcionalmente se saltarian igual, pero tenerlos escritos en una
    escalera hace creer que se usan."""
    apagados = {n for n, _ in prov.desactivados()}
    culpables = {}
    for tier, chain in prov.CHAIN_BY_TIER.items():
        malos = [str(c) for c in chain if c.provider in apagados]
        if malos:
            culpables[tier] = malos
    assert not culpables, f"escaleras con proveedores de pago: {culpables}"


def test_el_respaldo_universal_tampoco():
    apagados = {n for n, _ in prov.desactivados()}
    assert not [c for c in prov.UNIVERSAL_BACKSTOP if c.provider in apagados]


def test_configured_providers_no_los_cuenta():
    """Tener su key puesta no los hace utilizables: contarlos daria una
    sensacion falsa de cobertura."""
    apagados = {n for n, _ in prov.desactivados()}
    assert not (set(prov.configured_providers()) & apagados)


def test_el_asistente_no_pide_su_key():
    from groq_agent.config_wizard import _ORDEN

    apagados = {n for n, _ in prov.desactivados()}
    preguntados = {n for n, _, _, _ in _ORDEN}
    assert not (preguntados & apagados), (
        "el asistente sigue pidiendo la key de un proveedor que no sirve"
    )


def test_el_cliente_nunca_contacta_a_un_desactivado(monkeypatch):
    """La prueba que de verdad importa: que NO SALGA una peticion HTTP a un
    proveedor de pago. Se intercepta httpx y se comprueba a que hosts se
    llamo."""
    apagados = [n for n, _ in prov.desactivados()]
    if not apagados:
        pytest.skip("ninguno desactivado")

    # Key para todos, incluidos los apagados: el filtro tiene que ser el
    # marcador, no la falta de credenciales.
    for nombre, provider in prov.PROVIDERS.items():
        if provider.needs_key:
            monkeypatch.setenv(f"{provider.key_prefix}_API_KEY", "clave-de-prueba-0123456789")

    hosts_llamados: list[str] = []

    def _post_falso(self, url, **kwargs):
        hosts_llamados.append(str(self.base_url))
        raise httpx.ConnectError("cortado por el test")

    monkeypatch.setattr(httpx.Client, "post", _post_falso)
    prov.reset_runtime_state()

    client = GroqClient()
    try:
        for tier in prov.CHAIN_BY_TIER:
            with pytest.raises(RuntimeError):
                client.chat([{"role": "user", "content": "x"}], model=tier)
    finally:
        client.close()
        prov.reset_runtime_state()

    assert hosts_llamados, "el test no probo nada: no hubo ninguna llamada"
    for nombre in apagados:
        base = prov.PROVIDERS[nombre].base_url
        host = base.split("//", 1)[-1].split("/", 1)[0]
        assert not any(host in h for h in hosts_llamados), (
            f"se contacto a '{nombre}' ({host}) pese a estar desactivado"
        )


def test_los_activos_si_se_contactan(monkeypatch):
    """Contrapeso del test anterior: si el filtro fuera demasiado agresivo y
    bloqueara a todos, el test de arriba pasaria igual y no nos enterariamos."""
    for nombre, provider in prov.PROVIDERS.items():
        if provider.needs_key:
            monkeypatch.setenv(f"{provider.key_prefix}_API_KEY", "clave-de-prueba-0123456789")

    hosts: list[str] = []

    def _post_falso(self, url, **kwargs):
        hosts.append(str(self.base_url))
        raise httpx.ConnectError("cortado")

    monkeypatch.setattr(httpx.Client, "post", _post_falso)
    prov.reset_runtime_state()
    client = GroqClient()
    try:
        with pytest.raises(RuntimeError):
            client.chat([{"role": "user", "content": "x"}], model="coder-main")
    finally:
        client.close()
        prov.reset_runtime_state()

    # nvidia cierra todas las escaleras: tiene que haberse intentado.
    assert any("integrate.api.nvidia.com" in h for h in hosts)
