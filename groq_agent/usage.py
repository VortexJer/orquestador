"""Contabilidad de tokens por sesion, y medidor de llenado del contexto.

POR QUE
Todo el trabajo de recorte de este proyecto (carga progresiva de skills,
filtrado de herramientas por especialista, poda y compactacion del
historial) se hizo a ciegas: se median los prompts en un script aparte,
pero en uso real no habia forma de saber si una tarea gasto 20.000 o
400.000 tokens. Sin esto, "gasta menos" es una creencia.

DE DONDE SALEN LOS NUMEROS
Del campo `usage` que devuelve el propio proveedor en cada respuesta. Es
dato suyo, no una estimacion nuestra - los `chars // 4` que usa
context_manager sirven para DECIDIR si compactar, pero no para informar de
un gasto. Los proveedores que no mandan `usage` (algunos free tier no lo
hacen) simplemente no suman: es mejor un total incompleto y honesto que
uno inventado.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Gasto:
    """Acumulado de una sesion."""

    entrada: int = 0          # prompt_tokens
    salida: int = 0           # completion_tokens
    cache: int = 0            # los que el proveedor sirvio de cache
    llamadas: int = 0
    # Cuantas llamadas no informaron de su gasto. Se muestra para que el
    # total no parezca mas exacto de lo que es.
    sin_datos: int = 0
    por_modelo: dict[str, int] = field(default_factory=dict)
    # modelo -> (entrada, cacheados). Dice QUIEN cachea, no solo cuanto.
    cache_por_proveedor: dict[str, tuple[int, int]] = field(default_factory=dict)
    # 'proveedor:modelo' -> cuantas llamadas SIRVIO ese candidato. A diferencia
    # de por_modelo (que cuenta tokens y se salta las respuestas sin `usage`),
    # esto cuenta TODA llamada servida, tenga o no datos de tokens - es lo que
    # responde "cuantas llamadas a cada API" (orquestador -api).
    llamadas_por_modelo: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.entrada + self.salida

    @property
    def tasa_cache(self) -> float:
        """Fraccion de la entrada que vino de cache. Es la metrica que dice
        si el prompt estable esta funcionando: un prompt que cambia en cada
        llamada nunca cachea, y eso se ve aca antes que en la factura."""
        return (self.cache / self.entrada) if self.entrada else 0.0

    def sumar(self, respuesta: dict, modelo: str = "") -> None:
        """Acumula el `usage` de una respuesta del proveedor."""
        self.llamadas += 1
        # Conteo de llamadas por candidato ANTES de mirar el `usage`: una
        # respuesta sin tokens informados sigue siendo una llamada servida.
        if modelo:
            self.llamadas_por_modelo[modelo] = self.llamadas_por_modelo.get(modelo, 0) + 1
        uso = (respuesta or {}).get("usage") or {}
        entrada = int(uso.get("prompt_tokens") or 0)
        salida = int(uso.get("completion_tokens") or 0)
        if not entrada and not salida:
            self.sin_datos += 1
            return
        self.entrada += entrada
        self.salida += salida
        # Cada proveedor lo llama distinto; se prueban las formas conocidas
        # en vez de asumir la de uno.
        detalle = uso.get("prompt_tokens_details") or {}
        cacheados = int(
            detalle.get("cached_tokens")
            or uso.get("cached_tokens")
            or uso.get("cache_read_input_tokens")
            or 0
        )
        self.cache += cacheados
        if modelo:
            self.por_modelo[modelo] = self.por_modelo.get(modelo, 0) + entrada + salida
            # Entrada y cache POR PROVEEDOR. Es la diferencia entre pagar el
            # bruto y pagar una fraccion, y hasta ahora solo se veia el total
            # de la sesion: con varios proveedores en la escalera, un unico
            # numero mezcla al que cachea con el que no y no dice cual es
            # cual. Ademas es lo que decide si conviene podar el historial
            # (ver context_manager.cadencia_de_poda).
            ent, cac = self.cache_por_proveedor.get(modelo, (0, 0))
            self.cache_por_proveedor[modelo] = (ent + entrada, cac + cacheados)

    def como_dict(self) -> dict:
        return {
            "entrada": self.entrada, "salida": self.salida, "cache": self.cache,
            "llamadas": self.llamadas, "sin_datos": self.sin_datos,
            "por_modelo": self.por_modelo,
            "cache_por_proveedor": {k: list(v) for k, v in self.cache_por_proveedor.items()},
            "llamadas_por_modelo": self.llamadas_por_modelo,
        }

    @classmethod
    def desde_dict(cls, datos: dict | None) -> Gasto:
        datos = datos or {}
        return cls(
            entrada=int(datos.get("entrada") or 0),
            salida=int(datos.get("salida") or 0),
            cache=int(datos.get("cache") or 0),
            llamadas=int(datos.get("llamadas") or 0),
            sin_datos=int(datos.get("sin_datos") or 0),
            por_modelo=dict(datos.get("por_modelo") or {}),
            cache_por_proveedor={
                k: (int(v[0]), int(v[1]))
                for k, v in (datos.get("cache_por_proveedor") or {}).items()
            },
            llamadas_por_modelo={
                k: int(v) for k, v in (datos.get("llamadas_por_modelo") or {}).items()
            },
        )

    def quien_cachea(self) -> list[tuple[str, float, int]]:
        """(modelo, tasa de cache, entrada) por proveedor, de mejor a peor.

        Es LA cifra que decide la factura: con un 85% de cache pagas una
        fraccion del bruto, y con un 0% lo pagas entero. Como la escalera de
        failover mezcla proveedores, el total de la sesion no sirve para
        saber a cual conviene ir - hace falta el desglose."""
        filas = [
            (modelo, (cac / ent if ent else 0.0), ent)
            for modelo, (ent, cac) in self.cache_por_proveedor.items()
        ]
        return sorted(filas, key=lambda f: f[1], reverse=True)

    def llamadas_por_proveedor(self) -> list[tuple[str, int, list[tuple[str, int]]]]:
        """(proveedor, total_llamadas, [(modelo, llamadas), ...]) de mas a menos
        llamado. La 'API' es el PROVEEDOR (groq, sambanova, nvidia...); el
        desglose por modelo va dentro. Las claves de llamadas_por_modelo son
        'proveedor:modelo' (str(Candidate)); se agrupan por el proveedor."""
        por_prov: dict[str, dict[str, int]] = {}
        for clave, n in self.llamadas_por_modelo.items():
            proveedor, sep, modelo = clave.partition(":")
            por_prov.setdefault(proveedor, {})
            etiqueta = modelo if sep else clave
            por_prov[proveedor][etiqueta] = por_prov[proveedor].get(etiqueta, 0) + n
        filas = [
            (proveedor, sum(modelos.values()),
             sorted(modelos.items(), key=lambda kv: kv[1], reverse=True))
            for proveedor, modelos in por_prov.items()
        ]
        return sorted(filas, key=lambda f: f[1], reverse=True)


# Ventana de contexto que se asume para el medidor. Los modelos de estos
# tiers van de 32K a 256K; 128K es un punto medio prudente. Se puede
# ajustar por modelo cuando haga falta - lo que importa del medidor no es
# el porcentaje exacto sino ver la tendencia dentro de una tarea.
VENTANA_ASUMIDA = 128_000


def barra_contexto(chars: int, ancho: int = 24, ventana: int = VENTANA_ASUMIDA) -> str:
    """Barra de llenado del contexto, en texto plano.

    Se dibuja con caracteres de bloque, no con emojis ni colores: tiene que
    verse igual en la consola clasica de Windows que en Windows Terminal.
    """
    tokens = max(0, chars) // 4
    fraccion = min(1.0, tokens / ventana) if ventana else 0.0
    llenos = int(round(fraccion * ancho))
    return f"[{'█' * llenos}{'·' * (ancho - llenos)}] {int(fraccion * 100)}%"


# --- persistencia ------------------------------------------------------
#
# El gasto se guarda junto a la sesion, para que `--usage` pueda informar
# de sesiones pasadas y no solo de la que esta corriendo.

def _ruta(workspace: Path) -> Path:
    return workspace / ".orquestador_sessions" / "_gasto.json"


def cargar_todo(workspace: Path) -> dict[str, dict]:
    ruta = _ruta(workspace)
    if not ruta.exists():
        return {}
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        # Un fichero de contabilidad corrupto no puede impedir trabajar.
        return {}


def guardar(workspace: Path, session_id: str, gasto: Gasto) -> None:
    ruta = _ruta(workspace)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    datos = cargar_todo(workspace)
    # Antes de sobreescribir el snapshot de esta sesion, calcula el DELTA de
    # llamadas por API respecto al guardado anterior y sumalo al historico
    # global. Asi guardar la misma sesion varias veces (cada turno) no cuenta
    # dos veces: solo cuenta las llamadas NUEVAS.
    previo = (datos.get(session_id) or {}).get("llamadas_por_modelo") or {}
    delta = {
        clave: n - int(previo.get(clave, 0))
        for clave, n in gasto.llamadas_por_modelo.items()
        if n - int(previo.get(clave, 0)) > 0
    }
    _sumar_al_historico(delta)
    datos[session_id] = gasto.como_dict()
    try:
        ruta.write_text(json.dumps(datos, indent=2), encoding="utf-8")
    except OSError:
        pass


# --- historico GLOBAL de llamadas por API -----------------------------
#
# El gasto de tokens se guarda por sesion (arriba). Las llamadas por API en
# cambio se acumulan en un unico fichero a nivel de usuario, para que
# `orquestador -api` muestre el TOTAL historico de TODOS los workspaces y
# sesiones, no solo del workspace actual.

def _ruta_historico() -> Path:
    raiz = os.environ.get("ORQUESTADOR_HOME") or str(Path.home())
    return Path(raiz) / ".orquestador" / "api_historico.json"


def cargar_historico() -> dict[str, int]:
    """'proveedor:modelo' -> llamadas acumuladas de siempre."""
    ruta = _ruta_historico()
    if not ruta.exists():
        return {}
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        return {k: int(v) for k, v in (datos.get("llamadas_por_modelo") or {}).items()}
    except (ValueError, OSError, TypeError):
        return {}


def _sumar_al_historico(delta: dict[str, int]) -> None:
    if not delta:
        return
    ruta = _ruta_historico()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    actual = cargar_historico()
    for clave, n in delta.items():
        actual[clave] = actual.get(clave, 0) + int(n)
    try:
        ruta.write_text(
            json.dumps({"llamadas_por_modelo": actual}, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def cargar(workspace: Path, session_id: str) -> Gasto:
    return Gasto.desde_dict(cargar_todo(workspace).get(session_id))


def borrar(workspace: Path, session_id: str) -> None:
    """Quita el gasto de una sesion (al borrarla, para no dejar un huerfano
    en el total de --usage). Si no habia registro, no hace nada."""
    ruta = _ruta(workspace)
    datos = cargar_todo(workspace)
    if session_id in datos:
        datos.pop(session_id)
        try:
            ruta.write_text(json.dumps(datos, indent=2), encoding="utf-8")
        except OSError:
            pass
