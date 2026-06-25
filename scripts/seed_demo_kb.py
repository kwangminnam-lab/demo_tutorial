"""demo_data 의 KB 기사(kb_articles.jsonl)를 docux-api 로 적재하는 시드 스크립트.

왜: 적재는 **파일 기반**(IngestItem.file_path)이라, JSONL 레코드를 그대로 못 올린다.
각 KB 기사를 markdown(.md)로 만들어 `POST /v1/ingest/upload` 로 업로드한다. 데모 인증
(ADR-026)이라 토큰 불요. docux-api 에 HTTP 로 닿는 곳(예: code-server)에서 실행한다.

대상: kb_articles.jsonl(사내 규정/지식 = RAG 코퍼스)만 적재.
제외: employee_questions.jsonl·chat_conversations.jsonl(평가/샘플 — 지식 아님).

사용법(code-server 터미널):
    pip install requests
    # docux-api 의 in-cluster svc 로(또는 외부 노출 URL):
    python scripts/seed_demo_kb.py \
        --api http://docux-api.001-doxdemo.svc.cluster.local:8000 \
        --jsonl demo_data/kb_articles.jsonl \
        --source onedrive

선행: 차원 불일치였다면 kms_chunks DROP + docux-api 재시작(1024 재생성) 후 실행.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import requests


def _md(article: dict[str, Any]) -> str:
    """KB 기사 dict → markdown 본문(제목 + 내용 + 메타 푸터)."""
    title = str(article.get("title") or article.get("id") or "무제")
    content = str(article.get("content") or "").strip()
    tags = article.get("tags") or []
    meta_lines = []
    for key, label in (
        ("category_label", "분류"),
        ("owner_dept", "담당부서"),
        ("source", "근거"),
        ("effective_date", "시행일"),
        ("last_updated", "최종수정"),
    ):
        val = article.get(key)
        if val:
            meta_lines.append(f"- {label}: {val}")
    if tags:
        meta_lines.append(f"- 태그: {', '.join(str(t) for t in tags)}")
    meta = ("\n\n## 메타데이터\n" + "\n".join(meta_lines)) if meta_lines else ""
    return f"# {title}\n\n{content}{meta}\n"


def _upload(api: str, source: str, filename: str, text: str, timeout: float) -> str:
    """1건 업로드 → job_id 반환. 실패 시 예외."""
    resp = requests.post(
        f"{api.rstrip('/')}/v1/ingest/upload",
        files={"file": (filename, text.encode("utf-8"), "text/plain")},
        data={"source": source},
        timeout=timeout,
    )
    resp.raise_for_status()
    return str(resp.json()["job_id"])


def _wait(api: str, job_id: str, timeout: float) -> dict[str, Any]:
    """잡 상태를 done/failed 까지 폴링."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(f"{api.rstrip('/')}/v1/ingest/jobs/{job_id}", timeout=10)
        r.raise_for_status()
        data = r.json()
        status = str(data.get("status", "")).lower()
        if status in {"done", "completed", "success", "succeeded", "failed", "error"}:
            return data
        time.sleep(0.5)
    return {"status": "timeout", "job_id": job_id}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="demo_data KB 적재(docux-api 업로드)")
    ap.add_argument("--api", required=True, help="docux-api 베이스 URL (예: http://docux-api:8000)")
    ap.add_argument("--jsonl", default="demo_data/kb_articles.jsonl", help="KB jsonl 경로")
    ap.add_argument("--source", default="onedrive", choices=["onedrive", "googledrive", "slack"])
    ap.add_argument("--timeout", type=float, default=180.0, help="요청·잡 타임아웃(초)")
    ap.add_argument(
        "--workers", type=int, default=4,
        help="동시 처리 수(업로드+폴링 병렬). 서버 부담되면 낮춰라.",
    )
    ap.add_argument(
        "--no-wait", action="store_true",
        help="잡 완료를 기다리지 않고 제출만 — 서버가 백그라운드로 처리(빠른 반환).",
    )
    args = ap.parse_args(argv)

    path = Path(args.jsonl)
    if not path.is_file():
        print(f"파일 없음: {path}", file=sys.stderr)
        return 2

    # 업로드 허용 확장자는 .pdf/.docx/.pptx/.xlsx/.txt — .md는 415. txt로 올린다(평문 파싱).
    articles = [
        json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()
    ]

    def _one(article: dict[str, Any]) -> bool:
        filename = f"{str(article.get('id') or 'kb')}.txt"
        try:
            job_id = _upload(args.api, args.source, filename, _md(article), args.timeout)
            if args.no_wait:
                print(f"  제출: {filename}")
                return True
            result = _wait(args.api, job_id, args.timeout)
            status = str(result.get("status", "?")).lower()
            if status in {"failed", "error", "timeout"}:
                print(f"  실패: {filename} — {result.get('error') or status}", file=sys.stderr)
                return False
            print(f"  적재: {filename} ({status})")
            return True
        except Exception as exc:  # noqa: BLE001 — 1건 실패가 전체를 멈추지 않게(부분 적재).
            print(f"  실패: {filename} — {exc}", file=sys.stderr)
            return False

    # 동시 처리 — 서버측 처리가 겹쳐 벽시계 단축. 순차 1s 폴링 갭도 제거.
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        results = list(pool.map(_one, articles))
    ok = sum(1 for r in results if r)
    fail = len(results) - ok

    print(f"\n완료: 성공 {ok} / 실패 {fail} (총 {len(results)})"
          + (" — 서버 백그라운드 처리 중(no-wait)" if args.no_wait else ""))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
