# One-command project setup for JanSarthi AI.
#
# Creates a real, isolated Python virtual environment for the backend (`.venv/` -- previously
# nothing existed here, so `pip install` calls were silently hitting the system-wide Python
# install instead), installs the frontend's npm packages, and sets up Phoenix's own SEPARATE
# isolated venv (`.phoenix-venv/`).
#
# Why Phoenix gets its own venv instead of living in `.venv/` too: confirmed directly (2026-08-29)
# that installing the full `arize-phoenix` server package forces Starlette from 0.41 -> 1.6 (a
# full major-version jump) and FastAPI from 0.115 -> 0.141 -- both sit directly under this app's
# own auth/CSRF middleware, so that's a real compatibility risk, not a formality. The two
# lightweight Phoenix CLIENT packages this app actually imports (`arize-phoenix-otel`,
# `arize-phoenix-client`) stay in `.venv/` via requirements.txt, same as always -- only the full
# SERVER package is kept isolated. See docs/OBSERVABILITY.md for the full writeup.
#
# Usage: right-click > Run with PowerShell, or from a terminal: .\setup.ps1
# Safe to re-run -- every step below skips work that's already done.

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

function Write-Step($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

# --- 1. Backend: main virtual environment ---------------------------------------------------
Write-Step "Backend virtual environment (.venv)"
if (-not (Test-Path ".venv")) {
    python -m venv .venv
    Write-Host "Created .venv"
} else {
    Write-Host ".venv already exists, reusing it"
}

$venvPython = ".\.venv\Scripts\python.exe"
& $venvPython -m pip install --upgrade pip --quiet

# torch's default `pip install` on Windows pulls the much larger CUDA wheel -- this project only
# ever runs CPU inference (see requirements.txt's own comment on the torch line), so the CPU-only
# wheel is installed explicitly first, same manual step requirements.txt already documents.
Write-Host "Installing CPU-only torch (avoids the much larger default CUDA wheel)..."
& $venvPython -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.13.0 --quiet

Write-Host "Installing backend requirements (this includes sentence-transformers/chromadb/langgraph -- can take a few minutes on first run)..."
& $venvPython -m pip install -r requirements.txt

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example -- fill in your real API keys before running the backend." -ForegroundColor Yellow
} else {
    Write-Host ".env already exists, leaving it alone"
}

# --- 2. Frontend: npm packages ----------------------------------------------------------------
Write-Step "Frontend packages (frontend-react/node_modules)"
if (Get-Command npm -ErrorAction SilentlyContinue) {
    Push-Location "frontend-react"
    npm install
    if (-not (Test-Path ".env")) {
        Copy-Item ".env.example" ".env" -ErrorAction SilentlyContinue
    }
    Pop-Location
} else {
    Write-Host "npm not found on PATH -- skipping frontend setup. Install Node.js, then run 'npm install' inside frontend-react/ yourself." -ForegroundColor Yellow
}

# --- 3. Phoenix: its OWN isolated venv, kept separate on purpose (see comment at the top) -----
Write-Step "Phoenix observability server (.phoenix-venv) -- optional, only needed for local tracing"
if (-not (Test-Path ".phoenix-venv")) {
    python -m venv .phoenix-venv
    Write-Host "Created .phoenix-venv"
} else {
    Write-Host ".phoenix-venv already exists, reusing it"
}
$phoenixPython = ".\.phoenix-venv\Scripts\python.exe"
& $phoenixPython -m pip install --upgrade pip --quiet
& $phoenixPython -m pip install arize-phoenix --quiet
Write-Host "Phoenix server installed in its own venv."

# --- Done ---------------------------------------------------------------------------------------
Write-Step "Setup complete. To run everything:"
Write-Host "  1. Backend:   .\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload"
Write-Host "  2. Frontend:  cd frontend-react; npm run dev"
Write-Host "  3. Phoenix (optional, for local tracing):"
Write-Host "     .\.phoenix-venv\Scripts\python.exe -m phoenix.server.main serve"
Write-Host ""
Write-Host "First real run also needs: python scripts\seed_admin.py (from inside .venv) to create your first Super Admin account."
Write-Host "See docs\OBSERVABILITY.md for what Phoenix/LangSmith actually do and how to configure them."
