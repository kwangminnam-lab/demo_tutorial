"""적재(ingestion) 유스케이스 — extract → 검증 → chunk → 색인 오케스트레이션.

흐름(ADR-006·ARCHITECTURE 적재): 추출기 registry로 IR을 뽑고, item 메타로
`DocumentMetadata`를 구성해 청커에 넘긴 뒤, 콘텐츠 해시 기반 멱등 ID를 부여해
vectorstore(InMemory/OpenSearch k-NN)·graph(Neo4j/InMemory) 양쪽에 색인한다.

**멱등(CONVENTIONS 재적재 안전)**: `doc_id = sha256(파일 콘텐츠)`,
`chunk_id = sha256(doc_id + locator)`. 같은 자료를 다시 적재하면 같은 ID로
upsert/MERGE되어 중복 없이 갱신된다.

**부분 실패(CONVENTIONS)**: `ingest_manifest`는 일부 항목이 실패해도 전체를
중단하지 않고 부분 적재 + 실패 목록을 보고한다.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from kms.adapters.graph.base import GraphStore
from kms.adapters.llm.base import LLMClient
from kms.adapters.ingestion.chunk.models import Chunk
from kms.adapters.ingestion.chunk.registry import get_chunker
from kms.adapters.ingestion.extract.registry import get_extractor
from kms.adapters.searchindex.base import SearchIndex
from kms.adapters.vectorstore.base import VectorStore
from kms.adapters.vectorstore.embedder import Embedder
from kms.domain.access import AccessLevel
from kms.domain.errors import MetadataValidationError
from kms.domain.models import DocumentMetadata, FileDoc, SourceType

logger = logging.getLogger(__name__)


class IngestItem(BaseModel):
    """매니페스트 항목 — 적재할 파일 1건 + 메타데이터.

    `source`·`access`는 필수다(누락 시 검증 에러 → 색인 안 함). 나머지 메타는
    선택. `from_raw`로 매니페스트의 원시 dict를 검증·변환한다.
    """

    file_path: str
    source: SourceType
    access: AccessLevel
    author: str | None = None
    author_department: str | None = None
    source_url: str | None = None

    @classmethod
    def from_raw(cls, raw: dict[str, object]) -> "IngestItem":
        """매니페스트 원시 항목을 검증해 `IngestItem`으로 만든다.

        pydantic 검증 실패(필수 `source`·`access` 누락·형식 오류)는 도메인
        `MetadataValidationError`로 변환해 적재 경계에서 명확히 거부한다.
        """
        try:
            return cls.model_validate(raw)
        except ValidationError as exc:
            raise MetadataValidationError(
                f"적재 메타데이터 검증 실패(file_path={raw.get('file_path', '?')}): {exc}"
            ) from exc


class IngestReport(BaseModel):
    """적재 결과 1건 — 성공 시 doc_id·청크 수, 실패 시 사유.

    `ingest_manifest`는 성공·실패 항목을 모두 이 리포트 목록으로 보고한다
    (부분 적재 + 실패 목록).
    """

    file_path: str
    ok: bool
    doc_id: str | None = None
    chunk_count: int = 0
    error: str | None = None


class IngestionService:
    """적재 파이프라인을 조립·실행하는 유스케이스."""

    def __init__(
        self,
        vectorstore: VectorStore,
        graph_store: GraphStore,
        embedder: Embedder,
        search_index: SearchIndex,
        summarizer: LLMClient | None = None,
    ) -> None:
        self._vectorstore = vectorstore
        self._graph_store = graph_store
        # VectorStore.index가 내부에서 임베딩을 수행한다(주입된 embedder 사용).
        # 서비스는 의존 조립의 일관성을 위해 같은 embedder 참조를 보관한다 —
        # 호출자는 동일 인스턴스를 vectorstore와 서비스에 함께 주입한다.
        self._embedder = embedder
        # 어휘 검색 인덱스 — 적재 시 파일 단위 FileDoc을 색인한다(vector/graph와 별개).
        self._search_index = search_index
        # 검색 화면 요약(description)을 만드는 LLM. None이면 추출 발췌로 폴백한다
        # (LLM 없이도 적재는 동작 — 요약 품질만 떨어진다).
        self._summarizer = summarizer

    def ingest_item(self, item: IngestItem) -> IngestReport:
        """단건 적재: 추출 → 메타 구성 → 청킹 → 멱등 ID 부여 → vector+graph+search 색인.

        `source`·`access`는 `IngestItem` 검증으로 보장된다(누락 항목은 생성 단계에서
        `MetadataValidationError`). 미지원 형식은 `UnsupportedFormatError`,
        추출/색인 실패는 시스템 에러로 전파된다 — 조용히 삼키지 않는다.
        """
        path = Path(item.file_path)

        # 추출기 registry로 형식별 IR 추출 (미지원 형식 → UnsupportedFormatError).
        extractor = get_extractor(path)
        ir = extractor.extract(path)

        # item 메타 → DocumentMetadata. 적재일자는 서비스가 적재 시점으로 기록한다.
        # title은 파일명 기본 (source_url에 파일명이 있으면 그것을 우선).
        title = _title_from_item(item, path)
        doc_meta = DocumentMetadata(
            source=item.source,
            access=item.access,
            author=item.author,
            author_department=item.author_department,
            source_url=item.source_url,
            title=title,
            ingested_at=datetime.now(UTC),
        )

        # IR 타입에 맞는 청커로 청킹 (모든 청크가 doc-level 메타 상속).
        chunker = get_chunker(ir)
        local_chunks = chunker.chunk(ir, doc_meta)

        # 멱등 ID: 콘텐츠 해시 기반. 같은 파일 → 같은 doc_id → 같은 chunk_id.
        doc_id = _content_hash(path)
        chunks = [_with_global_id(chunk, doc_id) for chunk in local_chunks]

        # vector(InMemory/OpenSearch) + graph(Neo4j/InMemory) 양쪽 색인. 메타 없는 색인 금지.
        self._vectorstore.index(chunks)
        self._graph_store.add_document(doc_id, doc_meta)
        self._graph_store.add_chunks(doc_id, chunks)

        # 파일 단위 어휘 인덱스에도 한 건 색인 — vector/graph와 같은 doc_id라 멱등
        # 덮어쓰기. 실패는 삼키지 않고 전파한다(상위 _ingest_one이 항목 단위로 격리).
        # description(검색 화면 요약)은 LLM으로 깔끔히 생성한다(없으면 추출 발췌 폴백).
        description = self._describe(chunks)
        file_doc = _build_file_doc(item, doc_meta, doc_id, description)
        self._search_index.index_file(file_doc)

        logger.info(
            "문서 적재 완료",
            extra={"doc_id": doc_id, "chunk_count": len(chunks), "source": doc_meta.source.value},
        )
        return IngestReport(
            file_path=item.file_path,
            ok=True,
            doc_id=doc_id,
            chunk_count=len(chunks),
        )

    def ingest_items(self, items: list[IngestItem]) -> list[IngestReport]:
        """검증된 `IngestItem` 목록을 일괄 적재 — 부분 실패 허용.

        HTTP 적재 경로(api/v1/ingest)가 쓴다 — FastAPI가 본문을 `IngestItem`으로
        이미 검증하므로 여기서는 적재 실패(추출/색인)만 항목별로 격리한다.
        한 항목이 실패해도 전체를 중단하지 않고 성공·실패를 모두 보고한다.
        """
        return [self._ingest_one(item) for item in items]

    def _describe(self, chunks: list[Chunk]) -> str:
        """검색 화면 요약(description)을 만든다 — LLM 1~2문장 요약, 실패 시 발췌 폴백.

        요약기(LLM)가 주입돼 있으면 본문 앞부분을 넘겨 깔끔한 한국어 요약을 받는다.
        LLM 미주입·빈 본문(이미지 전용)·호출 실패·빈 응답이면 추출 발췌
        (`_first_meaningful_excerpt`)로 폴백한다 — 적재 자체는 절대 막지 않는다.
        """
        fallback = _first_meaningful_excerpt(chunks)
        if self._summarizer is None:
            return fallback
        source = _summary_source(chunks)
        if not source:
            return fallback
        try:
            raw = self._summarizer.generate(
                _SUMMARY_PROMPT.format(source=source), system=_SUMMARY_SYSTEM
            )
        except Exception as exc:  # noqa: BLE001 — 요약은 부가 기능: 실패해도 적재는 진행
            # 조용한 실패 아님: 사유를 로깅하고 발췌로 폴백한다(LLM 장애가 적재를 막지 않게).
            logger.warning("LLM 요약 실패 — 추출 발췌로 폴백 (%s)", type(exc).__name__)
            return fallback
        summary = " ".join(raw.split()).strip()
        if not summary:
            logger.warning("LLM 요약 빈 응답 — 추출 발췌로 폴백")
            return fallback
        return summary[:_DESCRIPTION_MAX_LEN].rstrip()

    def delete_document(self, doc_id: str) -> bool:
        """문서를 세 저장소(vector·graph·search_index)에서 모두 삭제한다.

        search_index의 FileDoc로 존재 여부와 source_url을 해소한다 — 없으면 False
        (이미 없거나 잘못된 doc_id). 청크는 doc_id를 메타에 안 들고 source_url을
        들고 있어 vectorstore는 source_url로 지운다. graph·search_index는 doc_id로.
        부분 삭제 방지를 위해 vector→graph→search 순으로 지우되, 실패는 전파한다
        (조용한 실패 금지 — 호출자가 5xx로 처리).
        """
        file_doc = self._search_index.get(doc_id)
        if file_doc is None:
            return False
        if file_doc.source_url:
            self._vectorstore.delete_by_source_url(file_doc.source_url)
        self._graph_store.delete(doc_id)
        self._search_index.delete(doc_id)
        logger.info("문서 삭제", extra={"doc_id": doc_id})
        return True

    def ingest_manifest(self, path: str | Path) -> list[IngestReport]:
        """매니페스트(YAML/JSON) 일괄 적재 — 부분 실패 허용.

        항목별로 검증·적재하고, 한 항목이 실패해도 전체를 중단하지 않는다.
        성공·실패를 모두 `IngestReport` 목록으로 보고한다.
        """
        raw_items = _load_manifest(Path(path))
        reports: list[IngestReport] = []
        for raw in raw_items:
            file_path = str(raw.get("file_path", "?"))
            try:
                item = IngestItem.from_raw(raw)
            except MetadataValidationError as exc:
                # 메타데이터 검증 실패는 도메인 거부 — 색인 전 실패로 보고(삼키지 않음).
                logger.warning(
                    "적재 항목 메타데이터 검증 실패",
                    extra={"file_path": file_path, "error": str(exc)},
                )
                reports.append(IngestReport(file_path=file_path, ok=False, error=str(exc)))
                continue
            reports.append(self._ingest_one(item))
        return reports

    def _ingest_one(self, item: IngestItem) -> IngestReport:
        """단건 적재를 실패 격리해 `IngestReport`로 만든다 (부분 실패 규약).

        추출·색인 실패는 한 항목이 배치를 죽이지 않도록 폭넓게 잡아 실패로 보고한다 —
        삼키지 않고 경고 로깅한다(CONVENTIONS 부분 실패 허용).
        """
        try:
            return self.ingest_item(item)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "적재 항목 실패", extra={"file_path": item.file_path, "error": str(exc)}
            )
            return IngestReport(file_path=item.file_path, ok=False, error=str(exc))


# 검색 화면 요약(description)으로 보여줄 발췌 최대 길이 — 1~2줄.
_DESCRIPTION_MAX_LEN = 200
# 정제 후 이 길이 미만이면 "의미 없는 청크"로 보고 다음 청크를 본다(이미지/표만 있는 첫 청크 회피).
_DESCRIPTION_MIN_LEN = 10

# 추출 노이즈 패턴 — 검색 요약 품질을 떨어뜨리는 추출기 산출물.
#  - 이미지 플레이스홀더: pdf 마크다운의 `**==> picture [W x H] intentionally omitted <==**`.
#  - 빈 표 헤더: xlsx 마크다운 표의 `Unnamed: N`(헤더 없는 컬럼) 셀.
_PICTURE_PLACEHOLDER_RE = re.compile(r"\*\*==>.*?<==\*\*")
_UNNAMED_COLUMN_RE = re.compile(r"Unnamed:\s*\d+")


def _strip_extraction_noise(text: str) -> str:
    """추출기 산출 노이즈(이미지 플레이스홀더·빈 헤더·표 구분행)를 걷어낸다.

    검색 요약(description)에 `**==> picture ... <==**`나 `| Unnamed: 0 | ... |`,
    표 구분행(`| --- | --- |`), 빈 마크다운 헤딩(`##`)이 그대로 노출되지 않도록
    의미 있는 텍스트만 남긴다. 본문 검색 색인(vector/graph)에는 영향을 주지 않는다 —
    화면 요약 가독성만을 위한 정제다.
    """
    text = _PICTURE_PLACEHOLDER_RE.sub(" ", text)
    text = _UNNAMED_COLUMN_RE.sub(" ", text)
    kept: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # 빈 마크다운 헤딩(`#`/`##`/`###`처럼 텍스트 없는 헤더) 스킵.
        if set(line) <= {"#"}:
            continue
        # 표 구분행/빈 파이프행(`|`,`-`,`:`,공백만 남은 줄) 스킵.
        if not line.replace("|", "").replace("-", "").replace(":", "").strip():
            continue
        # 표 셀 구분자(`|`)는 공백으로, 빈 셀(`nan`)은 제거.
        cell = re.sub(r"\bnan\b", " ", line.replace("|", " "))
        if cell.strip():
            kept.append(cell)
    return " ".join(kept)


def _excerpt(text: str, max_len: int = _DESCRIPTION_MAX_LEN) -> str:
    """본문 앞부분 발췌 — 추출 노이즈 제거 + 공백 정규화 후 `max_len`자로 자른다.

    검색 화면 요약(1~2줄)용. 이미지 플레이스홀더·빈 표 헤더 등은
    `_strip_extraction_noise`로 걷어낸 뒤 발췌한다.
    """
    normalized = " ".join(_strip_extraction_noise(text).split())
    if len(normalized) <= max_len:
        return normalized
    return normalized[:max_len].rstrip()


# LLM 요약 입력 상한 — 본문 앞부분 이만큼만 모델에 넘긴다(토큰·지연 한도, 요약엔 충분).
_SUMMARY_INPUT_MAX_CHARS = 3000
# 요약 시스템 프롬프트 — 간결·근거 기반·머리말/따옴표 금지.
_SUMMARY_SYSTEM = (
    "너는 사내 문서 검색 요약기다. 주어진 문서 발췌를 바탕으로 한국어 1~2문장의 "
    "간결한 요약만 출력한다. 발췌에 없는 내용은 지어내지 않는다. "
    "'요약:' 같은 머리말, 따옴표, 마크다운, 목록 기호 없이 요약문만 쓴다."
)
# 요약 사용자 프롬프트 — 본문 발췌를 끼운다.
_SUMMARY_PROMPT = "다음 문서 내용을 1~2문장으로 요약하라.\n\n---\n{source}\n---"


def _summary_source(chunks: list[Chunk]) -> str:
    """LLM 요약 입력 본문을 만든다 — 노이즈 제거한 앞쪽 청크들을 상한까지 이어붙인다.

    이미지/빈 표만 있는 청크는 정제 후 비어 건너뛴다. 정제분이 모두 비면(이미지 전용
    문서) 빈 문자열을 돌려 호출자가 발췌 폴백을 쓰게 한다.
    """
    parts: list[str] = []
    total = 0
    for chunk in chunks:
        clean = " ".join(_strip_extraction_noise(chunk.text).split())
        if not clean:
            continue
        parts.append(clean)
        total += len(clean)
        if total >= _SUMMARY_INPUT_MAX_CHARS:
            break
    return " ".join(parts)[:_SUMMARY_INPUT_MAX_CHARS]


def _first_meaningful_excerpt(chunks: list[Chunk]) -> str:
    """청크를 순서대로 훑어 노이즈 정제 후 충분한 길이의 첫 발췌를 고른다.

    이미지 플레이스홀더·빈 표만 있는 앞쪽 청크(정제 후 거의 빈 문자열)를 건너뛰고
    실제 본문이 처음 나오는 청크를 요약으로 쓴다(예: 표지가 전부 이미지인 PDF).
    모든 청크가 비면 빈 문자열을 반환한다(이미지 전용 문서 — 표시할 본문 없음).
    """
    for chunk in chunks:
        excerpt = _excerpt(chunk.text)
        if len(excerpt) >= _DESCRIPTION_MIN_LEN:
            return excerpt
    return ""


def _title_from_item(item: IngestItem, path: Path) -> str:
    """파일 제목 결정 — source_url의 basename을 우선, 없으면 path.name."""
    if item.source_url:
        without_scheme = item.source_url.split("://", 1)[-1]
        candidate = without_scheme.rsplit("/", 1)[-1]
        if candidate:
            return candidate
    return path.name


def _build_file_doc(
    item: IngestItem,
    doc_meta: DocumentMetadata,
    doc_id: str,
    description: str,
) -> FileDoc:
    """적재 파일 1건의 어휘 인덱스 도큐먼트(`FileDoc`)를 구성한다 (순수 함수).

    - `title`: 확장자 포함 파일명(`Path.name`) — 검색 결과 화면의 제목.
    - `description`: 호출자가 만든 검색 화면 요약(LLM 1~2문장 또는 추출 발췌 폴백).
    - `tags`: 빈 리스트(현 단계 태그 입력 없음 — 추후 확장 여지).
    - `doc_type`: 확장자 대문자(`.pdf`→`PDF`), 확장자 없으면 None.
    - `doc_id`: vector/graph와 같은 콘텐츠 해시 ID라 재적재 시 멱등 덮어쓰기.
    """
    path = Path(item.file_path)
    doc_type = path.suffix.lstrip(".").upper() or None
    return FileDoc(
        doc_id=doc_id,
        title=path.name,
        description=description,
        tags=[],
        author=item.author,
        author_department=item.author_department,
        source=item.source,
        source_url=item.source_url,
        doc_type=doc_type,
        ingested_at=doc_meta.ingested_at,
        access=item.access,
    )


def _content_hash(path: Path) -> str:
    """파일 콘텐츠의 SHA-256 hexdigest — 멱등 `doc_id`."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _with_global_id(chunk: Chunk, doc_id: str) -> Chunk:
    """청커가 매긴 locator(예: `p1:0`)를 콘텐츠 해시 기반 전역 ID로 교체한다.

    `chunk_id = sha256(doc_id + ':' + locator)`. 같은 콘텐츠·같은 locator는 항상
    같은 ID라 재적재 시 vectorstore upsert·graph MERGE로 중복 없이 갱신된다.
    """
    locator = chunk.chunk_id
    global_id = hashlib.sha256(f"{doc_id}:{locator}".encode()).hexdigest()
    return chunk.model_copy(update={"chunk_id": global_id})


def _load_manifest(path: Path) -> list[dict[str, object]]:
    """매니페스트 파일(YAML/JSON)을 항목 dict 목록으로 로드한다.

    YAML 파서는 JSON도 파싱하므로 확장자 무관하게 `yaml.safe_load`로 읽는다.
    최상위는 항목 리스트여야 한다 (아니면 형식 오류).
    """
    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise MetadataValidationError(f"매니페스트 파싱 실패({path}): {exc}") from exc
    if not isinstance(data, list):
        raise MetadataValidationError(
            f"매니페스트 최상위는 항목 리스트여야 한다({path}): {type(data).__name__}"
        )
    return [_as_item_dict(entry, path) for entry in data]


def _as_item_dict(entry: object, path: Path) -> dict[str, object]:
    """매니페스트 항목이 dict인지 확인한다 (아니면 형식 오류)."""
    if not isinstance(entry, dict):
        raise MetadataValidationError(
            f"매니페스트 항목은 매핑이어야 한다({path}): {type(entry).__name__}"
        )
    return entry
