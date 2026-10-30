# Starts EdgeHop, installing pyserial first if it's missing.
$python = "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"
& $python -c "import serial" 2>$null
if ($LASTEXITCODE -ne 0) { & $python -m pip install --user pyserial }
& $python "$PSScriptRoot\desktop\edgehop.py"
