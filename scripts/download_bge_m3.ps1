# Download BGE-M3 (Embedding) ~2.12GB
# Run: .\scripts\download_bge_m3.ps1

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

$Mirrors = @("https://hf-mirror.com", "https://huggingface.co")
$BestMirror = $null
foreach ($m in $Mirrors) {
    try {
        $req = [System.Net.WebRequest]::Create("$m/BAAI/bge-m3/resolve/main/config.json")
        $req.Timeout = 5000
        $req.GetResponse().Close()
        $BestMirror = $m
        Write-Host "Mirror: $m [OK]" -ForegroundColor Green
        break
    } catch {
        Write-Host "Mirror: $m [FAIL]" -ForegroundColor DarkGray
    }
}
if (-not $BestMirror) { Write-Host "All mirrors unreachable." -ForegroundColor Red; exit 1 }

$Repo = "BAAI/bge-m3"
$Cache = "$env:USERPROFILE\.cache\huggingface\hub\models--BAAI--bge-m3\snapshots\main"
New-Item -ItemType Directory -Force -Path $Cache | Out-Null

$Files = @("pytorch_model.bin","tokenizer.json","sentencepiece.bpe.model","config.json","tokenizer_config.json","special_tokens_map.json","config_sentence_transformers.json","modules.json","sentence_bert_config.json")

foreach ($f in $Files) {
    $url = "$BestMirror/$Repo/resolve/main/$f"
    $out = Join-Path $Cache $f
    if ((Test-Path $out) -and ((Get-Item $out).Length -gt 500)) {
        Write-Host "[SKIP] $f" -ForegroundColor DarkGray
        continue
    }
    Write-Host "[DOWNLOAD] $f" -ForegroundColor Yellow
    try {
        Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing -TimeoutSec 3600
        $mb = [math]::Round((Get-Item $out).Length / 1MB, 1)
        Write-Host "  [DONE] ${mb}MB" -ForegroundColor Green
    } catch {
        Write-Host "  [FAIL] $_" -ForegroundColor Red
    }
}

# Verify
$model = Join-Path $Cache "pytorch_model.bin"
if ((Test-Path $model) -and ((Get-Item $model).Length -gt 2000000000)) {
    $gb = [math]::Round((Get-Item $model).Length / 1GB, 2)
    Write-Host "SUCCESS: BGE-M3 ${gb}GB ready!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Next step - rebuild vectors:" -ForegroundColor Cyan
    Write-Host "  python -c ""from retrieval.embedder import build_embeddings; build_embeddings(model_name='BAAI/bge-m3')""" -ForegroundColor White
} else {
    Write-Host "INCOMPLETE: pytorch_model.bin missing or too small" -ForegroundColor Red
}
Read-Host "Press Enter to exit"
