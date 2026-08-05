"""La ventana deslizante del selector de flechas (ui.select).

Bug: en `orquestador -r` con mas sesiones de las que caben en pantalla, al
bajar con la flecha la opcion elegida se iba FUERA de pantalla porque se
pintaba la lista entera sin scroll. `_ventana_scroll` calcula la franja
visible que SIGUE a la seleccion.
"""
from groq_agent.ui import _ventana_scroll


def test_seleccion_dentro_no_mueve_la_ventana():
    assert _ventana_scroll(2, 0, 10, 4) == (0, 4)
    assert _ventana_scroll(3, 0, 10, 4) == (0, 4)   # ultimo visible, aun dentro


def test_bajar_por_debajo_desliza_hacia_abajo():
    # i=4 ya no cabe en [0,4): la ventana baja una posicion
    assert _ventana_scroll(4, 0, 10, 4) == (1, 5)
    assert _ventana_scroll(5, 1, 10, 4) == (2, 6)


def test_subir_por_encima_desliza_hacia_arriba():
    assert _ventana_scroll(5, 6, 10, 4) == (5, 9)
    assert _ventana_scroll(0, 6, 10, 4) == (0, 4)


def test_el_ultimo_elemento_siempre_es_visible():
    top, fin = _ventana_scroll(9, 0, 10, 4)
    assert fin == 10 and top == 6 and top <= 9 < fin


def test_top_nunca_pasa_del_maximo():
    # aunque el top pedido sea grande, se recorta a n-max_visibles
    top, fin = _ventana_scroll(9, 8, 10, 4)
    assert top == 6 and fin == 10


def test_lista_mas_corta_que_la_ventana_no_scrollea():
    for i in range(3):
        assert _ventana_scroll(i, 0, 3, 8) == (0, 3)


def test_una_sola_opcion_visible():
    assert _ventana_scroll(7, 0, 20, 1) == (7, 8)
