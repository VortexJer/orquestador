"""Descarga SEGURA de una imagen por URL para incrustarla en un .docx/.pptx.

La URL la elige el modelo (normalmente de `search_images`), asi que se aplica
la misma prudencia que el resto de la capa de red: solo https, timeout, tope de
bytes y content-type de imagen. Ademas se re-codifica con PIL a un formato que
python-docx/python-pptx aceptan siempre (JPEG/PNG): un .webp o un content-type
raro reventaria el add_picture, y asi da igual lo que sirva el CDN.
"""
from __future__ import annotations

from io import BytesIO

import httpx
from PIL import Image

_TIMEOUT_S = 15.0
_MAX_BYTES = 12_000_000  # 12 MB: una foto grande, no un video
_USER_AGENT = "orquestador-modelos/1.0 (+https://github.com/VortexJer)"
_TIPOS_OK = ("image/jpeg", "image/jpg", "image/png", "image/gif", "image/webp", "image/bmp", "image/tiff")


class ImagenNoValida(Exception):
    """La URL no es https, no responde, no es una imagen o no se pudo leer."""


def descargar_imagen(url: str, timeout: float = _TIMEOUT_S, max_bytes: int = _MAX_BYTES) -> BytesIO:
    """Descarga la imagen de `url` y devuelve un BytesIO ya normalizado a un
    formato incrustable (JPEG, o PNG si tiene transparencia). Lanza
    ImagenNoValida si algo no cuadra - el llamador decide si seguir sin ella."""
    if not isinstance(url, str) or not url.lower().startswith("https://"):
        raise ImagenNoValida(f"solo https para imagenes (era: {str(url)[:50]}...).")
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout,
                          headers={"User-Agent": _USER_AGENT}) as client:
            resp = client.get(url)
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if ct and ct not in _TIPOS_OK:
                raise ImagenNoValida(f"content-type no es imagen: '{ct}'.")
            data = resp.content[:max_bytes]
    except httpx.HTTPError as exc:
        raise ImagenNoValida(f"no se pudo descargar: {type(exc).__name__}: {exc}") from exc
    if not data:
        raise ImagenNoValida("respuesta vacia.")
    try:
        img = Image.open(BytesIO(data))
        img.load()
    except Exception as exc:  # noqa: BLE001 - cualquier fallo de PIL = imagen ilegible
        raise ImagenNoValida(f"no se pudo leer la imagen: {exc}") from exc
    salida = BytesIO()
    if img.mode in ("RGBA", "LA", "P"):
        img.convert("RGBA").save(salida, format="PNG")
    else:
        img.convert("RGB").save(salida, format="JPEG", quality=85)
    salida.seek(0)
    return salida
