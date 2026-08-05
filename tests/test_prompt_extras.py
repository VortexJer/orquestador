"""@archivo, preclasificacion y nombres de sesion.

Los tres comparten una propiedad: fallan de forma ASIMETRICA. Equivocarse
hacia "cargar de mas" cuesta tokens; equivocarse hacia "cargar de menos"
deja al agente sin poder trabajar. Los tests fijan esa asimetria.
"""
import pytest

from groq_agent import prompt_classifier as pc
from groq_agent import prompt_refs, session_naming


# --- @archivo ----------------------------------------------------------


def test_encuentra_referencias_sin_tragarse_la_puntuacion():
    assert prompt_refs.encontrar_referencias("mira @app.py, esta roto") == ["app.py"]
    assert prompt_refs.encontrar_referencias("revisa @src/a.py y @src/b.py") == [
        "src/a.py", "src/b.py",
    ]


def test_un_email_no_es_una_referencia():
    """@ es tambien la arroba de los correos y de los usuarios."""
    refs = prompt_refs.encontrar_referencias("escribe a juan@empresa.com sobre esto")
    assert "empresa.com" not in refs


def test_inyecta_el_contenido_y_conserva_el_texto_original(tmp_path):
    (tmp_path / "app.py").write_text("print('hola')\n", encoding="utf-8")
    texto, notas = prompt_refs.expandir("revisa @app.py por favor", tmp_path)

    assert "revisa @app.py por favor" in texto, "no debe reescribirse la frase"
    assert "print('hola')" in texto
    assert "NO hace falta que los vuelvas a leer" in texto
    assert notas


def test_una_referencia_que_no_existe_se_ignora_en_silencio(tmp_path):
    """Puede ser un usuario de redes o una ruta que el modelo buscara: no
    es un error del usuario y no debe interrumpir la tarea."""
    texto, _ = prompt_refs.expandir("mira @noexiste.py", tmp_path)
    assert texto == "mira @noexiste.py"


def test_un_binario_no_se_inyecta(tmp_path):
    (tmp_path / "foto.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    texto, notas = prompt_refs.expandir("mira @foto.png", tmp_path)
    assert "binario" in " ".join(notas)
    assert "PNG" not in texto


def test_un_archivo_enorme_se_recorta(tmp_path):
    """Inyectar 2 MB arruinaria el proposito: se comeria el contexto que
    veniamos de liberar."""
    (tmp_path / "grande.txt").write_text("x" * 100_000, encoding="utf-8")
    texto, _ = prompt_refs.expandir("mira @grande.txt", tmp_path)
    assert "recortado" in texto
    assert len(texto) < 40_000


def test_sin_referencias_no_toca_nada(tmp_path):
    texto, notas = prompt_refs.expandir("hazme una web bonita", tmp_path)
    assert texto == "hazme una web bonita"
    assert notas == []


# --- preclasificacion --------------------------------------------------


@pytest.mark.parametrize(
    "mensaje",
    [
        "hazme una web para el bar la prosperidad",
        "crea un excel con los gastos",
        "arregla el bug del login",
        "revisa app/config.py",
        "¿me puedes crear un excel?",          # peticion disfrazada de pregunta
        "¿que hace app/config.py?",            # pregunta, pero sobre un archivo
        "mira @src/app.py",
        "si",                                   # continuacion: ante la duda, todo
        "ok",                                   # NO es saludo: contesta a algo
        "vale",
        "dale",
        "",
    ],
)
def test_estos_necesitan_herramientas(mensaje):
    assert pc.necesita_herramientas(mensaje) is True


@pytest.mark.parametrize(
    "mensaje",
    [
        "¿que diferencia hay entre flex y grid?",
        "explicame como funciona el failover",
        "¿cual es mejor practica para nombrar variables?",
        "what is the difference between a list and a tuple",
        "¿por que se usa un tier router-tiny?",
        # Saludos: un "hola" cargaba el skill entero y las 28 herramientas,
        # y el modelo, con herramientas y sin tarea, abria un menu con
        # ask_user preguntando que se queria. Visto en uso real.
        "hola",
        "buenas",
        "gracias",
        "que tal",
    ],
)
def test_estos_son_conversacionales(mensaje):
    assert pc.necesita_herramientas(mensaje) is False


def test_el_prompt_conversacional_es_pequeno():
    """El sentido de todo esto: ~500 tokens en vez de ~19.000. Lleva la guia
    de busqueda, asi que crecio un poco, pero sigue siendo una fraccion del
    skill+catalogo de un especialista."""
    assert len(pc.prompt_conversacional()) < 2500


def test_el_prompt_conversacional_manda_buscar_directo_sin_anunciar():
    """La charla tiene busqueda web y debe USARLA sola cuando no sabe algo o
    depende del momento - buscar y responder, SIN anunciarlo ni pedir permiso
    ('puedo buscarlo si quieres' era el bug)."""
    p = pc.prompt_conversacional().lower()
    assert "no puedes leer ni escribir archivos" in p   # no promete tocar archivos
    assert "search_web" in p                            # tiene busqueda
    assert "momento" in p                               # y la usa para lo del momento
    assert "dato concreto" in p or "datos concretos" in p  # y para datos concretos
    # ...y buscar directamente en vez de escaquearse ofreciendo buscar.
    assert "puedo buscarlo" in p  # aparece como lo que NUNCA debe responder
    assert "sin avisar" in p


def test_el_prompt_conversacional_mantiene_la_regla_de_emojis():
    """Es una regla del sistema entero, no del skill: no puede perderse por
    tomar el camino corto."""
    assert "emoji" in pc.prompt_conversacional().lower()


# --- nombres de sesion -------------------------------------------------


@pytest.mark.parametrize(
    ("prompt", "esperado_en"),
    [
        ("hazme una web para el bar la prosperidad", ["web", "prosperidad"]),
        ("crea un excel con los gastos de enero", ["excel", "gastos"]),
        ("arregla el bug del login", ["fix", "login"]),
        ("escribe tests para validators.py", ["tests", "validators"]),
    ],
)
def test_el_nombre_captura_lo_que_distingue(prompt, esperado_en):
    nombre = session_naming.nombre_para(prompt)
    for palabra in esperado_en:
        assert palabra in nombre.lower(), f"'{palabra}' falta en '{nombre}'"


def test_quita_las_palabras_de_relleno():
    """'hazme una' aparece en TODOS los prompts: no distingue ninguno."""
    nombre = session_naming.nombre_para("hazme una web para el restaurante")
    assert "hazme" not in nombre
    assert "una" not in nombre.split()


def test_dos_prompts_distintos_dan_nombres_distintos():
    """El fallo del sistema anterior: todas las sesiones empezaban igual."""
    a = session_naming.nombre_para("hazme una web para el bar la prosperidad")
    b = session_naming.nombre_para("hazme una web para la peluqueria lola")
    assert a != b


def test_nunca_devuelve_vacio():
    for p in ("", "   ", "?!", "si"):
        assert session_naming.nombre_para(p).strip()


def test_respeta_el_largo_maximo():
    largo = "hazme una web " + " ".join(f"palabra{i}" for i in range(40))
    assert len(session_naming.nombre_para(largo, maximo=40)) <= 40
