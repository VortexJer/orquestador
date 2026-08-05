"""Demo visual de animate_dispatch (puntos + frases rotando), sin
necesitar un modelo real ni gastar ninguna llamada - corre directo en tu
terminal para ver el efecto tal cual queda hoy y decidir que ajustar.

Uso:
    ./.venv/Scripts/python.exe -m groq_agent.experiments.demo_animacion
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

from groq_agent import ui
from groq_agent.tools import ToolExecutor


def main() -> None:
    workspace = Path(tempfile.mkdtemp(prefix="demo_animacion_"))
    executor = ToolExecutor(workspace_root=workspace, auto_yes=True)

    ui.console.print("\n[bold white]Demo: write_file (efecto maquina de escribir)[/bold white]\n")
    ui.animate_dispatch(
        "write_file",
        {"path": "index.html", "content": "<html>\n<body>\n<h1>Hola</h1>\n</body>\n</html>\n"},
        executor,
        root=workspace,
    )

    ui.console.print("\n[bold white]Demo: puntos + frases rotando (tool 'lenta' simulada, 20s)[/bold white]\n")

    class _ExecutorLento:
        root = workspace

        def dispatch(self, name: str, arguments: dict) -> str:
            time.sleep(20.0)
            return "Restaurante La Fragua de Vulcano, Calle Mayor 12, Madrid"

    ui.animate_dispatch(
        "render_check",
        {"target": "index.html"},
        _ExecutorLento(),
        root=workspace,
    )

    ui.console.print(f"\n[grey58]  (workspace de prueba: {workspace})[/grey58]\n")


if __name__ == "__main__":
    main()
