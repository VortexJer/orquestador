"""Los checkpoints tocan archivos REALES del usuario. Un fallo aca no es
"gasta mas tokens": es trabajo perdido.
"""
from pathlib import Path

from groq_agent import checkpoint as cp


def _ws(tmp_path: Path) -> Path:
    (tmp_path / "index.html").write_text("<h1>v1</h1>", encoding="utf-8")
    (tmp_path / "css").mkdir()
    (tmp_path / "css" / "estilo.css").write_text("body{}", encoding="utf-8")
    return tmp_path


def test_crear_y_listar(tmp_path):
    ws = _ws(tmp_path)
    punto = cp.crear(ws, "hazme una web")
    assert punto is not None
    assert punto.n_archivos == 2
    assert [p.id for p in cp.listar(ws)] == [punto.id]


def test_un_workspace_vacio_no_genera_checkpoint(tmp_path):
    """Un checkpoint vacio no sirve para nada y ensucia la lista."""
    assert cp.crear(tmp_path, "tarea") is None
    assert cp.listar(tmp_path) == []


def test_no_copia_carpetas_de_dependencias(tmp_path):
    """Copiar node_modules haria que cada tarea empezase con segundos de
    I/O, y no es lo que nadie quiere recuperar."""
    ws = _ws(tmp_path)
    (ws / "node_modules").mkdir()
    for i in range(30):
        (ws / "node_modules" / f"f{i}.js").write_text("x", encoding="utf-8")
    (ws / ".git").mkdir()
    (ws / ".git" / "HEAD").write_text("ref", encoding="utf-8")

    punto = cp.crear(ws, "tarea")
    assert punto.n_archivos == 2, "se colaron archivos de node_modules o .git"


def test_diff_detecta_los_tres_estados(tmp_path):
    ws = _ws(tmp_path)
    cp.crear(ws, "tarea")

    (ws / "index.html").write_text("<h1>v2</h1>", encoding="utf-8")
    (ws / "nuevo.js").write_text("console.log(1)", encoding="utf-8")
    (ws / "css" / "estilo.css").unlink()

    cambios = dict((ruta, estado) for estado, ruta in cp.diff(ws))
    assert cambios["index.html"] == "modificado"
    assert cambios["nuevo.js"] == "nuevo"
    assert cambios["css/estilo.css"] == "borrado"


def test_diff_no_marca_como_modificado_lo_que_solo_cambio_de_fecha(tmp_path):
    """El agente reescribe archivos enteros: si se comparase por fecha, el
    diff saldria lleno de ruido y seria inutil."""
    ws = _ws(tmp_path)
    cp.crear(ws, "tarea")
    # Mismo contenido, escrito de nuevo (mtime cambia).
    (ws / "index.html").write_text("<h1>v1</h1>", encoding="utf-8")
    assert cp.diff(ws) == []


def test_restaurar_devuelve_el_contenido_anterior(tmp_path):
    ws = _ws(tmp_path)
    cp.crear(ws, "tarea")
    (ws / "index.html").write_text("<h1>ROTO</h1>", encoding="utf-8")

    n, nota = cp.restaurar(ws)
    assert n >= 1
    assert (ws / "index.html").read_text(encoding="utf-8") == "<h1>v1</h1>"
    assert "restaurados" in nota


def test_restaurar_crea_un_checkpoint_antes(tmp_path):
    """Deshacer tampoco puede ser irreversible."""
    ws = _ws(tmp_path)
    cp.crear(ws, "tarea")
    (ws / "index.html").write_text("<h1>v2</h1>", encoding="utf-8")

    antes = len(cp.listar(ws))
    cp.restaurar(ws)
    assert len(cp.listar(ws)) == antes + 1


def test_restaurar_no_borra_archivos_nuevos_pero_los_avisa(tmp_path):
    """Podrian ser trabajo del usuario ajeno a la tarea: borrarlos seria
    destruir algo que nadie pidio destruir."""
    ws = _ws(tmp_path)
    cp.crear(ws, "tarea")
    (ws / "mis_notas.txt").write_text("importante", encoding="utf-8")

    _, nota = cp.restaurar(ws)
    assert (ws / "mis_notas.txt").exists()
    assert "NO se borraron" in nota
    assert "mis_notas.txt" in nota


def test_restaurar_sin_checkpoints_avisa_en_vez_de_reventar(tmp_path):
    n, nota = cp.restaurar(tmp_path)
    assert n == 0
    assert "no hay checkpoints" in nota


def test_se_podan_los_viejos(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "MAX_CHECKPOINTS", 3)
    ws = _ws(tmp_path)
    for i in range(6):
        (ws / "index.html").write_text(f"<h1>v{i}</h1>", encoding="utf-8")
        cp.crear(ws, f"tarea {i}")
    assert len(cp.listar(ws)) <= 3


def test_un_fallo_de_disco_no_tumba_la_tarea(tmp_path, monkeypatch):
    """Un checkpoint es una red de seguridad: que la red falle no puede
    impedir hacer el trabajo."""
    ws = _ws(tmp_path)

    def _explota(*a, **k):
        raise OSError("disco lleno")

    monkeypatch.setattr(cp.shutil, "copy2", _explota)
    assert cp.crear(ws, "tarea") is None
