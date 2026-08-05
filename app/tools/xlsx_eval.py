"""Evaluador de FORMULAS para el especialista office-spreadsheet.

`xlsx_tool.check_workbook` valida estructura pero NO evalua formulas:
openpyxl las guarda como texto, asi que volver a leer el archivo no dice
si `=B2/$C$2` da 16,7% o si por escribir `=C2/B2` da 600%. Esta tool monta
el libro y CALCULA las formulas de verdad (libreria `formulas`), devuelve
el valor de cada celda de formula y marca los porcentajes sospechosos y
los errores (#DIV/0!, referencia a celda vacia, etc.).

Con esto se cazan los dos fallos medidos del dominio que el formato solo
no revela: la cuota invertida (`=C2/B2` -> 600%) y la referencia a la hoja
equivocada (`$B$6` local en vez de `='Supuestos'!$B$2` -> celda vacia,
crecimiento plano o division por cero).

Depende de la libreria `formulas`. Si no esta instalada, la tool lo dice
y el especialista puede seguir razonando el resultado a mano (dejando
claro que no se verifico).
"""
from __future__ import annotations

import tempfile
import warnings
from pathlib import Path

from app.tools.xlsx_tool import create_workbook

_MAX_CELDAS = 200


def _a_escalar(valor):
    """`formulas` devuelve arrays numpy anidados ([[x]]); saca el escalar."""
    v = valor
    for _ in range(4):
        try:
            if hasattr(v, "tolist"):
                v = v.tolist()
            if isinstance(v, (list, tuple)) and len(v) == 1:
                v = v[0]
            else:
                break
        except Exception:  # noqa: BLE001
            break
    return v


def eval_workbook(sheets: list[dict]) -> dict:
    """Monta el libro a partir de la MISMA spec que create_workbook y
    calcula sus formulas.

    Devuelve {ok, valores, avisos, error}:
    - valores: {'Hoja!Celda': valor_calculado} solo de las celdas de formula.
    - avisos: lista de problemas detectados (porcentaje fuera de rango,
      error de formula, celda de formula que da None).
    """
    try:
        import formulas  # noqa: PLC0415 - import perezoso: libreria pesada
    except ImportError:
        return {
            "ok": False,
            "error": "La libreria 'formulas' no esta instalada (pip install formulas). "
            "Sin ella no puedo CALCULAR las formulas; razona el resultado a mano y di "
            "explicitamente que no se verifico el valor.",
        }

    # Que columnas llevan formato de porcentaje, por hoja, para avisar si
    # una cuota cae fuera de [0,1] (señal de formula invertida o sin anclar).
    pct_por_hoja: dict[str, set[int]] = {}
    formula_celdas: set[str] = set()
    for hoja in sheets:
        nombre = str(hoja.get("name", "Hoja1"))[:31]
        formatos = {int(c): str(f) for c, f in (hoja.get("column_formats") or {}).items()}
        pct_por_hoja[nombre.upper()] = {c for c, f in formatos.items() if "%" in f}
        # Formulas declaradas en el dict `formulas`...
        for celda in (hoja.get("formulas") or {}):
            formula_celdas.add(f"{nombre.upper()}!{str(celda).upper()}")
        # ...y formulas INLINE dentro de las filas (una celda cuyo valor es
        # un texto que empieza por '='). El modelo mete la cuota/margen ahi
        # tan a menudo como en el dict, y son justo las que hay que verificar.
        from openpyxl.utils import get_column_letter

        for row_idx, fila in enumerate(hoja.get("rows") or [], start=2):
            for col_idx, valor in enumerate(fila, start=1):
                if isinstance(valor, str) and valor.startswith("="):
                    ref = f"{get_column_letter(col_idx)}{row_idx}"
                    formula_celdas.add(f"{nombre.upper()}!{ref.upper()}")

    tmp = Path(tempfile.mkdtemp(prefix="xlsxeval_")) / "libro.xlsx"
    try:
        create_workbook(sheets, tmp)
        # `formulas`/schedula escupen una barra de progreso a stdout/stderr;
        # se silencian para no ensuciar la terminal del agente (el resultado
        # va en el dict de retorno, no por esos canales).
        import contextlib
        import io

        with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            warnings.simplefilter("ignore")
            modelo = formulas.ExcelModel().loads(str(tmp)).finish()
            solucion = modelo.calculate()
    except Exception as exc:  # noqa: BLE001 - cualquier fallo del motor se reporta como resultado
        return {"ok": False, "error": f"No se pudieron calcular las formulas: {exc}"}
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

    valores: dict[str, object] = {}
    avisos: list[str] = []
    for clave, celda_val in solucion.items():
        # clave viene como "'[libro.xlsx]HOJA'!D2"; nos quedamos HOJA!D2.
        ref = clave.split("]", 1)[-1].replace("'", "")
        if "!" not in ref:
            continue
        hoja_u, _, celda_u = ref.partition("!")
        ref_norm = f"{hoja_u.upper()}!{celda_u.upper()}"
        if ref_norm not in formula_celdas:
            continue
        valor = _a_escalar(celda_val.value if hasattr(celda_val, "value") else celda_val)
        valores[f"{hoja_u}!{celda_u}"] = valor

        # Aviso 1: error de Excel propagado.
        if isinstance(valor, str) and valor.startswith("#"):
            avisos.append(f"{hoja_u}!{celda_u} da un error de formula: {valor}")
            continue
        # Aviso 2: celda de formula vacia/None -> referencia a celda vacia
        # (tipico del $B$6 local cuando el supuesto esta en otra hoja).
        if valor is None or valor == "":
            avisos.append(
                f"{hoja_u}!{celda_u} calcula a vacio: la formula referencia una celda vacia "
                "(¿apunta a la hoja/celda equivocada?)."
            )
            continue
        # Aviso 3: porcentaje fuera de [0,1] en una columna con formato %.
        col_0 = _col_a_indice(celda_u)
        if col_0 in pct_por_hoja.get(hoja_u.upper(), set()) and isinstance(valor, (int, float)):
            if valor > 1.5 or valor < 0:
                avisos.append(
                    f"{hoja_u}!{celda_u} = {valor:.4g} en una columna de PORCENTAJE: fuera de "
                    f"[0,1] se veria como {valor*100:.1f}%. ¿Formula invertida (total/parte en "
                    "vez de parte/total), sin anclar, o multiplicada por 100 de mas?"
                )

    return {
        "ok": True,
        "valores": dict(list(valores.items())[:_MAX_CELDAS]),
        "avisos": avisos or ["Sin anomalias: las formulas calculan a valores en rango."],
    }


def _col_a_indice(celda: str) -> int:
    """'D2' -> 3 (indice 0-based de la columna D)."""
    letras = "".join(c for c in celda if c.isalpha())
    n = 0
    for c in letras.upper():
        n = n * 26 + (ord(c) - ord("A") + 1)
    return n - 1
