$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (Test-Path '.venv-stable/Scripts/python.exe') {
    $appPython = Join-Path $PSScriptRoot '.venv-stable/Scripts/python.exe'
} elseif (-not (Test-Path '.venv/Scripts/python.exe')) {
    python -c "import sys; assert sys.version_info >= (3,12) and sys.version_info.releaselevel == 'final', 'Please install stable Python 3.12 or newer'"
    if ($LASTEXITCODE -ne 0) { throw '需要稳定版 Python 3.12 或更新版本。' }
    python -m venv .venv
    & .venv/Scripts/python.exe -m pip install -r requirements.lock.txt
    if ($LASTEXITCODE -ne 0) { throw '依赖安装失败，请检查网络后重试。' }
    $appPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
} else {
    $appPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
}
if (-not (Test-Path 'dist/index.html')) {
    Push-Location frontend
    npm.cmd install --cache ../.npm-cache
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw '前端构建失败，请检查输出。' }
    Pop-Location
}
& $appPython -m uvicorn app.main:app --host 127.0.0.1 --port 8765
