"""Interfaz del router. Swapear la implementacion es un cambio de config,
no del core (ver doc de arquitectura, seccion 5 y 11).
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.schemas import GenerateRequest, RoutingDecision


class Router(ABC):
    @abstractmethod
    def route(self, request: GenerateRequest, currently_warm_ref: str | None) -> RoutingDecision:
        """Clasifica la tarea y decide especialista(s) + esfuerzo."""

    @abstractmethod
    def validate(
        self,
        request: GenerateRequest,
        candidate_content: str,
        tools_passed: bool,
    ) -> tuple[bool, str]:
        """Validacion final. Decision confirmada: la hace el mismo modelo
        del router (ya esta caliente, sin costo extra de VRAM) - ver
        app/validation/final_validator.py para la version que ademas
        incorpora el resultado real de las herramientas.
        """
