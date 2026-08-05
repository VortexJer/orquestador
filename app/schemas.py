"""Contratos Pydantic de la API. Ver arquitectura, seccion 4 (Esquema de API)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Domain = Literal[
    "python", "typescript", "javascript", "java", "csharp", "cpp",
    "sql", "rust", "go", "php", "ruby", "kotlin", "iac", "web",
    "testing", "refactor", "docs", "security",
    "office_email", "office_word", "office_spreadsheet", "office_presentation",
    "trivial_text", "code_review",
    "debugging",
    "accessibility",
    "research",
    "multi",
]

EffortLevel = Literal["quick_pass", "standard", "iterative_verify"]


class FileContext(BaseModel):
    path: str
    content: str


class TaskContext(BaseModel):
    files: list[FileContext] = Field(default_factory=list)
    project_stack: list[str] = Field(default_factory=list)


class Constraints(BaseModel):
    max_iterations: int = 3
    quality_level: Literal["draft", "standard", "high"] = "standard"
    max_latency_ms: int = 20_000
    max_cost_usd: float | None = None


class GenerateRequest(BaseModel):
    task: str
    language_hint: str | None = None
    context: TaskContext = Field(default_factory=TaskContext)
    constraints: Constraints = Field(default_factory=Constraints)
    stream: bool = False


class RoutingDecision(BaseModel):
    domain: Domain
    subtasks: list[dict] = Field(default_factory=list)
    effort_level: EffortLevel
    risk_signals: list[str] = Field(default_factory=list)
    specialists_to_invoke: list[str]
    reused_warm_model: bool = False


class ToolExecutionResult(BaseModel):
    tool: str
    result: Literal["pass", "fail", "skipped"]
    detail: str = ""
    duration_ms: int = 0


class HardCaseHit(BaseModel):
    id: str
    used: bool
    tags: list[str] = Field(default_factory=list)


class ValidationResult(BaseModel):
    passed: bool
    notes: str
    validated_by: Literal["router_model", "rule_based"] = "rule_based"


class ModelUsed(BaseModel):
    base_model: str
    specialist: str
    was_cold_start: bool = False


class Trace(BaseModel):
    router_decision: RoutingDecision
    model_used: ModelUsed
    tools_executed: list[ToolExecutionResult] = Field(default_factory=list)
    correction_iterations: int = 0
    hard_cases_consulted: list[HardCaseHit] = Field(default_factory=list)
    validation: ValidationResult


class CostInfo(BaseModel):
    gpu_seconds: float
    estimated_usd: float


class GenerateResult(BaseModel):
    content: str
    explanation: str = ""
    files_changed: list[dict] = Field(default_factory=list)


class GenerateResponse(BaseModel):
    request_id: str
    status: Literal["completed", "partial", "failed"]
    result: GenerateResult
    trace: Trace
    cost: CostInfo
