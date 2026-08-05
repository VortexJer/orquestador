"""Regresion de un fallo REAL: al probar el asistente se le paso "fin" por
una tuberia de PowerShell 5.1, que añade un BOM al codificar. `input()`
devolvio "﻿fin", la comparacion con "fin" no coincidio, y el asistente
guardo esa cadena como si fuera una API key. Luego httpx reventaba con
UnicodeEncodeError al construir la cabecera Authorization y se llevaba por
delante el comando entero.

Tres defensas, una por capa, y aca se prueban las tres:
  1. sanear_entrada: quita invisibles antes de comparar y de guardar.
  2. validar_key: descarta lo que con certeza no es una key.
  3. GroqClient: una key no-ASCII es key muerta, no una excepcion suelta.
"""
import pytest

from groq_agent import providers as prov
from groq_agent.config_wizard import (
    _escribir_env,
    _leer_env,
    _siguiente_slot_libre,
    sanear_entrada,
    validar_key,
)
from groq_agent.groq_client import GroqClient, InvalidKeyError

BOM = "﻿"


# --- 1) saneo de la entrada ------------------------------------------


@pytest.mark.parametrize(
    ("crudo", "esperado"),
    [
        (BOM + "fin", "fin"),          # el caso exacto que fallo
        ("  fin  ", "fin"),
        (BOM + "fin\n", "fin"),
        ("​fin", "fin"),          # zero-width space
        (" fin", "fin"),          # non-breaking space
        (BOM + "nvapi-abc123def456", "nvapi-abc123def456"),
        ("", ""),
    ],
)
def test_sanear_entrada_quita_invisibles(crudo, esperado):
    assert sanear_entrada(crudo) == esperado


def test_fin_con_bom_se_reconoce_como_corte():
    """El fallo original en una linea: si esto falla, el asistente vuelve a
    guardar 'fin' como API key."""
    from groq_agent.config_wizard import _CORTAR

    assert sanear_entrada(BOM + "fin").lower() in _CORTAR


# --- 2) validacion de la key ------------------------------------------


def test_validar_key_acepta_una_key_plausible():
    assert validar_key("nvapi-0123456789abcdefghij") is None


@pytest.mark.parametrize(
    "basura",
    [
        "fin",                 # palabra del propio asistente
        "q",
        "corto",               # menos del minimo creible
        "nvapi con espacios",
        "nvapi-clave-con-ñ",   # no-ASCII: reventaria la cabecera HTTP
        BOM + "abcdefghij",    # BOM sin sanear
    ],
)
def test_validar_key_rechaza_lo_que_no_es_una_key(basura):
    assert validar_key(basura) is not None


def test_el_motivo_del_rechazo_es_accionable():
    """El usuario tiene que saber QUE hacer, no solo que fallo."""
    motivo = validar_key("clave-con-ñ-dentro")
    assert "no-ASCII" in motivo
    assert "copiarla" in motivo


# --- 3) el cliente no revienta con una key invalida -------------------


def test_una_key_no_ascii_es_key_muerta_y_no_una_excepcion_suelta(monkeypatch):
    """Antes: UnicodeEncodeError sin capturar tumbaba la sesion. Ahora se
    aparta la key y se sigue con el proveedor siguiente."""
    prov.reset_runtime_state()
    monkeypatch.setenv("NVIDIA_API_KEY", BOM + "clave-invalida-123")
    client = GroqClient()
    try:
        with pytest.raises(InvalidKeyError):
            client._client_for("nvidia", 0, BOM + "clave-invalida-123")
    finally:
        client.close()
        prov.reset_runtime_state()


def test_probe_informa_del_problema_en_vez_de_propagarlo(monkeypatch):
    """--check-providers existe para diagnosticar: si una excepcion se
    escapa, muere el comando que deberia haber senalado el fallo."""
    prov.reset_runtime_state()
    monkeypatch.setenv("NVIDIA_API_KEY", BOM + "clave-invalida-123")
    client = GroqClient()
    try:
        ok, detalle = client.probe(prov.Candidate("nvidia", "un/modelo"))
        assert ok is False
        assert "no-ASCII" in detalle or "InvalidKey" in detalle
    finally:
        client.close()
        prov.reset_runtime_state()


# --- 4) el .env no se corrompe ----------------------------------------


def test_escribir_env_preserva_variables_ajenas(tmp_path):
    """El .env tambien lleva API_KEYS, QDRANT_*, INFERENCE_MODE... Perderlas
    rompe el orquestador de produccion, no solo la terminal. (Aprendido a
    la mala: una limpieza mia aplico una regla de longitud a TODAS las
    variables y se llevo cuatro valores de configuracion legitimos.)"""
    env = tmp_path / ".env"
    env.write_text(
        "# cabecera\n"
        "INFERENCE_MODE=mock\n"
        "TOOL_SANDBOX_TIMEOUT_S=20\n"
        "API_KEYS=dev-local-key\n",
        encoding="utf-8",
    )
    lines, _ = _leer_env(env)
    _escribir_env(env, lines, {"NVIDIA_API_KEY": "nvapi-123456789"})

    texto = env.read_text(encoding="utf-8")
    assert "INFERENCE_MODE=mock" in texto
    assert "TOOL_SANDBOX_TIMEOUT_S=20" in texto      # 2 chars, legitimo
    assert "API_KEYS=dev-local-key" in texto
    assert "# cabecera" in texto
    assert "NVIDIA_API_KEY=nvapi-123456789" in texto


def test_escribir_env_actualiza_en_su_sitio_sin_duplicar(tmp_path):
    env = tmp_path / ".env"
    env.write_text("NVIDIA_API_KEY=vieja\nAPI_KEYS=x\n", encoding="utf-8")
    lines, _ = _leer_env(env)
    _escribir_env(env, lines, {"NVIDIA_API_KEY": "nueva-123456789"})
    texto = env.read_text(encoding="utf-8")
    assert texto.count("NVIDIA_API_KEY=") == 1
    assert "nueva-123456789" in texto


def test_leer_env_tolera_bom(tmp_path):
    """Un .env guardado por PowerShell u otro editor de Windows puede llevar
    BOM; sin tolerarlo, la PRIMERA variable del archivo se lee con el BOM
    pegado al nombre y se considera inexistente."""
    env = tmp_path / ".env"
    env.write_bytes(b"\xef\xbb\xbfNVIDIA_API_KEY=nvapi-123456789\n")
    _, valores = _leer_env(env)
    assert "NVIDIA_API_KEY" in valores, (
        "la primera variable se perdio por el BOM"
    )


def test_no_salta_el_proveedor_pero_no_cierra_el_asistente():
    """Fallo real: "no" estaba en la lista de palabras de corte, asi que un
    usuario que escribio "no" para saltar UN proveedor cerro el asistente
    entero. En un prompt que pide una key, "no" significa "esta no la
    tengo"."""
    from groq_agent.config_wizard import _CORTAR, _SALTAR

    assert "no" in _SALTAR
    assert "no" not in _CORTAR
    assert not (_SALTAR & _CORTAR), "una palabra no puede saltar y cortar a la vez"


def test_escribir_env_es_idempotente_en_la_cabecera(tmp_path):
    """Se llama una vez POR KEY (guardado incremental). Sin guarda, el .env
    acababa con una linea de comentario por cada key pegada."""
    env = tmp_path / ".env"
    env.write_text("API_KEYS=x\n", encoding="utf-8")

    acumulado = {}
    for i, nombre in enumerate(("NVIDIA_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY")):
        acumulado[nombre] = f"clave-de-prueba-{i}-0123456789"
        lines, _ = _leer_env(env)
        _escribir_env(env, lines, acumulado)

    texto = env.read_text(encoding="utf-8")
    assert texto.count("# --- API keys de proveedores") == 1
    for nombre in acumulado:
        assert texto.count(f"{nombre}=") == 1
    assert "API_KEYS=x" in texto


def test_cada_key_se_guarda_al_momento_no_al_final(tmp_path, monkeypatch):
    """Pegar 8 keys son varios minutos: una salida a mitad no puede tirar
    todo lo tecleado. Con guardado incremental, lo anterior sobrevive."""
    import builtins

    from groq_agent import config_wizard

    env = tmp_path / ".env"
    env.write_text("API_KEYS=x\n", encoding="utf-8")
    monkeypatch.setattr(config_wizard, "_env_path", lambda: env)
    # Limpia el entorno para que no cuente como "ya configurado".
    for var in ("NVIDIA_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    # nvidia: key buena -> ¿otra? no -> groq: 'fin' (corta el asistente)
    guion = iter(["nvapi-CLAVE-DE-PRUEBA-1234567890", "no", "fin"])
    monkeypatch.setattr(builtins, "input", lambda *a, **k: next(guion, "fin"))

    config_wizard.run_config()

    texto = env.read_text(encoding="utf-8")
    assert "NVIDIA_API_KEY=nvapi-CLAVE-DE-PRUEBA-1234567890" in texto, (
        "la key entrada antes del corte se perdio"
    )


def test_el_banquillo_no_pisa_la_key_que_ya_hay():
    assert _siguiente_slot_libre("NVIDIA", {}) == "NVIDIA_API_KEY"
    assert _siguiente_slot_libre("NVIDIA", {"NVIDIA_API_KEY": "k"}) == "NVIDIA_API_KEY_2"
    assert _siguiente_slot_libre(
        "NVIDIA", {"NVIDIA_API_KEY": "k", "NVIDIA_API_KEY_2": "k2"}
    ) == "NVIDIA_API_KEY_3"
