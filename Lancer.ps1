$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = 'python'
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.venv\Scripts\python.exe')) { $python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe' }
# Options supplémentaires (HTTPS, dossier de données) : voir docs/HEBERGEMENT.md
& $python -m app.server @args
if ($LASTEXITCODE -ne 0) { Read-Host 'Le démarrage a échoué. Consulter le message ci-dessus puis appuyer sur Entrée' }
