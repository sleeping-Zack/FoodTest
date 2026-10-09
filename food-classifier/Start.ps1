param([int]$Port = 8011)
$ErrorActionPreference = 'Stop'
$appDirectory = $PSScriptRoot
$runtimePython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
if (-not (Test-Path -LiteralPath $runtimePython)) {
    $runtimePython = (Get-Command python -ErrorAction Stop).Source
}
$address = "http://127.0.0.1:$Port"
try {
    $health = Invoke-RestMethod -Uri "$address/api/health" -TimeoutSec 2
    if ($health.app -eq 'food-classifier') {
        Write-Host "食品分类系统已运行：$address"
        exit 0
    }
    throw "端口 $Port 已被其他服务使用。请使用 -Port 指定其他端口。"
} catch [System.Net.WebException] {
    # No service is listening yet.
}
$dataDirectory = Join-Path $appDirectory 'data'
New-Item -ItemType Directory -Path $dataDirectory -Force | Out-Null
$scriptPath = Join-Path $appDirectory 'server.py'
$launchArguments = @('-X', 'utf8', ('"' + $scriptPath + '"'), '--port', $Port)
$process = Start-Process -FilePath $runtimePython -ArgumentList $launchArguments -WorkingDirectory $appDirectory -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $dataDirectory 'server.log') -RedirectStandardError (Join-Path $dataDirectory 'server-error.log')
$process.Id | Set-Content -LiteralPath (Join-Path $dataDirectory 'server.pid')
$ready = $false
for ($attempt = 0; $attempt -lt 15; $attempt++) {
    Start-Sleep -Milliseconds 400
    try {
        $health = Invoke-RestMethod -Uri "$address/api/health" -TimeoutSec 2
        if ($health.app -eq 'food-classifier') { $ready = $true; break }
    } catch { }
    if ($process.HasExited) { break }
}
if (-not $ready) { throw '启动失败，请查看 food-classifier/data/server-error.log。' }
Write-Host "食品分类系统已启动：$address"
Write-Host '在浏览器打开上面的地址即可使用。仅允许本机访问。'
