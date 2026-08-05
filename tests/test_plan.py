"""La tool `plan`: un plan/todo VISIBLE del especialista (dividir la tarea en
pasos y marcar hecho/en curso/pendiente en pantalla, como la lista de tareas
del asistente principal). Se pidio que TODOS los profesionales planifiquen
antes de empezar."""
from groq_agent import ui
from groq_agent.skills_loader import build_tools_preamble
from groq_agent.tools import ToolExecutor, schemas_for


def _pintado(monkeypatch):
    lineas = []
    monkeypatch.setattr(ui.console, "print", lambda *a, **k: lineas.append(a[0] if a else ""))
    return lineas


def test_print_plan_dibuja_estados_y_conteo(monkeypatch):
    lineas = _pintado(monkeypatch)
    ui.print_plan([
        {"paso": "Investigar el tema", "estado": "hecho"},
        {"paso": "Escribir el index.html", "estado": "en_curso"},
        {"paso": "Verificar el render", "estado": "pendiente"},
    ])
    texto = "\n".join(lineas)
    assert "◆ plan" in texto
    assert "✓" in texto and "▶" in texto and "○" in texto
    assert "Investigar el tema" in texto and "Verificar el render" in texto
    assert "1/3 hechos" in texto


def test_print_plan_tolera_variantes_y_strings(monkeypatch):
    lineas = _pintado(monkeypatch)
    # ingles, claves alternativas, y un paso como string pelado
    ui.print_plan([
        {"step": "step A", "status": "done"},
        {"tarea": "tarea B", "estado": "in_progress"},
        "paso C en crudo",
    ])
    texto = "\n".join(lineas)
    assert "step A" in texto and "tarea B" in texto and "paso C en crudo" in texto
    assert "1/3 hechos" in texto


def test_print_plan_vacio_no_dibuja_nada(monkeypatch):
    lineas = _pintado(monkeypatch)
    ui.print_plan([])
    assert lineas == []


def test_executor_plan_guarda_y_resume(tmp_path):
    ex = ToolExecutor(tmp_path, auto_yes=True)
    r = ex.plan([{"paso": "a", "estado": "hecho"}, {"paso": "b", "estado": "pendiente"}])
    assert "1/2" in r
    assert len(ex.plan_actual) == 2


def test_plan_esta_en_el_schema_y_en_core_para_todos():
    # un especialista cualquiera del config recibe `plan` (esta en core)
    nombres = {t["function"]["name"] for t in schemas_for("python-specialist")}
    assert "plan" in nombres
    # y hasta el mas restringido (generalist-tiny, solo core) lo tiene
    assert "plan" in {t["function"]["name"] for t in schemas_for("generalist-tiny")}


def test_el_prompt_pide_planificar_si_tiene_la_tool():
    con = build_tools_preamble({"plan", "write_file"})
    sin = build_tools_preamble({"write_file"})
    assert "PLANIFICA ANTES DE EMPEZAR" in con
    assert "PLANIFICA ANTES DE EMPEZAR" not in sin
