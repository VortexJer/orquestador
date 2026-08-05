"""resolver_skill: mapea lo que el modelo pide al skill REAL, para que
delegate_to_specialist no falle 3 veces por adivinar el nombre ('presentaciones'
en vez de 'office-presentation-specialist'). Red de seguridad del router."""
from groq_agent.tools import resolver_skill

_DISP = [
    "python-specialist", "office-word-specialist", "office-presentation-specialist",
    "office-spreadsheet-specialist", "office-email-specialist", "web-builder-specialist",
    "web-designer-specialist",
]


def test_nombre_exacto_pasa_tal_cual():
    assert resolver_skill("office-word-specialist", _DISP) == "office-word-specialist"


def test_dominio_corto_se_completa():
    assert resolver_skill("python", _DISP) == "python-specialist"


def test_coloquiales_de_oficina():
    assert resolver_skill("presentaciones", _DISP) == "office-presentation-specialist"
    assert resolver_skill("powerpoint", _DISP) == "office-presentation-specialist"
    assert resolver_skill("documentos", _DISP) == "office-word-specialist"
    assert resolver_skill("word", _DISP) == "office-word-specialist"
    assert resolver_skill("excel", _DISP) == "office-spreadsheet-specialist"
    assert resolver_skill("web", _DISP) == "web-builder-specialist"


def test_frase_que_contiene_el_termino():
    assert resolver_skill("una presentacion sobre el bmw", _DISP) == "office-presentation-specialist"


def test_desconocido_devuelve_none():
    assert resolver_skill("xyz", _DISP) is None
    assert resolver_skill("", _DISP) is None
