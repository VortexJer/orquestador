"""Herramientas para el especialista office-presentation: generar un
.pptx con DISEÑO REAL - tema de color, tipografia, y sobre todo VARIEDAD de
diapositivas (portada, seccion, bullets, comparacion, cifra grande, cita,
imagen a sangre, tabla) - en vez de la misma diapositiva plana repetida.

POR QUE UNA BIBLIOTECA DE TEMAS + LAYOUTS PROPIA
No hay una base de datos/API gratis y decente de PLANTILLAS .pptx para
descargar (solo librerias y servicios de pago). Asi que el "banco de
plantillas" se hace aqui: THEMES (paletas curadas) x tipos de diapositiva
(`kind`) compuestos a mano. Antes esto era titulo+bullets siempre: salia
PLANO. Ademas se incrustan fotos (search_images) SIN repetir la misma.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TypedDict

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

from app.tools.image_fetch import ImagenNoValida, descargar_imagen

MAX_BULLETS_RECOMENDADO = 6
_EMU_IN = 914400

# BANCO DE TEMAS: paletas con caracter (nada de violeta/cristal generico).
# 'panel' es para fondos suaves (tablas); (r,g,b).
THEMES: dict[str, dict] = {
    "medianoche":  {"bg": (0x0F, 0x14, 0x1A), "titulo": (0xFF, 0xFF, 0xFF), "cuerpo": (0xC9, 0xCD, 0xD6), "acento": (0xE0, 0xA6, 0x3A), "panel": (0x1A, 0x21, 0x2B)},
    "pizarra":     {"bg": (0x13, 0x1A, 0x1E), "titulo": (0xF2, 0xF5, 0xF4), "cuerpo": (0xB9, 0xC3, 0xC4), "acento": (0x2D, 0xBF, 0xA6), "panel": (0x1D, 0x26, 0x2A)},
    "profundo":    {"bg": (0x1A, 0x12, 0x18), "titulo": (0xFF, 0xFF, 0xFF), "cuerpo": (0xD6, 0xC9, 0xCE), "acento": (0xE8, 0x6A, 0x5C), "panel": (0x27, 0x1C, 0x23)},
    "bosque":      {"bg": (0x10, 0x1A, 0x14), "titulo": (0xF3, 0xF7, 0xF2), "cuerpo": (0xBF, 0xCC, 0xC2), "acento": (0xD8, 0xA6, 0x4A), "panel": (0x1A, 0x26, 0x1E)},
    "editorial":   {"bg": (0xF7, 0xF4, 0xEE), "titulo": (0x1A, 0x18, 0x14), "cuerpo": (0x3A, 0x36, 0x30), "acento": (0xC0, 0x39, 0x2B), "panel": (0xEC, 0xE6, 0xDC)},
    "corporativo": {"bg": (0xFF, 0xFF, 0xFF), "titulo": (0x0E, 0x2A, 0x47), "cuerpo": (0x33, 0x3A, 0x42), "acento": (0x1E, 0x6F, 0xD6), "panel": (0xEE, 0xF3, 0xF9)},
}
_TEMA_DEFECTO = "medianoche"
_KINDS = ("cover", "section", "bullets", "two_column", "stat", "quote", "image", "table")
_MD_INLINE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__|\*(.+?)\*|_(.+?)_", re.DOTALL)


class SlideSpec(TypedDict, total=False):
    kind: str
    title: str
    subtitle: str
    bullets: list[str]
    left_title: str
    left_bullets: list[str]
    right_title: str
    right_bullets: list[str]
    stat: str
    caption: str
    quote: str
    author: str
    image: str
    table: dict
    speaker_notes: str


def _rgb(t: tuple) -> RGBColor:
    return RGBColor(*t)


def _sin_md(texto: str) -> str:
    return _MD_INLINE.sub(lambda m: m.group(1) or m.group(2) or m.group(3) or m.group(4), str(texto))


def tema_valido(nombre: str | None) -> str:
    return nombre if nombre in THEMES else _TEMA_DEFECTO


def _kind_valido(k: str | None) -> str:
    return k if k in _KINDS else "bullets"


def _caja(slide, left, top, width, height, anchor=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    return box


def _linea(tf, texto, size, color, bold=False, italic=False, align=PP_ALIGN.LEFT, space_after=6, first=False):
    p = tf.paragraphs[0] if first else tf.add_paragraph()
    p.alignment = align
    p.space_after = Pt(space_after)
    run = p.add_run()
    run.text = _sin_md(texto)
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = _rgb(color)
    return p


def _rect(slide, left, top, width, height, color):
    shp = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(left), Inches(top), Inches(width), Inches(height))
    shp.fill.solid()
    shp.fill.fore_color.rgb = _rgb(color)
    shp.line.fill.background()
    shp.shadow.inherit = False
    return shp


def _titulo(slide, texto, t, top=0.5, size=32, con_barra=True):
    """Titulo de contenido (arriba) + barra de acento. Usa el placeholder de
    titulo (para que check_presentation lo vea) reposicionado."""
    ph = slide.shapes.title
    ph.text = _sin_md(texto)
    ph.left, ph.top, ph.width, ph.height = Inches(0.6), Inches(top), Inches(8.8), Inches(0.9)
    p = ph.text_frame.paragraphs[0]
    p.alignment = PP_ALIGN.LEFT
    for run in p.runs:
        run.font.size = Pt(size)
        run.font.bold = True
        run.font.color.rgb = _rgb(t["titulo"])
    if con_barra:
        _rect(slide, 0.62, top + 0.92, 1.9, Pt(4) / _EMU_IN, t["acento"])


def _imagen(slide, url: str, left, top, width) -> bool:
    try:
        data = descargar_imagen(url)
    except ImagenNoValida:
        return False
    slide.shapes.add_picture(data, Inches(left), Inches(top), width=Inches(width))
    return True


# --- renderers por tipo de diapositiva ---------------------------------

def _r_cover(slide, s, t):
    _rect(slide, 0.0, 0.0, 0.25, 7.5, t["acento"])  # franja lateral
    ph = slide.shapes.title
    ph.text = _sin_md(s.get("title", ""))
    ph.left, ph.top, ph.width, ph.height = Inches(0.9), Inches(2.5), Inches(8.4), Inches(1.8)
    p = ph.text_frame.paragraphs[0]
    for run in p.runs:
        run.font.size = Pt(46)
        run.font.bold = True
        run.font.color.rgb = _rgb(t["titulo"])
    if s.get("subtitle"):
        box = _caja(slide, 0.95, 4.35, 8.2, 1.2)
        _linea(box.text_frame, s["subtitle"], 20, t["cuerpo"], first=True)


def _r_section(slide, s, t):
    slide.shapes.title.text = _sin_md(s.get("title", ""))  # para el checker
    slide.shapes.title.left, slide.shapes.title.top = Inches(-3), Inches(-3)  # fuera de vista
    _rect(slide, 0.0, 3.05, 10.0, 0.06, t["acento"])
    if s.get("subtitle"):
        eb = _caja(slide, 0.9, 2.35, 8.2, 0.5)
        _linea(eb.text_frame, s["subtitle"].upper(), 14, t["acento"], bold=True, first=True)
    box = _caja(slide, 0.9, 3.25, 8.2, 1.6)
    _linea(box.text_frame, s.get("title", ""), 40, t["titulo"], bold=True, first=True)


def _r_bullets(slide, s, t):
    _titulo(slide, s.get("title", ""), t)
    tiene_img = bool(s.get("_image_ok"))
    ancho = 4.9 if tiene_img else 8.8
    box = _caja(slide, 0.6, 1.95, ancho, 4.8)
    for i, b in enumerate(s.get("bullets", [])):
        _linea(box.text_frame, f"•  {b}", 18, t["cuerpo"], space_after=10, first=(i == 0))
    if tiene_img:
        _imagen(slide, s["image"], 5.8, 1.95, 3.9)


def _r_two_column(slide, s, t):
    _titulo(slide, s.get("title", ""), t)
    _rect(slide, 4.98, 2.05, 0.03, 4.4, t["acento"])  # divisoria
    for lado, x in (("left", 0.6), ("right", 5.3)):
        box = _caja(slide, x, 1.95, 4.0, 4.8)
        tit = s.get(f"{lado}_title")
        if tit:
            _linea(box.text_frame, tit, 20, t["acento"], bold=True, space_after=10, first=True)
        for i, b in enumerate(s.get(f"{lado}_bullets", [])):
            _linea(box.text_frame, f"•  {b}", 16, t["cuerpo"], space_after=8, first=(not tit and i == 0))


def _r_stat(slide, s, t):
    _titulo(slide, s.get("title", ""), t)
    box = _caja(slide, 0.6, 2.6, 8.8, 2.6, anchor=MSO_ANCHOR.MIDDLE)
    _linea(box.text_frame, s.get("stat", ""), 96, t["acento"], bold=True, align=PP_ALIGN.CENTER, first=True)
    if s.get("caption"):
        _linea(box.text_frame, s["caption"], 20, t["cuerpo"], align=PP_ALIGN.CENTER, space_after=0)


def _r_quote(slide, s, t):
    slide.shapes.title.text = _sin_md(s.get("title") or "Cita")
    slide.shapes.title.left, slide.shapes.title.top = Inches(-3), Inches(-3)
    _linea(_caja(slide, 0.9, 1.6, 2.0, 1.4).text_frame, "“", 96, t["acento"], bold=True, first=True)
    box = _caja(slide, 1.0, 2.7, 8.0, 3.0, anchor=MSO_ANCHOR.MIDDLE)
    _linea(box.text_frame, s.get("quote", ""), 26, t["titulo"], italic=True, first=True)
    if s.get("author"):
        _linea(box.text_frame, f"— {s['author']}", 18, t["cuerpo"], space_after=0)


def _r_image(slide, s, t):
    _titulo(slide, s.get("title", ""), t)
    ok = _imagen(slide, s.get("image", ""), 1.6, 2.0, 6.8) if s.get("_image_ok") else False
    if not ok:
        _linea(_caja(slide, 0.6, 3.2, 8.8, 1.0).text_frame, s.get("caption", "(imagen no disponible)"), 18, t["cuerpo"], first=True)
        return
    if s.get("caption"):
        cap = _caja(slide, 0.6, 6.55, 8.8, 0.6)
        _linea(cap.text_frame, s["caption"], 13, t["cuerpo"], italic=True, align=PP_ALIGN.CENTER, first=True)


def _r_table(slide, s, t):
    _titulo(slide, s.get("title", ""), t)
    spec = s.get("table") or {}
    headers = [_sin_md(h) for h in spec.get("headers", [])]
    filas = [[_sin_md(c) for c in row] for row in spec.get("rows", [])]
    if not headers:
        return
    n = 1 + len(filas)
    gfx = slide.shapes.add_table(n, len(headers), Inches(0.6), Inches(2.0), Inches(8.8), Inches(min(0.55 * n, 4.6)))
    tabla = gfx.table
    tabla.first_row = False  # se estiliza a mano (el estilo por defecto choca con el tema)
    for c, h in enumerate(headers):
        _celda(tabla.cell(0, c), h, t["acento"], (0xFF, 0xFF, 0xFF), bold=True)
    for r, row in enumerate(filas, start=1):
        panel = t["panel"] if r % 2 else t["bg"]
        for c in range(len(headers)):
            val = row[c] if c < len(row) else ""
            _celda(tabla.cell(r, c), val, panel, t["cuerpo"])


def _celda(cell, texto, fondo, color, bold=False):
    cell.fill.solid()
    cell.fill.fore_color.rgb = _rgb(fondo)
    cell.margin_left = cell.margin_right = Inches(0.12)
    tf = cell.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    run = p.add_run()
    run.text = texto
    run.font.size = Pt(13)
    run.font.bold = bold
    run.font.color.rgb = _rgb(color)


_RENDERERS = {
    "cover": _r_cover, "section": _r_section, "bullets": _r_bullets,
    "two_column": _r_two_column, "stat": _r_stat, "quote": _r_quote,
    "image": _r_image, "table": _r_table,
}


def create_presentation(slides: list[SlideSpec], output_path: Path, tema: str = _TEMA_DEFECTO) -> Path:
    prs = Presentation()
    layout = prs.slide_layouts[5]  # "Title Only": trae placeholder de titulo (lo ve el checker)
    t = THEMES[tema_valido(tema)]
    fotos_usadas: set[str] = set()

    for spec in slides:
        s = dict(spec)
        kind = _kind_valido(s.get("kind"))
        # Dedupe de fotos: la misma imagen repetida "queda fatal". Si la URL ya
        # se uso, esta diapositiva va sin foto (el kind se adapta).
        url = s.get("image")
        s["_image_ok"] = bool(url) and url not in fotos_usadas
        if s["_image_ok"]:
            fotos_usadas.add(url)

        slide = prs.slides.add_slide(layout)
        _pintar_fondo(slide, t)
        _RENDERERS[kind](slide, s, t)

        if s.get("speaker_notes"):
            slide.notes_slide.notes_text_frame.text = s["speaker_notes"]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(output_path)
    return output_path


def _pintar_fondo(slide, t) -> None:
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = _rgb(t["bg"])


def check_presentation(path: Path) -> dict:
    """Validador de estructura. Ver checklist de
    skills/office-presentation-specialist.md."""
    prs = Presentation(path)
    issues: list[str] = []

    if not prs.slides:
        issues.append("La presentacion no tiene ninguna diapositiva.")

    for idx, slide in enumerate(prs.slides, start=1):
        title_shape = slide.shapes.title
        tiene_titulo = title_shape is not None and title_shape.text.strip()
        # Hay texto visible en alguna caja (una portada/cifra/cita puede tener
        # el titulo fuera de vista a proposito): eso tambien cuenta como que la
        # diapositiva no esta vacia.
        hay_texto = any(
            sh.has_text_frame and sh != title_shape and sh.text_frame.text.strip()
            for sh in slide.shapes
        )
        if not tiene_titulo and not hay_texto:
            issues.append(f"Diapositiva {idx} no tiene ni titulo ni contenido.")

        # El limite de 6 es POR lista (una columna), no por diapositiva: una
        # comparacion a dos columnas suma mas y es correcto.
        max_por_caja = 0
        for shape in slide.shapes:
            if shape.has_text_frame and shape != title_shape:
                n = sum(1 for p in shape.text_frame.paragraphs if p.text.strip())
                max_por_caja = max(max_por_caja, n)
        if max_por_caja > MAX_BULLETS_RECOMENDADO:
            issues.append(
                f"Diapositiva {idx}: una lista tiene {max_por_caja} bullets (recomendado <= {MAX_BULLETS_RECOMENDADO})."
            )

    return {"passed": not issues, "issues": issues}
