#!/bin/sh
# DocuX LLM 컨테이너 엔트리포인트 — env를 llama-server 인자로 조립해 실행한다.
# (Dockerfile.llm에서 사용. serve_llm.py의 llamacpp 경로와 동일한 커맨드를 K8s env로 구성.)
set -eu

# llama-server 위치 — PATH 우선, 공식 이미지 기본 경로(/app/llama-server) 폴백.
BIN="$(command -v llama-server 2>/dev/null || echo /app/llama-server)"
if [ ! -x "$BIN" ]; then
    echo "llama-server 실행 파일을 찾지 못했습니다 (PATH·/app 확인) — 베이스 이미지가 server-cuda 변종인지 확인하세요." >&2
    exit 1
fi

# 모델 파일 존재 점검(조용한 실패 금지) — GGUF는 보통 /models PVC로 마운트한다.
if [ ! -f "${MODEL_PATH}" ]; then
    echo "모델을 찾을 수 없습니다: ${MODEL_PATH} — /models 볼륨 마운트와 MODEL_PATH env를 확인하세요." >&2
    exit 1
fi

echo "[llm] exec: $BIN --model ${MODEL_PATH} --alias ${MODEL_ALIAS} --host ${LLAMA_HOST} --port ${LLAMA_PORT} --n-gpu-layers ${LLAMA_N_GPU_LAYERS} --ctx-size ${LLAMA_CTX_SIZE} --jinja"
exec "$BIN" \
    --model "${MODEL_PATH}" \
    --alias "${MODEL_ALIAS}" \
    --host "${LLAMA_HOST}" \
    --port "${LLAMA_PORT}" \
    --n-gpu-layers "${LLAMA_N_GPU_LAYERS}" \
    --ctx-size "${LLAMA_CTX_SIZE}" \
    --jinja
