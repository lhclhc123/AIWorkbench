# 用「装了 PyQt6 的那个 Python」打包为 onedir（dist\AIWorkbench\）。
# 注意：托管 venv 里没有 PyQt6，用它打包会得到一个 18MB、缺 Qt DLL 的废产物。
# 所以脚本会依次探测：venv -> 系统 Python 3.12 -> 当前 python，取第一个能 import PyQt6 的。
# 用法：在 AIWorkbuddy 目录下右键"使用 PowerShell 运行"本脚本。
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# 路径一律按环境变量拼，仓库里不写死任何用户名。
$candidates = @(
    (Join-Path $env:USERPROFILE ".workbuddy\binaries\python\envs\default\Scripts\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
    (Get-Command python -ErrorAction SilentlyContinue).Source
) | Where-Object { $_ -and (Test-Path $_) }

$py = $null
foreach ($c in $candidates) {
    $hasQt = & $c -c "import PyQt6" 2>$null; if ($LASTEXITCODE -eq 0) { $py = $c; break }
}
if (-not $py) {
    Write-Host "没有找到装了 PyQt6 的 Python，请先: pip install PyQt6 requests markdown Pygments pyinstaller"
    exit 1
}
Write-Host "使用 Python: $py"

& $py -m PyInstaller --name AIWorkbench --onedir --windowed --noconfirm `
    --hidden-import PyQt6.sip `
    --hidden-import markdown.extensions.tables `
    --hidden-import markdown.extensions.nl2br `
    --exclude-module numpy --exclude-module PIL `
    main.py
Write-Host "构建完成，产物在 dist\AIWorkbench\"
