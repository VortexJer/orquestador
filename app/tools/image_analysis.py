"""Analisis visual de una imagen (color dominante, brillo, si es
'calida' o 'fria', que tan compleja es) - sin esto, buscar/scrapear una
foto y usarla directo da resultados horribles: un fondo oscuro con
texto oscuro encima, una foto de comida fria de tonos usada en un sitio
con paleta calida, una imagen muy "ruidosa" (alta variancia) que hace
el texto superpuesto illegible. Ver
hard_cases/by_domain/web.json:web-image-selection-without-visual-analysis
y skills/web-builder-specialist.md.

Usa Pillow (ya es dependencia transitiva de python-pptx) - no hace
falta instalar nada nuevo.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image, ImageFilter, ImageStat

_USER_AGENT = "Mozilla/5.0 (compatible; OrquestadorWebBuilder/1.0; +local-testing)"
_TIMEOUT_S = 15.0
_THUMBNAIL_SIZE = (150, 150)
_SHARPNESS_ANALYSIS_SIZE = (500, 500)

# Umbrales calibrados empiricamente (ver notas de la sesion de
# desarrollo): la varianza de un filtro de deteccion de bordes cae
# drasticamente incluso con desenfoque leve, pero el valor "nitido"
# base varia bastante segun cuanto detalle tenga la foto en si (una
# foto de un cielo despejado es "nitida" y aun asi da un numero bajo).
# Es una heuristica aproximada, no una medicion exacta - un resultado
# "algo_borrosa" en el limite conviene confirmarlo con render_check o
# preguntandole al usuario, no confiar ciegamente en el numero.
_SHARPNESS_BLURRY_MAX = 300
_SHARPNESS_SOMEWHAT_BLURRY_MAX = 800

# Por debajo de esto, una foto usada a pantalla completa (hero/fondo de
# seccion) va a mostrar pixelacion visible en un monitor de escritorio
# comun - regla practica, no un estandar formal.
_MIN_DIMENSION_FULLWIDTH_BACKGROUND = 1280
_MIN_DIMENSION_LOW_RES = 640


def _load_image(url_or_path: str) -> Image.Image:
    if url_or_path.startswith(("http://", "https://")):
        response = httpx.get(url_or_path, headers={"User-Agent": _USER_AGENT}, timeout=_TIMEOUT_S, follow_redirects=True)
        response.raise_for_status()
        image = Image.open(BytesIO(response.content))
    else:
        image = Image.open(Path(url_or_path))
    return image.convert("RGB")


def _dominant_colors(image: Image.Image, top_n: int = 5) -> list[dict]:
    small = image.resize((64, 64))
    quantized = small.quantize(colors=8, method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette()
    color_counts = sorted(quantized.getcolors(), key=lambda c: c[0], reverse=True)

    total_pixels = small.width * small.height
    results = []
    for count, palette_index in color_counts[:top_n]:
        r, g, b = palette[palette_index * 3: palette_index * 3 + 3]
        results.append({
            "hex": f"#{r:02x}{g:02x}{b:02x}",
            "percentage": round(count / total_pixels * 100, 1),
        })
    return results


def _brightness_classification(mean_luminance: float) -> str:
    if mean_luminance < 85:
        return "oscura"
    if mean_luminance > 170:
        return "clara"
    return "media"


def _warmth_classification(mean_r: float, mean_g: float, mean_b: float, threshold: float = 12.0) -> str:
    if mean_r - mean_b > threshold:
        return "calida"
    if mean_b - mean_r > threshold:
        return "fria"
    return "neutra"


def _sharpness_variance(image: Image.Image) -> float:
    analysis_image = image.copy()
    analysis_image.thumbnail(_SHARPNESS_ANALYSIS_SIZE)
    edges = analysis_image.convert("L").filter(ImageFilter.FIND_EDGES)
    return ImageStat.Stat(edges).stddev[0] ** 2


def _sharpness_classification(edge_variance: float) -> str:
    if edge_variance < _SHARPNESS_BLURRY_MAX:
        return "borrosa"
    if edge_variance < _SHARPNESS_SOMEWHAT_BLURRY_MAX:
        return "algo_borrosa"
    return "nitida"


def _resolution_classification(min_dimension: int) -> str:
    if min_dimension < _MIN_DIMENSION_LOW_RES:
        return "baja"
    if min_dimension < _MIN_DIMENSION_FULLWIDTH_BACKGROUND:
        return "media"
    return "alta"


def analyze_image(url_or_path: str) -> dict:
    image = _load_image(url_or_path)
    original_size = image.size
    thumbnail = image.copy()
    thumbnail.thumbnail(_THUMBNAIL_SIZE)

    grayscale = thumbnail.convert("L")
    luminance_stat = ImageStat.Stat(grayscale)
    mean_luminance = luminance_stat.mean[0]
    stddev_luminance = luminance_stat.stddev[0]

    rgb_stat = ImageStat.Stat(thumbnail)
    mean_r, mean_g, mean_b = rgb_stat.mean

    brightness = _brightness_classification(mean_luminance)
    warmth = _warmth_classification(mean_r, mean_g, mean_b)

    width, height = original_size
    orientation = "cuadrada" if width == height else ("horizontal" if width > height else "vertical")
    min_dimension = min(width, height)

    edge_variance = _sharpness_variance(image)
    sharpness = _sharpness_classification(edge_variance)
    resolution_quality = _resolution_classification(min_dimension)

    quality_warnings = []
    if sharpness == "borrosa":
        quality_warnings.append("La imagen parece borrosa o con muy poco detalle - confirmar visualmente antes de usarla.")
    elif sharpness == "algo_borrosa":
        quality_warnings.append("La nitidez es dudosa (resultado limite) - confirmar visualmente o probar otra candidata.")
    if resolution_quality == "baja":
        quality_warnings.append(
            f"Resolucion baja ({width}x{height}) - NO usar como fondo a pantalla completa, se va a ver pixelada; sirve como thumbnail chico."
        )
    elif resolution_quality == "media":
        quality_warnings.append(
            f"Resolucion media ({width}x{height}) - aceptable para secciones chicas, arriesgada como hero/fondo a pantalla completa en monitores grandes."
        )

    return {
        "width": width,
        "height": height,
        "orientation": orientation,
        "dominant_colors": _dominant_colors(thumbnail),
        "mean_brightness_0_255": round(mean_luminance, 1),
        "brightness": brightness,
        "warmth": warmth,
        "complexity_stddev_0_255": round(stddev_luminance, 1),
        "busy_image": stddev_luminance > 60,
        "suggested_overlay_text_color": "clara" if brightness == "oscura" else (
            "oscura" if brightness == "clara" else "clara con scrim oscuro semi-transparente detras del texto"
        ),
        "sharpness": sharpness,
        "sharpness_edge_variance": round(edge_variance, 1),
        "resolution_quality": resolution_quality,
        "usable_as_fullwidth_background": resolution_quality == "alta" and sharpness != "borrosa",
        "quality_warnings": quality_warnings,
    }
