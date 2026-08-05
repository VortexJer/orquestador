# Instalador de Orquestador (Windows PowerShell).
# Uso rapido (una linea, en una terminal PowerShell):
#   irm https://raw.githubusercontent.com/VortexJer/orquestador/main/install.ps1 | iex
#
# Clona el repo, crea el entorno virtual, instala dependencias, prepara el .env
# y deja el comando `orquestador` disponible desde cualquier carpeta.

$ErrorActionPreference = "Stop"
$Repo = "https://github.com/VortexJer/orquestador.git"
$Dest = if ($env:ORQUESTADOR_DIR) { $env:ORQUESTADOR_DIR } else { Join-Path $HOME "orquestador" }

Write-Host "== Instalador de Orquestador ==" -ForegroundColor Cyan

# 1) Requisitos
foreach ($cmd in @("git", "python")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Host "Falta '$cmd'. Instalalo y vuelve a ejecutar (Python: python.org, Git: git-scm.com)." -ForegroundColor Red
        exit 1
    }
}

# 2) Clonar o actualizar
if (Test-Path (Join-Path $Dest ".git")) {
    Write-Host "Ya existe en $Dest, actualizando..."
    git -C "$Dest" pull --ff-only
} else {
    Write-Host "Clonando en $Dest..."
    git clone "$Repo" "$Dest"
}
Set-Location "$Dest"

# 3) Entorno virtual + dependencias
if (-not (Test-Path ".venv")) { python -m venv .venv }
$py = Join-Path $Dest ".venv\Scripts\python.exe"
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r requirements.txt
Write-Host "Dependencias instaladas."

# 4) .env (a partir de la plantilla, si no existe)
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Creado .env (vacio). Rellenalo con 'orquestador --config' o el sync de NovaChat."
}

# 5) Comando `orquestador` en tu perfil de PowerShell (idempotente, con marcadores)
$ps1 = Join-Path $Dest "orquestador.ps1"
$marker = "# >>> orquestador >>>"
$block = "$marker`nfunction orquestador { & `"$ps1`" @args }`n# <<< orquestador <<<"
if (-not (Test-Path $PROFILE)) { New-Item -ItemType File -Path $PROFILE -Force | Out-Null }
$prof = Get-Content $PROFILE -Raw -ErrorAction SilentlyContinue
if (-not $prof -or ($prof -notmatch [regex]::Escape($marker))) {
    Add-Content $PROFILE "`n$block"
    Write-Host "Anadida la funcion 'orquestador' a tu perfil ($PROFILE)."
} else {
    Write-Host "La funcion 'orquestador' ya estaba en tu perfil."
}

Write-Host ""
Write-Host "LISTO. Abre una terminal NUEVA y ejecuta:" -ForegroundColor Green
Write-Host "  orquestador --config      # anade tus API keys (gratis, sin tarjeta), una a una"
Write-Host "  orquestador               # abre la terminal agentica"
Write-Host ""
Write-Host "O importa las keys de tu cuenta NovaChat de golpe:" -ForegroundColor Green
Write-Host "  python scripts\sync_novachat_keys.py"
