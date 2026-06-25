"""적재 CLI 진입점 — 매니페스트를 받아 일괄 적재한다.

사용법:
    python scripts/ingest.py <manifest.yaml|.json>

`Settings`(.env)가 정한 backend를 `build_services`로 조립해 `IngestionService`를
실행하고 적재 요약(성공·실패 건수)을 출력한다. 진입점은 얇게 — 조립·실행·출력만
하고 비즈니스 로직은 서비스에 둔다 (ARCHITECTURE 진입점 규칙).

backend는 .env 토글이 가른다: 사내 K8s 토폴로지면 PG(pgvector·tsvector) + Neo4j +
openai_compat 임베딩으로 실적재한다. env 미설정이면 기존 dev 경로(FakeEmbedder +
InMemory)로 그대로 동작한다 — 같은 진입점이 dev/prod를 Settings로만 가른다.
"""

from __future__ import annotations

import sys
from pathlib import Path

from kms.config.settings import get_settings
from kms.factory import build_services


def main(argv: list[str]) -> int:
    """매니페스트 경로를 받아 적재하고 요약을 출력한다. 종료 코드 반환."""
    if len(argv) != 1:
        print("사용법: python scripts/ingest.py <manifest.yaml|.json>", file=sys.stderr)
        return 2

    manifest_path = Path(argv[0])
    if not manifest_path.is_file():
        print(f"매니페스트를 찾을 수 없음: {manifest_path}", file=sys.stderr)
        return 2

    # .env(Settings)로 선택된 실 backend를 조립한다 — EMBEDDER/VECTOR/SEARCH/GRAPH
    # 토글에 따라 PG·Neo4j·openai_compat 임베딩으로 적재한다. 검색·적재가 같은
    # 어댑터 인스턴스를 공유하도록 build_services가 조립한다(일관성).
    service = build_services(get_settings()).ingestion

    reports = service.ingest_manifest(manifest_path)

    succeeded = [report for report in reports if report.ok]
    failed = [report for report in reports if not report.ok]
    total_chunks = sum(report.chunk_count for report in succeeded)
    print(
        f"적재 완료: 성공 {len(succeeded)}건({total_chunks} 청크), 실패 {len(failed)}건"
    )
    for report in failed:
        print(f"  실패: {report.file_path} — {report.error}", file=sys.stderr)

    # 부분 실패는 비0 종료 코드로 알린다 (성공이 하나도 없거나 실패가 있으면 1).
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
