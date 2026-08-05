from app.router.heuristic_router import HeuristicRouter
from app.schemas import GenerateRequest

router = HeuristicRouter()


def test_python_domain_routes_to_python_specialist():
    req = GenerateRequest(task="Escribe una funcion def suma(a, b) que sume dos numeros.")
    decision = router.route(req, None)
    assert decision.domain == "python"
    assert decision.specialists_to_invoke == ["python-specialist"]


def test_disabled_specialist_falls_back_to_python_with_signal():
    req = GenerateRequest(task="Escribe una query SELECT * FROM ventas;")
    decision = router.route(req, None)
    assert decision.domain == "sql"
    assert decision.specialists_to_invoke == ["python-specialist"]
    assert any(sig.startswith("domain_specialist_unavailable") for sig in decision.risk_signals)


def test_office_email_falls_back_to_generalist_tiny():
    req = GenerateRequest(task="Redacta un correo corto avisando que voy a llegar tarde.")
    decision = router.route(req, None)
    assert decision.domain == "office_email"
    # office-email-specialist esta deshabilitado en fase 1: degrada a generalist-tiny, no a python-specialist
    assert decision.specialists_to_invoke == ["generalist-tiny"]


def test_generic_summary_routes_to_trivial_text():
    req = GenerateRequest(task="Resume el texto que sigue en tres bullets para mi jefe.")
    decision = router.route(req, None)
    assert decision.domain == "trivial_text"
    assert decision.specialists_to_invoke == ["generalist-tiny"]


def test_security_priority_over_sql():
    req = GenerateRequest(
        task="Esta consulta SELECT * FROM usuarios tiene un problema de inyeccion sql, arreglala."
    )
    decision = router.route(req, None)
    assert decision.domain == "security"
