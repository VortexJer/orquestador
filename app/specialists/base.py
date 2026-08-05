from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.schemas import GenerateRequest, GenerateResult, HardCaseHit, RoutingDecision, ToolExecutionResult


@dataclass
class SpecialistRun:
    result: GenerateResult
    tools_executed: list[ToolExecutionResult] = field(default_factory=list)
    correction_iterations: int = 0
    hard_cases_consulted: list[HardCaseHit] = field(default_factory=list)
    tools_passed: bool = True


class Specialist(ABC):
    name: str

    @abstractmethod
    def run(self, request: GenerateRequest, routing: RoutingDecision) -> SpecialistRun:
        ...
