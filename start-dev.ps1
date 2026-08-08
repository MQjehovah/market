$root = $PSScriptRoot
Write-Host "启动后端 (http://localhost:8000) ..."
Start-Process -FilePath "python" -ArgumentList "run.py" -WorkingDirectory (Join-Path $root "backend") -WindowStyle Hidden
Write-Host "启动前端 (http://localhost:5173) ..."
Set-Location (Join-Path $root "frontend")
npm run dev
