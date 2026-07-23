param(
    [string]$Python
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Python) {
    $LocalPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    $Python = if (Test-Path -LiteralPath $LocalPython) { $LocalPython } else { "python" }
}

Push-Location $ProjectRoot
try {
    & $Python -m pip install -e ".[build]"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install build dependencies."
    }
    & $Python -m PyInstaller --noconfirm --clean "NAI-Prompt-Planner.spec"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed."
    }
    Write-Output "Built: $ProjectRoot\dist\NAI-Prompt-Planner.exe"
}
finally {
    Pop-Location
}
