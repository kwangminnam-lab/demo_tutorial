"""문서 삭제 단위 테스트 — 세 저장소 일괄 삭제·멱등·미존재 처리를 잠근다.

`IngestionService.delete_document`이 vector(source_url 기준)·graph·search_index
(doc_id 기준)에서 모두 지우고, 존재 여부를 bool로 정확히 보고하는지 검증한다.
실 DB 없이 InMemory 구현으로 결정론적으로 확인한다.
"""

from __future__ import annotations

from kms.adapters.graph.memory_store import InMemoryGraphStore
from kms.adapters.ingestion.chunk.models import Chunk, ChunkMetadata
from kms.adapters.searchindex.memory_store import InMemorySearchIndex
from kms.adapters.vectorstore.embedder import FakeEmbedder
from kms.adapters.vectorstore.memory_store import InMemoryVectorStore
from kms.domain.access import AccessLevel
from kms.domain.models import DocumentMetadata, FileDoc, SourceType
from kms.services.ingestion_service import IngestionService

_DOC = "abc123"
_URL = "local://이성주.docx"


def _seed() -> tuple[IngestionService, InMemoryVectorStore, InMemoryGraphStore, InMemorySearchIndex]:
    emb = FakeEmbedder()
    vs = InMemoryVectorStore(emb)
    gs = InMemoryGraphStore()
    si = InMemorySearchIndex()
    svc = IngestionService(vs, gs, emb, si)

    si.index_file(
        FileDoc(
            doc_id=_DOC, title="이성주.docx", source=SourceType.SLACK,
            access=AccessLevel.멤버, source_url=_URL,
        )
    )
    cmeta = ChunkMetadata(
        source=SourceType.SLACK, access=AccessLevel.멤버, source_url=_URL, chunk_index=0,
    )
    chunk = Chunk(chunk_id="c1", text="hello", metadata=cmeta)
    vs.index([chunk])
    gs.add_document(_DOC, DocumentMetadata(
        source=SourceType.SLACK, access=AccessLevel.멤버, source_url=_URL,
    ))
    gs.add_chunks(_DOC, [chunk])
    return svc, vs, gs, si


def test_delete_removes_from_all_three_stores() -> None:
    svc, vs, gs, si = _seed()

    assert svc.delete_document(_DOC) is True

    assert si.get(_DOC) is None              # 어휘 인덱스
    assert vs.get(["c1"]) == []              # 벡터(청크) — source_url로 삭제
    assert _DOC not in gs._documents         # 그래프 문서
    assert "c1" not in gs._chunk_doc         # 그래프 청크


def test_delete_missing_returns_false() -> None:
    svc, _vs, _gs, _si = _seed()
    assert svc.delete_document("존재안함") is False


def test_delete_is_scoped_to_one_doc() -> None:
    # 다른 source_url의 문서는 안 지워진다.
    svc, vs, _gs, si = _seed()
    other_url = "local://다른문서.docx"
    si.index_file(FileDoc(
        doc_id="other", title="다른문서", source=SourceType.SLACK,
        access=AccessLevel.멤버, source_url=other_url,
    ))
    vs.index([Chunk(chunk_id="c2", text="x", metadata=ChunkMetadata(
        source=SourceType.SLACK, access=AccessLevel.멤버, source_url=other_url, chunk_index=0,
    ))])

    svc.delete_document(_DOC)

    assert si.get("other") is not None
    assert vs.get(["c2"]) != []
