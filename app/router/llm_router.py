"""Router real basado en LLM (Qwen2.5-1.5B/3B-Instruct via endpoint
OpenAI-compatible: vLLM, llama.cpp server u Ollama). Implementa la misma
interfaz que HeuristicRouter para que el swap sea un cambio de config
(ROUTER_IMPL=llm) y no del core.

No se puede ejercitar de punta a punta sin un servidor de inferencia
corriendo (requiere GPU o al menos un runtime CPU con el modelo
cargado) - por eso en fase 1, sin GPU todavia, HeuristicRouter es el
default. Una vez el servidor este arriba (ver DEPLOY_PROMPT.txt), activar
este router y correr eval/scripts/eval_router.py contra el >= umbral de
aceptacion antes de confiar en el en produccion (criterio de aceptacion
pedido explicitamente).
"""
from __future__ import annotations

import json

from app.inference.base import InferenceClient
from app.schemas import GenerateRequest, RoutingDecision

_ROUTER_SYSTEM_PROMPT = """Eres el router de un sistema de orquestacion de \
modelos especializados de codigo. Tu unica tarea es clasificar la tarea \
entrante. Devolves EXCLUSIVAMENTE un JSON con esta forma, sin texto \
adicional:

{"domain": "python|typescript|sql|rust|go|iac|testing|refactor|docs|\
security|trivial_text|multi", "risk_signals": ["concurrency","auth",\
"sql_write"], "effort_level": "quick_pass|standard|iterative_verify", \
"specialists_to_invoke": ["<nombre-especialista>"]}

Prefiere el especialista cuyo modelo base ya esta cargado en GPU cuando \
sea razonablemente adecuado para la tarea, para evitar una recarga de \
pesos. Solo marca "iterative_verify" si detectas riesgo real \
(concurrencia, autenticacion, escritura de datos) o si el usuario \
adjunto archivos de contexto."""

_VALIDATOR_SYSTEM_PROMPT = """Eres el validador final del mismo sistema de \
orquestacion (reutilizas el modelo del router, sin costo extra de VRAM). \
Dado el pedido original, la respuesta candidata y si las herramientas de \
verificacion pasaron, respondes EXCLUSIVAMENTE un JSON:
{"passed": true|false, "notes": "..."}"""


class LLMRouter:
    def __init__(self, client: InferenceClient, model_name: str):
        self._client = client
        self._model_name = model_name

    def route(self, request: GenerateRequest, currently_warm_ref: str | None = None) -> RoutingDecision:
        user_payload = json.dumps({
            "task": request.task,
            "language_hint": request.language_hint,
            "has_context_files": bool(request.context.files),
            "currently_warm_model_ref": currently_warm_ref,
        }, ensure_ascii=False)

        raw = self._client.chat(
            model=self._model_name,
            system_prompt=_ROUTER_SYSTEM_PROMPT,
            user_prompt=user_payload,
            temperature=0.0,
            max_tokens=300,
        )
        data = json.loads(raw)
        return RoutingDecision(
            domain=data["domain"],
            subtasks=data.get("subtasks", []),
            effort_level=data["effort_level"],
            risk_signals=data.get("risk_signals", []),
            specialists_to_invoke=data["specialists_to_invoke"],
            reused_warm_model=data.get("reused_warm_model", False),
        )

    def validate(self, request: GenerateRequest, candidate_content: str, tools_passed: bool) -> tuple[bool, str]:
        user_payload = json.dumps({
            "task": request.task,
            "candidate_content": candidate_content[:4000],
            "tools_passed": tools_passed,
        }, ensure_ascii=False)
        raw = self._client.chat(
            model=self._model_name,
            system_prompt=_VALIDATOR_SYSTEM_PROMPT,
            user_prompt=user_payload,
            temperature=0.0,
            max_tokens=200,
        )
        data = json.loads(raw)
        return bool(data["passed"]), str(data.get("notes", ""))
