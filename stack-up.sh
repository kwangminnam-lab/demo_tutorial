#!/usr/bin/env bash
# DocuX 로컬 풀스택 기동 — DB(postgres+neo4j) + MLX LLM(:8001) + FastAPI(:8000) + Vite(:5173).
# 사용: bash da_h/stack-up.sh   (또는 chmod +x 후 ./stack-up.sh)
set -euo pipefail
cd "$(dirname "$0")"                       # da_h 로 이동

# .venv 활성화 — make 타깃이 bare `python`(pyenv shim)을 써서 kms/mlx_lm 미발견되는 것 방지.
if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
else
  echo "❌ .venv 없음 — 'pip install -e \".[dev,ml]\"' + mlx-lm 설치 필요." >&2
  exit 1
fi

ENVF=/tmp/docux_local_env.sh               # 로컬 전용 설정(throwaway). 재부팅 시 날아가면 복구 필요.
if [ ! -f "$ENVF" ]; then
  echo "❌ $ENVF 없음 — 로컬 env 복구해야 함(클러스터용 .env 아님)." >&2
  exit 1
fi
set -a; source "$ENVF"; set +a

echo "[1/4] DB 컨테이너(docux) 기동…"
docker compose -p docux start 2>/dev/null || docker compose -p docux up -d

echo "[2/4] MLX LLM :8001 …"
nohup make serve-llm  > /tmp/docux-llm.log 2>&1 &

echo "[3/4] FastAPI :8000 …"
nohup make serve-api  > /tmp/docux-api.log 2>&1 &

echo "[4/4] Vite 프론트 :5173 …"
( cd frontend && nohup npm run dev > /tmp/docux-fe.log 2>&1 & )

echo "기동 대기(로그: /tmp/docux-{llm,api,fe}.log) …"
curl -s --retry 40 --retry-delay 2 --retry-all-errors -o /dev/null -w "  API  :8000 = %{http_code}\n" http://127.0.0.1:8000/healthz   || echo "  API 미응답"
curl -s --retry 40 --retry-delay 2 --retry-all-errors -o /dev/null -w "  LLM  :8001 = %{http_code}\n" http://127.0.0.1:8001/v1/models || echo "  LLM 미응답(모델 로딩 길 수)"
curl -s --retry 20 --retry-delay 2 --retry-all-errors -o /dev/null -w "  FE   :5173 = %{http_code}\n" http://127.0.0.1:5173        || echo "  FE 미응답"
echo "✅ 완료 → http://localhost:5173"
