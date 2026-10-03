$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m pytest tests -q
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
python -m PyInstaller --noconfirm km_windows.spec
if ($LASTEXITCODE -ne 0) { throw 'Executable build failed' }
$env:KM_DATA_DIR = Join-Path $env:TEMP ('km_windows_build_check_' + [guid]::NewGuid().ToString('N'))
$process = Start-Process -FilePath (Join-Path $PSScriptRoot 'dist\km_windows.exe') -ArgumentList '--self-test' -WindowStyle Hidden -Wait -PassThru
if ($process.ExitCode -ne 0) { throw 'Packaged runtime checks failed' }
$report = Get-Content -LiteralPath (Join-Path $env:KM_DATA_DIR 'self-test-result.json') -Raw | ConvertFrom-Json
if (-not $report.ok -or $report.sounds -ne 59) { throw 'Packaged runtime checks failed' }
$hash = (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'dist\km_windows.exe') -Algorithm SHA256).Hash.ToLower()
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'dist\km_windows.exe.sha256'), ($hash + "  km_windows.exe`n"), [Text.Encoding]::ASCII)
Write-Output 'Executable, checksum and packaged runtime checks completed.'
