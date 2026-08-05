"""Smoke test end-to-end contra una instancia ya corriendo del gateway
(mock o real). Pensado para correrse tanto en desarrollo local como en
el servidor GPU recien desplegado (ver DEPLOY_PROMPT.txt).

Uso:
    python scripts/smoke_test.py --base-url http://localhost:8080 --api-key dev-local-key
"""
from __future__ import annotations

import argparse
import sys

import httpx


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--api-key", default="dev-local-key")
    args = parser.parse_args()

    headers = {"Authorization": f"Bearer {args.api_key}"}
    ok = True

    with httpx.Client(base_url=args.base_url, timeout=30.0) as client:
        r = client.get("/v1/health")
        print(f"[health] {r.status_code} {r.json()}")
        ok &= r.status_code == 200

        r = client.get("/v1/catalog", headers=headers)
        print(f"[catalog] {r.status_code} especialistas habilitados: "
              f"{[k for k, v in r.json().get('specialists', {}).items() if v.get('enabled')]}")
        ok &= r.status_code == 200

        r = client.get("/v1/hard-cases/search", headers=headers, params={"q": "argumento por defecto mutable"})
        hits = r.json().get("results", [])
        print(f"[hard-cases/search] {r.status_code} {len(hits)} resultado(s)")
        ok &= r.status_code == 200 and len(hits) > 0

        r = client.post(
            "/v1/generate",
            headers=headers,
            json={
                "task": "Escribe una funcion is_valid_email(value) que valide el formato de un email, con tests.",
                "constraints": {"quality_level": "standard"},
            },
        )
        print(f"[generate] {r.status_code}")
        if r.status_code == 200:
            body = r.json()
            print(f"  status={body['status']} iteraciones={body['trace']['correction_iterations']} "
                  f"tools={[t['tool'] + ':' + t['result'] for t in body['trace']['tools_executed']]}")
            ok &= body["status"] in ("completed", "partial")
        else:
            print(f"  body={r.text[:500]}")
            ok = False

    print()
    print("RESULTADO: " + ("OK" if ok else "FALLO"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
