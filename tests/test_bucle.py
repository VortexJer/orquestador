"""El corte de bucles.

Fallo real: "hazme un modelo 3D de un bloque motor". mypy decia que
matplotlib no estaba instalado - un error que el modelo NO puede arreglar
escribiendo codigo - y se quedo escribiendo requirements.txt y revisando,
una y otra vez, hasta que el usuario pulso ESC.

La racha de fallos que ya existia no lo cubria: se reinicia con cada
exito, y un bucle real ALTERNA exito y fallo (escribir sale bien, revisar
falla), asi que valia 1 en cada vuelta y nunca llegaba al umbral.
"""
import json

from groq_agent import agent_loop


class _ClienteEnBucle:
    """Siempre pide lo mismo: es el modelo atascado."""

    def __init__(self):
        self.llamadas = 0

    def chat(self, messages, tools=None, model="", **kwargs):
        self.llamadas += 1
        return {"choices": [{"message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": f"c{self.llamadas}", "type": "function",
                            "function": {"name": "run_check",
                                         "arguments": json.dumps({"tool": "mypy"})}}],
        }}]}


class _EjecutorQueSiempreFalla:
    """El entorno no cambia entre vueltas: por eso repetir es estar
    atascado y no insistir."""

    auto_yes = True
    session_accept_all = True

    def __init__(self, root):
        self.root = root

    def dispatch(self, name, arguments):
        return "ERROR: Cannot find implementation or library stub for module named 'matplotlib'"


def test_se_corta_cuando_la_misma_llamada_da_lo_mismo(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_loop.interrupt, "pedido", lambda: False)
    cliente = _ClienteEnBucle()

    texto, _ = agent_loop.run_agent(
        client=cliente, executor=_EjecutorQueSiempreFalla(tmp_path),
        system_prompt="s", user_task="hazme un modelo 3D", model="m",
        max_turns=50, tool_schemas=[],
    )

    assert cliente.llamadas <= agent_loop._REPETICIONES_PARA_CORTAR + 1, (
        "tenia que cortar a las pocas vueltas, no agotar max_turns"
    )
    assert "atascado" in texto
    assert "install_dependency" in texto, "si es una dependencia, hay que decir que se puede instalar"
