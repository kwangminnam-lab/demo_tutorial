"""RAGService 단위 테스트 — 권한 인지 근거 기반 RAG + 출처 인용.

FakeEmbedder + InMemoryVectorStore + InMemoryGraphStore + StubLLM로 실모델·서버
없이 돈다(CONVENTIONS: LLM은 모킹/계약 테스트). `_RecordingLLM`은 StubLLM을 감싸
호출 여부를 기록해 "근거 0건이면 LLM 미호출"을 검증한다.

검증 핵심(AC):
- 권한 밖(상위 access) 청크는 컨텍스트(프롬프트)·인용 어디에도 안 들어간다.
- 근거 0건이면 LLM 호출 없이 grounded=False 응답.
- 근거 있으면 citations가 실제 사용한 출처를 담는다.
- 같은 부서 작성 근거가 우선 포함된다(search_service 부서 가중 위임).
"""

from collections.abc import Iterator
from pathlib import Path

from kms.adapters.graph import InMemoryGraphStore
from kms.adapters.ingestion.chunk import Chunk, ChunkMetadata
from tests._stub_llm import StubLLM
from kms.adapters.searchindex import InMemorySearchIndex
from kms.adapters.vectorstore import InMemoryVectorStore, FakeEmbedder
from kms.config.settings import Settings
from kms.domain.access import AccessLevel
from kms.domain.models import SourceType, UserContext
from kms.services.rag_service import RAGService
from kms.services.search_service import SearchService

USER = UserContext(user_id="u1", department="영업부", access_level=AccessLevel.임직원)


class _RecordingLLM:
    """StubLLM을 감싸 호출을 기록하는 스파이. `LLMClient` 프로토콜을 만족한다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []
        self._inner = StubLLM()

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        response_format: dict[str, object] | None = None,
    ) -> str:
        self.calls.append(("generate", prompt, system))
        return self._inner.generate(prompt, system=system)

    def stream(self, prompt: str, *, system: str | None = None) -> Iterator[str]:
        self.calls.append(("stream", prompt, system))
        yield from self._inner.stream(prompt, system=system)


def _chunk(
    chunk_id: str,
    text: str,
    *,
    source: SourceType = SourceType.ONEDRIVE,
    access: AccessLevel = AccessLevel.임직원,
    department: str | None = "영업부",
) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        text=text,
        metadata=ChunkMetadata(
            source=source,
            access=access,
            author_department=department,
            chunk_index=0,
        ),
    )


def _rag(
    tmp_path: Path, chunks: list[Chunk], llm: _RecordingLLM
) -> RAGService:
    """주어진 청크를 vectorstore·graph에 적재한 RAGService를 만든다."""
    embedder = FakeEmbedder()
    vectorstore = InMemoryVectorStore(embedder)
    graph = InMemoryGraphStore()
    vectorstore.index(chunks)
    for chunk in chunks:
        doc_id = f"doc-{chunk.chunk_id}"
        graph.add_document(doc_id, chunk.metadata)
        graph.add_chunks(doc_id, [chunk])
    settings = Settings(
        database_url="postgresql://test",
        neo4j_uri="bolt://test",
        neo4j_user="u",
        neo4j_password="p",
    )
    search_service = SearchService(
        vectorstore, graph, embedder, settings, InMemorySearchIndex()
    )
    return RAGService(search_service, llm, settings)


def test_answer_excludes_higher_access_chunk_from_context_and_citations(
    tmp_path: Path,
) -> None:
    # Arrange: 임직원 본문 + 사장 본문(권한 밖). 본문을 달리해 컨텍스트 누출을 검증.
    llm = _RecordingLLM()
    rag = _rag(
        tmp_path,
        [
            _chunk("emp", "임직원용 매출 보고", access=AccessLevel.임직원),
            _chunk("ceo", "사장만 보는 기밀 전략", access=AccessLevel.사장),
        ],
        llm,
    )

    # Act
    answer = rag.answer("매출", USER)

    # Assert: 사장 청크는 인용에도, LLM에 넘긴 프롬프트(컨텍스트)에도 없다.
    cited_ids = {citation.doc_id for citation in answer.citations}
    assert "emp" in cited_ids
    assert "ceo" not in cited_ids
    prompt = llm.calls[0][1]
    assert "사장만 보는 기밀 전략" not in prompt
    assert "임직원용 매출 보고" in prompt


def test_answer_no_evidence_returns_ungrounded_without_llm_call(
    tmp_path: Path,
) -> None:
    # Arrange: 사장 전용 문서만 적재 → 임직원 사용자에겐 근거 0건.
    llm = _RecordingLLM()
    rag = _rag(
        tmp_path,
        [_chunk("ceo", "기밀", access=AccessLevel.사장)],
        llm,
    )

    # Act
    answer = rag.answer("아무거나", USER)

    # Assert: 근거 0건 → LLM 미호출, grounded=False, 인용 없음.
    assert answer.grounded is False
    assert answer.citations == []
    assert llm.calls == []


def test_citations_returns_sources_without_llm_call(tmp_path: Path) -> None:
    # Arrange: 접근 가능한 근거 1건 + 권한 밖 1건.
    llm = _RecordingLLM()
    rag = _rag(
        tmp_path,
        [
            _chunk("emp", "임직원용 매출 보고", access=AccessLevel.임직원),
            _chunk("ceo", "사장만 보는 기밀 전략", access=AccessLevel.사장),
        ],
        llm,
    )

    # Act: 출처 전용 경로(citations_only) — LLM 재생성 없이 출처만.
    answer = rag.citations("매출", USER)

    # Assert: LLM 미호출(지연 제거), 본문 비움, grounded=True, 권한 내만 인용.
    assert llm.calls == []
    assert answer.text == ""
    assert answer.grounded is True
    cited_ids = {citation.doc_id for citation in answer.citations}
    assert "emp" in cited_ids
    assert "ceo" not in cited_ids


def test_citations_no_evidence_returns_ungrounded_without_llm_call(
    tmp_path: Path,
) -> None:
    # Arrange: 권한 밖 문서만 → 임직원에겐 근거 0건.
    llm = _RecordingLLM()
    rag = _rag(tmp_path, [_chunk("ceo", "기밀", access=AccessLevel.사장)], llm)

    # Act
    answer = rag.citations("아무거나", USER)

    # Assert: 근거 0건 → LLM 미호출, grounded=False, 인용 없음.
    assert llm.calls == []
    assert answer.grounded is False
    assert answer.citations == []


def test_answer_citations_contain_used_sources(tmp_path: Path) -> None:
    # Arrange: 접근 가능한 두 청크.
    llm = _RecordingLLM()
    rag = _rag(
        tmp_path,
        [
            _chunk("a", "첫째 근거 본문"),
            _chunk("b", "둘째 근거 본문"),
        ],
        llm,
    )

    # Act
    answer = rag.answer("근거", USER)

    # Assert: 근거 있음 + 사용한 두 출처를 모두 인용.
    assert answer.grounded is True
    cited_ids = {citation.doc_id for citation in answer.citations}
    assert cited_ids == {"a", "b"}
    # 인용 source는 메타 출처 그대로.
    assert all(citation.source == SourceType.ONEDRIVE for citation in answer.citations)


def test_answer_same_department_evidence_ranked_first(tmp_path: Path) -> None:
    # Arrange: 유사도 동점(같은 텍스트), 부서만 다른 두 근거.
    llm = _RecordingLLM()
    rag = _rag(
        tmp_path,
        [
            _chunk("same_dept", "공통 본문", department="영업부"),
            _chunk("other_dept", "공통 본문", department="인사부"),
        ],
        llm,
    )

    # Act
    answer = rag.answer("공통", USER)

    # Assert: 부서 가중 위임 — 같은 부서(영업부) 근거가 첫 인용.
    assert answer.citations[0].doc_id == "same_dept"
    assert answer.citations[1].doc_id == "other_dept"


def test_summary_intent_uses_summary_system_prompt(tmp_path: Path) -> None:
    """질의에 요약/정리 키워드가 있으면 요약용 system 프롬프트를 쓴다."""
    llm = _RecordingLLM()
    rag = _rag(tmp_path, [_chunk("a", "긴 본문 내용")], llm)

    rag.answer("이 자료 요약해줘", USER)

    _, _, system = llm.calls[-1]
    assert system is not None
    assert "요약" in system and "정리" in system  # 요약 프롬프트 지시 포함


def test_plain_question_uses_qa_system_prompt(tmp_path: Path) -> None:
    """요약 키워드가 없으면 일반 QA용 system 프롬프트를 쓴다(자연스러운 답변)."""
    llm = _RecordingLLM()
    rag = _rag(tmp_path, [_chunk("a", "본문")], llm)

    rag.answer("계약 조건이 뭐야?", USER)

    _, _, system = llm.calls[-1]
    assert system is not None
    assert "자연스러운 문장으로" in system  # QA 프롬프트 특유 문구


def test_select_system_prompt_differs_by_intent() -> None:
    from kms.services.rag_service import _select_system_prompt

    assert _select_system_prompt("요점만 정리") != _select_system_prompt("이건 뭐야?")
