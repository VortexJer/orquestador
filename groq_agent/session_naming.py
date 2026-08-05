"""Nombres de sesion legibles, con CERO llamadas a ninguna API.

El selector de sesiones mostraba el primer prompt en crudo recortado a 70
caracteres: "hazme una web para el bar la prosperidad que este en madrid y
tenga una secc…". Legible a medias, y todas empiezan igual, que es lo peor
para elegir en una lista.

Se resuelve con reglas y extraccion de palabras clave, no con un modelo.
Pedirle a un LLM que titule cada sesion costaria una llamada por sesion
para un texto de cuatro palabras - exactamente el tipo de gasto que este
proyecto existe para evitar.
"""
from __future__ import annotations

import re

# Palabras sin valor para identificar una sesion. Incluye las formas de
# pedir (hazme, necesito, quiero), que son justo las que se repiten en
# TODOS los prompts y por eso no distinguen ninguno.
_VACIAS = {
    "hazme", "haceme", "hace", "haz", "necesito", "quiero", "puedes",
    "podrias", "puedes", "porfa", "porfavor", "por", "favor", "me", "te", "se",
    "un", "una", "unos", "unas", "el", "la", "los", "las", "lo", "de", "del",
    "al", "en", "con", "para", "que", "y", "o", "u", "es", "sea",
    "esta", "este", "estos", "estas", "su", "sus", "mi", "mis", "tu", "tus",
    "como", "cuando", "donde", "muy", "mas", "menos", "todo", "toda", "todos",
    "algo", "alguna", "alguno", "sobre", "desde", "hasta", "tambien", "pero",
    "si", "no", "ya", "solo", "bien", "hola", "gracias", "please", "the",
    "an", "of", "to", "for", "and", "or", "on", "with", "make",
    "create", "build", "want", "need", "can", "you",
}

# Verbos/sustantivos que SI dicen de que va la tarea. Si aparece uno, se
# usa como primera palabra del nombre aunque no sea la primera del texto.
_ACCIONES = {
    "web": "web", "pagina": "web", "sitio": "web", "landing": "web",
    "excel": "excel", "hoja": "excel", "calculo": "excel",
    "word": "word", "documento": "word", "informe": "informe",
    "presentacion": "presentacion", "powerpoint": "presentacion",
    "correo": "correo", "email": "correo", "mail": "correo",
    "test": "tests", "tests": "tests", "pruebas": "tests",
    "refactor": "refactor", "refactorizar": "refactor",
    "bug": "bug", "error": "fix", "arregla": "fix", "arreglar": "fix",
    "corrige": "fix", "corregir": "fix", "falla": "fix",
    "script": "script", "funcion": "funcion", "clase": "clase",
    "api": "api", "endpoint": "api", "consulta": "sql", "query": "sql",
    "revisa": "revision", "revisar": "revision", "audita": "revision",
}

_MAX_PALABRAS = 5


def _normalizar(texto: str) -> list[str]:
    limpio = re.sub(r"[^\w\sáéíóúüñÁÉÍÓÚÜÑ-]", " ", texto.lower())
    return [p for p in limpio.split() if p]


def nombre_para(prompt: str, maximo: int = 48) -> str:
    """Titulo corto y distinguible a partir del primer prompt.

    Estrategia: primero la palabra de ACCION si la hay (dice el dominio de
    un vistazo), luego las palabras con contenido, sin las de relleno. Si no
    queda nada util, se cae al texto recortado - un nombre feo es mejor que
    uno vacio.
    """
    if not prompt or not prompt.strip():
        return "(sesion vacia)"

    palabras = _normalizar(prompt)
    if not palabras:
        return prompt.strip()[:maximo]

    accion = next((_ACCIONES[p] for p in palabras if p in _ACCIONES), None)

    utiles: list[str] = []
    for p in palabras:
        if p in _VACIAS or len(p) < 3:
            continue
        if _ACCIONES.get(p) == accion:
            continue
        if p not in utiles:
            utiles.append(p)
        if len(utiles) >= _MAX_PALABRAS:
            break

    partes = ([accion] if accion else []) + utiles
    if not partes:
        return prompt.strip()[:maximo]

    nombre = " ".join(partes)
    if len(nombre) > maximo:
        nombre = nombre[: maximo - 1].rsplit(" ", 1)[0] + "…"
    return nombre
