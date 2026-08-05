"""Cliente de inferencia simulado. Existe para poder ejercitar TODO el
pipeline de orquestacion (router -> especialista -> ciclo de
herramientas -> RAG -> validacion -> traza) sin GPU y sin pesos reales.

No es un mock "tonto" que siempre devuelve lo mismo: a proposito simula
un primer intento con un error real (import sin usar) y una segunda
version corregida cuando detecta que se le esta re-inyectando el error
de la herramienta - asi los tests ejercitan de verdad la logica de
iteracion, no solo el camino feliz de una sola pasada.
"""
from __future__ import annotations

import json

from app.inference.base import InferenceClient

_BUGGY_EMAIL_CODE = '''import os
import re


def is_valid_email(value: str) -> bool:
    pattern = r"^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$"
    return re.match(pattern, value) is not None
'''

_FIXED_EMAIL_CODE = '''import re


def is_valid_email(value: str) -> bool:
    pattern = r"^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$"
    return re.match(pattern, value) is not None
'''

_EMAIL_TESTS = '''from solution import is_valid_email


def test_valid_email_accepted():
    assert is_valid_email("user@example.com") is True


def test_missing_at_sign_rejected():
    assert is_valid_email("user.example.com") is False


def test_empty_string_rejected():
    assert is_valid_email("") is False
'''


class MockInferenceClient(InferenceClient):
    """Determinista: misma entrada -> misma salida. Selecciona el
    escenario mirando marcadores en el system_prompt (contrato de salida)
    y en el user_prompt (si trae el error de una iteracion previa)."""

    def chat(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 2000,
    ) -> str:
        if "Eres el router" in system_prompt:
            return self._mock_routing(user_prompt)
        if "Eres el validador final" in system_prompt:
            return json.dumps({"passed": True, "notes": "mock: herramientas en verde"})
        if "CONTRATO_SALIDA_JSON_PYTHON" in system_prompt:
            return self._mock_python_codegen(user_prompt)
        if "CONTRATO_SALIDA_TEXTO_PLANO" in system_prompt:
            return self._mock_generalist_text(user_prompt)
        return "[mock] no hay escenario definido para este system_prompt"

    def _mock_routing(self, user_prompt: str) -> str:
        payload = json.loads(user_prompt)
        task = payload.get("task", "")
        domain = "python" if "def " in task or "python" in task.lower() else "trivial_text"
        specialist = "python-specialist" if domain == "python" else "generalist-tiny"
        return json.dumps({
            "domain": domain,
            "risk_signals": [],
            "effort_level": "standard",
            "specialists_to_invoke": [specialist],
        })

    def _mock_python_codegen(self, user_prompt: str) -> str:
        is_retry = "TOOL_FAILURE_CONTEXT" in user_prompt
        if "email" in user_prompt.lower():
            code = _FIXED_EMAIL_CODE if is_retry else _BUGGY_EMAIL_CODE
            return json.dumps({
                "code": code,
                "tests": _EMAIL_TESTS,
                "explanation": (
                    "Valida formato de email con una regex simple; se corrigio "
                    "un import sin usar detectado por ruff."
                    if is_retry else
                    "Valida formato de email con una regex simple."
                ),
            })
        # Fallback generico para cualquier otra tarea python en tests/dev:
        return json.dumps({
            "code": "def solve() -> str:\n    return \"ok\"\n",
            "tests": "from solution import solve\n\n\ndef test_solve():\n    assert solve() == \"ok\"\n",
            "explanation": "Implementacion minima (escenario mock generico).",
        })

    def _mock_generalist_text(self, user_prompt: str) -> str:
        return (
            "Hola,\n\nGracias por tu mensaje. Te confirmo que quedo registrado "
            "y te aviso en cuanto tenga novedades.\n\nSaludos."
        )
