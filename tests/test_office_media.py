"""Fotos e imagenes en documentos/presentaciones y el banco de temas .pptx.
Offline: la descarga se sustituye por un BytesIO en memoria (no red)."""
from io import BytesIO

import pytest
from docx import Document
from PIL import Image
from pptx import Presentation

from app.tools import docx_tool, pptx_tool
from app.tools.image_fetch import ImagenNoValida, descargar_imagen


def _png_bytes():
    b = BytesIO()
    Image.new("RGB", (320, 240), (90, 120, 180)).save(b, format="JPEG")
    b.seek(0)
    return b


# --- descargador seguro -------------------------------------------------

def test_image_fetch_rechaza_no_https():
    with pytest.raises(ImagenNoValida):
        descargar_imagen("http://ejemplo.com/foto.jpg")
    with pytest.raises(ImagenNoValida):
        descargar_imagen("ftp://x/y.png")


# --- docx: incrustar foto ----------------------------------------------

def test_docx_incrusta_la_foto_con_pie_y_alt(monkeypatch, tmp_path):
    monkeypatch.setattr(docx_tool, "descargar_imagen", lambda url, **k: _png_bytes())
    out = docx_tool.create_docx([
        {"heading": "BMW M5 F90", "level": 1},
        {"image": "https://x/foto.jpg", "caption": "El M5 en pista"},
    ], tmp_path / "img.docx")
    doc = Document(out)
    assert len(doc.inline_shapes) == 1                    # la foto quedo incrustada
    assert "El M5 en pista" in "\n".join(p.text for p in doc.paragraphs)  # pie visible
    assert doc.inline_shapes[0]._inline.docPr.get("descr") == "El M5 en pista"  # alt-text


def test_docx_una_foto_caida_no_rompe_el_documento(monkeypatch, tmp_path):
    def cae(url, **k):
        raise ImagenNoValida("404")
    monkeypatch.setattr(docx_tool, "descargar_imagen", cae)
    out = docx_tool.create_docx([
        {"text": "Antes."},
        {"image": "https://x/rota.jpg", "caption": "no carga"},
        {"text": "Despues."},
    ], tmp_path / "rota.docx")
    doc = Document(out)
    texto = "\n".join(p.text for p in doc.paragraphs)
    assert "Antes." in texto and "Despues." in texto      # el doc se completa igual
    assert "imagen no disponible" in texto                # con una nota discreta
    assert not doc.inline_shapes                          # y sin foto


# --- pptx: banco de temas + foto ---------------------------------------

def test_pptx_tema_invalido_cae_al_de_defecto():
    assert pptx_tool.tema_valido("no-existe") == "medianoche"
    assert pptx_tool.tema_valido(None) == "medianoche"
    assert pptx_tool.tema_valido("corporativo") == "corporativo"


@pytest.mark.parametrize("tema,bg", [
    ("corporativo", "FFFFFF"),
    ("profundo", "1A1218"),
    ("medianoche", "0F141A"),
])
def test_pptx_aplica_el_color_de_fondo_del_tema(tmp_path, tema, bg):
    out = pptx_tool.create_presentation(
        [{"title": "T", "bullets": ["a"], "speaker_notes": "n"}], tmp_path / f"{tema}.pptx", tema=tema)
    prs = Presentation(out)
    assert str(prs.slides[0].background.fill.fore_color.rgb) == bg


def test_pptx_no_repite_la_misma_foto(monkeypatch, tmp_path):
    """Repetir la misma foto 'queda fatal': la 2a aparicion de una URL se salta
    (no se descarga ni se incrusta)."""
    descargas = []
    def fake(url, **k):
        descargas.append(url)
        return _png_bytes()
    monkeypatch.setattr(pptx_tool, "descargar_imagen", fake)
    out = pptx_tool.create_presentation([
        {"kind": "image", "title": "A", "image": "https://x/misma.jpg"},
        {"kind": "image", "title": "B", "image": "https://x/misma.jpg"},   # repetida
        {"kind": "image", "title": "C", "image": "https://x/otra.jpg"},
    ], tmp_path / "dedup.pptx")
    assert descargas == ["https://x/misma.jpg", "https://x/otra.jpg"]      # la repetida no se baja
    prs = Presentation(out)
    imgs = sum(1 for sl in prs.slides for sh in sl.shapes if sh.shape_type == 13)
    assert imgs == 2


def test_pptx_todos_los_kinds_generan_y_pasan_el_chequeo(monkeypatch, tmp_path):
    monkeypatch.setattr(pptx_tool, "descargar_imagen", lambda url, **k: _png_bytes())
    slides = [
        {"kind": "cover", "title": "T", "subtitle": "sub"},
        {"kind": "section", "title": "Parte 1", "subtitle": "intro"},
        {"kind": "bullets", "title": "B", "bullets": ["a", "b"]},
        {"kind": "two_column", "title": "C", "left_title": "P", "left_bullets": ["x"], "right_title": "Q", "right_bullets": ["y"]},
        {"kind": "stat", "title": "S", "stat": "625 CV", "caption": "pot"},
        {"kind": "quote", "title": "Q", "quote": "cita", "author": "autor"},
        {"kind": "image", "title": "I", "image": "https://x/f.jpg", "caption": "pie"},
        {"kind": "table", "title": "Ficha", "table": {"headers": ["D", "V"], "rows": [["Motor", "S63"]]}},
    ]
    out = pptx_tool.create_presentation(slides, tmp_path / "kinds.pptx", tema="corporativo")
    prs = Presentation(out)
    assert len(prs.slides._sldIdLst) == 8
    assert any(sh.has_table for sl in prs.slides for sh in sl.shapes)      # la tabla existe
    assert pptx_tool.check_presentation(out)["passed"]


def test_pptx_kind_invalido_cae_a_bullets(monkeypatch, tmp_path):
    out = pptx_tool.create_presentation(
        [{"kind": "xyz", "title": "T", "bullets": ["a"]}], tmp_path / "k.pptx")
    assert pptx_tool.check_presentation(out)["passed"]


def test_pptx_incrusta_la_foto_y_pasa_el_chequeo(monkeypatch, tmp_path):
    monkeypatch.setattr(pptx_tool, "descargar_imagen", lambda url, **k: _png_bytes())
    out = pptx_tool.create_presentation([
        {"title": "BMW M5", "bullets": ["**V8** biturbo", "625 CV"], "speaker_notes": "n", "image": "https://x/f.jpg"},
    ], tmp_path / "foto.pptx", tema="profundo")
    prs = Presentation(out)
    imgs = sum(1 for sh in prs.slides[0].shapes if sh.shape_type == 13)  # 13 = PICTURE
    assert imgs == 1
    assert pptx_tool.check_presentation(out)["passed"]
    # el markdown del bullet no sale con asteriscos
    textos = " ".join(p.text for sh in prs.slides[0].shapes if sh.has_text_frame for p in sh.text_frame.paragraphs)
    assert "**" not in textos
