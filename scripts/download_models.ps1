# BCM-RAG Model Download Script
# Downloads BGE-Reranker-v2-m3 + BGE-M3 from fastest mirror
# Usage: .\scripts\download_models.ps1
#        .\scripts\download_models.ps1 -RerankerOnly
#        .\scripts\download_models.ps1 -BgeM3Only

param(
    [switch]$RerankerOnly,
    [switch]$BgeM3Only
)

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

# ============================================
# Mirror list (tried in order)
# ============================================
$Mirrors = @(
    "https://hf-mirror.com",
    "https://huggingface.co"
)

# ============================================
# Speed test: pick fastest mirror
# ============================================
function Test-Mirror {
    param([string]$Url)
    try {
        $req = [System.Net.WebRequest]::Create("$Url/BAAI/bge-m3/resolve/main/config.json")
        $req.Timeout = 5000
        $resp = $req.GetResponse()
        $resp.Close()
        return $true
    } catch {
        return $false
    }
}

Write-Host "Testing mirrors..." -ForegroundColor Yellow
$BestMirror = $null
foreach ($Mirror in $Mirrors) {
    if (Test-Mirror $Mirror) {
        $BestMirror = $Mirror
        Write-Host "  [OK] $Mirror" -ForegroundColor Green
        break
    } else {
        Write-Host "  [FAIL] $Mirror" -ForegroundColor DarkGray
    }
}

if (-not $BestMirror) {
    Write-Host "ERROR: All mirrors unreachable. Check network." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "Using: $BestMirror" -ForegroundColor Cyan
Write-Host ""

# ============================================
# Download function
# ============================================
function Download-File {
    param(
        [string]$Url,
        [string]$OutFile,
        [string]$Description = ""
    )
    $OutDir = Split-Path $OutFile -Parent
    if (-not (Test-Path $OutDir)) {
        New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
    }
    if ((Test-Path $OutFile) -and ((Get-Item $OutFile).Length -gt 500)) {
        Write-Host "    [SKIP] $Description (already exists)" -ForegroundColor DarkGray
        return $true
    }
    try {
        Write-Host "    [DOWNLOAD] $Description" -ForegroundColor Yellow
        Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing -TimeoutSec 3600
        $size = [math]::Round((Get-Item $OutFile).Length / 1MB, 1)
        Write-Host "    [DONE] ${size}MB" -ForegroundColor Green
        return $true
    } catch {
        Write-Host "    [FAIL] $_" -ForegroundColor Red
        return $false
    }
}

# ============================================
# Model 1: BGE-Reranker-v2-m3
# ============================================
if (-not $BgeM3Only) {
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  1/2  BGE-Reranker-v2-m3 (Cross-Encoder)" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan

    $Repo = "BAAI/bge-reranker-v2-m3"
    $Cache = "$env:USERPROFILE\.cache\huggingface\hub\models--BAAI--bge-reranker-v2-m3\snapshots\main"

    $RerankerFiles = @(
        "model.safetensors",
        "tokenizer.json",
        "sentencepiece.bpe.model",
        "config.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "configuration.json"
    )

    foreach ($File in $RerankerFiles) {
        $Url = "$BestMirror/$Repo/resolve/main/$File"
        $Out = Join-Path $Cache $File
        Download-File -Url $Url -OutFile $Out -Description $File
    }
    Write-Host ""
}

# ============================================
# Model 2: BGE-M3
# ============================================
if (-not $RerankerOnly) {
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  2/2  BGE-M3 (Embedding)" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan

    $Repo = "BAAI/bge-m3"
    $Cache = "$env:USERPROFILE\.cache\huggingface\hub\models--BAAI--bge-m3\snapshots\main"

    $BgeM3Files = @(
        "pytorch_model.bin",
        "tokenizer.json",
        "sentencepiece.bpe.model",
        "config.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "config_sentence_transformers.json",
        "modules.json",
        "sentence_bert_config.json"
    )

    foreach ($File in $BgeM3Files) {
        $Url = "$BestMirror/$Repo/resolve/main/$File"
        $Out = Join-Path $Cache $File
        Download-File -Url $Url -OutFile $Out -Description $File
    }
    Write-Host ""
}

# ============================================
# Verify
# ============================================
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Verify" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

$RerankerOk = $false
$RerankerModel = "$env:USERPROFILE\.cache\huggingface\hub\models--BAAI--bge-reranker-v2-m3\snapshots\main\model.safetensors"
if (Test-Path $RerankerModel) {
    $gb = [math]::Round((Get-Item $RerankerModel).Length / 1GB, 2)
    if ($gb -gt 1.5) {
        Write-Host "  [OK] BGE-Reranker: ${gb}GB" -ForegroundColor Green
        $RerankerOk = $true
    } else {
        Write-Host "  [INCOMPLETE] BGE-Reranker: ${gb}GB (expect ~2.1GB)" -ForegroundColor Red
    }
} else {
    Write-Host "  [MISSING] BGE-Reranker: not downloaded" -ForegroundColor DarkGray
}

$BgeM3Ok = $false
$BgeM3Model = "$env:USERPROFILE\.cache\huggingface\hub\models--BAAI--bge-m3\snapshots\main\pytorch_model.bin"
if (Test-Path $BgeM3Model) {
    $gb = [math]::Round((Get-Item $BgeM3Model).Length / 1GB, 2)
    if ($gb -gt 1.5) {
        Write-Host "  [OK] BGE-M3: ${gb}GB" -ForegroundColor Green
        $BgeM3Ok = $true
    } else {
        Write-Host "  [INCOMPLETE] BGE-M3: ${gb}GB (expect ~2.12GB)" -ForegroundColor Red
    }
} else {
    Write-Host "  [MISSING] BGE-M3: not downloaded" -ForegroundColor DarkGray
}

Write-Host ""

# ============================================
# Next steps
# ============================================
if ($RerankerOk) {
    Write-Host "Cross-Encoder ready! Auto-activates on next pipeline start." -ForegroundColor Green
}
if ($BgeM3Ok) {
    Write-Host "BGE-M3 ready! Run to rebuild vectors:" -ForegroundColor Green
    Write-Host "  python -c ""from retrieval.embedder import build_embeddings; build_embeddings(model_name='BAAI/bge-m3')""" -ForegroundColor White
    Write-Host ""
    Write-Host "Then run eval:  python -m retrieval.eval" -ForegroundColor Cyan
}

if (-not $RerankerOk -and -not $BgeM3Ok) {
    Write-Host "Both models failed to download. Check network and retry." -ForegroundColor Red
    Write-Host ""
    Write-Host "Manual download URLs:" -ForegroundColor Yellow
    Write-Host "  $BestMirror/BAAI/bge-reranker-v2-m3/resolve/main/model.safetensors"
    Write-Host "  $BestMirror/BAAI/bge-m3/resolve/main/pytorch_model.bin"
}

Write-Host ""
Read-Host "Press Enter to exit"
