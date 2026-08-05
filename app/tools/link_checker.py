"""Chequeo de links rotos - el "comprobador" del especialista
web-builder antes de dar un sitio por terminado. HEAD primero (mas
barato); si el servidor no lo soporta bien (405/501, algunos CDNs), cae
a GET.
"""
from __future__ import annotations

import httpx

_USER_AGENT = "Mozilla/5.0 (compatible; OrquestadorWebBuilder/1.0; +local-testing)"
_TIMEOUT_S = 10.0


def _check_one(client: httpx.Client, url: str) -> dict:
    try:
        response = client.head(url)
        if response.status_code in (405, 501):
            response = client.get(url)
        return {"url": url, "status": response.status_code, "ok": response.status_code < 400, "error": None}
    except httpx.HTTPError as exc:
        return {"url": url, "status": None, "ok": False, "error": str(exc)}


def check_links(urls: list[str], max_links: int = 50) -> list[dict]:
    results = []
    with httpx.Client(headers={"User-Agent": _USER_AGENT}, timeout=_TIMEOUT_S, follow_redirects=True) as client:
        for url in urls[:max_links]:
            results.append(_check_one(client, url))
    return results


def summarize(results: list[dict]) -> dict:
    broken = [r for r in results if not r["ok"]]
    return {
        "total": len(results),
        "broken_count": len(broken),
        "broken": broken,
        "passed": not broken,
    }
