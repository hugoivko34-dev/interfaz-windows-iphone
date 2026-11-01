# Starts Interfaz Windows-iPhone, installing its dependencies first if they're missing.
$python = "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
& $python -c "import serial" 2>$null
if ($LASTEXITCODE -ne 0) { & $python -m pip install --user -r "$PSScriptRoot\requirements.txt" }
& $python "$PSScriptRoot\desktop\interfaz.py"
