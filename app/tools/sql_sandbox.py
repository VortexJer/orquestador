"""Sandbox de ejecucion para el especialista SQL: crea un esquema y unos
datos de prueba en una base SQLite EN MEMORIA, corre la query y devuelve
las filas reales y el plan de ejecucion (`EXPLAIN QUERY PLAN`).

Existe porque en single-shot el especialista SQL FABRICABA el resultado
de EXPLAIN ("el plan usa un indice...") sin ejecutar nada, y devolvia
queries que parecen correctas pero cuentan otra cosa (un `MAX(ranking)`
que en realidad es el numero de filas, un `GROUP BY` que colapsa y pierde
el valor por fila). Ejecutar la query contra datos reales convierte esas
dudas en un numero que se puede mirar.

LIMITE HONESTO: el motor es SQLite, no Postgres/MySQL. La mayoria del SQL
estandar (JOIN, GROUP BY, funciones de ventana, CTEs) corre igual, pero
sintaxis especifica de otro motor (ej. `EXTRACT`, tipos `SERIAL`,
`::cast`) dara un error de SQLite - ese error tambien es informacion util
(la query no es portable), no un fallo de la tool. `EXPLAIN QUERY PLAN`
es el de SQLite: sirve para ver si hay SCAN vs SEARCH (uso de indice),
no es identico al plan de Postgres.
"""
from __future__ import annotations

import sqlite3

# Topes para que el resultado no llene el contexto: una verificacion se
# mira para confirmar la forma del resultado, no para volcar una tabla.
_MAX_FILAS = 50
_MAX_SENTENCIAS_SETUP = 200


def run_sql(
    query: str,
    schema: str = "",
    seed: str = "",
) -> dict:
    """Ejecuta `query` sobre una SQLite en memoria montada con `schema`
    (CREATE TABLE...) y `seed` (INSERT...).

    - `schema`: DDL para crear las tablas (una o varias sentencias).
    - `seed`: filas de prueba (INSERT...). Opcional pero MUY recomendable:
      sin datos, una query de agregacion no revela si cuenta bien.
    - `query`: la consulta a verificar (una sola sentencia SELECT).

    Devuelve {ok, columnas, filas, num_filas, plan, error}.
    """
    if not query or not query.strip():
        return {"ok": False, "error": "No se paso ninguna query."}

    conn = sqlite3.connect(":memory:")
    try:
        cur = conn.cursor()
        # El setup puede traer varias sentencias (varias tablas, muchos
        # INSERT): executescript las corre todas. Va en su propio try para
        # distinguir "el esquema/datos estan mal" de "la query esta mal".
        for etiqueta, bloque in (("schema", schema), ("seed", seed)):
            if bloque and bloque.strip():
                if bloque.count(";") > _MAX_SENTENCIAS_SETUP:
                    return {
                        "ok": False,
                        "error": f"Demasiadas sentencias en {etiqueta} "
                        f"(>{_MAX_SENTENCIAS_SETUP}); usa un dataset de prueba pequeño.",
                    }
                try:
                    cur.executescript(bloque)
                except sqlite3.Error as exc:
                    return {"ok": False, "error": f"Error montando {etiqueta}: {exc}"}

        # Plan de ejecucion (no ejecuta la query, solo la planifica).
        plan_texto = ""
        try:
            plan_filas = cur.execute("EXPLAIN QUERY PLAN " + query).fetchall()
            plan_texto = "\n".join(str(f[-1]) for f in plan_filas)
        except sqlite3.Error as exc:
            # Si el plan falla suele ser un error de sintaxis que tambien
            # hara fallar la ejecucion - se reporta abajo con mas detalle.
            plan_texto = f"(no se pudo obtener el plan: {exc})"

        try:
            cur.execute(query)
        except sqlite3.Error as exc:
            return {"ok": False, "error": f"Error ejecutando la query: {exc}", "plan": plan_texto}

        columnas = [d[0] for d in cur.description] if cur.description else []
        filas = cur.fetchmany(_MAX_FILAS + 1)
        truncado = len(filas) > _MAX_FILAS
        filas = filas[:_MAX_FILAS]

        # Marca si el plan hace un SCAN de tabla completa (sin indice), que
        # es la señal que el especialista suele afirmar sin verificar.
        usa_scan = "SCAN" in plan_texto.upper()

        return {
            "ok": True,
            "columnas": columnas,
            "filas": [list(f) for f in filas],
            "num_filas": len(filas),
            "truncado": truncado,
            "plan": plan_texto,
            "aviso_plan": (
                "El plan incluye un SCAN de tabla completa (no usa indice). "
                "Si esperabas uso de indice, revisa el WHERE/JOIN o crea el indice."
                if usa_scan
                else "El plan no hace SCAN completo (usa SEARCH/indice donde aplica)."
            ),
        }
    finally:
        conn.close()
