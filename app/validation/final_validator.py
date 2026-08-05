"""Validacion final. Decision de diseño confirmada (feedback del
usuario): el validador es SIEMPRE el mismo modelo del router, nunca un
tercer modelo separado - ya esta caliente, cero costo extra de VRAM.
Con HeuristicRouter (fase 1, sin LLM todavia) la validacion es
rule-based; con LLMRouter la misma llamada la resuelve el modelo.
En ambos casos el contrato de salida (ValidationResult) es identico,
asi el gateway no necesita saber cual esta activo.
"""
from __future__ import annotations

from app.router.base import Router
from app.schemas import GenerateRequest, ValidationResult


def run_final_validation(
    router: Router,
    request: GenerateRequest,
    candidate_content: str,
    tools_passed: bool,
    validated_by: str,
) -> ValidationResult:
    passed, notes = router.validate(request, candidate_content, tools_passed)
    return ValidationResult(passed=passed, notes=notes, validated_by=validated_by)
