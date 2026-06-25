"""IngestionService 통합 테스트 — extract→chunk→색인 파이프라인 검증.

실 자격증명 없이 도는 dev 조립으로 검증한다: `FakeEmbedder` +
`InMemoryVectorStore` + `InMemoryGraphStore`. 추출은 `.txt` 추출기로 충분하므로
픽스처는 테스트가 `tmp_path`에 직접 써서 외부 파일·바이너리 의존을 없앤다.

AC(step 4): 매니페스트 적재 후 vectorstore 검색·메타 부착, 멱등(재적재 시 청크
수 불변), `source`/`access` 누락 거부, 다건 중 1건 실패 시 부분 적재 + 실패 보고.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from kms.adapters.graph.memory_store import InMemoryGraphStore
from kms.adapters.searchindex.memory_store import InMemorySearchIndex
from kms.adapters.vectorstore.memory_store import InMemoryVectorStore
from kms.adapters.vectorstore.embedder import FakeEmbedder
from kms.domain.errors import MetadataValidationError
from kms.services.ingestion_service import IngestItem, IngestionService


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def vectorstore(embedder: FakeEmbedder) -> InMemoryVectorStore:
    # InMemory — 서버 불요.
    return InMemoryVectorStore(embedder)


@pytest.fixture
def service(
    embedder: FakeEmbedder, vectorstore: InMemoryVectorStore
) -> Iterator[IngestionService]:
    yield IngestionService(
        vectorstore, InMemoryGraphStore(), embedder, InMemorySearchIndex()
    )


def _write_doc(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _write_manifest(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "manifest.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_manifest_ingest_is_searchable_with_metadata(
    tmp_path: Path,
    service: IngestionService,
    vectorstore: InMemoryVectorStore,
    embedder: FakeEmbedder,
) -> None:
    doc = _write_doc(tmp_path, "policy.txt", "연차 휴가는 입사 1년 후 15일 부여된다.")
    manifest = _write_manifest(
        tmp_path,
        f"""
- file_path: {doc}
  source: slack
  access: 1
  author: 김민
  author_department: research
""",
    )

    reports = service.ingest_manifest(manifest)

    assert len(reports) == 1
    assert reports[0].ok is True
    assert reports[0].chunk_count >= 1

    # vectorstore에서 검색되고 메타(source·access·author_department)가 붙어 있다.
    query_embedding = embedder.embed([doc.read_text(encoding="utf-8")])[0]
    results = vectorstore.query(query_embedding, top_k=5)
    assert results, "색인된 청크가 검색되어야 한다"
    _, _, metadata, _ = results[0]
    assert metadata["source"] == "slack"
    assert metadata["access"] == 1
    assert metadata["author_department"] == "research"


def test_reingesting_same_item_does_not_duplicate_chunks(
    tmp_path: Path,
    service: IngestionService,
    vectorstore: InMemoryVectorStore,
) -> None:
    doc = _write_doc(tmp_path, "guide.txt", "온보딩 가이드: 첫 주 체크리스트.")
    manifest = _write_manifest(
        tmp_path,
        f"""
- file_path: {doc}
  source: onedrive
  access: 2
""",
    )

    first = service.ingest_manifest(manifest)
    count_after_first = len(vectorstore._store)

    second = service.ingest_manifest(manifest)
    count_after_second = len(vectorstore._store)

    # 멱등: 콘텐츠 해시 ID라 재적재해도 doc_id·청크 수가 같고 중복이 안 생긴다.
    assert first[0].doc_id == second[0].doc_id
    assert first[0].chunk_count == second[0].chunk_count
    assert count_after_second == count_after_first


def test_missing_access_is_rejected_and_not_indexed(
    tmp_path: Path,
    service: IngestionService,
    vectorstore: InMemoryVectorStore,
) -> None:
    doc = _write_doc(tmp_path, "draft.txt", "권한 태그 없는 초안.")
    manifest = _write_manifest(
        tmp_path,
        f"""
- file_path: {doc}
  source: slack
""",  # access 누락
    )

    reports = service.ingest_manifest(manifest)

    # 도메인 에러로 거부되어 실패 보고 + 색인 안 됨.
    assert len(reports) == 1
    assert reports[0].ok is False
    assert reports[0].error is not None
    assert len(vectorstore._store) == 0


def test_from_raw_raises_domain_error_on_missing_required_field() -> None:
    # source/access 누락 항목은 도메인 에러(MetadataValidationError)로 거부된다.
    with pytest.raises(MetadataValidationError):
        IngestItem.from_raw({"file_path": "x.txt", "source": "slack"})


def test_partial_failure_ingests_rest_and_reports_failure(
    tmp_path: Path,
    service: IngestionService,
    vectorstore: InMemoryVectorStore,
) -> None:
    good = _write_doc(tmp_path, "ok.txt", "정상 적재되는 문서.")
    missing = tmp_path / "missing.txt"  # 파일을 만들지 않음 → 추출 실패
    manifest = _write_manifest(
        tmp_path,
        f"""
- file_path: {good}
  source: slack
  access: 1
- file_path: {missing}
  source: slack
  access: 1
""",
    )

    reports = service.ingest_manifest(manifest)

    # 1건 추출 실패해도 전체 중단 없이 나머지는 적재되고 실패가 보고된다.
    ok_reports = [report for report in reports if report.ok]
    failed_reports = [report for report in reports if not report.ok]
    assert len(ok_reports) == 1
    assert len(failed_reports) == 1
    assert ok_reports[0].file_path == str(good)
    assert failed_reports[0].file_path == str(missing)
    # 색인된 청크는 성공 문서 것만 존재한다.
    assert len(vectorstore._store) == ok_reports[0].chunk_count
