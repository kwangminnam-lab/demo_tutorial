#!/usr/bin/env bash
# DocuX 로컬 풀스택 종료 — 앱 프로세스 kill + DB 컨테이너 stop(볼륨·데이터 보존).
# 사용: bash da_h/stack-down.sh
set -uo pipefail
cd "$(dirname "$0")"                        # da_h 로 이동

echo "[앱] 프로세스 종료…"
pkill -f "scripts/serve_api.py" 2>/dev/null && echo "  API kill"  || echo "  API 없음"
pkill -f "scripts/serve_llm.py" 2>/dev/null; pkill -f "mlx_lm.server" 2>/dev/null && echo "  LLM kill" || echo "  LLM 없음"
pkill -f "vite" 2>/dev/null && echo "  FE kill"   || echo "  FE 없음"

echo "[DB] 컨테이너 stop(볼륨 보존, down 아님)…"
# compose 파싱이 env 참조할 수 있어 있으면 source(없어도 stop 은 동작).
[ -f /tmp/docux_local_env.sh ] && { set -a; source /tmp/docux_local_env.sh; set +a; }
docker compose -p docux stop 2>/dev/null || docker stop docux-postgres docux-neo4j 2>/dev/null || true

echo "✅ 종료 완료(데이터 보존). 다시 켜기: bash stack-up.sh"
