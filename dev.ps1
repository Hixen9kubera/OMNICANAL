# ╔══════════════════════════════════════════════════════════════╗
# ║  OMNICANAL · comando ÚNICO para desarrollo local               ║
# ║  Uso:   .\dev.ps1                                              ║
# ║  Hace setup (si falta) y arranca BACKEND + FRONTEND a la vez.  ║
# ╚══════════════════════════════════════════════════════════════╝
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$venvPython = Join-Path $root "backend\.venv\Scripts\python.exe"

Write-Host "`n=== OMNICANAL · arranque local ===`n" -ForegroundColor Cyan

# ── 1) Backend: crear venv + instalar deps si no existen ──────────
if (-not (Test-Path $venvPython)) {
    Write-Host "[backend] Creando entorno virtual e instalando dependencias..." -ForegroundColor Yellow
    python -m venv (Join-Path $root "backend\.venv")
    & $venvPython -m pip install --upgrade pip --quiet
    & $venvPython -m pip install -r (Join-Path $root "backend\requirements.txt") --quiet
}

# ── 2) Frontend: instalar deps si no existen ──────────────────────
if (-not (Test-Path (Join-Path $root "frontend\node_modules"))) {
    Write-Host "[frontend] Instalando dependencias (npm install)..." -ForegroundColor Yellow
    Push-Location (Join-Path $root "frontend"); npm install; Pop-Location
}

# ── 3) Arrancar BACKEND en segundo plano (misma consola) ──────────
Write-Host "[backend]  -> http://localhost:8000  (/docs para la API)" -ForegroundColor Green
$backend = Start-Process -FilePath $venvPython `
    -ArgumentList "-m","uvicorn","main:app","--reload","--port","8000" `
    -WorkingDirectory (Join-Path $root "backend") -PassThru -NoNewWindow

# ── 4) Arrancar FRONTEND en primer plano (Ctrl+C corta todo) ──────
Write-Host "[frontend] -> http://localhost:3000`n" -ForegroundColor Green
try {
    Push-Location (Join-Path $root "frontend")
    npm run dev
}
finally {
    Pop-Location
    if ($backend -and -not $backend.HasExited) {
        Write-Host "`nDeteniendo backend..." -ForegroundColor Yellow
        Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
    }
}
