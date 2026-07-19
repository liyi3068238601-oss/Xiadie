param(
  [string]$PackagedResources = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$expectedFile = Join-Path $root "backend\app\knowledge_embeddings.py"
$modelSource = Join-Path (Split-Path $root -Parent) "bge-m3"
$modelStage = Join-Path $root "desktop\model-stage\bge-m3"

function Require-File([string]$Path, [string]$Label) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "Missing $Label`: $Path"
  }
  Write-Host "[ok] $Label" -ForegroundColor Green
}

function Verify-Model([string]$ModelRoot, [string]$Label, [string]$ExpectedHash) {
  Require-File (Join-Path $ModelRoot "config.json") "$Label config"
  Require-File (Join-Path $ModelRoot "tokenizer.json") "$Label tokenizer"
  $onnx = Join-Path $ModelRoot "onnx\model_quantized.onnx"
  Require-File $onnx "$Label ONNX"
  $actual = (Get-FileHash -LiteralPath $onnx -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($actual -ne $ExpectedHash) {
    throw "$Label ONNX fingerprint mismatch: expected $ExpectedHash, got $actual"
  }
  Write-Host "[ok] $Label ONNX SHA-256 $actual" -ForegroundColor Green
}

$contract = Get-Content -LiteralPath $expectedFile -Raw -Encoding UTF8
$match = [regex]::Match($contract, 'MODEL_SHA256\s*=\s*"([0-9a-f]{64})"')
if (-not $match.Success) { throw "Could not read MODEL_SHA256 from $expectedFile" }
$expectedHash = $match.Groups[1].Value

Require-File (Join-Path $root "frontend\dist\index.html") "frontend entry"
Require-File (Join-Path $root "backend\dist\xiadie-backend\xiadie-backend.exe") "frozen backend"
Require-File (Join-Path $root "backend\dist\xiadie-backend\_internal\app\knowledge\xiadie_lore.md") "frozen lore"

if (Test-Path -LiteralPath (Join-Path $modelSource "onnx\model_quantized.onnx")) {
  Verify-Model $modelSource "source BGE-M3" $expectedHash
  Verify-Model $modelStage "staged BGE-M3" $expectedHash
}

if (-not $PackagedResources) {
  $PackagedResources = Join-Path $root "dist-installer\win-unpacked\resources"
}
if (Test-Path -LiteralPath $PackagedResources -PathType Container) {
  Require-File (Join-Path $PackagedResources "frontend\index.html") "packaged frontend"
  Require-File (Join-Path $PackagedResources "backend\xiadie-backend.exe") "packaged backend"
  Require-File (Join-Path $PackagedResources "backend\_internal\app\knowledge\xiadie_lore.md") "packaged lore"
  if (Test-Path -LiteralPath (Join-Path $modelStage "onnx\model_quantized.onnx")) {
    Verify-Model (Join-Path $PackagedResources "models\bge-m3") "packaged BGE-M3" $expectedHash
  }
} else {
  Write-Host "[skip] Packaged resources do not exist yet: $PackagedResources" -ForegroundColor Yellow
}

Write-Host "Release resource verification passed." -ForegroundColor Cyan
