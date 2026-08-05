"""Saneo de mensajes antes de enviarlos al proveedor.

Bug real (visto en vivo con web-builder): un proveedor de razonamiento devolvio
un mensaje de asistente con `reasoning_content`; al reenviarlo en el historial,
mistral/codestral lo rechazaron con `HTTP 422 extra_forbidden` y se agoto la
escalera ENTERA (la cabeza mistral-large, con 128K de contexto, incluida).
"""
from groq_agent.groq_client import _sanear_mensajes


def test_quita_reasoning_content():
    msgs = [
        {"role": "system", "content": "eres util"},
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "hey", "reasoning_content": "pensando..."},
    ]
    fuera = _sanear_mensajes(msgs)
    assert "reasoning_content" not in fuera[2]
    assert fuera[2] == {"role": "assistant", "content": "hey"}


def test_conserva_los_campos_estandar():
    msgs = [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function",
            "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "content": "resultado", "tool_call_id": "c1"},
        {"role": "user", "content": "sigue", "name": "ana"},
    ]
    fuera = _sanear_mensajes(msgs)
    assert fuera[0]["tool_calls"][0]["id"] == "c1"
    assert fuera[0]["content"] is None            # None es valido (assistant con tool_calls)
    assert fuera[1]["tool_call_id"] == "c1"
    assert fuera[2]["name"] == "ana"


def test_no_toca_lo_que_ya_esta_limpio():
    limpio = {"role": "user", "content": "x"}
    fuera = _sanear_mensajes([limpio])
    assert fuera[0] is limpio                     # sin copia innecesaria


def test_quita_cualquier_campo_no_estandar():
    m = {"role": "assistant", "content": "ok", "raro": 1, "provider_meta": {"a": 2}}
    fuera = _sanear_mensajes([m])
    assert fuera[0] == {"role": "assistant", "content": "ok"}
