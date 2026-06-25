"""핵심 도메인 모델·규칙 (외부 의존 없음 — 의존성 방향 최내곽).

여기에는 FastAPI·DB·LangChain·OpenSearch·Neo4j를 import하지 않는다.
검증은 pydantic으로, 권한·랭킹 규칙은 순수 함수로 둔다.

용어 (CONVENTIONS): `source`=소스 종류, `access`(권한 태그)=접근 통제 하드 필터,
`author_department`(부서)=부서 가중 soft boost 기준.
"""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel

from kms.domain.access import AccessLevel


class SourceType(str, Enum):
    """적재 자료의 소스 종류 (ADR-012). 출처 표기·검색 필터에 쓰인다."""

    ONEDRIVE = "onedrive"
    GOOGLEDRIVE = "googledrive"
    SLACK = "slack"


class DocumentMetadata(BaseModel):
    """적재 문서 메타데이터. `source`·`access`는 필수 (누락 시 검증 에러).

    `title`은 파일 표시명(파일명 또는 추출된 문서 제목). 벡터 메타에 함께 저장돼
    검색 결과·인용 표시에 즉시 사용된다 (별도 인덱스 조회 없이).
    """

    source: SourceType
    access: AccessLevel
    author: str | None = None
    author_department: str | None = None
    source_url: str | None = None
    title: str | None = None
    ingested_at: datetime | None = None


class Document(BaseModel):
    """색인 대상 문서 — 본문 + 메타데이터."""

    doc_id: str
    content: str
    metadata: DocumentMetadata


class UserContext(BaseModel):
    """요청 사용자 맥락 — 권한 필터·부서 가중의 기준 (PostgreSQL SSO 계정에서 조회)."""

    user_id: str
    department: str
    access_level: AccessLevel


class SearchQuery(BaseModel):
    """통합 검색 질의."""

    text: str
    source_filter: SourceType | None = None
    top_k: int = 10
    # 추가 필터(옵션). None이면 미적용. doc_type은 대문자(`PDF`/`DOCX`/...).
    doc_type_filter: str | None = None
    # 최근 N일 필터(`ingested_at >= now - N days`). None이면 미적용.
    days_window: int | None = None
    # 명시 날짜 범위(ISO YYYY-MM-DD). days_window가 None일 때만 사용.
    date_from: str | None = None
    date_to: str | None = None
    # 사용자가 비활성화한 커넥터(source) 집합 — 적재는 유지되지만 조회에서 제외된다.
    # 예: {"slack"} → Slack 출처는 검색·RAG 결과에 나타나지 않음.
    disabled_sources: list[SourceType] = []


class SearchResult(BaseModel):
    """검색 결과 한 건 — 문서 + 관련도 점수."""

    document: Document
    score: float


class FileDoc(BaseModel):
    """파일 단위 통합 검색 인덱스 도큐먼트 (파일당 1건).

    청크 벡터(OpenSearch k-NN, RAG용)와 분리된 어휘 검색 인덱스의 단위다. 검색 화면이
    보여주는 6필드(제목·요약·작성일·원본소스·작성자·문서유형)를 그대로 담는다.
    `access`는 권한 인지 하드 필터, `author_department`는 부서 가중(soft) 기준 —
    역할이 다르다(ADR-012/ADR-013, 혼동 금지).
    """

    doc_id: str
    title: str
    description: str = ""
    tags: list[str] = []
    author: str | None = None
    author_department: str | None = None
    source: SourceType
    source_url: str | None = None
    doc_type: str | None = None
    ingested_at: datetime | None = None
    access: AccessLevel


class FileHit(BaseModel):
    """파일 단위 검색 결과 한 건 — 파일 도큐먼트 + 관련도 점수."""

    file: FileDoc
    score: float


class Citation(BaseModel):
    """RAG 답변이 근거로 쓴 출처 한 건 (출처 인용 강제 — ADR-007).

    `page`·`slide_no`는 형식별로 있을 때만 채워진다(없으면 None).
    `title`은 UI 툴팁용 표시명(snippet 첫 라인 또는 메타 제목), `source_url`은
    원본 다운로드/이동 클릭 시 사용한다(둘 다 옵션 — 없으면 doc_id로 폴백).
    """

    source: SourceType
    doc_id: str
    page: int | None = None
    slide_no: int | None = None
    snippet: str
    title: str | None = None
    source_url: str | None = None


class Answer(BaseModel):
    """RAG 답변 — 본문 + 사용한 출처 인용 + 근거 기반 여부.

    `grounded=False`는 검색 근거가 0건이라 LLM 자유생성 없이 반환한 경우다
    (근거 없는 자유생성 금지 — CONVENTIONS). 이때 `citations`는 비어 있다.
    """

    text: str
    citations: list[Citation]
    grounded: bool


class WordSpan(BaseModel):
    """변경 라인 안의 단어 한 조각 — 단어 단위 강조용 (ADR-008 문서 비교).

    `changed=True`면 두 문서 사이에서 바뀐 단어다. 프론트가 이 플래그로
    변경 단어만 하이라이트한다. 강조는 `change` 라인 안에서만 의미가 있다.
    """

    text: str
    changed: bool


class DiffOp(BaseModel):
    """두 문서 비교에서 라인 단위 연산 한 건.

    `equal`은 양쪽 동일, `add`는 오른쪽에만, `delete`는 왼쪽에만, `change`는
    같은 위치의 라인이 바뀐 경우다. `change`일 때만 `left_words`·`right_words`에
    단어 단위 강조(`WordSpan`)가 채워진다(그 외에는 None).
    """

    op: Literal["equal", "add", "delete", "change"]
    left: str | None = None
    right: str | None = None
    left_words: list[WordSpan] | None = None
    right_words: list[WordSpan] | None = None


class DiffResult(BaseModel):
    """두 문서 비교 결과 — 라인 연산 목록 + 추가/삭제/변경 라인 수.

    `image_blobs`는 `[IMAGE sha=...]` 마커가 가리키는 실제 이미지의 data URL 맵
    (sha → `data:image/...;base64,...`). 두 문서에서 발견된 이미지를 합쳐 보낸다 —
    같은 sha면 같은 그림이므로 한 번만 보내고 양쪽 UI가 같은 src를 쓴다. 그림이
    없으면 빈 dict.

    `page_previews_a`/`page_previews_b`는 각 원본 문서의 페이지 단위 PNG 프리뷰
    맵 (page_number(1-base) → data URL). 비교 결과 옆에 "원본 페이지 그대로"를
    띄워 사용자가 어떤 페이지의 어디가 바뀌었는지 시각적으로 확인할 수 있게 한다.
    프리뷰는 보조 기능 — 미지원 포맷·렌더러 부재 시 빈 dict로 graceful degrade.
    """

    ops: list[DiffOp]
    added: int
    deleted: int
    changed: int
    image_blobs: dict[str, str] = {}
    page_previews_a: dict[int, str] = {}
    page_previews_b: dict[int, str] = {}


def is_visible_to(meta: DocumentMetadata, user: UserContext) -> bool:
    """권한 인지(하드): 사용자 access 레벨이 문서 access를 포함하면 노출 가능.

    이 함수만이 노출 여부를 결정한다. 부서 가중은 노출을 바꾸지 않는다.
    """
    return user.access_level.can_access(meta.access)


def department_boost(meta: DocumentMetadata, user: UserContext, weight: float) -> float:
    """부서 가중(soft): 작성자 부서 == 사용자 부서면 weight, 아니면 0.0.

    점수만 반환하며 노출 여부와 무관하다 (권한 통과분의 순위 조정용 — ADR-013).
    권한 밖 문서를 이 가중으로 끌어올리지 마라.
    """
    if meta.author_department is not None and meta.author_department == user.department:
        return weight
    return 0.0
