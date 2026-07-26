param(
  [int]$Port = 18756
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $root "backend\dist\xiadie-backend\xiadie-backend.exe"
$model = Join-Path $root "desktop\model-stage\bge-m3"
$tempRoot = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
$data = [IO.Path]::GetFullPath((Join-Path $env:TEMP "xiadie-k9-frozen-smoke"))
if (-not ($data + '\').StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
  throw "Smoke-test data path escaped the temporary directory: $data"
}
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw "Missing frozen backend: $exe" }
if (-not (Test-Path -LiteralPath (Join-Path $model "onnx\model_quantized.onnx"))) {
  throw "Missing staged BGE-M3 model: $model"
}

New-Item -ItemType Directory -Force -Path $data | Out-Null
$token = "k9-frozen-smoke-token-at-least-32-bytes"
$env:XIADIE_PORT = [string]$Port
$env:XIADIE_API_TOKEN = $token
$env:XIADIE_DATA_DIR = $data
$env:XIADIE_BGE_M3_DIR = $model
$process = Start-Process -FilePath $exe -PassThru -WindowStyle Hidden
try {
  $ready = $false
  for ($attempt = 0; $attempt -lt 60; $attempt++) {
    try {
      $health = Invoke-RestMethod "http://127.0.0.1:$Port/api/health" -Headers @{ "X-Xiadie-Token" = $token }
      $ready = $true
      break
    } catch {
      Start-Sleep -Milliseconds 500
    }
  }
  if (-not $ready) { throw "Frozen backend did not become healthy within 30 seconds." }
  $embedding = Invoke-RestMethod "http://127.0.0.1:$Port/api/knowledge/embedding/status" -Headers @{ "X-Xiadie-Token" = $token }
  $dateBody = @{
    label = "LIFE timezone smoke"
    recurrence = "once"
    date_year = 2028
    date_month = 2
    date_day = 29
    timezone_id = "Asia/Shanghai"
    celebration_policy = "none"
  } | ConvertTo-Json
  $lifeDate = Invoke-RestMethod "http://127.0.0.1:$Port/api/life/dates" -Method Post `
    -Headers @{ "X-Xiadie-Token" = $token } -ContentType "application/json" -Body $dateBody
  if ($health.status -ne "ok" -or -not $embedding.available -or -not $embedding.local_only) {
    throw "Frozen backend or local embedding contract is unavailable."
  }
  if ($lifeDate.timezone_id -ne "Asia/Shanghai" -or $lifeDate.status -ne "active") {
    throw "Frozen backend LIFE timezone contract is unavailable."
  }
  [pscustomobject]@{
    Health = $health.status
    EmbeddingAvailable = $embedding.available
    LocalOnly = $embedding.local_only
    LifeTimezone = $lifeDate.timezone_id
    ModelHash = $embedding.model_sha256
  }
} finally {
  if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force }
  if (Test-Path -LiteralPath $data) { Remove-Item -LiteralPath $data -Recurse -Force }
}
