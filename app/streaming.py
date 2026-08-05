"""Construye la secuencia de eventos SSE a partir de una GenerateResponse
ya calculada.

Limitacion honesta de este prototipo: como el MockClient y el cliente
OpenAI-compatible actuales no exponen streaming token por token, el
"streaming" de fase 1 es una reconstruccion post-hoc de los eventos
(no reduce latencia al primer byte). El streaming real de tokens
requiere pedirle a vLLM su modo `stream: true` nativo desde
OpenAICompatibleClient - queda para fase 2 (ver roadmap).
"""
from __future__ import annotations

from app.schemas import GenerateResponse


def build_sse_events(response: GenerateResponse) -> list[dict]:
    events: list[dict] = []
    routing = response.trace.router_decision
    events.append({
        "event": "routing_decision",
        "data": {
            "specialists": routing.specialists_to_invoke,
            "effort_level": routing.effort_level,
            "domain": routing.domain,
        },
    })
    events.append({
        "event": "model_loading",
        "data": {
            "model": response.trace.model_used.base_model,
            "cold_start": response.trace.model_used.was_cold_start,
        },
    })
    for tool in response.trace.tools_executed:
        events.append({
            "event": "tool_execution",
            "data": {"tool": tool.tool, "status": tool.result, "detail": tool.detail[:500]},
        })
    if response.trace.correction_iterations > 0:
        events.append({
            "event": "correction_iteration",
            "data": {"iterations": response.trace.correction_iterations},
        })
    for hit in response.trace.hard_cases_consulted:
        events.append({"event": "hard_case_hit", "data": {"id": hit.id, "tags": hit.tags}})
    events.append({"event": "final_result", "data": response.model_dump()})
    return events
