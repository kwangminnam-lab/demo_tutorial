#!/usr/bin/env pwsh
# Windows GPU LLM 서버 런처 — make 없이 PowerShell로 실행한다.
# serve_llm.py(--backend llamacpp)로 인자를 전달한다. llama-server.exe가 PATH에 있어야 한다.
#
# 사용 예:
#   .\scripts\serve_llm.ps1
#   .\scripts\serve_llm.ps1 -Model .\local_models\gemma-e4b-Q4_K_M.gguf -NGpuLayers 32
#   $env:GGUF_MODEL = "D:\models\gemma-e4b-Q4_K_M.gguf"; .\scripts\serve_llm.ps1

param(
    [string]$Model = $env:GGUF_MODEL,
    [int]$Port = 8001,
    [int]$NGpuLayers = $(if ($env:GGUF_NGL) { [int]$env:GGUF_NGL } else { -1 }),
    [int]$CtxSize = 8192
)

$ErrorActionPreference = "Stop"

# python 런처 탐색(python 우선, 없으면 py).
$py = if (Get-Command python -ErrorAction SilentlyContinue) { "python" }
      elseif (Get-Command py -ErrorAction SilentlyContinue) { "py" }
      else { throw "Python을 찾지 못했습니다 — python 또는 py가 PATH에 있어야 합니다." }

# 스크립트 위치 기준으로 serve_llm.py 경로 해소(어느 디렉토리에서 실행해도 동작).
$scriptPath = Join-Path $PSScriptRoot "serve_llm.py"

$cliArgs = @($scriptPath, "--backend", "llamacpp",
             "--port", $Port, "--n-gpu-layers", $NGpuLayers, "--ctx-size", $CtxSize)
if ($Model) { $cliArgs += @("--model", $Model) }

& $py @cliArgs
exit $LASTEXITCODE
