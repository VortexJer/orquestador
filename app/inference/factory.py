from __future__ import annotations

from functools import lru_cache

from app.config import get_settings
from app.inference.base import InferenceClient
from app.inference.mock_client import MockInferenceClient
from app.inference.openai_compatible_client import OpenAICompatibleClient


@lru_cache
def get_specialist_inference_client() -> InferenceClient:
    settings = get_settings()
    if settings.inference_mode == "mock":
        return MockInferenceClient()
    return OpenAICompatibleClient(settings.inference_base_url, settings.inference_api_key)


@lru_cache
def get_router_inference_client() -> InferenceClient:
    """El router (y el validador, que reusa el mismo modelo) hablan con
    un endpoint propio (router_base_url) porque en produccion es un
    proceso vLLM/llama.cpp distinto y mas chico que el de los
    especialistas de codigo."""
    settings = get_settings()
    if settings.inference_mode == "mock":
        return MockInferenceClient()
    return OpenAICompatibleClient(settings.router_base_url, settings.inference_api_key)
