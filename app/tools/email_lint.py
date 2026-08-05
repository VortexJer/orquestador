"""Chequeo heuristico de forma (no de correctitud semantica) para el
especialista office-email - ver skills/office-email-specialist.md y
hard_cases/by_domain/office_email.json. No hay compilador para un
correo: esto detecta artefactos de plantilla sin completar, asuntos
genericos y ausencia de saludo/cierre.
"""
from __future__ import annotations

import re

_PLACEHOLDER_PATTERN = re.compile(r"\[[A-ZÁÉÍÓÚÑ_ ]{2,}\]|\{\{\s*\w+\s*\}\}")
_GENERIC_SUBJECTS = {"info", "actualizacion", "actualización", "update", "hola", "consulta"}
_GREETING_PATTERN = re.compile(r"^(hola|estimad[oa]s?|buen[oa]s? d[ií]as|buenas tardes)", re.I)
_SIGNOFF_PATTERN = re.compile(r"(saludos|atentamente|gracias|un abrazo|cordialmente)\s*,?\s*$", re.I | re.M)


def lint_email(subject: str, body: str) -> dict:
    issues: list[str] = []

    if not subject.strip():
        issues.append("Falta el asunto.")
    elif subject.strip().lower() in _GENERIC_SUBJECTS:
        issues.append(f"El asunto '{subject}' es demasiado generico - deberia mencionar el tema/accion concreta.")

    placeholders = _PLACEHOLDER_PATTERN.findall(subject + "\n" + body)
    if placeholders:
        issues.append(f"Quedaron placeholders de plantilla sin completar: {placeholders}")

    if not _GREETING_PATTERN.search(body.strip()):
        issues.append("El cuerpo no parece tener un saludo inicial claro.")

    if not _SIGNOFF_PATTERN.search(body.strip()):
        issues.append("El cuerpo no parece tener un cierre/firma claro.")

    word_count = len(body.split())
    if word_count > 400:
        issues.append(f"El correo tiene {word_count} palabras - considera acortarlo o resumir el contexto.")

    return {"passed": not issues, "issues": issues}
