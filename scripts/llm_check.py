#!/usr/bin/env python3
"""LLM(gpt-oss) 연결 진단 — 챗봇이 LLM에 못 붙을 때 원인 단계를 갈라서 짚는다.

앱(OpenAICompatLLM)과 **동일한 계약**으로 찌른다:
    ping     = GET  {LLM_BASE_URL}/models        (Authorization: Bearer <key>)
    generate = POST {LLM_BASE_URL}/chat/completions

실행 위치가 중요하다. **반드시 api 파드 안에서** 돌려야 env·egress가 실제 런타임과 같다:
    kubectl -n 001-doxdemo exec deploy/docux-api -- python /app/scripts/llm_check.py
(로컬에서 돌리면 LLM_BASE_URL 등 env를 직접 export 후 실행.)

시크릿 무로깅: 키는 출력하지 않고 존재/길이만 표시한다.
"""

from __future__ import annotations

import os
import sys

import httpx

BASE = os.environ.get("LLM_BASE_URL", "").rstrip("/")
MODEL = os.environ.get("LLM_MODEL_NAME", "")
KEY = os.environ.get("LLM_API_KEY") or None


def line(label: str, ok: bool, detail: str = "") -> None:
    mark = "OK " if ok else "FAIL"
    print(f"[{mark}] {label}" + (f" — {detail}" if detail else ""))


def headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if KEY:
        h["Authorization"] = f"Bearer {KEY}"
    return h


def main() -> int:
    print("=== LLM 연결 진단 (gpt-oss) ===")

    # 1) env 존재 — 가장 흔한 원인: LLM_API_KEY 빈 값(배포 화면서 secret 미입력)
    line("LLM_BASE_URL 설정", bool(BASE), BASE or "(비어있음!)")
    line("LLM_MODEL_NAME 설정", bool(MODEL), MODEL or "(비어있음!)")
    line("LLM_API_KEY 존재", bool(KEY), f"len={len(KEY)}" if KEY else "(비어있음 — 게이트웨이면 401)")
    if not BASE or not MODEL:
        print(">> base_url/model 비었음. ConfigMap docux-config 확인. 중단.")
        return 2

    # 2) ping = GET /models — 도달성 + 인증. 단계별 예외로 원인 분리.
    models_url = f"{BASE}/models"
    print(f"\n-- GET {models_url}")
    try:
        r = httpx.get(models_url, headers=headers(), timeout=10.0)
        if r.status_code == 200:
            ids = [m.get("id") for m in r.json().get("data", [])][:10]
            line("GET /models", True, f"200, models={ids}")
            if MODEL not in ids and ids:
                line("MODEL_NAME이 /models 목록에 있음", False,
                     f"'{MODEL}' 없음 → chat/completions 404/400 유발 가능")
        elif r.status_code in (401, 403):
            line("GET /models", False, f"{r.status_code} 인증 실패 → LLM_API_KEY 틀림/빈값")
        elif r.status_code == 404:
            line("GET /models", False,
                 f"404 → BASE_URL 경로 오류(게이트웨이 deployment 경로 %2F 인코딩 확인)")
        else:
            line("GET /models", False, f"{r.status_code} {r.text[:200]}")
    except httpx.ConnectError as e:
        line("GET /models", False, f"연결 실패(ConnectError) → DNS/egress 차단 의심: {e}")
        print(">> 파드 egress가 게이트웨이 호스트에 못 닿음. NetworkPolicy/방화벽 확인.")
        return 3
    except httpx.ConnectTimeout:
        line("GET /models", False, "연결 타임아웃 → egress/방화벽 차단 의심")
        return 3
    except httpx.HTTPError as e:
        line("GET /models", False, f"{type(e).__name__}: {e}")

    # 3) generate = POST /chat/completions — 실제 추론 한 방(짧게).
    chat_url = f"{BASE}/chat/completions"
    print(f"\n-- POST {chat_url}")
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "ping. reply with: pong"}],
        "stream": False,
        "max_tokens": int(os.environ.get("LLM_MAX_TOKENS", "64")),
    }
    try:
        r = httpx.post(chat_url, json=payload, headers=headers(), timeout=60.0)
        if r.status_code == 200:
            data = r.json()
            content = data["choices"][0]["message"].get("content")
            line("POST /chat/completions", True, f"content={content!r}")
            if not content:
                line("content 비어있음", False,
                     "reasoning 모델이 max_tokens 다 소진 → LLM_MAX_TOKENS 키워라(현재 16000 권장)")
        else:
            line("POST /chat/completions", False, f"{r.status_code} {r.text[:300]}")
    except httpx.HTTPError as e:
        line("POST /chat/completions", False, f"{type(e).__name__}: {e}")
        return 3

    print("\n=== 끝 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
