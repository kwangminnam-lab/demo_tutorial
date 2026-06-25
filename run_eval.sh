#!/usr/bin/env bash
# AI OCR(Qwen3-VL) 성능 eval 원샷 실행 — 플랫폼 code-server용.
# venv 셋업 → 경량 의존 설치 → env 주입 → scripts/eval_ocr.py 실행.
# 결과: 콘솔(문서별 CER/WER/faithful/iou + 요약) + MLflow run 1건.
#
# 사용:  chmod +x run_eval.sh && ./run_eval.sh
set -euo pipefail

# ============ 0. 설정값 (환경에 맞게만 수정) ============
IMAGES_DIR="${IMAGES_DIR:-samlple_data/images}"            # 원천 jpg 위치
LABELS_DIR="${LABELS_DIR:-samlple_data/annotations_label}" # 라벨 json 위치
LIMIT="${LIMIT:-20}"                                        # 0=전체

export VLM_BASE_URL="${VLM_BASE_URL:-http://qwen3-vl-4b.001-doxdemo.svc.cluster.local:8000/v1}"
export VLM_MODEL="${VLM_MODEL:-Qwen3-VL-4B-Instruct}"
export MLFLOW_ENABLED="${MLFLOW_ENABLED:-true}"
export MLFLOW_TRACKING_URI="${MLFLOW_TRACKING_URI:-https://mlflow.try.mrxrunway.ai}"
export MLFLOW_EXPERIMENT="${MLFLOW_EXPERIMENT:-001-doxdemo.qwen3-4b-vlm}"
# Settings 필수 필드(eval은 DB 미사용 — 더미여도 됨)
export DATABASE_URL="${DATABASE_URL:-postgresql://x}"
export NEO4J_URI="${NEO4J_URI:-bolt://x}"
export NEO4J_USER="${NEO4J_USER:-x}"
export NEO4J_PASSWORD="${NEO4J_PASSWORD:-x}"
export JWT_SECRET="${JWT_SECRET:-x}"

# 스크립트 위치를 기준으로 이동(어디서 호출해도 동작).
cd "$(dirname "$0")"

# ============ 1. venv + eval 의존(경량) ============
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install -U pip >/dev/null
pip install httpx pymupdf pydantic pydantic-settings "mlflow-skinny>=2,<3"

export PYTHONPATH="$PWD/src"

# ============ 2. 데이터 확인(없으면 중단) ============
if [ ! -d "$IMAGES_DIR" ] || [ ! -d "$LABELS_DIR" ]; then
  echo "데이터 없음: $IMAGES_DIR / $LABELS_DIR 에 원천 jpg + 라벨 json 배치 후 재실행." >&2
  echo "(repo는 samlple_data 제외 — code-server에 직접 업로드/복사)" >&2
  exit 1
fi
echo "이미지 $(ls "$IMAGES_DIR"/*.jpg 2>/dev/null | wc -l)장, 라벨 $(ls "$LABELS_DIR"/*.json 2>/dev/null | wc -l)건"

# ============ 3. eval 실행 ============
python scripts/eval_ocr.py \
  --images-dir "$IMAGES_DIR" \
  --labels-dir "$LABELS_DIR" \
  --limit "$LIMIT"
