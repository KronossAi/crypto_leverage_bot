& "C:\Users\erwan\Dev\Scripts\sync_sandbox_test.ps1"
if ($LASTEXITCODE -ne 0) { exit 1 }

$venv = "$PWD\venv\Scripts\python.exe"

if (-not (Test-Path $venv)) {
    python -m venv venv
    & "$PWD\venv\Scripts\pip.exe" install -r requirements.txt
}

$process = Start-Process -FilePath $venv -ArgumentList "bot.py" -PassThru

Start-Sleep -Seconds 120

if (!$process.HasExited) {
    Stop-Process -Id $process.Id -Force
    Write-Host "Test 2min OK (process killed)" -ForegroundColor Green
    exit 0
} else {
    Write-Error "Bot stopped early"
    exit 1
}
