"""Chequeo de estructura HTML para el especialista web-builder - el
equivalente de docx_tool.check_docx pero para paginas web. Ver
skills/web-builder-specialist.md y hard_cases/by_domain/web.json.
"""
from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup

_HEADING_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6"]


def lint_html(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    issues: list[str] = []

    html_tag = soup.find("html")
    if not html_tag or not html_tag.get("lang"):
        issues.append("Falta el atributo lang en <html> (ej. lang=\"es\").")

    if not soup.find("meta", attrs={"name": "viewport"}):
        issues.append("Falta <meta name=\"viewport\"> - la pagina no sera responsive en movil.")

    title = soup.find("title")
    if not title or not title.get_text(strip=True):
        issues.append("Falta un <title> con contenido especifico.")

    description = soup.find("meta", attrs={"name": "description"})
    if not description or not description.get("content", "").strip():
        issues.append("Falta <meta name=\"description\"> con contenido especifico.")

    images_without_alt = [
        img.get("src", "?") for img in soup.find_all("img") if img.get("alt") is None
    ]
    if images_without_alt:
        issues.append(f"Imagenes sin atributo alt: {images_without_alt[:5]}")

    heading_levels = [int(tag.name[1]) for tag in soup.find_all(_HEADING_TAGS)]
    h1_count = heading_levels.count(1)
    if h1_count == 0:
        issues.append("No hay ningun <h1> en la pagina.")
    elif h1_count > 1:
        issues.append(f"Hay {h1_count} <h1> - deberia haber exactamente uno por pagina.")

    previous = 0
    for level in heading_levels:
        if level > previous + 1:
            issues.append(f"Salto de nivel de encabezado: de H{previous} a H{level} sin nivel intermedio.")
        previous = max(previous, level) if level <= previous + 1 else level

    return {"passed": not issues, "issues": issues}


def lint_html_file(path: Path) -> dict:
    return lint_html(Path(path).read_text(encoding="utf-8"))
