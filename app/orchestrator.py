"""Glue entre router, especialistas, ciclo de herramientas, validacion
final y contabilidad de costo. Es lo que llama el gateway (app/main.py)
en /v1/generate. Ver doc de arquitectura, seccion 3 (flujo end-to-end).
"""
from __future__ import annotations

import os
import time
import uuid
from functools import lru_cache

from app.config import get_settings, get_specialist
from app.inference.factory import get_router_inference_client, get_specialist_inference_client
from app.router.base import Router
from app.router.heuristic_router import HeuristicRouter
from app.router.llm_router import LLMRouter
from app.schemas import (
    CostInfo,
    GenerateRequest,
    GenerateResponse,
    ModelUsed,
    Trace,
)
from app.specialists.base import Specialist
from app.specialists.generalist_tiny_specialist import GeneralistTinySpecialist
from app.specialists.python_specialist import PythonSpecialist
from app.validation.final_validator import run_final_validation

# Simula, en un solo proceso, cual es el base_model_ref actualmente
# "caliente" en la GPU - en produccion esto lo lleva el Model Manager
# real (ver doc arquitectura seccion 10); aca alcanza para poder medir
# la afinidad de costo del router en tests/eval.
_currently_warm_ref: str | None = None


def get_currently_warm_ref() -> str | None:
    return _currently_warm_ref


@lru_cache
def get_router() -> Router:
    impl = os.environ.get("ROUTER_IMPL", "heuristic")
    if impl == "llm":
        client = get_router_inference_client()
        from app.config import get_router_model_config
        model_name = get_router_model_config()["model_name"]
        return LLMRouter(client, model_name)
    return HeuristicRouter()


@lru_cache
def get_specialist_registry() -> dict[str, Specialist]:
    client = get_specialist_inference_client()
    return {
        "python-specialist": PythonSpecialist(client),
        "generalist-tiny": GeneralistTinySpecialist(client),
    }


def generate(request: GenerateRequest) -> GenerateResponse:
    global _currently_warm_ref
    settings = get_settings()
    start = time.monotonic()

    router = get_router()
    routing = router.route(request, _currently_warm_ref)

    specialist_name = routing.specialists_to_invoke[0]
    registry = get_specialist_registry()
    specialist = registry.get(specialist_name)
    if specialist is None:
        # No deberia pasar si el router respeta el catalogo habilitado,
        # pero si pasa, degradamos al especialista python en vez de
        # devolver un 500 (ver escalera de fallback, doc seccion 5).
        specialist_name = "python-specialist"
        specialist = registry[specialist_name]

    spec_cfg = get_specialist(specialist_name)
    was_cold_start = spec_cfg["base_model_ref"] != _currently_warm_ref
    _currently_warm_ref = spec_cfg["base_model_ref"]

    run = specialist.run(request, routing)

    validated_by = "router_model" if os.environ.get("ROUTER_IMPL", "heuristic") == "llm" else "rule_based"
    validation = run_final_validation(
        router, request, run.result.content, run.tools_passed, validated_by=validated_by
    )

    if validation.passed:
        status = "completed"
    elif run.result.content:
        status = "partial"
    else:
        status = "failed"

    elapsed_s = time.monotonic() - start
    cost = CostInfo(
        gpu_seconds=round(elapsed_s, 3),
        estimated_usd=round(elapsed_s * settings.gpu_cost_usd_per_second, 6),
    )

    trace = Trace(
        router_decision=routing,
        model_used=ModelUsed(
            base_model=spec_cfg["model_name"],
            specialist=specialist_name,
            was_cold_start=was_cold_start,
        ),
        tools_executed=run.tools_executed,
        correction_iterations=run.correction_iterations,
        hard_cases_consulted=run.hard_cases_consulted,
        validation=validation,
    )

    return GenerateResponse(
        request_id=f"req_{uuid.uuid4().hex[:10]}",
        status=status,
        result=run.result,
        trace=trace,
        cost=cost,
    )
