"""IngestionService 단위 테스트 — 적재가 파일 단위 어휘 인덱스에도 색인하는지 검증.

vector(임시 디스크 Chroma)·graph(InMemoryGraphStore)·search(InMemorySearchIndex)를
모두 dev 조립으로 주입해 외부 서버·실모델 없이 결정론으로 돈다(test_ingestion.py
패턴). 여기서는 vector/graph가 아니라 **search_index 적재**(FileDoc)에 초점을 둔다.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from kms.adapters.graph.memory_store import InMemoryGraphStore
from kms.adapters.ingestion.chunk.models import Chunk, ChunkMetadata
from kms.adapters.searchindex.memory_store import InMemorySearchIndex
from kms.adapters.vectorstore.memory_store import InMemoryVectorStore
from kms.adapters.vectorstore.embedder import FakeEmbedder
from kms.domain.access import AccessLevel
from kms.domain.models import SourceType
from kms.services.ingestion_service import (
    IngestItem,
    IngestionService,
    _excerpt,
    _first_meaningful_excerpt,
)


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def search_index() -> InMemorySearchIndex:
    return InMemorySearchIndex()


@pytest.fixture
def service(
    embedder: FakeEmbedder,
    search_index: InMemorySearchIndex,
    tmp_path: Path,
) -> Iterator[IngestionService]:
    vectorstore = InMemoryVectorStore(embedder)
    yield IngestionService(vectorstore, InMemoryGraphStore(), embedder, search_index)


def _write_doc(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_ingest_indexes_file_in_search_index_with_metadata(
    tmp_path: Path,
    service: IngestionService,
    search_index: InMemorySearchIndex,
) -> None:
    doc = _write_doc(tmp_path, "vacation_policy.txt", "연차 휴가는 입사 1년 후 부여된다.")
    item = IngestItem(
        file_path=str(doc),
        source="slack",  # type: ignore[arg-type]
        access=AccessLevel.임직원,
        author="김민",
        author_department="research",
    )

    report = service.ingest_item(item)
    assert report.ok is True

    # 파일명 토큰으로 어휘 검색 시 그 파일 1건이 잡힌다 (title·source·access 보존).
    hits = search_index.search("vacation_policy", AccessLevel.임직원)
    assert len(hits) == 1
    hit = hits[0]
    assert hit.file.title == "vacation_policy.txt"
    assert hit.file.source.value == "slack"
    assert hit.file.access == AccessLevel.임직원


def test_doc_type_is_derived_from_extension(
    tmp_path: Path,
    service: IngestionService,
    search_index: InMemorySearchIndex,
) -> None:
    doc = _write_doc(tmp_path, "onboarding.txt", "온보딩 가이드: 첫 주 체크리스트.")
    item = IngestItem(
        file_path=str(doc),
        source="onedrive",  # type: ignore[arg-type]
        access=AccessLevel.임직원,
    )

    service.ingest_item(item)

    hits = search_index.search("onboarding", AccessLevel.임직원)
    assert len(hits) == 1
    # 확장자 .txt → 대문자 "TXT".
    assert hits[0].file.doc_type == "TXT"


def test_reingesting_same_file_is_idempotent_one_hit(
    tmp_path: Path,
    service: IngestionService,
    search_index: InMemorySearchIndex,
) -> None:
    doc = _write_doc(tmp_path, "handbook.txt", "사내 핸드북 본문.")
    item = IngestItem(
        file_path=str(doc),
        source="slack",  # type: ignore[arg-type]
        access=AccessLevel.임직원,
    )

    first = service.ingest_item(item)
    second = service.ingest_item(item)

    # 멱등: 같은 콘텐츠 → 같은 doc_id → index_file 덮어쓰기 → 검색 결과 1건.
    assert first.doc_id == second.doc_id
    hits = search_index.search("handbook", AccessLevel.임직원)
    assert len(hits) == 1


def _chunk(text: str) -> Chunk:
    """발췌 테스트용 최소 Chunk (메타는 발췌에 안 쓰여 기본값으로 채움)."""
    return Chunk(
        chunk_id="c",
        text=text,
        metadata=ChunkMetadata(
            source=SourceType.ONEDRIVE, access=AccessLevel.임직원, chunk_index=0
        ),
    )


def test_excerpt_strips_image_placeholders() -> None:
    text = (
        "**==> picture [293 x 151] intentionally omitted <==**\n\n"
        "신규 요금제와 할인 정책을 정리한다."
    )
    excerpt = _excerpt(text)
    assert "picture" not in excerpt
    assert "==>" not in excerpt
    assert "신규 요금제와 할인 정책을 정리한다." in excerpt


def test_excerpt_strips_empty_table_headers_and_separators() -> None:
    text = (
        "| Unnamed: 0 | Unnamed: 1 | Unnamed: 2 |\n"
        "| --- | --- | --- |\n"
        "| nan | 분기 매출 분석 | nan |"
    )
    excerpt = _excerpt(text)
    assert "Unnamed" not in excerpt
    assert "nan" not in excerpt
    assert "---" not in excerpt
    assert "분기 매출 분석" in excerpt


def test_first_meaningful_excerpt_skips_noise_only_first_chunk() -> None:
    # 첫 청크가 이미지 플레이스홀더만 → 건너뛰고 본문 있는 둘째 청크를 요약으로.
    chunks = [
        _chunk("**==> picture [10 x 14] intentionally omitted <==**"),
        _chunk("Runway 2.0 요금정책 개정안 본문입니다."),
    ]
    assert _first_meaningful_excerpt(chunks) == "Runway 2.0 요금정책 개정안 본문입니다."


# ── LLM 요약(description) ──────────────────────────────────────────────────


class _FixedSummarizer:
    """결정론 요약기 — 고정 문자열 반환(LLM 경로 검증용, LLMClient 프로토콜 충족)."""

    def __init__(self, text: str = "문서 핵심을 한 문장으로 요약한 결과.") -> None:
        self._text = text

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        response_format: dict[str, object] | None = None,
    ) -> str:
        return self._text


class _BoomSummarizer:
    """항상 예외를 던지는 요약기 — 폴백 경로 검증용."""

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        response_format: dict[str, object] | None = None,
    ) -> str:
        raise RuntimeError("llm unavailable")


def _svc_with(summarizer: object | None) -> IngestionService:
    emb = FakeEmbedder()
    return IngestionService(
        InMemoryVectorStore(emb),
        InMemoryGraphStore(),
        emb,
        InMemorySearchIndex(),
        summarizer=summarizer,  # type: ignore[arg-type]
    )


def test_describe_uses_llm_summary_over_raw_excerpt() -> None:
    # 요약기가 있으면 raw 발췌가 아니라 LLM 요약을 description으로 쓴다.
    chunks = [_chunk("Runway 2.0 요금정책 " * 20)]
    assert _svc_with(_FixedSummarizer())._describe(chunks) == "문서 핵심을 한 문장으로 요약한 결과."


def test_describe_falls_back_to_excerpt_without_summarizer() -> None:
    # 요약기 미주입 → 추출 발췌로 폴백(LLM 없이도 적재 동작).
    assert _svc_with(None)._describe([_chunk("Runway 요금정책 본문입니다.")]) == "Runway 요금정책 본문입니다."


def test_describe_falls_back_when_summarizer_raises() -> None:
    # LLM 호출 실패는 삼키지 않고(로그) 발췌로 폴백 — 적재를 막지 않는다.
    assert _svc_with(_BoomSummarizer())._describe([_chunk("Runway 요금정책 본문입니다.")]) == (
        "Runway 요금정책 본문입니다."
    )


def test_describe_truncates_long_summary() -> None:
    # 과한 길이 요약은 검색 화면 상한(200자)으로 자른다.
    summary = _svc_with(_FixedSummarizer("가" * 500))._describe([_chunk("본문 텍스트.")])
    assert len(summary) == 200
