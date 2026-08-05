"""Carga config declarativa (YAML) + variables de entorno.

Nada de catalogo de modelos hardcodeado en el core: todo sale de
config/specialists.yaml. Ver doc de arquitectura, seccion 11 (NFR).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("SPECIALISTS_CONFIG", ROOT_DIR / "config" / "specialists.yaml"))


@dataclass(frozen=True)
class Settings:
    inference_mode: str = os.environ.get("INFERENCE_MODE", "mock")  # mock | http
    inference_base_url: str = os.environ.get("INFERENCE_BASE_URL", "http://localhost:8001/v1")
    router_base_url: str = os.environ.get("ROUTER_BASE_URL", "http://localhost:8000/v1")
    inference_api_key: str | None = os.environ.get("INFERENCE_API_KEY")

    qdrant_mode: str = os.environ.get("QDRANT_MODE", "embedded")  # embedded | remote
    qdrant_path: str = os.environ.get("QDRANT_PATH", str(ROOT_DIR / "qdrant_local_data"))
    qdrant_url: str = os.environ.get("QDRANT_URL", "http://localhost:6333")

    api_keys: tuple[str, ...] = tuple(
        k.strip() for k in os.environ.get("API_KEYS", "dev-local-key").split(",") if k.strip()
    )

    # $/segundo de GPU, referencia RunPod L4 on-demand (ver doc arquitectura seccion 10)
    gpu_cost_usd_per_second: float = float(os.environ.get("GPU_COST_USD_PER_SECOND", "0.00018"))

    tool_sandbox_timeout_s: int = int(os.environ.get("TOOL_SANDBOX_TIMEOUT_S", "20"))

    router_eval_min_accuracy: float = float(os.environ.get("ROUTER_EVAL_MIN_ACCURACY", "0.85"))


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def load_catalog() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def get_enabled_specialists() -> dict:
    catalog = load_catalog()
    return {
        name: spec
        for name, spec in catalog["specialists"].items()
        if spec.get("enabled")
    }


def get_specialist(name: str) -> dict:
    catalog = load_catalog()
    spec = catalog["specialists"].get(name)
    if spec is None:
        raise KeyError(f"Especialista '{name}' no esta registrado en {CONFIG_PATH}")
    return spec


def get_router_model_config() -> dict:
    return load_catalog()["router_model"]


def get_embedder_config() -> dict:
    return load_catalog()["embedder"]
