"""La tool check_contrast: ratio WCAG exacto para el especialista de a11y.

Motivo (visto en el afinado): accessibility-specialist dejaba `#bbb` sobre
`#ccc` sin arreglar porque NO tenia forma de calcular el contraste - lo
estimaba a ojo y salia del paso con "asegurate de 4.5:1". La tool le da el
numero. Es un caso de "el resultado flojea por FALTA de herramienta", no por
el skill.
"""
from groq_agent.tools import _a_rgb, _check_contrast


def test_negro_sobre_blanco_es_el_maximo():
    out = _check_contrast("#000000", "#ffffff")
    assert "21.0:1" in out or "21:1" in out
    assert "AAA texto normal (>=7:1): CUMPLE" in out


def test_grises_claros_parecidos_fallan():
    # el caso real: #bbb sobre #ccc
    out = _check_contrast("#bbbbbb", "#cccccc")
    assert "1.2:1" in out
    assert "AA texto normal (>=4.5:1): NO cumple" in out
    assert "Arreglo:" in out


def test_borde_AA_normal():
    # #767676 sobre blanco ~ 4.54:1: el minimo que pasa AA normal
    out = _check_contrast("#767676", "#ffffff")
    assert "AA texto normal (>=4.5:1): CUMPLE" in out


def test_acepta_hex_corto_y_nombres_css():
    assert _a_rgb("#bbb") == (187, 187, 187)
    assert _a_rgb("white") == (255, 255, 255)
    assert _a_rgb("BLACK") == (0, 0, 0)
    # el orden texto/fondo no cambia el ratio
    a = _check_contrast("#fff", "#000")
    b = _check_contrast("#000", "#fff")
    assert a.split(":1")[0].split()[-1] == b.split(":1")[0].split()[-1]


def test_color_invalido_da_error_claro():
    out = _check_contrast("azulado", "#fff")
    assert out.startswith("ERROR")
    assert "azulado" in out
