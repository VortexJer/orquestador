"""Especialista para tareas triviales no verificables (correos,
resumenes, texto de un documento). Reusa el modelo del router - no
dispara ninguna carga de VRAM adicional. Sin ciclo de herramientas ni
RAG: no hay "compila o no" para un correo (ver skills/generalist-tiny.md).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from app.config import get_specialist
from app.inference.base import InferenceClient
from app.schemas import GenerateRequest, GenerateResult, RoutingDecision
from app.specialists.base import Specialist, SpecialistRun
from app.tools.docx_tool import text_to_docx

_OUTPUT_CONTRACT = "CONTRATO_SALIDA_TEXTO_PLANO\nRespondes solo el texto final, sin JSON ni markdown."


class GeneralistTinySpecialist(Specialist):
    name = "generalist-tiny"

    def __init__(self, client: InferenceClient):
        self._client = client
        cfg = get_specialist(self.name)
        self._skill_text = Path(cfg["skill_file"]).read_text(encoding="utf-8")
        self._model_name = cfg["model_name"]

    def run(self, request: GenerateRequest, routing: RoutingDecision) -> SpecialistRun:
        system_prompt = f"{self._skill_text}\n\n{_OUTPUT_CONTRACT}"
        text = self._client.chat(
            model=self._model_name,
            system_prompt=system_prompt,
            user_prompt=request.task,
            temperature=0.4,
        )

        files_changed: list[dict] = []
        wants_docx = any(k in request.task.lower() for k in (".docx", "word", "documento word"))
        if wants_docx:
            out_path = Path(tempfile.mkdtemp(prefix="docx-out-")) / "output.docx"
            text_to_docx(text, out_path)
            files_changed.append({"path": str(out_path), "diff": "(binario .docx generado)"})

        return SpecialistRun(
            result=GenerateResult(content=text, explanation="", files_changed=files_changed),
            tools_executed=[],
            correction_iterations=0,
            hard_cases_consulted=[],
            tools_passed=True,
        )
