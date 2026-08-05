"""Los prompts van en castellano de España, no en rioplatense.

El skill ES el system prompt del especialista: si el skill está escrito en
voseo ("Sos el especialista", "tenes", "usalo"), el especialista responde
en voseo. El usuario lo corrigió dos veces, la segunda porque los 27 skills
seguían escritos así aunque la conversación ya no lo estuviera. Esta prueba
lo vigila en la fuente, que es donde se arregla de verdad.

Es una lista EXPLÍCITA de formas, no un detector general de español. Se
probó primero con patrones (`\\w+alo`, `\\w+ate`) y marcaba "candidate",
"virtuales", "template", "intervalo": una prueba con falsos positivos se
acaba desactivando, y entonces no vigila nada.
"""
import pathlib
import re

import pytest

RAIZ = pathlib.Path(__file__).resolve().parent.parent

# Todo lo que acaba siendo texto que el modelo lee o que el usuario ve.
# hard_cases/guide.md y config/ entran porque se PEGAN al system prompt: el
# voseo se coló justo por ahí ("Si no encontras nada relevante") mientras el
# resto ya estaba convertido.
FUENTES = (
    sorted(RAIZ.glob("skills/*.md"))
    + sorted(RAIZ.glob("groq_agent/*.py"))
    + sorted(RAIZ.glob("app/**/*.py"))
    + sorted(RAIZ.glob("hard_cases/*.md"))
    + sorted(RAIZ.glob("config/*.yaml"))
)

# Pronombre, "ser" y presente de vos. OJO con lo que NO va aquí: en los
# verbos sin cambio de raíz, la forma de tú es la misma sin tilde ("tú
# sabes" / "vos sabés"), así que solo la versión ACENTUADA es rioplatense.
# Meter "sabes", "haces" o "usas" en esta lista marcaba castellano correcto.
_PRESENTE = """
    sos vos
    tenes tenés podes podés queres querés sabés hacés ponés debés
    seguis seguís sentis sentís decidis decidís escribis escribís
    preferis preferís pedis pedís encontras encontrás
    usás intentás acabás necesitás
"""

# Imperativos de vos: sin tilde (deci, elegi) o con tilde final (pensá,
# usá). En castellano son di, elige, piensa, usa.
_IMPERATIVOS = """
    deci decí escribi elegi elegí segui seguí preferi volve cerra cerrá
    pensa pensá recorda recordá meti leé poné tené hacé veni vení usá armá
    volvé resumi resumí omiti omití
    dejá entregá gastá justificá llamá mencioná nombrá quitá reservá
    respondé tomá avisá priorizá comprobá verificá mantené derivá guardá
    decidí definí decidi defini
"""

# Imperativo + enclítico sin tilde. En castellano la tilde es obligatoria
# ("úsalo", "anótalo", "fíjate"), así que la forma sin ella está mal
# escrita además de sonar rioplatense.
_ENCLITICOS = """
    usalo usala usalos usalas decilo decile anotalo fijate acordate
    preguntate preguntale tratalo tratala dejalo dejale dejaselo pedila
    pedilas pedile pedilos pasale pasalo pasaselo comprometete parate
    hacelo hacela guardalo miralo leelo leela creala descartala partila
    partilo resolvelo resolvela revisalo revisala correlo correlos corrilo
    combinalas combinalo confirmalo confirmala comprobalo aplicalo
    adaptalo activala ajustala buscala cambialo cambiala cargala
    clasificala completala consultalos contestale corregilo definila
    escribila escribilo incluila llamala marcalo pensalo probalo
    reemplazala relanzala respondelo retomalo verificala verificalo
    borralo borralas olvidate sumale apoyate arreglalo chequealo
"""

FORMAS = sorted(set((_PRESENTE + _IMPERATIVOS + _ENCLITICOS).split()))
VOSEO = re.compile(r"\b(" + "|".join(map(re.escape, FORMAS)) + r")\b", re.IGNORECASE)


@pytest.mark.parametrize("ruta", FUENTES, ids=lambda p: p.name)
def test_sin_voseo(ruta: pathlib.Path):
    encontradas = VOSEO.findall(ruta.read_text(encoding="utf-8"))
    assert not encontradas, (
        f"{ruta.name} usa formas rioplatenses: {sorted(set(encontradas))}. "
        "Los prompts van en castellano de España (tú, tienes, puedes, úsalo)."
    )


def test_el_detector_reconoce_lo_que_busca():
    """Una prueba que no detecta nada pasa siempre: esto la ancla."""
    assert VOSEO.findall("Sos el especialista y tenes que usalo") == [
        "Sos", "tenes", "usalo",
    ]
    assert VOSEO.findall("Eres el especialista y tienes que usarlo") == []


def test_el_detector_no_marca_palabras_normales():
    """El motivo de que la lista sea explícita: los patrones genéricos
    marcaban 'candidate', 'virtuales', 'template' e 'intervalo'."""
    normal = (
        "candidate virtuales template intervalo escala instala compila "
        "señala vigila totales reales delete update compite permite"
    )
    assert VOSEO.findall(normal) == []
