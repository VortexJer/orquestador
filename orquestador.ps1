#Requires -Version 5.1
<#
Lanzador de la terminal agentica de pruebas (groq_agent). Activa el
entorno virtual, carga las variables de .env (incluidas las API keys de los proveedores),
y llama a `python -m groq_agent.cli` pasando cualquier argumento tal
cual - asi da lo mismo correr:

    .\orquestador.ps1
    .\orquestador.ps1 --skill web-builder-specialist "landing page para..."
    .\orquestador.ps1 --list-models

que escribir el comando largo entero. Ver el pie de este archivo para
como hacer que "orquestador" funcione desde cualquier carpeta/terminal,
no solo parado en este directorio.
#>

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

$venvActivate = Join-Path $repoRoot ".venv\Scripts\Activate.ps1"
if (-not (Test-Path $venvActivate)) {
    Write-Error "No se encontro el entorno virtual en $venvActivate. Corre primero: python -m venv .venv; pip install -r requirements-dev.txt"
    exit 1
}
& $venvActivate

$envFile = Join-Path $repoRoot ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | Where-Object { $_ -match '^[^#]' -and $_ -match '=' } | ForEach-Object {
        $key, $value = $_ -split '=', 2
        Set-Item "env:$($key.Trim())" $value.Trim()
    }
} else {
    Write-Warning "No hay .env en $repoRoot - corré: .\orquestador.ps1 --config  (te pide las API keys una a una)."
}

Push-Location $repoRoot
try {
    python -m groq_agent.cli @args
} finally {
    Pop-Location
}

<#
Para que "orquestador" (sin ".\" ni ruta) funcione desde CUALQUIER
carpeta/terminal nueva, agregar a tu perfil de PowerShell una funcion
que apunte aca (no lo hacemos automaticamente - es tu $PROFILE, un
archivo fuera de este repo):

    notepad $PROFILE
    # agregar esta linea (ajustando la ruta si moviste el repo):
    function orquestador { & "C:\Users\Joaquin ERE\Desktop\orquestador-modelos\orquestador.ps1" @args }

Guardar, cerrar, y abrir una terminal nueva - a partir de ahi "orquestador"
funciona como cualquier otro comando.
#>
