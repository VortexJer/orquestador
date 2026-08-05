"""API Gateway. Ver doc de arquitectura, seccion 4 (esquema de API).

Correr en desarrollo:
    uvicorn app.main:app --reload

Modo por defecto: INFERENCE_MODE=mock (sin GPU, sin pesos reales) -
ver .env.example para pasar a modo http contra un servidor real.
"""
from __future__ import annotations

import json
import time

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from app import orchestrator
from app.config import get_settings, load_catalog
from app.rag.query import search_hard_cases
from app.schemas import GenerateRequest
from app.streaming import build_sse_events

app = FastAPI(title="Orquestador de Modelos Especializados", version="0.1.0")

_start_time = time.monotonic()


def require_api_key(request: Request) -> str:
    settings = get_settings()
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Falta el header Authorization: Bearer <api_key>")
    key = auth.removeprefix("Bearer ").strip()
    if key not in settings.api_keys:
        raise HTTPException(status_code=401, detail="API key invalida")
    return key


@app.post("/v1/generate", response_model=None)
def generate(payload: GenerateRequest, api_key: str = Depends(require_api_key)):
    try:
        response = orchestrator.generate(payload)
    except Exception as exc:  # noqa: BLE001 - limite del sistema, se traduce a 500 explicito
        raise HTTPException(status_code=500, detail=f"Error interno generando la respuesta: {exc}") from exc

    if not payload.stream:
        return response

    def sse_stream():
        for event in build_sse_events(response):
            yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"

    return StreamingResponse(sse_stream(), media_type="text/event-stream")


@app.get("/v1/catalog")
def catalog(api_key: str = Depends(require_api_key)):
    data = load_catalog()
    return {
        "router_model": data["router_model"],
        "embedder": data["embedder"],
        "specialists": {
            name: {k: v for k, v in spec.items() if k != "skill_file"}
            for name, spec in data["specialists"].items()
        },
        "fallback_model": data["fallback_model"],
    }


@app.get("/v1/hard-cases/search")
def hard_cases_search(q: str, domain: str | None = None, api_key: str = Depends(require_api_key)):
    return {"results": search_hard_cases(q, domain=domain or "", limit=5)}


@app.get("/v1/usage")
def usage(api_key: str = Depends(require_api_key)):
    # Diferido a fase 3 (decision confirmada con el usuario): en fase 1
    # no hay medicion real por API key, solo un placeholder para que el
    # contrato de la API ya exista y no haya que romperlo despues.
    return {"api_key": api_key, "gpu_seconds_total": None, "note": "Medicion por API key: fase 3."}


@app.get("/v1/health")
def health():
    settings = get_settings()
    return {
        "status": "ok",
        "inference_mode": settings.inference_mode,
        "currently_warm_ref": orchestrator.get_currently_warm_ref(),
        "uptime_s": round(time.monotonic() - _start_time, 1),
    }
