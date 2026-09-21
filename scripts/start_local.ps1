param(
    [int]$Port = 8080,
    [string]$DataDirectory = 'data/local'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonExecutable = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonExecutable)) { throw 'Create .venv and install dependencies first; see README.md.' }
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -or -not (Get-Command ffprobe -ErrorAction SilentlyContinue)) { throw 'FFmpeg and ffprobe must be on PATH.' }
if (-not (Test-Path -LiteralPath 'frontend/dist/index.html')) { throw 'Build the frontend first: pnpm --dir frontend build' }
$dataCandidate = if ([IO.Path]::IsPathRooted($DataDirectory)) { $DataDirectory } else { Join-Path $projectRoot $DataDirectory }
$resolvedData = [IO.Path]::GetFullPath($dataCandidate)
New-Item -ItemType Directory -Force -Path $resolvedData | Out-Null
$env:REPLAY_DATA_DIR = $resolvedData
$env:REPLAY_DATABASE_URL = ''
$env:REPLAY_STORAGE = 'local'
if (-not $env:REPLAY_MODEL_DEVICE) { $env:REPLAY_MODEL_DEVICE = 'cpu' }
$workerProcess = Start-Process -FilePath $pythonExecutable -ArgumentList @('-m', 'replay_studio.cli', 'worker', '--queue', 'all') -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $resolvedData 'worker.stdout.log') -RedirectStandardError (Join-Path $resolvedData 'worker.stderr.log')
try {
    Write-Host "Replay Studio: http://127.0.0.1:$Port"
    Write-Host "Data: $resolvedData | Worker logs are in this directory. Ctrl+C stops this local session."
    & $pythonExecutable -m replay_studio.cli serve --host 127.0.0.1 --port $Port
    if ($LASTEXITCODE -ne 0) { throw "API exited with code $LASTEXITCODE" }
} finally {
    if (-not $workerProcess.HasExited) { Stop-Process -Id $workerProcess.Id -ErrorAction SilentlyContinue }
}
