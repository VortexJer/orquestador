"""Especialista Python: skill + ciclo generar->validar->corregir con
ruff/mypy/pytest + consulta condicional a la base de casos dificiles.

Ver doc de arquitectura, secciones 6, 7 y 9.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app.config import get_specialist
from app.inference.base import InferenceClient
from app.rag.query import search_hard_cases, should_consult_hard_cases
from app.schemas import GenerateRequest, GenerateResult, HardCaseHit, RoutingDecision, ToolExecutionResult
from app.specialists.base import Specialist, SpecialistRun
from app.tools.python_tools import run_full_chain

_OUTPUT_CONTRACT = """CONTRATO_SALIDA_JSON_PYTHON
Respondes EXCLUSIVAMENTE un JSON (sin texto antes ni despues, sin
markdown) con esta forma exacta:
{"code": "<codigo python final, listo para pegar>", \
"tests": "<tests pytest que importen desde 'solution'>", \
"explanation": "<3-5 lineas: que se decidio y por que>"}"""


class PythonSpecialist(Specialist):
    name = "python-specialist"

    def __init__(self, client: InferenceClient):
        self._client = client
        cfg = get_specialist(self.name)
        skill_path = Path(cfg["skill_file"])
        self._skill_text = skill_path.read_text(encoding="utf-8")
        self._model_name = cfg["model_name"]
        self._max_iterations = cfg["max_iterations"]
        self._rag_collections = cfg.get("rag_collections", [])

    def run(self, request: GenerateRequest, routing: RoutingDecision) -> SpecialistRun:
        system_prompt = f"{self._skill_text}\n\n{_OUTPUT_CONTRACT}"

        hard_cases: list[HardCaseHit] = []
        rag_context = ""
        if should_consult_hard_cases(routing.risk_signals, request.task):
            hits = search_hard_cases(request.task, domain="python", limit=3)
            hard_cases = [HardCaseHit(id=h["id"], used=True, tags=h.get("tags", [])) for h in hits]
            if hits:
                rag_context = "\n\nCASOS RELEVANTES DE LA BASE DE CONOCIMIENTO:\n" + "\n".join(
                    f"- [{h['id']}] {h['problem_description']} -> {h['correct_approach']}" for h in hits
                )

        context_blob = "\n".join(f"{f.path}:\n{f.content}" for f in request.context.files)
        base_user_prompt = f"TAREA: {request.task}{rag_context}\n\nARCHIVOS DE CONTEXTO:\n{context_blob}"

        all_tools: list[ToolExecutionResult] = []
        max_iterations = min(self._max_iterations, request.constraints.max_iterations)
        last_code, last_explanation = "", ""
        tools_passed = False

        with tempfile.TemporaryDirectory(prefix="py-sandbox-") as tmp:
            sandbox_dir = Path(tmp)
            user_prompt = base_user_prompt
            for iteration in range(1, max_iterations + 1):
                raw = self._client.chat(
                    model=self._model_name,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=0.1 if iteration == 1 else 0.0,
                )
                data = json.loads(raw)
                last_code = data["code"]
                last_explanation = data["explanation"]
                tests = data.get("tests", "")

                tool_results = run_full_chain(sandbox_dir, last_code, tests)
                all_tools.extend(tool_results)
                tools_passed = all(t.result == "pass" for t in tool_results)
                if tools_passed:
                    break

                failing = next(t for t in tool_results if t.result == "fail")
                user_prompt = (
                    f"{base_user_prompt}\n\nTOOL_FAILURE_CONTEXT\n"
                    f"Tu intento anterior fallo en la herramienta '{failing.tool}':\n{failing.detail}\n"
                    f"Codigo que fallo:\n{last_code}\n"
                    "Corregi la causa (no agregues workarounds) y vuelve a responder con el mismo contrato JSON."
                )

        return SpecialistRun(
            result=GenerateResult(
                content=last_code,
                explanation=last_explanation,
                files_changed=[{"path": "solution.py", "diff": last_code}],
            ),
            tools_executed=all_tools,
            correction_iterations=max(0, len([t for t in all_tools if t.tool == "ruff"]) - 1),
            hard_cases_consulted=hard_cases,
            tools_passed=tools_passed,
        )
