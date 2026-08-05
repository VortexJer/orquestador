"""Proveedores gratis net-new anadidos tras buscar (2026-08): Google Gemini
(AI Studio) y Z.AI (Zhipu GLM). Se verifico que son free tier REAL con tool
calling; GitHub Models (retirado), Nebius/DashScope/Moonshot/MiniMax/Hyperbolic
(credito de prueba o de pago) se DESCARTARON a proposito."""
from groq_agent import providers as prov


def test_gemini_y_zai_registrados():
    assert "gemini" in prov.PROVIDERS
    assert "zai" in prov.PROVIDERS
    # base_url oficial de Google (confirmada contra ai.google.dev)
    assert prov.PROVIDERS["gemini"].base_url.endswith("/v1beta/openai")
    assert prov.PROVIDERS["gemini"].verified_base_url is True
    assert prov.PROVIDERS["zai"].base_url == "https://api.z.ai/api/paas/v4"


def test_no_estan_marcados_de_pago():
    """Son gratis de verdad: no deben caer en la lista de desactivados."""
    apagados = {n for n, _ in prov.desactivados()}
    assert "gemini" not in apagados and "zai" not in apagados


def test_aparecen_en_alguna_escalera():
    en_cadena = {c.provider for chain in prov.CHAIN_BY_TIER.values() for c in chain}
    assert "gemini" in en_cadena
    assert "zai" in en_cadena


def test_no_encabezan_ninguna_escalera():
    """Nuevos y sin probar contra el prompt real: van en posicion media, nunca
    de cabeza (la cabeza se reserva a candidatos ya verificados)."""
    for tier, chain in prov.CHAIN_BY_TIER.items():
        assert chain[0].provider not in ("gemini", "zai", "modelscope"), (
            f"un proveedor nuevo encabeza '{tier}' sin haberse probado"
        )


def test_todo_candidato_referencia_un_proveedor_real():
    """Un id de proveedor mal escrito en una escalera = candidato muerto en
    silencio. Se valida que TODA la escalera apunta a PROVIDERS."""
    for tier, chain in prov.CHAIN_BY_TIER.items():
        for c in chain:
            assert c.provider in prov.PROVIDERS, f"{tier}: proveedor '{c.provider}' no existe"


def test_el_asistente_pide_las_keys_de_los_nuevos():
    from groq_agent.config_wizard import _ORDEN

    preguntados = {n for n, _, _, _ in _ORDEN}
    assert "gemini" in preguntados and "zai" in preguntados


def test_keys_for_lee_su_variable(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "clave-gemini-0123456789")
    monkeypatch.setenv("ZAI_API_KEY", "clave-zai-0123456789")
    assert prov.keys_for(prov.PROVIDERS["gemini"]) == ["clave-gemini-0123456789"]
    assert prov.keys_for(prov.PROVIDERS["zai"]) == ["clave-zai-0123456789"]


# --- Pollinations: net-new SIN key con tool calling, backstop de ultimo recurso -


def test_pollinations_registrado_sin_key():
    p = prov.PROVIDERS["pollinations"]
    assert p.needs_key is False
    assert p.base_url == "https://text.pollinations.ai/openai"
    # keys_for da [""] a los que no necesitan key: un slot que intentar.
    assert prov.keys_for(p) == [""]


def test_pollinations_no_es_de_pago():
    apagados = {n for n, _ in prov.desactivados()}
    assert "pollinations" not in apagados


def test_pollinations_va_al_FINAL_de_cada_escalera_nunca_de_cabeza():
    """Un proxy sin key de fiabilidad desconocida solo vale como ultimo recurso:
    en TODAS las escaleras y SIEMPRE en la ultima posicion, jamas encabezando."""
    for tier, chain in prov.CHAIN_BY_TIER.items():
        assert chain[0].provider != "pollinations", f"encabeza '{tier}'"
        assert chain[-1].provider == "pollinations", f"no cierra '{tier}'"


def test_el_asistente_NO_pide_key_de_pollinations():
    """No necesita key -> no debe aparecer en el asistente de configuracion."""
    from groq_agent.config_wizard import _ORDEN

    preguntados = {n for n, _, _, _ in _ORDEN}
    assert "pollinations" not in preguntados


# --- ModelScope (Alibaba): net-new free tier REAL con tool calling y key --------


def test_modelscope_registrado_con_key():
    m = prov.PROVIDERS["modelscope"]
    assert m.needs_key is True
    assert m.base_url == "https://api-inference.modelscope.cn/v1"


def test_modelscope_no_es_de_pago():
    apagados = {n for n, _ in prov.desactivados()}
    assert "modelscope" not in apagados


def test_modelscope_en_varias_escaleras_pero_antes_del_backstop_nvidia():
    """Es un free tier REAL que queremos usar ANTES de agotar nvidia: va en
    posicion media (antes del respaldo de nvidia), no como ultimo recurso, y
    nunca de cabeza."""
    en_cadena = {c.provider for chain in prov.CHAIN_BY_TIER.values() for c in chain}
    assert "modelscope" in en_cadena
    for tier, chain in prov.CHAIN_BY_TIER.items():
        provs = [c.provider for c in chain]
        if "modelscope" in provs:
            # aparece antes de la ULTIMA aparicion de nvidia en esa escalera
            assert provs.index("modelscope") < len(provs) - 1 - provs[::-1].index("nvidia"), (
                f"modelscope deberia ir antes del backstop nvidia en '{tier}'"
            )


def test_el_asistente_pide_la_key_de_modelscope():
    from groq_agent.config_wizard import _ORDEN

    preguntados = {n for n, _, _, _ in _ORDEN}
    assert "modelscope" in preguntados


def test_keys_for_modelscope(monkeypatch):
    monkeypatch.setenv("MODELSCOPE_API_KEY", "clave-modelscope-0123456789")
    assert prov.keys_for(prov.PROVIDERS["modelscope"]) == ["clave-modelscope-0123456789"]
