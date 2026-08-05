#!/usr/bin/env bash
# Instalador de Orquestador (Linux / macOS).
# Uso rapido:
#   curl -fsSL https://raw.githubusercontent.com/VortexJer/orquestador/main/install.sh | bash
#
# Clona el repo, crea el entorno virtual, instala dependencias, prepara el .env
# y anade un alias `orquestador` a tu shell.
set -euo pipefail

REPO="https://github.com/VortexJer/orquestador.git"
DEST="${ORQUESTADOR_DIR:-$HOME/orquestador}"

echo "== Instalador de Orquestador =="

# 1) Requisitos
for cmd in git python3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Falta '$cmd'. Instalalo y vuelve a ejecutar." >&2
    exit 1
  fi
done

# 2) Clonar o actualizar
if [ -d "$DEST/.git" ]; then
  echo "Ya existe en $DEST, actualizando..."
  git -C "$DEST" pull --ff-only
else
  echo "Clonando en $DEST..."
  git clone "$REPO" "$DEST"
fi
cd "$DEST"

# 3) Entorno virtual + dependencias
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/python -m pip install --quiet --upgrade pip
./.venv/bin/python -m pip install --quiet -r requirements.txt
echo "Dependencias instaladas."

# 4) .env
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Creado .env (vacio). Rellenalo con 'orquestador --config' o el sync de NovaChat."
fi

# 5) Alias en el shell
LAUNCHER="$DEST/orquestador.sh"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
cd "$DEST"
set -a; [ -f .env ] && . ./.env; set +a
exec ./.venv/bin/python -m groq_agent.cli "\$@"
EOF
chmod +x "$LAUNCHER"

RC="${HOME}/.bashrc"; [ -n "${ZSH_VERSION:-}" ] && RC="${HOME}/.zshrc"
MARK="# >>> orquestador >>>"
if ! grep -qF "$MARK" "$RC" 2>/dev/null; then
  printf '\n%s\nalias orquestador="%s"\n# <<< orquestador <<<\n' "$MARK" "$LAUNCHER" >> "$RC"
  echo "Anadido el alias 'orquestador' a $RC."
fi

echo ""
echo "LISTO. Abre una terminal NUEVA (o 'source $RC') y ejecuta:"
echo "  orquestador --config      # anade tus API keys (gratis, sin tarjeta)"
echo "  orquestador               # abre la terminal agentica"
echo ""
echo "O importa las keys de tu cuenta NovaChat:"
echo "  ./.venv/bin/python scripts/sync_novachat_keys.py"
