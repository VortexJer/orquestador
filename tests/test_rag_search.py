from app.rag.query import search_hard_cases


def test_search_finds_mutable_default_arg_case():
    results = search_hard_cases(
        "argumento por defecto mutable en funcion python que persiste entre llamadas",
        domain="python",
        limit=3,
    )
    assert any(r["id"] == "python-mutable-default-arg" for r in results)


def test_search_respects_domain_filter():
    results = search_hard_cases("problema de concurrencia", domain="rust", limit=5)
    assert all(r["language_or_domain"] == "rust" for r in results)
