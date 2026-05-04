& "C:\Users\erwan\Dev\Scripts\sync_sandbox_test.ps1"
if ($LASTEXITCODE -ne 0) { exit 1 }

$venv = "$PWD\venv\Scripts\python.exe"

if (-not (Test-Path $venv)) {
    python -m venv venv
    & "$PWD\venv\Scripts\pip.exe" install -r requirements.txt
}

$log = "$PWD\logs\runner.log"

while ($true) {
    $process = Start-Process `
        -FilePath $venv `
        -ArgumentList "bot.py" `
        -RedirectStandardOutput $log `
        -RedirectStandardError $log `
        -PassThru

    Wait-Process -Id $process.Id

    Write-Host "Bot stopped -> restart in 5s" -ForegroundColor Yellow
    Start-Sleep -Seconds 5
}
