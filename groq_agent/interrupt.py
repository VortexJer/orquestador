"""ESC para el modelo: corta las llamadas y devuelve el turno al usuario.

COMO FUNCIONA
Un hilo vigila el teclado mientras el agente trabaja. Al pulsar ESC se
levanta una bandera; el ciclo la mira ANTES de cada llamada al proveedor y
antes de ejecutar cada herramienta, y corta ahi. No se aborta a mitad de una
peticion HTTP: cortar una escritura de archivo por la mitad dejaria basura
en el disco, y abortar una peticion ya pagada tira su coste sin recuperar
nada. Se para en la primera frontera limpia.

QUE SE LE DICE AL MODELO DESPUES
Cuando el usuario escribe su nueva instruccion, el mensaje que se manda
lleva delante una nota explicando que lo pararon a mitad y que valore, con
lo nuevo delante, si sigue con lo que estaba haciendo o cambia. Sin esa
nota el modelo ve un mensaje suelto y no entiende por que su plan quedo a
medias - o peor, lo retoma como si nada aunque el usuario lo haya parado
justo para cambiarlo.
"""
from __future__ import annotations

import sys
import threading

_bandera = threading.Event()
_hilo: threading.Thread | None = None
_parar_vigilancia = threading.Event()


def _vigilar_windows() -> None:
    import msvcrt

    while not _parar_vigilancia.is_set():
        if msvcrt.kbhit():
            tecla = msvcrt.getch()
            if tecla == b"\x1b":  # ESC
                _bandera.set()
                return
        _parar_vigilancia.wait(0.05)


def _vigilar_posix() -> None:
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    previo = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while not _parar_vigilancia.is_set():
            if select.select([sys.stdin], [], [], 0.05)[0]:
                if sys.stdin.read(1) == "\x1b":
                    _bandera.set()
                    return
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previo)


def empezar_a_vigilar() -> None:
    """Arranca la vigilancia del teclado. Sin TTY no hace nada: no hay nadie
    para pulsar ESC y leer stdin robaria la entrada de una tuberia."""
    global _hilo
    if _hilo is not None or not sys.stdin.isatty():
        return
    _bandera.clear()
    _parar_vigilancia.clear()
    objetivo = _vigilar_windows if sys.platform == "win32" else _vigilar_posix
    try:
        _hilo = threading.Thread(target=objetivo, daemon=True)
        _hilo.start()
    except Exception:  # noqa: BLE001
        # Si no se puede vigilar el teclado (terminal rara, entorno sin
        # consola), el agente sigue funcionando: solo se pierde el ESC.
        _hilo = None


def dejar_de_vigilar() -> None:
    global _hilo
    _parar_vigilancia.set()
    _hilo = None


def pedido() -> bool:
    """¿Pulso ESC el usuario?"""
    return _bandera.is_set()


def limpiar() -> None:
    _bandera.clear()



# --- Ceder el teclado mientras el turno es del usuario -----------------
#
# msvcrt.getch() (y el modo cbreak en POSIX) CONSUMEN la tecla. Si la
# vigilancia sigue activa mientras el usuario responde a ask_user o mueve
# un selector, se come sus pulsaciones y la entrada llega vacia. Mientras
# el usuario escribe no hace falta ESC: el modelo no esta trabajando.

class turno_del_usuario:
    """Context manager: para la vigilancia mientras dura el bloque, y la
    reanuda al salir SOLO si estaba activa antes (asi anidarlo no la
    enciende donde no estaba)."""

    def __init__(self):
        self._estaba_activa = False

    def __enter__(self):
        global _hilo
        self._estaba_activa = _hilo is not None
        if self._estaba_activa:
            dejar_de_vigilar()
        return self

    def __exit__(self, *exc):
        if self._estaba_activa:
            empezar_a_vigilar()
        return False


NOTA_PARADA = (
    "[El usuario te PARO a mitad de lo que estabas haciendo pulsando ESC, y "
    "escribio esto a continuacion. Que te hayan parado es informacion: lo mas "
    "probable es que algo de lo que estabas haciendo no fuera lo que queria. "
    "Antes de seguir, valora con este mensaje nuevo delante si tu plan anterior "
    "sigue siendo el correcto: si lo nuevo lo cambia o lo contradice, abandona "
    "ese plan y haz lo que se te pide ahora; si solo lo complementa, retómalo "
    "incorporandolo. No des por hecho que hay que continuar donde lo dejaste.]"
)


def envolver_tras_parada(mensaje: str) -> str:
    """Antepone la nota de parada al siguiente mensaje del usuario."""
    return f"{NOTA_PARADA}\n\n{mensaje}"
