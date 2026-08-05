from app.inference.mock_client import MockInferenceClient
from app.schemas import GenerateRequest, RoutingDecision
from app.specialists.python_specialist import PythonSpecialist


def test_specialist_iterates_and_converges_on_real_tool_feedback():
    specialist = PythonSpecialist(MockInferenceClient())
    request = GenerateRequest(
        task="Escribe is_valid_email(value) que valide el formato de un email, con tests."
    )
    routing = RoutingDecision(
        domain="python",
        effort_level="standard",
        specialists_to_invoke=["python-specialist"],
    )

    run = specialist.run(request, routing)

    assert run.tools_passed is True
    # El mock devuelve, a proposito, un primer intento con un import sin
    # usar (falla ruff) y recien en el segundo converge - esto prueba que
    # el ciclo generar->validar->corregir realmente usa el error real.
    assert run.correction_iterations >= 1
    tool_names = [t.tool for t in run.tools_executed]
    assert "ruff" in tool_names
    assert "pytest" in tool_names
    assert run.tools_executed[0].result == "fail"
